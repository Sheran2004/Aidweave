# Aidweave

**Every problem deserves a chance at help.** Aidweave is an open-source community assistance platform. People can ask for any kind of practical support, and verified volunteers or organizations can coordinate a response.

## MVP features

- Email-verified personal accounts that can both request help and volunteer, plus verified organization accounts.
- Any-problem requests, with public board posts or private email invitations; public posts show a community label rather than the member’s display name.
- Search and category filters, city/region discovery, urgency and request status.
- One helper can claim an open request; requester and helper can privately message and track progress to completion.
- Report queue, request hiding, organization verification review, account suspension and audit events for admin users.
- Password reset, per-process throttling, CSRF protection, secure session cookie settings, privacy safeguards, self-service account deletion and mobile-friendly UI.
- PostgreSQL for a hosted deployment, SQLite for local development.
- Responsive light/dark appearance, following the device setting by default and remembering the user’s choice.
- Installable mobile web app with an offline cached shell; requests and account actions still need an internet connection.

## Run locally

Requires Python 3.11 or newer.

```sh
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
Copy-Item .env.example .env  # macOS/Linux: cp .env.example .env
python server.py
```

Open <http://localhost:8000>. The local database is `aidweave.db`. Demo requests are fictional. The development server writes verification, password-reset and private-invitation links to its console when SMTP is not configured. For local admin testing, set a temporary `BOOTSTRAP_ADMIN_TOKEN` in `.env`, restart the server, then run `scripts/bootstrap_admin.py` with `AIDWEAVE_URL=http://localhost:8000` and that token in your terminal. Remove the token from `.env` after the first admin is created.

For the full Windows/macOS/Linux steps, see [VS_CODE_GUIDE.md](VS_CODE_GUIDE.md).

## Roles

- **Personal account:** every verified person can create public or private requests, offer help on another person's request, message the match, and track status. The signup focus is only a preference; it does not lock the account to one activity.
- **Organization:** submit public verification evidence for admin review. Only approved organizations can post or claim requests.
- **Administrator:** review request reports and NGO submissions, hide requests, disable accounts, and see limited account data for moderation.

New accounts must verify their email before posting, offering help, or messaging. Private requests require an invitee email; they never appear on the public board. Public requests reject email addresses, phone-like strings and numbered location text, but people must still avoid sharing sensitive details in public text.

## Deploy on Render

The checked-in `render.yaml` describes a single web service and a PostgreSQL database in Singapore. It asks Render to create **Starter** resources, which may incur charges. Review the plan and current price in Render before creating anything. Render supports Blueprint database references and generated secret environment variables in this format; see its [Blueprint specification](https://render.com/docs/blueprint-spec) and [first deploy guide](https://render.com/docs/your-first-deploy).

1. Push this repository to a Git host and connect it to Render as a Blueprint.
2. Review the web and PostgreSQL plans before approving resource creation.
3. Set `PUBLIC_BASE_URL` to the exact HTTPS service URL, for example `https://aidweave.onrender.com`. Configure either `EMAIL_PROVIDER=resend` plus `RESEND_API_KEY`, or `EMAIL_PROVIDER=smtp` plus the SMTP host/login settings. In either case, `SMTP_FROM` must be a sender address on a domain verified by that provider. Email verification, password reset and private invitations depend on this setup. Production startup now stops if HTTPS, PostgreSQL, or email delivery configuration is missing. In local development, `EMAIL_PROVIDER=console` deliberately prints test links to the terminal; switch to `resend` or `smtp` in `.env` to deliver actual emails while testing.
4. Wait for the deployment health check at `/api/health` to pass.
5. Copy the generated `BOOTSTRAP_ADMIN_TOKEN` from the hosting dashboard. Run the one-time setup from a secure terminal:

   ```powershell
   $env:AIDWEAVE_URL = 'https://your-service.onrender.com'
   $env:BOOTSTRAP_ADMIN_TOKEN = 'paste-the-generated-token'
   python scripts/bootstrap_admin.py
   ```

6. Remove `BOOTSTRAP_ADMIN_TOKEN` from `render.yaml` and the service's environment, then commit and deploy that change. This disables first-admin setup.
7. Sign in as admin; verify that reports and organization checks are visible. Configure backups, retention, a monitored security contact and a staffed moderation process before inviting the public.

Do not put SMTP credentials, admin passwords, domain registrar credentials or real secrets in Git. Domain availability and legal naming clearance have not been confirmed. No domain has been purchased and this project has not been deployed.

## Local test checklist

The app has not been acceptance-tested in this workspace. Before public launch, use two separate verified accounts to check registration/verification, role restrictions, private invites, matching, private messages, status transitions, reports, organization approval, account deletion, password reset, and admin bootstrap removal. Also test narrow/mobile layouts, backup restore, and mail delivery. Do not test using real sensitive needs or personal information.

## Safety and operational boundary

This is an MVP codebase, not a staffed aid organization, identity-verification provider, medical service, legal service, or emergency service. Accounts are limited to adults (18+) under the current Terms. It does not verify that a request or helper is genuine. It does not use an AI chatbot or infer eligibility; keep sensitive requests and high-stakes safety decisions with people. The operator must review reports, organization evidence and abuse, and must provide local emergency guidance. For immediate danger, users should contact local emergency services.

Rate limits are process-local; keep one app instance until shared rate limiting is added. The database is not field-encrypted. Moderation is admin-only. Review [PRIVACY.md](PRIVACY.md), [SECURITY.md](SECURITY.md), and local legal/safeguarding obligations before accepting real users.

## Project files

- [PRIVACY.md](PRIVACY.md): data handling notice for operators to adapt.
- [SECURITY.md](SECURITY.md): current controls and known operational gaps.
- [CONTRIBUTING.md](CONTRIBUTING.md): contribution guide.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md): community expectations.
- [LICENSE](LICENSE): MIT License.
