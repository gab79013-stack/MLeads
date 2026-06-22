# Docker / 0brix MLeads

## Build

```bash
docker compose build web
```

## Web app only

Uses `.env.production` at runtime and mounts mutable state from the host:

- `./data` → `/app/data`
- `./contacts` → `/app/contacts`
- `./logs` → `/app/logs`

```bash
docker compose up -d web
docker compose ps
curl -fsS http://127.0.0.1:${PORT:-5001}/api/health
```

## Stripe billing

The web checkout requires production Stripe values in `.env.production`.
Configure them on the server without printing secrets:

```bash
python3 scripts/configure_stripe_billing.py
docker compose up -d --build web
```

Required values:

- `STRIPE_API_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_ID_PRO`
- `STRIPE_PRICE_ID_QUALITY`
- `STRIPE_PRICE_ID_PREMIUM`
- `STRIPE_PRICE_ID_ELITE`
- `BASE_URL` such as `http://2.25.162.58` or the production domain

In Stripe, point the webhook to:

```text
BASE_URL/api/stripe/webhook
```

Use the admin dashboard `Billing readiness` card to confirm checkout,
webhook, and Quality/Elite readiness before sending contractors to pay.

## Optional background agents

Only start the worker when you want the scheduled collection/orchestrator loop active:

```bash
docker compose --profile agents up -d worker
```

## Twenty CRM

The legacy `/pipeline` page redirects to TwentyHQ. For a same-server
self-hosted CRM, install Twenty on port `3000`:

```bash
sudo SERVER_URL=http://2.25.162.58:3000 ./scripts/setup_twenty_crm.sh
```

Then set the MLeads web runtime variable and rebuild/restart the web service:

```bash
TWENTY_URL=http://2.25.162.58:3000
docker compose up -d --build web
```

On the production VM, `setup_twenty_crm.sh` updates
`/etc/mleads/mleads.env` automatically when the file exists.

## Logs

```bash
docker compose logs -f web
docker compose logs -f worker
```

## Stop

```bash
docker compose down
```

## Notes

- `.env`, `.env.*`, local DBs, logs, CSVs, and tarballs are excluded by `.dockerignore` so secrets/runtime data are not baked into the image.
- Default container command runs the web dashboard/API with Gunicorn on `PORT` default `5001`; `WEB_CONCURRENCY` defaults to `1` to avoid SQLite write contention.
- The worker service reuses the same image and runs `python main.py`.
