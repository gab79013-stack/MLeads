#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════
#  update-server.sh — Pone al día la VM con la última versión del repo
# ════════════════════════════════════════════════════════════════════
#
#  Uso:
#    sudo ./update-server.sh                   # Actualiza desde main
#    sudo ./update-server.sh -b mi-rama        # Actualiza desde otra rama
#    sudo ./update-server.sh --no-deps         # Salta pip install
#    sudo ./update-server.sh --no-restart      # No reinicia los servicios
#    sudo ./update-server.sh --help
#
#  Qué hace:
#    1. git fetch + checkout + pull de la rama indicada
#    2. Activa el venv y corre pip install -r requirements.txt (si cambió)
#    3. Avisa de variables nuevas en .env.example que falten en .env
#    4. Reinicia los servicios systemd (mleads / mleads-web o insulleads*)
#    5. Verifica que arrancaron y muestra el estado
#
#  Funciona con cualquiera de los nombres de servicio comunes:
#    - mleads / mleads-web
#    - insulleads / insulleads-web
# ════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────
BRANCH="main"
INSTALL_DEPS=true
RESTART_SERVICES=true
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colores ─────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ── Helpers ─────────────────────────────────────────────────────────
log()   { echo -e "${BLUE}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
err()   { echo -e "${RED}✗${NC} $*" >&2; }
step()  { echo ""; echo -e "${YELLOW}━━━ $* ━━━${NC}"; }

usage() {
    sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

# ── Parse args ──────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        -b|--branch)      BRANCH="$2"; shift 2 ;;
        --no-deps)        INSTALL_DEPS=false; shift ;;
        --no-restart)     RESTART_SERVICES=false; shift ;;
        -h|--help)        usage ;;
        *)                err "Argumento desconocido: $1"; usage ;;
    esac
done

# ── Privilegios ─────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]] && $RESTART_SERVICES; then
    err "Este script necesita sudo para reiniciar systemd."
    err "Ejecuta:  sudo $0 $*"
    err "(o usa --no-restart si solo quieres bajar el código)"
    exit 1
fi

# Usuario real (cuando se corre con sudo, queremos su venv y su git)
REAL_USER="${SUDO_USER:-$(whoami)}"
run_as_user() {
    if [[ "$REAL_USER" != "root" ]] && [[ $EUID -eq 0 ]]; then
        sudo -u "$REAL_USER" "$@"
    else
        "$@"
    fi
}

echo "════════════════════════════════════════════════════════════"
echo "  MLeads — Update Server"
echo "════════════════════════════════════════════════════════════"
echo "  Proyecto:   $PROJECT_DIR"
echo "  Rama:       $BRANCH"
echo "  Usuario:    $REAL_USER"
echo "  Deps:       $($INSTALL_DEPS && echo sí || echo no)"
echo "  Reiniciar:  $($RESTART_SERVICES && echo sí || echo no)"
echo "════════════════════════════════════════════════════════════"

cd "$PROJECT_DIR"

# ── 1. Git pull ─────────────────────────────────────────────────────
step "1/5  Actualizando código desde Git"

if ! run_as_user git rev-parse --git-dir > /dev/null 2>&1; then
    err "No es un repo Git: $PROJECT_DIR"
    exit 1
fi

CURRENT_BRANCH="$(run_as_user git branch --show-current)"
log "Rama actual: $CURRENT_BRANCH"

# Aviso si hay cambios locales sin commitear
if ! run_as_user git diff --quiet || ! run_as_user git diff --cached --quiet; then
    warn "Hay cambios locales sin commitear:"
    run_as_user git status --short
    read -r -p "¿Continuar de todos modos? [y/N] " yn
    [[ "$yn" =~ ^[Yy]$ ]] || { err "Cancelado."; exit 1; }
fi

log "git fetch origin $BRANCH"
run_as_user git fetch origin "$BRANCH"

if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
    log "Cambiando a rama $BRANCH"
    run_as_user git checkout "$BRANCH"
fi

OLD_SHA="$(run_as_user git rev-parse HEAD)"
log "git pull origin $BRANCH"
run_as_user git pull --ff-only origin "$BRANCH"
NEW_SHA="$(run_as_user git rev-parse HEAD)"

if [[ "$OLD_SHA" == "$NEW_SHA" ]]; then
    ok "Ya estaba al día (${NEW_SHA:0:7})"
    REQS_CHANGED=false
