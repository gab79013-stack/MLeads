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

## Optional background agents

Only start the worker when you want the scheduled collection/orchestrator loop active:

```bash
docker compose --profile agents up -d worker
```

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
