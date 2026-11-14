"""Aidweave web application. Configure secrets and PostgreSQL before deployment."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, g, jsonify, make_response, request, send_from_directory, session
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

ROOT = Path(__file__).parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'aidweave.db'}")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]
if DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def sqlite_pragmas(connection, _record):
        cursor=connection.cursor(); cursor.execute("PRAGMA foreign_keys=ON"); cursor.execute("PRAGMA busy_timeout=30000"); cursor.close()
PRODUCTION = os.getenv("APP_ENV", "development").lower() == "production"
SECRET = os.getenv("SECRET_KEY", "")
if PRODUCTION and len(SECRET) < 32:
    raise RuntimeError("Set SECRET_KEY to a random value of at least 32 characters.")
if not SECRET:
    SECRET = secrets.token_urlsafe(48)
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "console" if not PRODUCTION else "").lower()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
if PRODUCTION:
    if not PUBLIC_BASE_URL or urlparse(PUBLIC_BASE_URL).scheme != "https" or not urlparse(PUBLIC_BASE_URL).netloc:
        raise RuntimeError("Set PUBLIC_BASE_URL to the canonical HTTPS site address.")
    if DATABASE_URL.startswith("sqlite:"):
        raise RuntimeError("Production requires a persistent PostgreSQL DATABASE_URL.")
    if EMAIL_PROVIDER not in ("smtp", "resend"):
        raise RuntimeError("Set EMAIL_PROVIDER to 'smtp' or 'resend' before production startup.")
    if not os.getenv("SMTP_FROM"):
        raise RuntimeError("Set SMTP_FROM to a sender address verified by your email provider.")
    if EMAIL_PROVIDER == "smtp" and not os.getenv("SMTP_HOST"):
        raise RuntimeError("Set SMTP_HOST for the selected SMTP email provider.")
    if EMAIL_PROVIDER == "resend" and not os.getenv("RESEND_API_KEY"):
        raise RuntimeError("Set RESEND_API_KEY for the selected Resend email provider.")

app = Flask(__name__, static_folder=None)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = SECRET
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=PRODUCTION,
                  SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
                  MAX_CONTENT_LENGTH=32_000)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('seeker','volunteer','ngo','admin')),
 country TEXT NOT NULL DEFAULT '', organization TEXT NOT NULL DEFAULT '',
 email_verified INTEGER NOT NULL DEFAULT 0, ngo_status TEXT NOT NULL DEFAULT 'none',
 terms_accepted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tokens (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL UNIQUE, purpose TEXT NOT NULL, expires_at TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS requests (
 id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id), title TEXT NOT NULL,
 details TEXT NOT NULL, category TEXT NOT NULL, location TEXT NOT NULL,
 urgency TEXT NOT NULL DEFAULT 'normal', visibility TEXT NOT NULL DEFAULT 'public',
 status TEXT NOT NULL DEFAULT 'open', assigned_id INTEGER REFERENCES users(id), invite_email TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 hidden INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
 sender_id INTEGER NOT NULL REFERENCES users(id), body TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reports (
 id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
 reporter_id INTEGER REFERENCES users(id), reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, resolved_by INTEGER REFERENCES users(id),
 resolution TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit_log (
 id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id), action TEXT NOT NULL,
 entity_type TEXT NOT NULL, entity_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ngo_submissions (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 proof TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', reviewed_by INTEGER REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS request_invites (
 id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
 recipient_email TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL,
 accepted_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_requests_public ON requests(hidden, visibility, status, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_request ON messages(request_id, id);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status, id);
"""
PRIMARY_KEY = "INTEGER PRIMARY KEY AUTOINCREMENT" if engine.dialect.name == "sqlite" else "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"
SCHEMA = SCHEMA.replace("id INTEGER PRIMARY KEY", "id " + PRIMARY_KEY)
if engine.dialect.name != "sqlite":
    SCHEMA = SCHEMA.replace(" INTEGER NOT NULL REFERENCES", " BIGINT NOT NULL REFERENCES").replace(" INTEGER REFERENCES", " BIGINT REFERENCES")