else
    ok "Actualizado: ${OLD_SHA:0:7} → ${NEW_SHA:0:7}"
    echo ""
    echo "Commits aplicados:"
    run_as_user git log --oneline "$OLD_SHA..$NEW_SHA" | sed 's/^/  /'

    # ¿Cambió requirements.txt?
    if run_as_user git diff --name-only "$OLD_SHA" "$NEW_SHA" | grep -q "^requirements.txt$"; then
        REQS_CHANGED=true
        warn "requirements.txt cambió — se reinstalarán dependencias"
    else
        REQS_CHANGED=false
    fi
fi

# ── 2. Dependencias ─────────────────────────────────────────────────
step "2/5  Dependencias Python"

if $INSTALL_DEPS && { $REQS_CHANGED || [[ ! -d "$PROJECT_DIR/venv" ]]; }; then
    if [[ ! -d "$PROJECT_DIR/venv" ]]; then
        log "Creando virtualenv..."
        run_as_user python3 -m venv venv
    fi
    log "pip install -r requirements.txt"
    run_as_user bash -c "source '$PROJECT_DIR/venv/bin/activate' && pip install --upgrade pip -q && pip install -q -r requirements.txt"
    ok "Dependencias actualizadas"
elif ! $INSTALL_DEPS; then
    warn "Saltado (--no-deps)"
else
    ok "requirements.txt no cambió — sin reinstalar"
fi

# ── 3. Variables de entorno ─────────────────────────────────────────
step "3/5  Comprobando .env"

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    warn ".env no existe. Cópialo desde la plantilla:"
    warn "    cp .env.example .env && nano .env"
elif [[ -f "$PROJECT_DIR/.env.example" ]]; then
    EXAMPLE_VARS="$(grep -oE '^[A-Z_][A-Z0-9_]*(?==)' "$PROJECT_DIR/.env.example" 2>/dev/null \
                    || grep -oE '^[A-Z_][A-Z0-9_]*=' "$PROJECT_DIR/.env.example" | sed 's/=$//')"
    CURRENT_VARS="$(grep -oE '^[A-Z_][A-Z0-9_]*=' "$PROJECT_DIR/.env" 2>/dev/null | sed 's/=$//' || true)"
    MISSING="$(comm -23 <(echo "$EXAMPLE_VARS" | sort -u) <(echo "$CURRENT_VARS" | sort -u) || true)"

    if [[ -n "$MISSING" ]]; then
        warn "Variables nuevas en .env.example que NO están en tu .env:"
        echo "$MISSING" | sed 's/^/    /'
        warn "Edítalo con:  nano $PROJECT_DIR/.env"
    else
        ok ".env tiene todas las variables del template"
    fi
fi

# ── 4. Reiniciar servicios ──────────────────────────────────────────
step "4/5  Reiniciando servicios systemd"

if ! $RESTART_SERVICES; then
    warn "Saltado (--no-restart)"
else
    # Detecta automáticamente cuál nombre de servicio usa la VM
    SERVICES=()
    for svc in mleads mleads-web insulleads insulleads-web; do
        if systemctl list-unit-files "${svc}.service" 2>/dev/null | grep -q "^${svc}.service"; then
            SERVICES+=("$svc")
        fi
    done

    if [[ ${#SERVICES[@]} -eq 0 ]]; then
        warn "No se encontraron servicios systemd (mleads* / insulleads*)."
        warn "Si corres la app de otra forma (Docker, PM2, manual), reiníciala tú."
    else
        for svc in "${SERVICES[@]}"; do
            log "systemctl restart $svc"
            systemctl restart "$svc"
        done
        sleep 3
        ok "Servicios reiniciados: ${SERVICES[*]}"
    fi
fi

# ── 5. Verificación ─────────────────────────────────────────────────
step "5/5  Verificación"

if $RESTART_SERVICES && [[ ${#SERVICES[@]} -gt 0 ]]; then
    ALL_OK=true
    for svc in "${SERVICES[@]}"; do
        if systemctl is-active --quiet "$svc"; then
            ok "$svc → activo"
        else
            err "$svc → NO está activo"
            ALL_OK=false
        fi
    done

    if ! $ALL_OK; then
        echo ""
        err "Algún servicio falló al arrancar. Logs recientes:"
        for svc in "${SERVICES[@]}"; do
            echo ""
            echo "── $svc ──────────────────────────────────────────"
            journalctl -u "$svc" -n 15 --no-pager
        done
        exit 1
    fi
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}✅ VM actualizada correctamente${NC}"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Commit actual: $(run_as_user git log --oneline -1)"
echo ""
echo "Para ver logs en vivo:"
for svc in "${SERVICES[@]:-mleads}"; do
    echo "  sudo journalctl -u $svc -f"
done
echo ""
