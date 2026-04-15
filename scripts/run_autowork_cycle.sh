#!/usr/bin/env bash
set -euo pipefail

python3 /workspace/scripts/build_shared_context.py --db /workspace/.kortix/kortix.db --output /workspace/.kortix/memory/shared-context.json >/dev/null 2>&1
python3 /workspace/scripts/autowork/verify_leads.py --db /workspace/.kortix/kortix.db --context /workspace/.kortix/memory/shared-context.json --batch-size "${AUTOWORK_BATCH_SIZE:-50}" --concurrency "${AUTOWORK_MAX_CONCURRENT:-5}" --mode auto >> /workspace/logs/autowork.log 2>&1
