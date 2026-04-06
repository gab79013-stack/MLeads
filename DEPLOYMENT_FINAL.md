# DEPLOYMENT FINAL - MLeads Droplet

## PASO 1: Actualizar código en el droplet

```bash
cd /home/mleads/MLeads
git fetch origin claude/check-lead-calendar-integration-K0AOx
git checkout claude/check-lead-calendar-integration-K0AOx  
git pull origin claude/check-lead-calendar-integration-K0AOx
```

## PASO 2: Instalar nuevas dependencias

```bash
source venv/bin/activate
pip install -r requirements.txt --upgrade
```

## PASO 3: Verificar que todo está correctamente instalado

```bash
python3 -m py_compile web/app.py web_server.py utils/web_db.py
echo "✅ Python files compile successfully"
```

## PASO 4: Reiniciar el servicio

```bash
sudo systemctl stop mleads-web
sleep 2
sudo systemctl start mleads-web
sleep 3
sudo systemctl status mleads-web
```

## PASO 5: Verificar que el servicio está corriendo con 2 workers

```bash
ps aux | grep gunicorn | grep mleads | grep -v grep
# Should show 2 worker processes + 1 master = 3 total
```

## PASO 6: Verificar que el dashboard responde

```bash
curl -s http://localhost:5001/ | head -20
# Should show HTML starting with <!DOCTYPE html>

curl -s http://localhost:5001/api/health | jq .
# Should show: {"status":"ok","timestamp":"..."}
```

## PASO 7: Acceder desde navegador

Abre: http://159.223.199.152:5001

### Credenciales de demo:
- Usuario: `admin`
- Contraseña: `admin123`

### Pruebas a realizar:

1. **Login**: Ingresa con admin/admin123
2. **Dashboard**: Verifica que carga sin errores
3. **Leads**: Click en "Leads" para ver leads (si hay)
4. **Users**: Click en "Users" para crear nuevos usuarios
5. **Inspections**: Click en "Inspections" para ver calendario integrado

## PASO 8: Monitorear logs

```bash
# Follow logs en tiempo real
sudo journalctl -u mleads-web -f

# Ver errores de los últimos 30 minutos
sudo journalctl -u mleads-web --since "30 minutes ago" | grep -i error
```

## PASO 9: Verificar performance

```bash
# Check memory usage (debe estar < 256MB)
ps aux | grep gunicorn | grep mleads

# Listar todos los logs
tail -100 /home/mleads/MLeads/logs/web-error.log
tail -100 /home/mleads/MLeads/logs/web-access.log
```

## Si algo falla:

1. **Error de módulos faltantes**: Ejecutar `pip install -r requirements.txt`
2. **Port already in use**: `sudo pkill -f gunicorn; sleep 2; sudo systemctl start mleads-web`
3. **Crashes continúos**: Ver logs en `/home/mleads/MLeads/logs/web-error.log`
4. **Out of memory**: Normal si hay muchas users/leads. Sistema está optimizado para estos crashes.

## Verificar que todas las correcciones fueron aplicadas:

```bash
# 1. Check que consolidated_leads table existe
sqlite3 /home/mleads/mleads.db ".tables" | grep consolidated_leads
# Output: consolidated_leads property_signals

# 2. Check que no hay N+1 queries (ver logs)
grep "SELECT COUNT FROM lead_contacts" /home/mleads/MLeads/logs/web-access.log | wc -l
# Should be LOW number (not one per lead)

# 3. Check que dependencies están instaladas
source /home/mleads/MLeads/venv/bin/activate
python3 -c "import apscheduler, pdfplumber, pandas; print('✅ All dependencies installed')"

# 4. Check que web_server.py no tiene rutas duplicadas
grep "@app.route" /home/mleads/MLeads/web_server.py
# Should output: (empty) - todas las rutas están en app.py
```

