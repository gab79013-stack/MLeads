#!/bin/bash
set -e

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MLeads — Instalador automático para Droplet DigitalOcean
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# Uso:
#   curl -fsSL https://raw.githubusercontent.com/gab79013-stack/MLeads/main/install.sh | bash
#
# O si ya clonaste el repo:
#   bash install.sh
#
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Funciones de logging
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PASO 1: Verificar si es root
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Iniciando instalación de MLeads..."

if [ "$EUID" -ne 0 ]; then
   log_error "Este script debe ejecutarse como root (usa: sudo bash install.sh)"
   exit 1
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PASO 2: Actualizar sistema
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Actualizando sistema..."
apt-get update -qq
apt-get upgrade -y -qq
log_success "Sistema actualizado"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PASO 3: Instalar dependencias del sistema
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Instalando dependencias del sistema..."
apt-get install -y -qq \
    python3.11 \
    python3-pip \
    python3-venv \
    git \
    curl \
    wget \
    build-essential \
    sqlite3

log_success "Dependencias instaladas"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PASO 4: Crear usuario para MLeads (si no existe)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Configurando usuario de aplicación..."

if ! id -u mleads &>/dev/null; then
    log_info "Creando usuario 'mleads'..."
    useradd --create-home --shell /bin/bash --groups sudo mleads
    log_success "Usuario 'mleads' creado"
else
    log_warn "Usuario 'mleads' ya existe"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PASO 5: Clonar el repositorio (si no existe)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Clonando repositorio..."

if [ ! -d /home/mleads/MLeads ]; then
    su - mleads -c "git clone https://github.com/gab79013-stack/MLeads.git"
    log_success "Repositorio clonado"
else
    log_warn "Repositorio ya existe en /home/mleads/MLeads"
    cd /home/mleads/MLeads
    su - mleads -c "cd /home/mleads/MLeads && git pull origin main"
fi

cd /home/mleads/MLeads

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PASO 6: Crear entorno virtual
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Creando entorno virtual..."

if [ ! -d /home/mleads/MLeads/venv ]; then
    python3 -m venv /home/mleads/MLeads/venv
    log_success "Entorno virtual creado"
else
    log_warn "Entorno virtual ya existe"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PASO 7: Instalar dependencias Python
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Instalando dependencias Python..."

su - mleads -c "
    cd /home/mleads/MLeads
    source venv/bin/activate
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
"

log_success "Dependencias Python instaladas"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PASO 8: Configurar variables de entorno
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Configurando variables de entorno..."

if [ ! -f /home/mleads/MLeads/.env ]; then
    log_warn "Archivo .env no encontrado. Creando plantilla..."

    # Generar JWT_SECRET_KEY aleatorio
    JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

    cat > /home/mleads/MLeads/.env << EOF
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MLeads Configuration (.env)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ⚠️  OBLIGATORIO: Telegram (el sistema funciona sin APIs de pago)
TELEGRAM_BOT_TOKEN=TU_TOKEN_AQUI
TELEGRAM_CHAT_ID=-1001234567890

# Dashboard Web (JWT)
JWT_SECRET_KEY=${JWT_SECRET}
JWT_ACCESS_EXPIRY=3600
JWT_REFRESH_EXPIRY=604800
PORT=5000
DB_PATH=data/leads.db

# APIs Gratuitas (recomendadas)
SOCRATA_APP_TOKEN=
NREL_API_KEY=
CENSUS_API_KEY=

# APIs Opcionales (pago)
ATTOM_API_KEY=
HUNTER_API_KEY=
SENDGRID_API_KEY=
SENDGRID_FROM_EMAIL=
GOOGLE_GEOCODE_API_KEY=

# Agentes (habilitar/deshabilitar)
AGENT_PERMITS=true
AGENT_SOLAR=true
AGENT_RODENTS=true
AGENT_FLOOD=true
AGENT_CONSTRUCTION=true
AGENT_DECONSTRUCTION=true
AGENT_REALESTATE=true
AGENT_ENERGY=true
AGENT_PLACES=false
AGENT_YELP=false

# Scheduler de Inspecciones (nuevo)
INSPECTION_SCHEDULER_ENABLED=true

