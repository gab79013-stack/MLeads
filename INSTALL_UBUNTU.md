# Instalación de MLeads en Ubuntu/Debian

## Instalación Automática (Recomendado)

### Opción 1: Ejecutar localmente
```bash
cd MLeads
sudo bash install.sh
```

### Opción 2: Instalación remota en Azure VM
```bash
sudo bash <(curl -s https://raw.githubusercontent.com/gab79013-stack/MLeads/main/install.sh)
```

### Opción 3: Descargar primero
```bash
curl -O https://raw.githubusercontent.com/gab79013-stack/MLeads/main/install.sh
sudo bash install.sh
```

## ¿Qué hace el script?

✅ Actualiza el sistema operativo  
✅ Instala Python 3, pip, venv  
✅ Instala Nginx y dependencias del sistema  
✅ Clona o actualiza el repositorio  
✅ Crea entorno virtual Python  
✅ Instala todas las dependencias Python  
✅ Crea archivo `.env` con contraseña segura  
✅ Inicializa la base de datos SQLite  
✅ Configura Nginx como reverse proxy  
✅ Crea servicio systemd  
✅ Inicia la aplicación automáticamente  

## Después de la instalación

### Acceder a la aplicación
```
http://localhost
http://<IP_PUBLICA>
```

### Ver logs
```bash
sudo journalctl -u mleads -f
```

### Ver estado del servicio
```bash
sudo systemctl status mleads
```

### Detener/Reiniciar
```bash
sudo systemctl stop mleads
sudo systemctl restart mleads
```

## Configuración en Azure

### 1. Abrir puertos en Network Security Group
1. Ir a tu VM en Azure Portal
2. **Networking** → **Inbound Port Rules**
3. **Add Inbound Rule**:
   - Protocol: TCP
   - Port: 80 (HTTP) y 443 (HTTPS)
   - Source: 0.0.0.0/0 (o tu IP)

### 2. Obtener la IP pública
```bash
curl https://api.ipify.org
```

### 3. Acceder a la aplicación
Usa la IP pública o el nombre de dominio en:
```
http://<IP_PUBLICA>
```

## Configuración HTTPS (SSL/TLS)

### Instalar Let's Encrypt (opcional)
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d tudominio.com
```

## Troubleshooting

### El servicio no inicia
```bash
sudo journalctl -u mleads -n 50  # Últimas 50 líneas de log
sudo systemctl status mleads      # Ver estado detallado
```

### Nginx no responde
```bash
sudo nginx -t                      # Verificar configuración
sudo systemctl restart nginx       # Reiniciar Nginx
sudo tail -f /var/log/nginx/error.log  # Ver errores
```

### Base de datos vacía
```bash
cd ~/MLeads
source venv/bin/activate
python3 << 'EOF'
from utils.web_db import init_web_db, seed_cities_and_agents
init_web_db()
seed_cities_and_agents()
print("✓ Base de datos inicializada")
EOF
```

## Variables de entorno (.env)

El script crea automáticamente un archivo `.env` con:
- `DB_PATH`: Ruta a la base de datos
- `FLASK_ENV`: production
- `FLASK_SECRET_KEY`: Contraseña segura (generada automáticamente)
- `PORT`: Puerto de la aplicación (5000)
- `HOST`: 0.0.0.0

## Actualizar la aplicación

```bash
cd ~/MLeads
git pull origin main
sudo systemctl restart mleads
```

## Desinstalación

```bash
sudo systemctl stop mleads
sudo systemctl disable mleads
sudo rm /etc/systemd/system/mleads.service
sudo rm /etc/nginx/sites-enabled/mleads
sudo systemctl restart nginx
# El código fuente se mantiene en ~/MLeads
```

## Soporte

Para problemas o preguntas:
- Revisa los logs: `sudo journalctl -u mleads -f`
- Verifica el archivo `.env` está configurado correctamente
- Asegúrate que los puertos 80/443 estén abiertos en el firewall
