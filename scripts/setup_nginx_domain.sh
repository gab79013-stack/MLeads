#!/usr/bin/env bash
# Create or update an Nginx virtual host for a domain on the shared 0brix VM.
#
# Examples:
#   sudo ./scripts/setup_nginx_domain.sh --domain yami.com --www --static-root /var/www/taco-taco --enable-ssl
#   sudo ./scripts/setup_nginx_domain.sh --domain obrits.com --www --proxy http://127.0.0.1:5001 --enable-ssl
#   sudo ./scripts/setup_nginx_domain.sh --domain crm.obrits.com --proxy http://127.0.0.1:3000
#
# DNS must point the domain's A record to the server IP before SSL issuance.

set -euo pipefail

DOMAIN=""
INCLUDE_WWW=false
STATIC_ROOT=""
PROXY_TARGET=""
ENABLE_SSL=false
EMAIL="${CERTBOT_EMAIL:-}"

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="${2:-}"; shift 2 ;;
    --www)
      INCLUDE_WWW=true; shift ;;
    --static-root)
      STATIC_ROOT="${2:-}"; shift 2 ;;
    --proxy)
      PROXY_TARGET="${2:-}"; shift 2 ;;
    --enable-ssl)
      ENABLE_SSL=true; shift ;;
    --email)
      EMAIL="${2:-}"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo so Nginx configuration can be written." >&2
  exit 1
fi

if [[ -z "${DOMAIN}" ]]; then
  echo "--domain is required." >&2
  usage
  exit 1
fi

if [[ -n "${STATIC_ROOT}" && -n "${PROXY_TARGET}" ]]; then
  echo "Choose either --static-root or --proxy, not both." >&2
  exit 1
fi

if [[ -z "${STATIC_ROOT}" && -z "${PROXY_TARGET}" ]]; then
  echo "Choose one target: --static-root /path or --proxy http://127.0.0.1:PORT." >&2
  exit 1
fi

command -v nginx >/dev/null 2>&1 || {
  echo "Nginx is required." >&2
  exit 1
}

SERVER_NAMES="${DOMAIN}"
if [[ "${INCLUDE_WWW}" == true ]]; then
  SERVER_NAMES="${SERVER_NAMES} www.${DOMAIN}"
fi

SITE_NAME="$(printf '%s' "${DOMAIN}" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9.-' '-')"
AVAILABLE="/etc/nginx/sites-available/${SITE_NAME}"
ENABLED="/etc/nginx/sites-enabled/${SITE_NAME}"

if [[ -n "${STATIC_ROOT}" ]]; then
  mkdir -p "${STATIC_ROOT}"
  cat > "${AVAILABLE}" <<NGINX
server {
    listen 80;
    server_name ${SERVER_NAMES};

    root ${STATIC_ROOT};
    index index.html;

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;
    gzip_min_length 256;
    gzip_vary on;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location ~* \\.(jpg|jpeg|png|gif|svg|webp|css|js|ico)$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
}
NGINX
else
  cat > "${AVAILABLE}" <<NGINX
server {
    listen 80;
    server_name ${SERVER_NAMES};

    client_max_body_size 20m;

    location / {
        proxy_pass ${PROXY_TARGET};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 120s;
    }
}
NGINX
fi

ln -sf "${AVAILABLE}" "${ENABLED}"
nginx -t
systemctl reload nginx

echo "Configured ${SERVER_NAMES}"
echo "Nginx file: ${AVAILABLE}"

if [[ "${ENABLE_SSL}" == true ]]; then
  command -v certbot >/dev/null 2>&1 || {
    echo "certbot is not installed; run: apt install -y certbot python3-certbot-nginx" >&2
    exit 1
  }

  CERTBOT_ARGS=(--nginx --redirect --non-interactive --agree-tos)
  if [[ -n "${EMAIL}" ]]; then
    CERTBOT_ARGS+=(--email "${EMAIL}")
  else
    CERTBOT_ARGS+=(--register-unsafely-without-email)
  fi

  CERTBOT_ARGS+=(-d "${DOMAIN}")
  if [[ "${INCLUDE_WWW}" == true ]]; then
    CERTBOT_ARGS+=(-d "www.${DOMAIN}")
  fi

  certbot "${CERTBOT_ARGS[@]}"
  nginx -t
  systemctl reload nginx
  echo "SSL enabled for ${SERVER_NAMES}"
fi