# Configuración de fuentes
SOURCE_TIMEOUT=45
CONSTRUCTION_MONTHS=1

# Slack (opcional)
SLACK_WEBHOOK_URL=
EOF

    chown mleads:mleads /home/mleads/MLeads/.env
    chmod 600 /home/mleads/MLeads/.env

    log_success "Archivo .env creado en /home/mleads/MLeads/.env"
    log_warn "⚠️  IMPORTANTE: Edita el archivo .env con tus credenciales:"
    log_warn "   nano /home/mleads/MLeads/.env"
else
    log_warn "Archivo .env ya existe"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PASO 9: Crear directorio de datos
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Creando directorios..."

mkdir -p /home/mleads/MLeads/data
mkdir -p /home/mleads/MLeads/contacts
mkdir -p /home/mleads/MLeads/logs

chown -R mleads:mleads /home/mleads/MLeads

log_success "Directorios creados"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PASO 10: Probar instalación
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

log_info "Probando instalación..."

su - mleads -c "
    cd /home/mleads/MLeads
    source venv/bin/activate
    python3 -c 'import flask; import requests; print(\"✓ Dependencias OK\")'
" || log_warn "Algunas dependencias no pudieron verificarse"

log_success "Instalación completada"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PASO 11: Mostrar próximos pasos
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

cat << EOF

${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}
${GREEN}║${NC}   ${GREEN}✓ MLeads instalado correctamente${NC}                          ${GREEN}║${NC}
${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}

${YELLOW}📋 PRÓXIMOS PASOS:${NC}

1. ${BLUE}Editar archivo .env con tus credenciales:${NC}
   ${BLUE}nano /home/mleads/MLeads/.env${NC}

   Obligatorio:
   - TELEGRAM_BOT_TOKEN: Token de @BotFather
   - TELEGRAM_CHAT_ID: ID del grupo donde recibirás leads

2. ${BLUE}(Opcional) Agregar CSVs de contactos GC:${NC}
   ${BLUE}scp ~/Downloads/*.csv mleads@${HOSTNAME}:/home/mleads/MLeads/contacts/${NC}

3. ${BLUE}Probar manualmente:${NC}
   ${BLUE}sudo su - mleads${NC}
   ${BLUE}cd /home/mleads/MLeads && source venv/bin/activate${NC}
   ${BLUE}python main.py --test${NC}

4. ${BLUE}Ver logs:${NC}
   ${BLUE}sudo journalctl -u mleads -f${NC}

${YELLOW}🚀 OPCIÓN A: Ejecutar como servicio systemd (24/7):${NC}

   ${BLUE}sudo bash /home/mleads/MLeads/setup-systemd.sh${NC}

   Esto creará 2 servicios:
   - mleads (agentes de leads) — siempre activo
   - mleads-web (dashboard) — puerto 5000

${YELLOW}🚀 OPCIÓN B: Ejecutar manualmente (testing):${NC}

   ${BLUE}sudo su - mleads${NC}
   ${BLUE}cd MLeads && source venv/bin/activate${NC}
   ${BLUE}python main.py${NC}                  # Todos los agentes
   ${BLUE}python main.py --run construction${NC}  # Solo construcción

${YELLOW}📡 Dashboard Web:${NC}

   URL: ${BLUE}http://${HOSTNAME}:5000${NC}
   Login: admin / admin123 (cambiar después!)

   Iniciar web:
   ${BLUE}sudo su - mleads${NC}
   ${BLUE}cd MLeads && source venv/bin/activate${NC}
   ${BLUE}python web_server.py${NC}

${YELLOW}📖 Documentación:${NC}

   - README: /home/mleads/MLeads/README.md
   - Dashboard: /home/mleads/MLeads/DASHBOARD.md
   - Calendarios: /home/mleads/MLeads/CALENDAR_INTEGRATION.md

${YELLOW}⚠️  IMPORTANTE:${NC}

   - Edita ${BLUE}.env${NC} antes de ejecutar
   - El primer ejecución crea ${BLUE}data/leads.db${NC}
   - Guarda backups de la BD periódicamente

${GREEN}¿Preguntas? Ver /home/mleads/MLeads/README.md${NC}

EOF

log_success "¡Instalación completada! 🎉"
