# SPEC-005: Phone Enrichment & Outreach

## Qué
Sistema para encontrar teléfonos de leads y hacer outreach automatizado.

## Por qué
1,141/8,407 leads tienen teléfono (13.6%). Sin teléfono, un lead no se puede vender. Y sin outreach, no hay clientes.

## Enrichment Pipeline

### Fase 1: Embedded Extraction ✅ HECHO
- Los permits de Honolulu ya traen teléfono en el campo contractor
- Regex: `PH: (XXX) XXX-XXXX`
- Resultado: +433 teléfonos, 0 costo
- Script: `extract_embedded_phones.py`

### Fase 2: Web Search (DuckDuckGo) ✅ HECHO (limitado)
- Buscar `"{contractor_name}" {city} contractor phone`
- Validar que el nombre aparezca en los resultados
- 36% hit rate para businesses, ~0% para nombres de persona
- Rate limit: 4s entre requests (ser nice)
- Script: `enrich_v3.py`
- Problema: falso positivos para nombres comunes

### Fase 3: Google Places API (PENDIENTE)
- $200 gratis/mes (5,000 requests)
- Buscar por nombre + ciudad → teléfono, dirección, rating
- Hit rate estimado: 60-80% para businesses
- Rate limit: 10 req/s

### Fase 4: Browser Harness (INSTALADO, no activo)
- Chrome headless en puerto 9222
- Navegar directorios (Yellow Pages, BBB, etc.)
- Problema: Cloudflare bloquea headless
- Útil para: formularios, login, interacción compleja

## Outreach (PENDIENTE)

### Email
- SMTP configurar (pendiente)
- Template: "Tengo 220 leads de energía en Chicago. ¿Quieres 5 gratis?"
- Follow-up: 3 días, 7 días

### WhatsApp Business API
- Pendiente conseguir acceso
- Template: "Hola {name}, soy de 0brix. Tenemos leads de {trade} en {city}. ¿Te interesa?"

### Scouts
- 3 Scouts buscando prospects en Chicago, San Jose, Dallas
- Meta: 150+ prospects

## Datos clave
- 7,266 leads sin teléfono
- 2,783 tienen nombre de contractor (buscable)
- 4,483 sin nombre ni teléfono (requieren enriquecimiento por dirección)