def init_db():
    with engine.begin() as db:
        # Keep data from the first local prototype rather than failing on its old table shape.
        inspector=inspect(engine); tables=set(inspector.get_table_names()); legacy_exists="requests_legacy" in tables
        existing="requests" in tables
        if existing:
            columns={c["name"] for c in inspector.get_columns("requests")}
            if "owner_id" not in columns:
                if engine.dialect.name!="sqlite": raise RuntimeError("The legacy database needs to be exported before upgrading to PostgreSQL.")
                db.execute(text("ALTER TABLE requests RENAME TO requests_legacy"))
                legacy_exists=True
        for statement in SCHEMA.split(";"):
            if statement.strip():
                db.execute(text(statement))
        if db.execute(text("SELECT COUNT(*) FROM requests")).scalar() == 0:
            # Clearly fictional examples; remove by setting SEED_DEMO=0 for a clean first launch.
            if os.getenv("SEED_DEMO", "1") != "0":
                demo = [("School supplies for the new term", "Looking for notebooks and pencils for two children. A community pickup point would help.", "Education", "Pune, India", "normal", "public", 1),
                        ("Grocery support this week", "A small grocery parcel would help. I can coordinate a handoff at a nearby community center.", "Food", "Bristol, UK", "high", "public", 2),
                        ("Wheelchair repair advice", "The front wheel is damaged. Looking for repair guidance or a local repair resource.", "Accessibility", "Austin, USA", "normal", "public", 3)]
                # Demo rows are owned by reserved non-login system identities.
                for i in range(1, 4):
                    db.execute(text("INSERT INTO users(id,email,name,password_hash,role,email_verified) VALUES(:id,:email,:name,'disabled','seeker',1) ON CONFLICT(email) DO NOTHING"), {"id": -i, "email": f"demo{i}@invalid.local", "name": f"Community member {i}"})
                for title, details, category, location, urgency, visibility, owner in demo:
                    db.execute(text("INSERT INTO requests(owner_id,title,details,category,location,urgency,visibility) VALUES(:owner,:title,:details,:category,:location,:urgency,:visibility)"), {"owner":-owner,"title":title,"details":details,"category":category,"location":location,"urgency":urgency,"visibility":visibility})
        if legacy_exists:
            for row in db.execute(text("SELECT title,details,category,location,urgency,requester,status FROM requests_legacy")).mappings():
                owner_id=-999
                db.execute(text("INSERT INTO users(id,email,name,password_hash,role,email_verified) VALUES(-999,'legacy@invalid.local','Community member','disabled','seeker',1) ON CONFLICT(email) DO NOTHING"))
                db.execute(text("INSERT INTO requests(owner_id,title,details,category,location,urgency,visibility,status) VALUES(:owner,:title,:details,:category,:location,:urgency,'public',:status)"), {"owner":owner_id,"title":row["title"],"details":row["details"],"category":row["category"],"location":row["location"],"urgency":str(row["urgency"]).lower(),"status":str(row["status"]).lower() if str(row["status"]).lower() in ("open","matched","completed","cancelled","in_progress") else "open"})
            db.execute(text("DROP TABLE requests_legacy"))

init_db()

def utcnow():
    return datetime.now(timezone.utc)

def audit(db, actor, action, kind, entity):
    db.execute(text("INSERT INTO audit_log(actor_id,action,entity_type,entity_id) VALUES(:actor,:action,:kind,:entity)"),
               {"actor": actor, "action": action, "kind": kind, "entity": entity})

def current_user():
    if not session.get("uid"):
        return None
    if not hasattr(g, "user"):
        with engine.connect() as db:
            row = db.execute(text("SELECT id,email,name,role,country,organization,email_verified,ngo_status,disabled FROM users WHERE id=:id"), {"id": session["uid"]}).mappings().first()
        g.user = dict(row) if row and not row["disabled"] else None
    return g.user

def auth_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify(error="Sign in to continue."), 401
        if not user["email_verified"]:
            return jsonify(error="Verify your email before using this feature."), 403
        return fn(*args, **kwargs)
    return wrapped

def roles(*allowed):
    def decorator(fn):
        @wraps(fn)
        @auth_required
        def wrapped(*args, **kwargs):
            if current_user()["role"] not in allowed:
                return jsonify(error="Your account role cannot do this."), 403
            if current_user()["role"] == "ngo" and current_user()["ngo_status"] != "approved":
                return jsonify(error="Your organization must be verified first."), 403
            return fn(*args, **kwargs)
        return wrapped
    return decorator

def csrf_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        expected, supplied = session.get("csrf"), request.headers.get("X-CSRF-Token", "")
        if not expected or not hmac.compare_digest(expected, supplied):
            return jsonify(error="Refresh the page and try again."), 403
        return fn(*args, **kwargs)
    return wrapped

# Lightweight per-process throttling. A multi-instance deployment should put a shared
# Redis-backed limiter in front of the service; the Render blueprint starts with one instance.
attempts = defaultdict(deque)
def rate_limited(bucket, limit, seconds):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            key = (bucket, request.remote_addr or "unknown")
            now = time.monotonic(); q = attempts[key]
            while q and q[0] < now-seconds: q.popleft()
            if len(q) >= limit: return jsonify(error="Too many attempts. Please wait a little and try again."), 429
            q.append(now)
            return fn(*args, **kwargs)
        return wrapped
    return decorator

def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    result = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + salt.hex() + "$" + result.hex()

def verify_password(password, encoded):
    try:
        _, salt, expected = encoded.split("$")
        actual = password_hash(password, bytes.fromhex(salt)).split("$")[-1]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def clean(value, maximum, required=True):
    value = str(value or "").strip()
    if required and not value: raise ValueError("Complete all required fields.")
    if len(value) > maximum: raise ValueError(f"Keep each field within {maximum} characters.")
    return value

