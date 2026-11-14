"""Create the first Aidweave administrator on a freshly deployed instance."""
import getpass
import http.cookiejar
import json
import os
import urllib.error
import urllib.request

base = os.environ.get("AIDWEAVE_URL", "").rstrip("/")
token = os.environ.get("BOOTSTRAP_ADMIN_TOKEN", "")
if not base or not token:
    raise SystemExit("Set AIDWEAVE_URL and BOOTSTRAP_ADMIN_TOKEN in your terminal first.")
name = input("Admin display name: ").strip()
email = input("Admin email: ").strip()
password = getpass.getpass("New admin password (16+ characters): ")
if len(password) < 16:
    raise SystemExit("Use at least 16 characters for the admin password.")

cookies = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
def send(path, payload=None, headers=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(base + path, data=body, headers=headers or {}, method="POST" if body else "GET")
    with opener.open(request, timeout=20) as response:
        return json.loads(response.read())

try:
    state = send("/api/bootstrap")
    result = send("/api/bootstrap/admin", {"name": name, "email": email, "password": password},
                  {"Content-Type": "application/json", "X-CSRF-Token": state["csrf"], "X-Bootstrap-Token": token})
except urllib.error.HTTPError as error:
    detail = error.read().decode(errors="replace")
    raise SystemExit(f"Admin setup failed ({error.code}): {detail}") from None
except urllib.error.URLError as error:
    raise SystemExit(f"Could not reach Aidweave: {error.reason}") from None
print("First administrator created. Remove BOOTSTRAP_ADMIN_TOKEN from the hosting configuration now.")
