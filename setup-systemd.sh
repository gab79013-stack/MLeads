#!/bin/bash
set -e

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MLeads — Setup de servicios systemd (24/7)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# Uso: sudo bash setup-systemd.sh
#
# Crea 2 servicios:
# 1. mleads — Agentes de leads (siempre activos, se reinician automaticamente)
# 2. mleads-web — Dashboard web (puerto 5000)
#
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Verificar root
if [ "$EUID" -ne 0 ]; then
   log_error "Este script debe ejecutarse con: sudo bash setup-systemd.sh"
   exit 1
fi

log_info "Configurando servicios systemd para MLeads..."

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Crear servicio mleads (agentes de leads)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Creando servicio 'mleads'..."

cat > /etc/systemd/system/mleads.service << 'EOF'
[Unit]
Description=MLeads — Agentes de generación de leads
After=network.target

[Service]
Type=simple
User=mleads
Group=mleads
WorkingDirectory=/home/mleads/MLeads
EnvironmentFile=/home/mleads/MLeads/.env

ExecStart=/home/mleads/MLeads/venv/bin/python /home/mleads/MLeads/main.py

Restart=always
RestartSec=10

StandardOutput=journal
StandardError=journal

# Limites de recursos
MemoryLimit=512M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
EOF

log_success "Servicio 'mleads' creado"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Crear servicio mleads-web (dashboard)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Creando servicio 'mleads-web'..."

cat > /etc/systemd/system/mleads-web.service << 'EOF'
[Unit]
Description=MLeads — Web Dashboard
After=network.target

[Service]
Type=simple
User=mleads
Group=mleads
WorkingDirectory=/home/mleads/MLeads
EnvironmentFile=/home/mleads/MLeads/.env

ExecStart=/home/mleads/MLeads/venv/bin/gunicorn \
    --workers 4 \
    --bind 0.0.0.0:5000 \
    --timeout 30 \
    --access-logfile /home/mleads/MLeads/logs/web-access.log \
    --error-logfile /home/mleads/MLeads/logs/web-error.log \
    web_server:app

Restart=always
RestartSec=10

StandardOutput=journal
StandardError=journal

# Limites de recursos
MemoryLimit=256M
CPUQuota=50%

[Install]
WantedBy=multi-user.target
EOF

log_success "Servicio 'mleads-web' creado"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Recargar systemd y habilitar servicios
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Recargando configuración de systemd..."

systemctl daemon-reload
systemctl enable mleads
systemctl enable mleads-web

log_success "Servicios habilitados para auto-inicio"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Iniciar servicios
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Iniciando servicios..."

systemctl start mleads
systemctl start mleads-web

sleep 2

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Verificar estado
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Verificando estado de servicios..."

STATUS_MLEADS=$(systemctl is-active mleads)
STATUS_WEB=$(systemctl is-active mleads-web)

if [ "$STATUS_MLEADS" = "active" ]; then
    log_success "Servicio 'mleads' está corriendo"
else
    log_error "Servicio 'mleads' NO está corriendo"
    log_info "Ver logs: journalctl -u mleads -n 50"
fi

if [ "$STATUS_WEB" = "active" ]; then
    log_success "Servicio 'mleads-web' está corriendo (puerto 5000)"
else
    log_error "Servicio 'mleads-web' NO está corriendo"
    log_info "Ver logs: journalctl -u mleads-web -n 50"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Mostrar instrucciones finales
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

cat << EOF

${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}
${GREEN}║${NC}   ${GREEN}✓ Servicios systemd configurados${NC}                         ${GREEN}║${NC}
${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}

${YELLOW}📍 Estado actual:${NC}

  Agentes (mleads):      ${STATUS_MLEADS}
  Dashboard (mleads-web): ${STATUS_WEB}

${YELLOW}🔧 Comandos útiles:${NC}

  Ver estado:
    ${BLUE}systemctl status mleads${NC}
    ${BLUE}systemctl status mleads-web${NC}

  Ver logs (tiempo real):
    ${BLUE}sudo journalctl -u mleads -f${NC}
    ${BLUE}sudo journalctl -u mleads-web -f${NC}

  Ver logs (últimas 50 líneas):
    ${BLUE}sudo journalctl -u mleads -n 50${NC}
    ${BLUE}sudo journalctl -u mleads-web -n 50${NC}

  Controlar servicios:
    ${BLUE}sudo systemctl restart mleads${NC}
    ${BLUE}sudo systemctl restart mleads-web${NC}
    ${BLUE}sudo systemctl stop mleads${NC}
    ${BLUE}sudo systemctl stop mleads-web${NC}

${YELLOW}📡 Dashboard Web:${NC}

  URL: ${BLUE}http://TU_IP:5000${NC}
  Usuario: ${BLUE}admin${NC}
  Contraseña: ${BLUE}admin123${NC}

  ${RED}⚠️  Cambia la contraseña en producción!${NC}

${YELLOW}📊 Monitoreo:${NC}

  Ver uso de CPU/memoria en tiempo real:
    ${BLUE}watch -n 2 'systemctl status mleads | grep -E "Active|Memory|CPU'${NC}

${YELLOW}📝 Notas:${NC}

  - Los servicios se reiniciarán automáticamente si fallan
  - Limites de recursos: 512MB RAM (mleads), 256MB RAM (web)
  - Logs se guardan en: /var/log/journal/
  - Base de datos: /home/mleads/MLeads/data/leads.db

${YELLOW}❓ Si algo no funciona:${NC}

  1. Verifica el archivo .env:
     ${BLUE}cat /home/mleads/MLeads/.env | grep TELEGRAM${NC}

  2. Chequea logs:
     ${BLUE}sudo journalctl -u mleads -n 100${NC}

  3. Reinstala (si es necesario):
     ${BLUE}sudo systemctl stop mleads mleads-web${NC}
     ${BLUE}cd /home/mleads/MLeads${NC}
     ${BLUE}sudo su - mleads -c 'source venv/bin/activate && pip install -r requirements.txt'${NC}
     ${BLUE}sudo systemctl start mleads mleads-web${NC}

${GREEN}✓ Configuración completada${NC}

EOF

log_success "Setup completado"
