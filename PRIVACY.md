# Aidweave privacy notice

**Last updated: 12 November 2026.** This is a project starter notice, not jurisdiction-specific legal advice. The operator of a deployed Aidweave instance must publish its own contact details, retention schedule, and regional terms before accepting real requests.

## Data the service stores

Account email, display name, account role, country or region, and (for organizations) organization name. Requests include the text, category, city or region, urgency, visibility choice and status. Private messages, invitation email addresses, organization verification evidence, request reports, account-security tokens and minimal administrative audit events are also stored when used.

## How data is used

To operate accounts, verify email addresses, show public requests, deliver private invitations, coordinate an accepted request, prevent abuse, and review reports or organization verification. Aidweave does not process payments or sell personal data.

## Public and private information

Public requests are visible to anyone. They show a generic community-member label (or an organization name), request text, category and city or region. Do not put phone numbers, email addresses, street addresses, financial details, or other identifying details in a public request. Private requests are excluded from public search and are visible to the requester, the verified invited email account, the matched helper and moderators who need to review a report. Conversations are limited to the requester and matched helper.

## Email and security

The service emails account verification, password reset and private-invitation links using the Resend or SMTP provider configured by the instance operator. That provider processes recipient addresses and one-time links to deliver mail. Passwords are stored as scrypt hashes. Session cookies are HttpOnly and SameSite=Lax and use Secure cookies in production. The database operator and hosting provider still process service data; the starter does not encrypt individual database fields.

## Retention and deletion

An account holder can request deletion from their account dashboard. The MVP removes that user's requests, messages, invitation records and organization evidence, disables the account, and anonymizes its profile. Minimal moderation/audit references may remain so the service can investigate abuse. The operator should set a retention period for resolved reports and audit records and configure backups to expire accordingly.

## Children, emergencies, and sensitive needs

The MVP is not designed for unsupervised use by children and is not an emergency service. Do not post identifying information about another person. For immediate danger, contact local emergency services. The operator must review age, health, safeguarding, and regional privacy requirements before public launch.

## Contact

This source repository does not yet have an operating organization or privacy contact. A live operator must replace this section with a monitored contact address before accepting users.