def send_token(email, purpose, token):
    provider = EMAIL_PROVIDER
    subject = {"verify":"Verify your Aidweave email","reset":"Reset your Aidweave password","invite":"A private Aidweave request was shared with you"}
    query = {"verify":"verify=","reset":"reset=","invite":"invite="}
    base_url=PUBLIC_BASE_URL if PRODUCTION else request.url_root.rstrip("/")
    link = base_url + "/#" + query[purpose] + token
    message = EmailMessage(); message["Subject"] = subject[purpose]
    message["From"] = os.getenv("SMTP_FROM", "Aidweave <no-reply@example.org>")
    message["To"] = email
    expiry="14 days" if purpose=="invite" else "30 minutes"
    instruction="Open this private invitation link" if purpose=="invite" else "Use this one-time link"
    message.set_content(f"{instruction} within {expiry}:\n\n{link}\n\nIf you did not request this, ignore this message.")
    if provider == "console":
        app.logger.warning("Development %s link for %s: %s", purpose, email, link)
        return
    if provider == "resend":
        payload=json.dumps({"from":message["From"],"to":[email],"subject":str(message["Subject"]),"text":message.get_content()}).encode()
        req=urllib.request.Request("https://api.resend.com/emails",data=payload,headers={"Authorization":"Bearer "+os.environ["RESEND_API_KEY"],"Content-Type":"application/json"},method="POST")
        try:
            with urllib.request.urlopen(req,timeout=15) as response:
                if response.status >= 300: raise RuntimeError("Email provider rejected the message.")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("Email provider could not deliver the message.") from exc
        return
    host = os.getenv("SMTP_HOST")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as smtp:
        smtp.starttls()
        username = os.getenv("SMTP_USER"); password = os.getenv("SMTP_PASSWORD")
        if username: smtp.login(username, password or "")
        smtp.send_message(message)

def create_token(db, user_id, purpose):
    token = secrets.token_urlsafe(32)
    db.execute(text("INSERT INTO tokens(user_id,token_hash,purpose,expires_at) VALUES(:uid,:hash,:purpose,:expiry)"),
               {"uid":user_id,"hash":hashlib.sha256(token.encode()).hexdigest(),"purpose":purpose,"expiry":(utcnow()+timedelta(minutes=30)).isoformat()})
    return token

def auth_data(user):
    return {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"],
            "country": user["country"], "organization": user["organization"], "verified": bool(user["email_verified"]),
            "ngo_status": user["ngo_status"]}

@app.get("/")
def home(): return send_from_directory(ROOT, "index.html")

@app.get("/privacy")
def privacy_page(): return send_from_directory(ROOT, "privacy.html")

@app.get("/terms")
def terms_page(): return send_from_directory(ROOT, "terms.html")

@app.get("/<path:asset>")
def assets(asset):
    if asset in {"app.js", "styles.css", "app.webmanifest", "icon.svg", "service-worker.js", "theme-init.js"}: return send_from_directory(ROOT, asset)
    return jsonify(error="Not found"), 404

@app.get("/api/health")
def health():
    with engine.connect() as db: db.execute(text("SELECT 1"))
    return jsonify(ok=True)

@app.get("/api/bootstrap")
def bootstrap():
    session.setdefault("csrf", secrets.token_urlsafe(24))
    return jsonify(csrf=session["csrf"], user=auth_data(current_user()) if current_user() else None)

@app.post("/api/auth/register")
@csrf_required
@rate_limited("register", 6, 3600)
def register():
    try:
        data=request.get_json(force=True); email=clean(data.get("email"),254).lower(); name=clean(data.get("name"),80)
        password=clean(data.get("password"),200)
        role=clean(data.get("role"),20)
        if data.get("accept_terms") not in (True,"on","true","1"): raise ValueError("Confirm you are 18 or older and agree to the Terms and Privacy Notice.")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email): raise ValueError("Enter a valid email address.")
        if len(password)<12: raise ValueError("Use a password with at least 12 characters.")
        if role not in ("seeker","volunteer","ngo"): raise ValueError("Choose a valid account type.")
        country=clean(data.get("country",""),80,False); organization=clean(data.get("organization",""),120,role=="ngo")
        with engine.begin() as db:
            result=db.execute(text("INSERT INTO users(email,name,password_hash,role,country,organization,ngo_status) VALUES(:email,:name,:hash,:role,:country,:org,:ngo) RETURNING id"),
                              {"email":email,"name":name,"hash":password_hash(password),"role":role,"country":country,"org":organization,"ngo":"pending" if role=="ngo" else "none"})
            uid=result.scalar_one()
            token=create_token(db,uid,"verify")
            audit(db,uid,"account.created","user",uid)
        try:
            send_token(email,"verify",token)
            return jsonify(ok=True,email_sent=True,message="Account created. Check your email for a verification link."),201
        except Exception:
            app.logger.exception("Verification email delivery failed")
            return jsonify(ok=True,email_sent=False,message="Account created, but the verification email could not be sent. Sign in and try Resend verification email."),201
    except IntegrityError: return jsonify(error="An account with that email already exists."),409
    except ValueError as e: return jsonify(error=str(e)),400
    except Exception as e:
        app.logger.exception("Registration failed")
        return jsonify(error="Could not send the verification email. Check email settings and try again."),503

