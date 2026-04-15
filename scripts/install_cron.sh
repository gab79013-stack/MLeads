#!/usr/bin/env bash
set -euo pipefail

TMP_CRON="$(mktemp)"
cat > "$TMP_CRON" <<'EOF'
*/15 * * * * /usr/bin/python3 /workspace/scripts/build_shared_context.py --db /workspace/.kortix/kortix.db --output /workspace/.kortix/memory/shared-context.json >/dev/null 2>&1
*/15 * * * * /usr/bin/python3 /workspace/scripts/autowork/verify_leads.py --db /workspace/.kortix/kortix.db --context /workspace/.kortix/memory/shared-context.json --batch-size 50 --concurrency 5 --mode auto >> /workspace/logs/autowork.log 2>&1
EOF
crontab "$TMP_CRON"
rm -f "$TMP_CRON"
crontab -l
