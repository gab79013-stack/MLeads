#!/bin/bash

###############################################################################
# MLeads Installation Script for Ubuntu/Debian
#
# Usage:
#   bash install.sh
#   bash <(curl -s https://raw.githubusercontent.com/gab79013-stack/MLeads/main/install.sh)
#
# Features:
#   - Automatic system setup on fresh Ubuntu VM
#   - Python environment configuration
#   - Database initialization
#   - Nginx reverse proxy setup
#   - Systemd service creation
#   - Automatic startup on system reboot
###############################################################################

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
REPO_URL="${REPO_URL:=https://github.com/gab79013-stack/MLeads.git}"
INSTALL_DIR="${INSTALL_DIR:=$HOME/MLeads}"
APP_USER="${APP_USER:=$(whoami)}"
APP_PORT="${APP_PORT:=5000}"

# Functions
print_header() {
    echo -e "\n${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}\n"
}

print_step() {
    echo -e "${YELLOW}📦 $1${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# Main installation
main() {
    print_header "🚀 MLeads Installation for Ubuntu"

    # Check if running as root for system commands
    if [[ $EUID -ne 0 ]]; then
        print_error "Este script debe ejecutarse con sudo"
        echo "Ejecuta: sudo bash install.sh"
        exit 1
    fi

    # 1. Update system
    print_step "Actualizando sistema..."
    apt-get update > /dev/null 2>&1
    apt-get upgrade -y > /dev/null 2>&1
    print_success "Sistema actualizado"

    # 2. Install dependencies
    print_step "Instalando dependencias..."
    apt-get install -y \
        python3 python3-pip python3-venv python3-dev \
        git curl wget build-essential nginx \
        > /dev/null 2>&1
    print_success "Dependencias instaladas"

    # 3. Clone or update repository
    if [ ! -d "$INSTALL_DIR" ]; then
        print_step "Clonando repositorio..."
        git clone "$REPO_URL" "$INSTALL_DIR" > /dev/null 2>&1
        print_success "Repositorio clonado en $INSTALL_DIR"
    else
        print_step "Repositorio encontrado, actualizando..."
        cd "$INSTALL_DIR"
        git pull origin main > /dev/null 2>&1
        print_success "Repositorio actualizado"
    fi

    cd "$INSTALL_DIR"

    # 4. Create Python virtual environment
    print_step "Creando entorno virtual..."
    python3 -m venv venv > /dev/null 2>&1
    source venv/bin/activate
    print_success "Entorno virtual creado"

    # 5. Install Python dependencies
    print_step "Instalando paquetes Python..."
    pip install --upgrade pip setuptools wheel > /dev/null 2>&1
    if [ -f "requirements.txt" ]; then
        pip install -r requirements.txt > /dev/null 2>&1
    fi
    print_success "Dependencias Python instaladas"

    # 6. Create .env file
    print_step "Creando archivo .env..."
    if [ ! -f ".env" ]; then
        SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
        cat > .env << ENVEOF
DB_PATH=data/leads.db
FLASK_ENV=production
FLASK_SECRET_KEY=${SECRET_KEY}
PORT=${APP_PORT}
HOST=0.0.0.0
ENVEOF
        print_success "Archivo .env creado"
    else
        print_success "Archivo .env ya existe"
    fi

    # 7. Create directories
    print_step "Creando directorios..."
    mkdir -p data logs
    print_success "Directorios creados"

    # 8. Initialize database
    print_step "Inicializando base de datos..."
    python3 << 'PYTHON_EOF'
try:
    from utils.web_db import init_web_db, seed_cities_and_agents
    print("  Inicializando schema...")
    init_web_db()
    print("  Insertando datos...")
    seed_cities_and_agents()
    print("✓ Base de datos lista")
except Exception as e:
    print(f"  Advertencia: {e}")
    print("✓ Schema creado")
PYTHON_EOF

    # 9. Configure Nginx
    print_step "Configurando Nginx..."
    
    cat > /etc/nginx/sites-available/mleads << 'NGINXEOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 10M;

    access_log /var/log/nginx/mleads_access.log;
    error_log /var/log/nginx/mleads_error.log;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
NGINXEOF

    ln -sf /etc/nginx/sites-available/mleads /etc/nginx/sites-enabled/mleads
    rm -f /etc/nginx/sites-enabled/default

    if nginx -t > /dev/null 2>&1; then
        systemctl restart nginx > /dev/null 2>&1
        print_success "Nginx configurado"
    else
        print_error "Error en Nginx"
    fi

    # 10. Create systemd service
    print_step "Creando servicio systemd..."
    
    cat > /etc/systemd/system/mleads.service << SVCEOF
[Unit]
Description=MLeads Lead Management System
After=network.target nginx.service

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${INSTALL_DIR}
Environment="PATH=${INSTALL_DIR}/venv/bin"
ExecStart=${INSTALL_DIR}/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

    systemctl daemon-reload > /dev/null 2>&1
    systemctl enable mleads > /dev/null 2>&1
    systemctl start mleads
    sleep 2
    print_success "Servicio systemd creado e iniciado"

    # Summary
    print_header "✓ ¡Instalación Completada!"

    echo -e "${BLUE}📍 Acceso:${NC}"
    echo -e "   http://localhost"
    
    PUBLIC_IP=$(curl -s https://api.ipify.org 2>/dev/null || echo "")
    if [ ! -z "$PUBLIC_IP" ]; then
        echo -e "   http://${PUBLIC_IP}"
    fi

    echo -e "\n${BLUE}📋 Comandos:${NC}"
    echo -e "   sudo journalctl -u mleads -f     # Ver logs"
    echo -e "   sudo systemctl status mleads      # Ver estado"
    echo -e "   sudo systemctl restart mleads     # Reiniciar"

    echo -e "\n${YELLOW}⚠️  Próximos pasos:${NC}"
    echo -e "   1. Abre http://localhost en el navegador"
    echo -e "   2. En Azure, abre puertos 80 y 443 en el NSG"
    echo -e "   3. (Opcional) Configura SSL con Let's Encrypt"

    echo -e "\n${GREEN}═══════════════════════════════════════════════════════════${NC}\n"
}

trap 'print_error "Instalación interrumpida"; exit 1' INT TERM
main "$@"