@app.post("/api/auth/verify")
@csrf_required
@rate_limited("verify", 12, 900)
def verify_email():
    token=str((request.get_json(silent=True) or {}).get("token", "")); digest=hashlib.sha256(token.encode()).hexdigest()
    with engine.begin() as db:
        row=db.execute(text("SELECT id,user_id FROM tokens WHERE token_hash=:hash AND purpose='verify' AND expires_at>:now"), {"hash":digest,"now":utcnow().isoformat()}).mappings().first()
        if not row: return jsonify(error="This verification link is invalid or expired."),400
        db.execute(text("UPDATE users SET email_verified=1 WHERE id=:uid"), {"uid":row["user_id"]}); db.execute(text("DELETE FROM tokens WHERE id=:id"), {"id":row["id"]})
        audit(db,row["user_id"],"email.verified","user",row["user_id"])
    session.clear(); session["uid"]=row["user_id"]; session["csrf"]=secrets.token_urlsafe(24); session.permanent=True
    return jsonify(ok=True,message="Email verified.")

@app.post("/api/auth/resend")
@csrf_required
@rate_limited("resend-verification", 4, 3600)
def resend_verification():
    user=current_user()
    if not user: return jsonify(ok=True,message="Sign in to request a verification link.")
    if user["email_verified"]: return jsonify(ok=True,message="Your email is already verified.")
    with engine.begin() as db:
        db.execute(text("DELETE FROM tokens WHERE user_id=:id AND purpose='verify'"), {"id":user["id"]})
        token=create_token(db,user["id"],"verify")
    try: send_token(user["email"],"verify",token)
    except Exception:
        app.logger.exception("Verification email resend failed")
        return jsonify(error="Could not send the email. Please try again later."),503
    return jsonify(ok=True,message="A new verification link has been sent.")

@app.post("/api/auth/login")
@csrf_required
@rate_limited("login", 12, 900)
def login():
    data=request.get_json(silent=True) or {}; email=str(data.get("email"," ")).strip().lower(); password=str(data.get("password",""))
    with engine.connect() as db: user=db.execute(text("SELECT * FROM users WHERE email=:email"), {"email":email}).mappings().first()
    if not user or user["disabled"] or not verify_password(password,user["password_hash"]): return jsonify(error="Email or password is incorrect."),401
    session.clear(); session["uid"]=user["id"]; session["csrf"]=secrets.token_urlsafe(24); session.permanent=True
    return jsonify(ok=True,user=auth_data(user))

@app.post("/api/auth/logout")
@csrf_required
def logout(): session.clear(); return jsonify(ok=True)

@app.post("/api/auth/forgot")
@csrf_required
@rate_limited("forgot", 5, 3600)
def forgot_password():
    email=str((request.get_json(silent=True) or {}).get("email"," ")).strip().lower()
    with engine.begin() as db:
        user=db.execute(text("SELECT id FROM users WHERE email=:email"), {"email":email}).mappings().first()
        if user: token=create_token(db,user["id"],"reset")
    if user:
        try: send_token(email,"reset",token)
        except Exception: app.logger.exception("Password reset email failed")
    return jsonify(ok=True,message="If an account exists, password reset instructions have been sent.")

@app.post("/api/auth/reset")
@csrf_required
@rate_limited("reset", 8, 900)
def reset_password():
    data=request.get_json(silent=True) or {}; token=str(data.get("token","")); password=str(data.get("password",""))
    if len(password)<12: return jsonify(error="Use a password with at least 12 characters."),400
    digest=hashlib.sha256(token.encode()).hexdigest()
    with engine.begin() as db:
        row=db.execute(text("SELECT id,user_id FROM tokens WHERE token_hash=:hash AND purpose='reset' AND expires_at>:now"), {"hash":digest,"now":utcnow().isoformat()}).mappings().first()
        if not row: return jsonify(error="This reset link is invalid or expired."),400
        db.execute(text("UPDATE users SET password_hash=:hash WHERE id=:uid"), {"hash":password_hash(password),"uid":row["user_id"]})
        db.execute(text("DELETE FROM tokens WHERE user_id=:uid"), {"uid":row["user_id"]}); audit(db,row["user_id"],"password.reset","user",row["user_id"])
    session.clear(); return jsonify(ok=True,message="Password updated. Sign in with your new password.")

@app.get("/api/requests")
def list_requests():
    user=current_user(); q=str(request.args.get("q",""))[:120]; category=str(request.args.get("category",""))[:60]; country=str(request.args.get("country",""))[:80]
    where=["r.hidden=0", "(r.visibility='public' OR r.owner_id=:uid OR r.assigned_id=:uid OR (:email!='' AND EXISTS (SELECT 1 FROM request_invites i WHERE i.request_id=r.id AND i.recipient_email=:email AND i.expires_at>:now)))"]
    args={"uid":user["id"] if user else 0,"email":user["email"] if user and user["email_verified"] else "","now":utcnow().isoformat(),"limit":100}
    if q: where.append("(LOWER(r.title) LIKE :q OR LOWER(r.details) LIKE :q OR LOWER(r.location) LIKE :q)"); args["q"]="%"+q.lower()+"%"
    if category: where.append("r.category=:category"); args["category"]=category
    if country: where.append("LOWER(r.location) LIKE :country"); args["country"]="%"+country.lower()+"%"
    sql="SELECT r.id,r.owner_id,r.assigned_id,r.title,r.details,r.category,r.location,r.urgency,r.visibility,r.status,r.created_at,COALESCE(NULLIF(u.organization,''),'Community member') requester,v.name volunteer FROM requests r JOIN users u ON u.id=r.owner_id LEFT JOIN users v ON v.id=r.assigned_id WHERE "+" AND ".join(where)+" ORDER BY CASE r.status WHEN 'open' THEN 0 WHEN 'matched' THEN 1 ELSE 2 END,r.created_at DESC LIMIT :limit"
    with engine.connect() as db: rows=db.execute(text(sql),args).mappings().all()
    result=[]
    for row in rows:
        item=dict(row)
        if not user or (user["id"] not in (item["owner_id"],item["assigned_id"]) and user["role"]!="admin"):
            item.pop("owner_id",None); item.pop("assigned_id",None); item.pop("volunteer",None)
        result.append(item)
    return jsonify(result)

