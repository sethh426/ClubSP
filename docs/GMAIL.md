# Gmail connection: local authorization release

This release builds a local Connect Gmail button and browser callback. It does
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

Localhost refers to the device opening the browser. This callback does not work
from an iPhone when ClubSP is running on a different computer. A mobile/public
release needs authenticated HTTPS hosting and a production callback; this
release deliberately keeps the server loopback-only.

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
