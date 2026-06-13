FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=5001 \
    DB_PATH=/app/data/leads.db \
    CONTACTS_DIR=/app/contacts \
    LOG_FILE=/app/logs/mleads.log

WORKDIR /app

# Runtime/build deps:
# - gcc/build-essential pieces for Python wheels that need compilation
# - curl for container healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/contacts /app/logs

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

# Default = web dashboard/API. Override command for workers/agents with `python main.py`.
CMD ["sh", "-c", "gunicorn --workers ${WEB_CONCURRENCY:-1} --bind 0.0.0.0:${PORT:-5001} --timeout ${GUNICORN_TIMEOUT:-120} web_server:app"]
