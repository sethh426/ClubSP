# Private service deployment

This deployment pattern keeps ClubSP bound to loopback, places the process behind the owner's VPN-only HTTPS reverse proxy, and lets the operating system restart it after a crash or reboot.

## Required private configuration

Copy `.env.example` to a private `.env` file and set:

- `CLUBSP_ENV=production`
- a strong `CLUBSP_OWNER_SECRET`
- the Gmail client ID, secret and expected mailbox if Gmail is used
- `GOOGLE_REDIRECT_URI=https://clubsp.online/auth/gmail/callback` for the private HTTPS deployment

Production preflight intentionally fails when owner authentication or the HTTPS callback is absent.

## Readiness

`/api/health` is a liveness endpoint: it answers when the process is running.

`/api/ready` is a readiness endpoint. In production it returns success only when the ClubSP database passes a SQLite quick check, owner authentication is enabled, and the explicit HTTPS callback is configured. It does not require a logged-in browser and exposes only readiness categories, not secrets.

The same checks can be run before startup:

```sh
python -m app.preflight --db /var/lib/clubsp/clubsp.sqlite3 --env-file /opt/clubsp/.env
```

A non-ready result exits nonzero.

## systemd example

`deploy/clubsp.service.example` is a hardened example, not a drop-in file for every host. Adjust the service account and paths to the actual installation, then install it under `/etc/systemd/system/clubsp.service`.

The example:

- runs preflight before every start
- restarts on failure
- uses an owner-only umask
- limits writable filesystem access to the data directory
- keeps the application listener on loopback through the normal ClubSP server
- does not terminate TLS or expose ClubSP publicly

After editing the service:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now clubsp
sudo systemctl status clubsp
```

Use the private reverse proxy/VPN health check against `/api/ready`, not the authenticated application pages.

## Recovery order

If the service fails repeatedly, do not disable preflight to force it online. Inspect the readiness result, fix configuration or restore the verified database, run `python -m app.maintenance verify`, then restart the service.

Backups remain a separate responsibility; see `docs/BACKUP_RECOVERY.md`.
