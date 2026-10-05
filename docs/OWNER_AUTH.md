# Private owner authentication

ClubSP is a single-owner operating workspace. Remote/private deployment must not rely only on the VPN or reverse proxy boundary.

Set a strong private environment value:

```sh
CLUBSP_OWNER_SECRET=<at least 24 unpredictable characters>
```

When this value is configured, all workspace pages and private APIs require a signed owner session. The only unauthenticated routes are the login page and script, shared stylesheet, health check, and Gmail OAuth callback.

The login secret is compared in constant time and is not written to the database, browser storage, logs, or source control. A successful login creates an HttpOnly, SameSite=Strict session cookie valid for 12 hours. The cookie is also marked Secure when ClubSP is configured with its private HTTPS Gmail callback.

Changing `CLUBSP_OWNER_SECRET` invalidates existing sessions. Removing the value disables the application login gate and is intended only for loopback-only local development.

## Private deployment boundary

Keep the Python server bound to `127.0.0.1`. Terminate TLS in the private reverse proxy and restrict network access to the owner's VPN/authorized peer. Do not expose the loopback application directly to the public internet.

The reverse proxy still needs the existing Host/Origin normalization described in the Gmail deployment notes. Application authentication is defense in depth; it does not replace VPN peer controls, TLS, operating-system permissions, secret management, backups, or firewall policy.

For a production/private deployment, require all of the following before using real deal or mailbox data:

- private VPN-only ingress
- browser-trusted HTTPS
- `CLUBSP_OWNER_SECRET` configured
- Gmail OAuth secrets stored outside source control
- verified database backups and a tested restore procedure
- operating-system access restricted to the owner/service account
- no public listener for the Python application
