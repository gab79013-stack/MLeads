#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y \
  build-essential \
  curl \
  git \
  jq \
  libsqlite3-dev \
  nginx \
  python3 \
  python3-pip \
  python3-venv \
  sqlite3 \
  unzip

if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt install -y nodejs
fi

if ! command -v pm2 >/dev/null 2>&1; then
  sudo npm install -g pm2
fi

node --version
npm --version
python3 --version
sqlite3 --version