@app.get("/api/invitations/<token>")
@auth_required
def get_invitation(token):
    digest=hashlib.sha256(token.encode()).hexdigest(); user=current_user()
    with engine.connect() as db:
        invite=db.execute(text("SELECT request_id FROM request_invites WHERE token_hash=:hash AND recipient_email=:email AND expires_at>:now"), {"hash":digest,"email":user["email"],"now":utcnow().isoformat()}).mappings().first()
        if not invite: return jsonify(error="This invitation is invalid, expired, or belongs to a different email."),404
        row=db.execute(text("SELECT id,title,category,location,status FROM requests WHERE id=:id AND hidden=0"), {"id":invite["request_id"]}).mappings().first()
    if not row: return jsonify(error="This request is no longer available."),404
    return jsonify(dict(row))

@app.post("/api/requests")
@roles("seeker","volunteer","ngo")
@csrf_required
@rate_limited("create-request", 8, 3600)
def create_request():
    try:
        d=request.get_json(force=True); title=clean(d.get("title"),120); details=clean(d.get("details"),2000); category=clean(d.get("category"),60); location=clean(d.get("location"),100)
        urgency=clean(d.get("urgency","normal"),10).lower(); visibility=clean(d.get("visibility","public"),10).lower(); invite_email=clean(d.get("invite_email",""),254,False).lower()
        if urgency not in ("normal","high"): raise ValueError("Choose a valid urgency.")
        if visibility not in ("public","private"): raise ValueError("Choose public or private visibility.")
        if visibility=="private" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",invite_email): raise ValueError("A private request needs the invitee's email address.")
        if visibility=="public" and (re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", title+" "+details) or re.search(r"(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)", title+" "+details) or re.search(r"\d", location)):
            raise ValueError("For privacy, remove email addresses, phone numbers, and street or house numbers from public requests.")
        user=current_user()
        with engine.begin() as db:
            result=db.execute(text("INSERT INTO requests(owner_id,title,details,category,location,urgency,visibility,invite_email) VALUES(:owner,:title,:details,:category,:location,:urgency,:visibility,:email) RETURNING id"),
                              {"owner":user["id"],"title":title,"details":details,"category":category,"location":location,"urgency":urgency,"visibility":visibility,"email":invite_email if visibility=="private" else ""})
            rid=result.scalar_one(); audit(db,user["id"],"request.created","request",rid)
            invite_token=None
            if visibility=="private":
                invite_token=secrets.token_urlsafe(32)
                db.execute(text("INSERT INTO request_invites(request_id,recipient_email,token_hash,expires_at) VALUES(:rid,:email,:hash,:expiry)"), {"rid":rid,"email":invite_email,"hash":hashlib.sha256(invite_token.encode()).hexdigest(),"expiry":(utcnow()+timedelta(days=14)).isoformat()})
        if invite_token:
            try: send_token(invite_email,"invite",invite_token)
            except Exception:
                app.logger.exception("Private request invitation email failed")
                return jsonify(ok=True,id=rid,invited=False,email_sent=False,message="Request saved, but the invitation email failed. Retry from your account dashboard."),201
        return jsonify(ok=True,id=rid,invited=bool(invite_token)),201
    except ValueError as e: return jsonify(error=str(e)),400

@app.post("/api/requests/<int:rid>/invite")
@auth_required
@csrf_required
@rate_limited("resend-invite", 5, 3600)
def resend_request_invite(rid):
    user=current_user()
    with engine.begin() as db:
        row=db.execute(text("SELECT invite_email FROM requests WHERE id=:id AND owner_id=:uid AND visibility='private' AND hidden=0"), {"id":rid,"uid":user["id"]}).mappings().first()
        if not row: return jsonify(error="Private request not found."),404
        token=secrets.token_urlsafe(32)
        db.execute(text("DELETE FROM request_invites WHERE request_id=:rid"), {"rid":rid})
        db.execute(text("INSERT INTO request_invites(request_id,recipient_email,token_hash,expires_at) VALUES(:rid,:email,:hash,:expiry)"), {"rid":rid,"email":row["invite_email"],"hash":hashlib.sha256(token.encode()).hexdigest(),"expiry":(utcnow()+timedelta(days=14)).isoformat()})
    try: send_token(row["invite_email"],"invite",token)
    except Exception:
        app.logger.exception("Private request invitation resend failed")
        return jsonify(error="Could not send the invitation email. Check email service settings."),503
    return jsonify(ok=True,message="A fresh invitation has been sent.")

@app.post("/api/requests/<int:rid>/offer")
@roles("seeker","volunteer","ngo")
@csrf_required
@rate_limited("offer", 12, 3600)
def offer_help(rid):
    user=current_user()
    with engine.begin() as db:
        row=db.execute(text("SELECT owner_id,status,visibility FROM requests WHERE id=:id AND hidden=0"), {"id":rid}).mappings().first()
        if not row: return jsonify(error="Request not found."),404
        if row["visibility"]=="private" and row["owner_id"]!=user["id"]:
            invited=db.execute(text("SELECT r.id FROM requests r JOIN request_invites i ON i.request_id=r.id WHERE r.id=:id AND r.visibility='private' AND i.recipient_email=:email AND i.expires_at>:now AND r.hidden=0"), {"id":rid,"email":user["email"],"now":utcnow().isoformat()}).first()
            if not invited: return jsonify(error="Request not found."),404
        if row["owner_id"]==user["id"]: return jsonify(error="You cannot offer help on your own request."),400
        result=db.execute(text("UPDATE requests SET status='matched',assigned_id=:uid,updated_at=CURRENT_TIMESTAMP WHERE id=:id AND status='open'"), {"uid":user["id"],"id":rid})
        if result.rowcount!=1: return jsonify(error="This request has already been matched or closed."),409
        audit(db,user["id"],"request.matched","request",rid)
    return jsonify(ok=True)

@app.post("/api/requests/<int:rid>/status")
@auth_required
@csrf_required
def update_status(rid):
    status=str((request.get_json(silent=True) or {}).get("status","")); user=current_user()
    if status not in ("in_progress","completed","cancelled"): return jsonify(error="Invalid status."),400
    with engine.begin() as db:
        row=db.execute(text("SELECT owner_id,assigned_id,status FROM requests WHERE id=:id AND hidden=0"), {"id":rid}).mappings().first()
        if not row: return jsonify(error="Request not found."),404
        owner=user["id"]==row["owner_id"]; helper=user["id"]==row["assigned_id"]
        allowed=(status=="cancelled" and owner and row["status"] in ("open","matched")) or (status=="in_progress" and helper and row["status"]=="matched") or (status=="completed" and (helper or owner) and row["status"] in ("matched","in_progress"))
        if user["role"]=="admin": allowed=True
        if not allowed: return jsonify(error="You cannot make that status change."),403
        db.execute(text("UPDATE requests SET status=:status,updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {"status":status,"id":rid}); audit(db,user["id"],"request."+status,"request",rid)
    return jsonify(ok=True)

@app.post("/api/requests/<int:rid>/report")
@csrf_required
@rate_limited("report", 15, 3600)
def report_request(rid):
    user=current_user(); reason=str((request.get_json(silent=True) or {}).get("reason","suspicious or unsafe content"))[:300]
    if not reason.strip(): return jsonify(error="Tell moderators briefly what seems wrong."),400
    with engine.begin() as db:
        exists=db.execute(text("SELECT id FROM requests WHERE id=:id AND hidden=0"), {"id":rid}).first()
        if not exists: return jsonify(error="Request not found."),404
        db.execute(text("INSERT INTO reports(request_id,reporter_id,reason) VALUES(:rid,:uid,:reason)"), {"rid":rid,"uid":user["id"] if user else None,"reason":reason})
        count=db.execute(text("SELECT COUNT(*) FROM reports WHERE request_id=:rid AND status='open'"), {"rid":rid}).scalar()
        if count>=3: db.execute(text("UPDATE requests SET hidden=1 WHERE id=:rid"), {"rid":rid})
        audit(db,user["id"] if user else None,"request.reported","request",rid)
    return jsonify(ok=True,message="Report sent to the moderation team.")

def can_access_conversation(db, rid, uid):
    return db.execute(text("SELECT id FROM requests WHERE id=:rid AND (owner_id=:uid OR assigned_id=:uid)"), {"rid":rid,"uid":uid}).first() is not None

@app.get("/api/requests/<int:rid>/messages")
@auth_required
def list_messages(rid):
    user=current_user()
    with engine.connect() as db:
        if not can_access_conversation(db,rid,user["id"]): return jsonify(error="Conversation not found."),404
        rows=db.execute(text("SELECT m.id,m.body,m.created_at,m.sender_id,u.name sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.request_id=:rid ORDER BY m.id LIMIT 200"), {"rid":rid}).mappings().all()
    return jsonify([dict(r) for r in rows])

@app.post("/api/requests/<int:rid>/messages")
@auth_required
@csrf_required
@rate_limited("message", 40, 3600)
def send_message(rid):
    body=clean((request.get_json(silent=True) or {}).get("body"),2000); user=current_user()
    with engine.begin() as db:
        if not can_access_conversation(db,rid,user["id"]): return jsonify(error="Conversation not found."),404
        row=db.execute(text("SELECT status FROM requests WHERE id=:id"), {"id":rid}).mappings().first()
        if row["status"] in ("completed","cancelled"): return jsonify(error="This conversation is closed."),409
        db.execute(text("INSERT INTO messages(request_id,sender_id,body) VALUES(:rid,:uid,:body)"), {"rid":rid,"uid":user["id"],"body":body})
        audit(db,user["id"],"message.sent","request",rid)
    return jsonify(ok=True),201

@app.get("/api/me/requests")
def my_requests():
    if not current_user(): return jsonify(error="Sign in to continue."),401
    uid=current_user()["id"]
    with engine.connect() as db:
        rows=db.execute(text("SELECT id,owner_id,title,category,location,visibility,status,created_at,assigned_id FROM requests WHERE owner_id=:uid OR assigned_id=:uid ORDER BY updated_at DESC LIMIT 100"), {"uid":uid}).mappings().all()
    return jsonify([dict(r) for r in rows])

@app.post("/api/me/delete")
@csrf_required
def delete_account():
    password=str((request.get_json(silent=True) or {}).get("password","")); user=current_user()
    if not user: return jsonify(error="Sign in to continue."),401
    with engine.begin() as db:
        row=db.execute(text("SELECT password_hash FROM users WHERE id=:id"), {"id":user["id"]}).mappings().first()
        if not row or not verify_password(password,row["password_hash"]): return jsonify(error="Password is incorrect."),403
        if user["role"]=="admin" and db.execute(text("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0")).scalar()<=1:
            return jsonify(error="Create another administrator before deleting the last admin account."),409
        audit(db,user["id"],"account.deleted","user",user["id"])
        db.execute(text("DELETE FROM ngo_submissions WHERE user_id=:id"), {"id":user["id"]})
        db.execute(text("DELETE FROM request_invites WHERE recipient_email=:email"), {"email":user["email"]})
        db.execute(text("DELETE FROM messages WHERE sender_id=:id"), {"id":user["id"]})
        db.execute(text("UPDATE requests SET assigned_id=NULL,status='open',updated_at=CURRENT_TIMESTAMP WHERE assigned_id=:id AND owner_id!=:id AND status IN ('matched','in_progress')"), {"id":user["id"]})
        db.execute(text("DELETE FROM requests WHERE owner_id=:id"), {"id":user["id"]})
        db.execute(text("DELETE FROM tokens WHERE user_id=:id"), {"id":user["id"]})
        db.execute(text("UPDATE users SET email=:email,name='Deleted member',password_hash=:hash,role='seeker',country='',organization='',email_verified=0,ngo_status='none',disabled=1 WHERE id=:id"), {"email":f"deleted-{user['id']}-{secrets.token_hex(8)}@invalid.local","hash":password_hash(secrets.token_urlsafe(32)),"id":user["id"]})
    session.clear()
    return jsonify(ok=True,message="Your account and requests have been deleted.")

@app.post("/api/ngo/verification")
@auth_required
@csrf_required
def ngo_verification():
    if current_user()["role"]!="ngo": return jsonify(error="Only organization accounts can submit verification."),403
    data=request.get_json(silent=True) or {}; proof=clean(data.get("proof"),1000)
    if len(proof)<20: return jsonify(error="Please include a website, registry reference or public contact that helps verify your organization."),400
    user=current_user()
    with engine.begin() as db:
        db.execute(text("UPDATE users SET ngo_status='pending' WHERE id=:id"), {"id":user["id"]})
        pending=db.execute(text("SELECT id FROM ngo_submissions WHERE user_id=:id AND status='pending' ORDER BY id DESC LIMIT 1"), {"id":user["id"]}).mappings().first()
        if pending:
            db.execute(text("UPDATE ngo_submissions SET proof=:proof WHERE id=:id"), {"proof":proof,"id":pending["id"]})
        else:
            db.execute(text("INSERT INTO ngo_submissions(user_id,proof) VALUES(:id,:proof)"), {"id":user["id"],"proof":proof})
        audit(db,user["id"],"ngo.verification_submitted","user",user["id"])
    return jsonify(ok=True,message="Verification submitted for review.")

@app.get("/api/admin/queue")
@roles("admin")
def admin_queue():
    with engine.connect() as db:
        reports=db.execute(text("SELECT p.id report_id,p.reason,p.created_at,r.id request_id,r.title,r.details,r.location FROM reports p JOIN requests r ON r.id=p.request_id WHERE p.status='open' ORDER BY p.id DESC LIMIT 100")).mappings().all()
        ngos=db.execute(text("SELECT n.id submission_id,n.proof,n.created_at,u.id user_id,u.email,u.name,u.organization FROM ngo_submissions n JOIN users u ON u.id=n.user_id WHERE n.status='pending' ORDER BY n.id LIMIT 100")).mappings().all()
        users=db.execute(text("SELECT id,email,name,role,disabled,email_verified,created_at FROM users WHERE role!='admin' ORDER BY id DESC LIMIT 50")).mappings().all()
    return jsonify(reports=[dict(r) for r in reports],ngos=[dict(n) for n in ngos],users=[dict(u) for u in users])

@app.post("/api/admin/reports/<int:report_id>")
@roles("admin")
@csrf_required
def resolve_report(report_id):
    data=request.get_json(silent=True) or {}; resolution=str(data.get("resolution","reviewed"))[:200]; hide=bool(data.get("hide",False)); uid=current_user()["id"]
    with engine.begin() as db:
        report=db.execute(text("SELECT request_id FROM reports WHERE id=:id AND status='open'"), {"id":report_id}).mappings().first()
        if not report: return jsonify(error="Report not found."),404
        db.execute(text("UPDATE reports SET status='resolved',resolved_by=:uid,resolution=:resolution WHERE id=:id"), {"uid":uid,"resolution":resolution,"id":report_id})
        if hide: db.execute(text("UPDATE requests SET hidden=1 WHERE id=:rid"), {"rid":report["request_id"]})
        elif data.get("restore"): db.execute(text("UPDATE requests SET hidden=0 WHERE id=:rid"), {"rid":report["request_id"]})
        audit(db,uid,"report.resolved","report",report_id)
    return jsonify(ok=True)

@app.post("/api/admin/ngos/<int:submission_id>")
@roles("admin")
@csrf_required
def resolve_ngo(submission_id):
    data=request.get_json(silent=True) or {}; decision=str(data.get("decision","")); uid=current_user()["id"]
    if decision not in ("approved","rejected"): return jsonify(error="Choose approved or rejected."),400
    with engine.begin() as db:
        row=db.execute(text("SELECT user_id FROM ngo_submissions WHERE id=:id AND status='pending'"), {"id":submission_id}).mappings().first()
        if not row: return jsonify(error="Submission not found."),404
        db.execute(text("UPDATE ngo_submissions SET status=:status,reviewed_by=:admin,reviewed_at=CURRENT_TIMESTAMP WHERE id=:id"), {"status":decision,"admin":uid,"id":submission_id})
        db.execute(text("UPDATE users SET ngo_status=:status WHERE id=:uid"), {"status":decision,"uid":row["user_id"]})
        audit(db,uid,"ngo."+decision,"user",row["user_id"])
    return jsonify(ok=True)

@app.post("/api/admin/users/<int:user_id>/disable")
@roles("admin")
@csrf_required
def disable_user(user_id):
    uid=current_user()["id"]
    with engine.begin() as db:
        if user_id==uid: return jsonify(error="You cannot disable your own account."),400
        result=db.execute(text("UPDATE users SET disabled=1 WHERE id=:id AND role!='admin'"), {"id":user_id})
        if result.rowcount!=1: return jsonify(error="User not found or cannot be disabled."),404
        db.execute(text("DELETE FROM messages WHERE sender_id=:id"), {"id":user_id})
        db.execute(text("UPDATE requests SET assigned_id=NULL,status='open',updated_at=CURRENT_TIMESTAMP WHERE assigned_id=:id AND status IN ('matched','in_progress')"), {"id":user_id})
        audit(db,uid,"user.disabled","user",user_id)
    return jsonify(ok=True)

@app.post("/api/admin/users/<int:user_id>/role")
@roles("admin")
@csrf_required
def change_user_role(user_id):
    role=str((request.get_json(silent=True) or {}).get("role","")); admin=current_user()["id"]
    if role not in ("seeker","volunteer","ngo","admin"): return jsonify(error="Choose a valid account role."),400
    with engine.begin() as db:
        if user_id==admin and role!="admin" and db.execute(text("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0")).scalar()<=1:
            return jsonify(error="Promote another administrator before changing your own role."),409
        result=db.execute(text("UPDATE users SET role=:role,ngo_status=CASE WHEN :role='ngo' AND ngo_status='none' THEN 'pending' ELSE ngo_status END WHERE id=:id AND email_verified=1 AND disabled=0"), {"role":role,"id":user_id})
        if result.rowcount!=1: return jsonify(error="Verified account not found."),404
        audit(db,admin,"user.role_changed","user",user_id)
    return jsonify(ok=True)

@app.post("/api/bootstrap/admin")
@csrf_required
@rate_limited("bootstrap", 5, 3600)
def bootstrap_admin():
    expected=os.getenv("BOOTSTRAP_ADMIN_TOKEN",""); supplied=request.headers.get("X-Bootstrap-Token","")
    if not expected or not hmac.compare_digest(expected,supplied): return jsonify(error="Admin setup is not enabled."),404
    d=request.get_json(silent=True) or {}; email=clean(d.get("email"),254).lower(); name=clean(d.get("name"),80); password=str(d.get("password",""))
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email): return jsonify(error="Enter a valid email address."),400
    if len(password)<16: return jsonify(error="Use at least 16 characters for the first admin password."),400
    with engine.begin() as db:
        if db.execute(text("SELECT COUNT(*) FROM users WHERE role='admin'")).scalar(): return jsonify(error="An admin already exists."),409
        result=db.execute(text("INSERT INTO users(email,name,password_hash,role,email_verified) VALUES(:email,:name,:hash,'admin',1) RETURNING id"), {"email":email,"name":name,"hash":password_hash(password)})
        uid=result.scalar_one(); audit(db,uid,"admin.bootstrapped","user",uid)
    session.clear(); session["uid"]=uid; session["csrf"]=secrets.token_urlsafe(24); session.permanent=True
    return jsonify(ok=True)

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"]="nosniff"; response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"]="DENY"; response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    response.headers["Cache-Control"]="no-store"
    if PRODUCTION: response.headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
    return response

@app.errorhandler(413)
def too_large(_): return jsonify(error="Request is too large."),413

if __name__ == "__main__":
    app.run(host="127.0.0.1",port=int(os.getenv("PORT","8000")),debug=False)
