#!/usr/bin/env bash
set -euo pipefail

PM2_BIN="$(npm prefix -g)/bin/pm2"
"$PM2_BIN" start /workspace/ecosystem.config.js
"$PM2_BIN" save
"$PM2_BIN" status
