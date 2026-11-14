# Security policy

Aidweave handles requests that may concern vulnerable people. Do not deploy a public instance until security and moderation responsibilities are staffed.

## Reporting a vulnerability

Do not post exploitable details or user data in a public issue. The project has no private security inbox yet; until one is established, do not submit real personal information to this prototype or expose a deployment containing live requests.

## Current safeguards

- Passwords use Python's scrypt password hashing; login, account creation, reports and messaging are throttled per process.
- Session cookies are HttpOnly, SameSite=Lax and Secure in production. Mutations require a CSRF token.
- Private requests are omitted from public results and restricted to their requester, the invited verified email, and the matched helper.
- Email verification is required before posting, offering help or messaging.
- Public requests reject email addresses, phone-like strings and numbered location text.
- Sensitive admin actions are recorded in an audit log; request reports can be reviewed, hidden or resolved.

## Limits before a real public launch

Rate limits are process-local, not shared across multiple service instances. There is no independent security audit, encrypted field-level storage, malware scanning, identity verification, abuse-response team, or disaster-recovery procedure. Use one app instance until shared rate limiting is added. Limit moderator access, configure database backups, review retention and deletion requirements, and test restore procedures before storing real requests.
