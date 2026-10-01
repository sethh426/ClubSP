# Gmail connection: read-only authorization and token refresh

This release builds a local Connect Gmail button and browser callback. It does
not send email, import conversations, or run follow-ups. No background mailbox
calls occur. Explicit authorization and Refresh Gmail access read the mailbox
profile; inbox contents are not fetched. The refresh helper renews tokens on
demand when expired or within sixty seconds of expiry; the button forces a
refresh to verify the saved grant. Future inbox operations can use that helper.

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

Localhost refers to the device opening the browser. For the owner's VPN-only
HTTPS deployment, set `GOOGLE_REDIRECT_URI=https://clubsp.online/auth/gmail/callback`
in the private environment and register that exact URI with Google. The app
still binds to loopback: the VPN-only reverse proxy must preserve the existing
trusted internal Host and Origin handling. Do not expose the app or proxy on a
public interface. The explicit callback must use HTTPS, the exact callback path,
and no userinfo, query, or fragment. Arbitrary forwarded headers are not trusted.
Do not pull over existing server-only callback patches without backing them up
and setting this variable first.

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

Google Testing-mode Gmail refresh grants normally expire after seven days.
Token refresh cannot bypass that limit or revoked permission; reconnect when
necessary. Failed refresh preserves the previous file and returns a sanitized
error. Concurrent refreshes are serialized; disconnect or replacement during
refresh prevents stale credentials from being written back. No tokens are
returned by `/api/gmail/refresh`, and a matching Origin is required. Inbox sync
and sending remain disabled. Automated tests use synthetic provider responses;
real refresh must still be verified by the owner after deployment.

References: https://developers.google.com/identity/protocols/oauth2/web-server
and https://developers.google.com/workspace/gmail/api/auth/scopes
