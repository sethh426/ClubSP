# Gmail connection: local or private HTTPS authorization

This release builds a Connect Gmail button and browser callback for same-computer local access or an explicitly configured private HTTPS reverse proxy. It does
not send email, import conversations, refresh expired access tokens, or run
follow-ups. Those integrations are separate work. No background mailbox calls
occur. Only explicit authorization exchanges a code and reads the mailbox
profile; inbox contents are not fetched.

## Setup on the computer running ClubSP

1. Enable the Gmail API in the Google Cloud project containing your web client.
2. Register `http://127.0.0.1:8000/auth/gmail/callback` as an authorized redirect
   URI. Leave JavaScript origins blank. Match the URL shown in the app exactly;
   localhost versus 127.0.0.1 and different ports are different callback URLs.
3. If the Google app is in testing, add your intended mailbox as a test user.
4. Put `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_MAILBOX_EMAIL` in
   the ignored local `.env` file or the process environment. Never commit secrets.
   Restrict the file to the operating-system user running ClubSP.
5. Run `python -m app.server`, open `http://127.0.0.1:8000`, expand Gmail, and
   click Connect Gmail. Approve Google's read-only permission for your mailbox.

Localhost refers to the device opening the browser. The default local callback does
not work from an iPhone when ClubSP is running on a different computer.

## Private server accessed from a phone

1. Provide a domain you control and a valid browser-trusted TLS certificate.
   With a VPN-only server, DNS-01 certificate validation can avoid public HTTP
   access. Automate certificate renewal with your DNS provider's supported API;
   manual DNS validation alone does not provide automatic renewal.
2. Bind the HTTPS reverse proxy only to the VPN interface and restrict VPN peers
   to the owner. A configured HTTPS callback alone is not access control.
3. Set `CLUBSP_GMAIL_ORIGIN=https://clubsp.online` (substitute your chosen domain)
   in the ignored private `.env` or process environment, alongside the three
   Google settings. Only a canonical HTTPS domain origin with no port, path,
   query, fragment or credentials is accepted. An invalid setting fails startup.
4. The proxy must reject unknown Host and Origin values before forwarding to
   `127.0.0.1:8000`. Forward Host as `127.0.0.1:8000`; map only your allowed
   browser HTTPS Origin to `http://127.0.0.1:8000`. Never blindly replace all
   Origins with the trusted value. Do not forward arbitrary public traffic.
   Disable or redact callback query-string access logs.
5. Register `https://clubsp.online/auth/gmail/callback` in the Google web client.
   Keep existing callback URLs required by your other apps.
6. Restart ClubSP and open its configured HTTPS URL with WireGuard enabled.
   The Connect button stays disabled at a different browser origin. The browser
   state cookie is Secure, HttpOnly and SameSite=Lax in private HTTPS mode;
   the backend remains loopback-only and never trusts forwarded headers to
   choose an OAuth callback.

This mode supports owner-only private hosting. Public multiuser access, mailbox
import, sending, refresh and scheduled follow-ups remain separate work.

Tokens are saved separately at `data/private/gmail-token.json` with owner-only
file permissions. They are not included in state APIs, rendered pages, or logs.
Treat the tokens, local credentials, and backups as sensitive. Files are
permission-protected, not encrypted at rest; this is not a production secret
vault. State and PKCE bind authorization to the initiating browser for ten
minutes; callbacks are single-use and authorization query strings are not logged.

Remove local connection deletes the local token file and cancels pending
authorization attempts. It does not revoke Google Account permission; manage
that permission in your Google Account separately. Restart cancels pending
attempts. Declined permission or a wrong mailbox preserves any prior connection.

Google testing grants can expire; offline token presence is not proof of
permanent access. The UI marks access-token expiry. Refresh and sync are not yet
implemented. Authorization success has only been verified with synthetic
provider responses until the owner completes a real Google consent flow.

References: https://developers.google.com/identity/protocols/oauth2/web-server
and https://developers.google.com/workspace/gmail/api/auth/scopes
