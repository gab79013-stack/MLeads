# MLeads — Instalación Rápida en Droplet

## 🚀 Una línea para instalar TODO

Conéctate al droplet como **root** y ejecuta:

```bash
curl -fsSL https://raw.githubusercontent.com/gab79013-stack/MLeads/main/install.sh | bash
```

O si prefieres descargarlo primero:

```bash
curl -O https://raw.githubusercontent.com/gab79013-stack/MLeads/main/install.sh
sudo bash install.sh
```

---

## ⏱️ ¿Qué hace el installer?

El script automáticamente:

1. ✅ Actualiza el sistema Ubuntu
2. ✅ Instala Python 3.11, git, pip, venv
3. ✅ Crea usuario `mleads`
4. ✅ Clona repositorio de GitHub
5. ✅ Crea entorno virtual
6. ✅ Instala todas las dependencias Python
7. ✅ Crea archivo `.env` con valores por defecto
8. ✅ Crea directorios (`data/`, `contacts/`, `logs/`)
9. ✅ Prueba la instalación

**Tiempo estimado: 3-5 minutos**

---

## 📝 Próximos pasos después de instalar

### Paso 1: Configurar Telegram

Edita el archivo `.env`:

```bash
nano /home/mleads/MLeads/.env
```

Actualiza estos valores:

```env
TELEGRAM_BOT_TOKEN=TU_TOKEN_DE_BOTFATHER
TELEGRAM_CHAT_ID=-1001234567890
```

**¿Cómo obtenerlos?**

1. En Telegram, busca **@BotFather**
2. Envía `/newbot` — te dará un TOKEN
3. Copia el token en `TELEGRAM_BOT_TOKEN`
4. Crea un grupo privado, agrega tu bot
5. Envía un mensaje en el grupo
6. Visita: `https://api.telegram.org/bot<TOKEN>/getUpdates`
7. Busca `"chat":{"id":` — ese número es tu Chat ID (incluye el negativo)

### Paso 2: Probar manualmente (opcional)

```bash
sudo su - mleads
cd MLeads
source venv/bin/activate
python main.py --test
```

Deberías recibir un mensaje de prueba en tu grupo de Telegram.

### Paso 3: Configurar para 24/7 (opcional)

Si quieres que los agentes corran automáticamente:

```bash
sudo bash /home/mleads/MLeads/setup-systemd.sh
```

Esto crea 2 servicios:

- **mleads** — Agentes de leads (se reinician automáticamente si fallan)
- **mleads-web** — Dashboard en `http://TU_IP:5000`

Verificar estado:

```bash
sudo systemctl status mleads
sudo systemctl status mleads-web
```

### Paso 4: Ver logs

```bash
# Agentes
sudo journalctl -u mleads -f

# Dashboard web
sudo journalctl -u mleads-web -f
```

---

## 📡 Dashboard Web (24/7)

Si ejecutaste `setup-systemd.sh`:

- **URL:** `http://TU_IP_DEL_DROPLET:5000`
- **Usuario:** `admin`
- **Contraseña:** `admin123` (⚠️ cambiar en producción)

---

## 🔧 Troubleshooting

### "TELEGRAM_BOT_TOKEN no está configurado"

Solución:

```bash
nano /home/mleads/MLeads/.env
```

Asegúrate de que `TELEGRAM_BOT_TOKEN` tiene un valor válido.

### "Permission denied" al ejecutar

Asegúrate de ser root:

```bash
sudo bash install.sh
```

### Los servicios no se inician

```bash
# Ver qué salió mal
sudo journalctl -u mleads -n 100

# Reintentar
sudo systemctl restart mleads
```

### Database locked

```bash
# Reiniciar el servicio
sudo systemctl restart mleads
```

---

## 📚 Documentación

- **README.md** — Guía completa del sistema
- **DASHBOARD.md** — Cómo usar el dashboard
- **CALENDAR_INTEGRATION.md** — Sistema de inspecciones
- **INTEGRATION.md** — Integración agents + dashboard

---

## 🎯 Casos de uso rápidos

### Solo agentes (sin dashboard)

```bash
sudo su - mleads
cd MLeads
source venv/bin/activate
python main.py
```

### Solo un agente específico

```bash
python main.py --run construction
python main.py --run solar
python main.py --run permits
```

### Dashboard web

```bash
python web_server.py
```

Visita: `http://localhost:5000`

### Verificar qué leads se encontraron

```bash
python main.py --stats
```

---

## ⚙️ Configuración avanzada

### Cambiar puerto del dashboard

En `.env`:

```env
PORT=8000
```

### Agregar CSVs de contactos

```bash
scp ~/Downloads/*.csv mleads@TU_IP:/home/mleads/MLeads/contacts/
```

Reinicia el servicio:

```bash
sudo systemctl restart mleads
```

### Deshabilitar agentes específicos

En `.env`:

```env
AGENT_SOLAR=false       # Deshabilitar agente solar
AGENT_PERMITS=false     # Deshabilitar agente de permisos
```

---

## 🆘 ¿Necesitas ayuda?

1. **Ver logs:** `sudo journalctl -u mleads -f`
2. **Verificar instalación:** `ls -la /home/mleads/MLeads/`
3. **Probar conexión:** `python main.py --test`
4. **Leer README:** `cat /home/mleads/MLeads/README.md`

---

## 📊 Monitoreo

Ver uso de recursos en tiempo real:

```bash
watch -n 2 'systemctl status mleads | grep -E "Active|Memory"'
```

Ver espacio en disco:

```bash
df -h
```

Backup de BD:

```bash
cp /home/mleads/MLeads/data/leads.db /home/mleads/MLeads/data/leads.db.backup.$(date +%Y%m%d)
```

---

## 🚀 Listo!

Felicidades! MLeads está instalado y listo para generar leads 🎉

**Próximos pasos:**

1. ✅ Configurar `.env` con tus credenciales
2. ✅ Probar con `python main.py --test`
3. ✅ Agregar CSVs de contactos (opcional)
4. ✅ Ejecutar `sudo bash setup-systemd.sh` para 24/7
5. ✅ Acceder al dashboard en `http://TU_IP:5000`

---

**Versión:** 1.0  
**Última actualización:** 2026-04-05  
**Soporte:** Ver README.md en el repositorio
