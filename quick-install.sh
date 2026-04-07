#!/bin/bash
# MLeads Quick Installer Wrapper
# Compatible with sh, bash, dash, and other shells
#
# Usage:
#   curl -s https://raw.githubusercontent.com/gab79013-stack/MLeads/main/quick-install.sh | sudo bash
#   bash quick-install.sh

REPO_URL="https://github.com/gab79013-stack/MLeads.git"
INSTALL_DIR="${INSTALL_DIR:=$HOME/MLeads}"
SCRIPT_URL="https://raw.githubusercontent.com/gab79013-stack/MLeads/main/install.sh"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}🚀 MLeads Quick Installer${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}\n"

# Check if running as root
if [ $EUID -ne 0 ]; then
    echo -e "${RED}✗ Este script requiere sudo${NC}"
    echo "Ejecuta:"
    echo -e "  ${YELLOW}sudo bash quick-install.sh${NC}"
    exit 1
fi

# Check bash availability
if ! command -v bash >/dev/null 2>&1; then
    echo -e "${RED}✗ Bash no está disponible${NC}"
    echo "Instálalo primero: apt-get install bash"
    exit 1
fi

# Download the full installer script
echo -e "${YELLOW}📥 Descargando script de instalación...${NC}"
TEMP_SCRIPT=$(mktemp)

if ! curl -s --output "$TEMP_SCRIPT" "$SCRIPT_URL"; then
    echo -e "${RED}✗ No se pudo descargar el script${NC}"
    rm -f "$TEMP_SCRIPT"
    exit 1
fi

if [ ! -s "$TEMP_SCRIPT" ]; then
    echo -e "${RED}✗ El script descargado está vacío${NC}"
    rm -f "$TEMP_SCRIPT"
    exit 1
fi

echo -e "${GREEN}✓ Script descargado${NC}\n"

# Execute the installer with bash
echo -e "${YELLOW}🔧 Ejecutando instalación...${NC}"
chmod +x "$TEMP_SCRIPT"
bash "$TEMP_SCRIPT"

RESULT=$?
rm -f "$TEMP_SCRIPT"

if [ $RESULT -eq 0 ]; then
    echo -e "\n${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}✓ Instalación completada exitosamente${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}\n"
    exit 0
else
    echo -e "\n${RED}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}✗ Error durante la instalación (código: $RESULT)${NC}"
    echo -e "${RED}═══════════════════════════════════════════════════════════${NC}\n"
    exit $RESULT
fi
