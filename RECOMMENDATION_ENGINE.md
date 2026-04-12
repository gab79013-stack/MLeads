# Motor de Recomendación Hiper-Personalizado

## 🎯 Visión General

Sistema de recomendación tipo Netflix para leads de construcción, implementado en `utils/recommendation_engine.py`.

## 📋 Características Principales

### 1. **Content-Based Filtering** (45% del score)
- Análisis de features de leads: trade, valor, ubicación, tipo de proyecto
- Matching con preferencias aprendidas del usuario
- Perfiles dinámicos que evolucionan con cada interacción

### 2. **Collaborative Filtering** (35% del score)
- Detección de usuarios similares usando Jaccard similarity
- Recomendación basada en comportamiento de peers
- "Usuarios como tú también mostraron interés en..."

### 3. **Online Learning**
- Actualización incremental de preferencias
- Learning rate adaptativo (0.3 para nuevos usuarios, 0.1 para establecidos)
- Tracking automático de:
  - Trade weights (roofing, electrical, painting, etc.)
  - Location weights (ciudades/regiones preferidas)
  - Value ranges (rango de valores de proyectos)

### 4. **Diversity Boost** (8% del score)
- Penalización por filter bubble (-30% si es muy similar a recientes)
- Bonus por diversidad (+15% si aporta variedad al feed)
- Evita mostrar solo un tipo de lead

### 5. **Recency Scoring** (12% del score)
- Decay temporal exponencial (half-life de 7 días)
- Leads de hoy: score 1.0
- Leads de 30 días: score 0.3

## 🚀 API Principal

### Generar Recomendaciones

```python
from utils.recommendation_engine import get_recommendations

recommendations = get_recommendations(
    user_id="contractor_123",
    leads=candidate_leads,  # Lista de dicts con leads
    limit=20,
    include_explanations=True
)

for rec in recommendations:
    print(f"{rec['id']}: {rec['_recommendation_score']:.3f}")
    print(f"  Explicación: {rec.get('_explanation', [])}")
```

### Registrar Interacción

```python
from utils.recommendation_engine import record_interaction

# Usuario hace swipe right en un lead
record_interaction(
    user_id="contractor_123",
    lead_id="lead_456",
    interaction_type="swipe_right",  # swipe_right, swipe_left, click, view, contact
    lead_data=lead_full_data,
    context={"device": "mobile", "session": "abc123"}
)
```

### Obtener Estadísticas del Usuario

```python
from utils.recommendation_engine import get_recommendation_stats

stats = get_recommendation_stats("contractor_123")

print(stats['status'])  # 'active' o 'cold_start'
print(stats['top_trades'])  # [('roofing', 0.8), ('electrical', 0.6)]
print(stats['value_range'])  # {'min': 10000, 'max': 500000}
```

### Explicar Recomendación

```python
from utils.recommendation_engine import explain_recommendation

explanation = explain_recommendation("contractor_123", lead)

print(explanation['reasons'])
# ["Coincide con tu interés en roofing", 
#  "Valor del proyecto ($45,000) en tu rango habitual"]
```

## 🗄️ Esquema de Base de Datos

### Tablas Creadas

1. **user_interactions** - Historial de swipes/interacciones
2. **user_profiles** - Preferencias aprendidas por usuario
3. **lead_embeddings** - Vectores de features (26 dimensiones)
4. **user_similarity** - Matriz de similaridad entre usuarios
5. **recommendation_cache** - Cache de recomendaciones con TTL

### Inicialización

```python
from utils.recommendation_engine import init_recommendation_db

init_recommendation_db()  # Crea todas las tablas necesarias
```

## 📊 Feature Engineering

### Embedding de Leads (26 dimensiones)

1. **Trade one-hot** (16 dims): roofing, electrical, plumbing, hvac, painting, drywall, landscaping, flooring, kitchen, bathroom, addition, new_construction, demolition, solar, general_contractor, other

2. **Value normalized** (1 dim): log-scale 0-1

3. **Project type one-hot** (5 dims): new_construction, remodel, repair, addition, other

4. **Urgency score** (1 dim): 0-1 basado en inspecciones próximas, AI urgency, recencia

5. **Lead score** (1 dim): score existente normalizado

6. **Location hash** (2 dims): city y region codificadas

## 🔄 Flujo de Aprendizaje

```
Usuario nuevo → Cold start (scores neutrales)
     ↓
Primeras 20 interacciones → Learning rate alto (0.3)
     ↓
Perfil se estabiliza → Learning rate bajo (0.1)
     ↓
Cada interacción actualiza:
  - Trade preferences
  - Location preferences  
  - Value range
  - User similarity matrix (batch nocturno)
```

## ⚙️ Configuración de Pesos

```python
_WEIGHTS = {
    "content": 0.45,      # Content-based filtering
    "collaborative": 0.35, # Collaborative filtering
    "recency": 0.12,      # Decay temporal
    "diversity": 0.08,    # Boost por diversidad
}

_DECAY_HALF_LIFE_DAYS = 7  # Half-life de interacciones
```

## 🧪 Tests

Ejecutar test suite completo:

```bash
python tests/test_recommendation_engine.py
```

Incluye 6 tests:
1. Extracción de features
2. Registro de interacciones y online learning
3. Generación de recomendaciones personalizadas
4. Explicabilidad
5. Estadísticas del sistema
6. Cold start handling

## 📈 Métricas de Ejemplo

Después de 4 interacciones de un usuario:

```
Top trades preferidos:
  roofing        ██████ (0.635)
  electrical     ██████ (0.635)
  painting       █████ (0.590)

Top ubicaciones:
  oakland        ██████ (0.635)
  san francisco  ██████ (0.635)
  berkeley       █████ (0.590)

Rango de valores: $9,600 - $1,000,000
```

## 🔧 Tareas Batch (Background Jobs)

### Calcular Similaridad entre Usuarios

```python
from utils.recommendation_engine import calculate_all_user_similarities

# Ejecutar nightly con cron o scheduler
calculate_all_user_similarities(batch_size=100)
```

### Store Lead Embeddings

```python
from utils.recommendation_engine import store_lead_embedding

# Cuando llega un lead nuevo
store_lead_embedding(lead_id, lead_data)
```

## 🎯 Casos de Uso

### 1. Feed Personalizado de Leads

```python
# En tu endpoint de API
@app.route('/api/leads/feed')
def get_personalized_feed():
    user_id = request.args.get('user_id')
    
    # Obtener candidatos (ej: últimos 100 leads no vistos)
    candidates = get_unseen_leads(user_id, limit=100)
    
    # Generar recomendaciones
    recs = get_recommendations(user_id, candidates, limit=20)
    
    # Cachear para próximas requests
    cache_recommendations(user_id, recs, ttl_hours=6)
    
    return jsonify(recs)
```

### 2. Dashboard de Preferencias

```python
@app.route('/api/user/preferences')
def get_user_preferences():
    user_id = request.args.get('user_id')
    stats = get_recommendation_stats(user_id)
    
    return jsonify({
        'learned_trades': stats.get('top_trades', []),
        'learned_locations': stats.get('top_locations', []),
        'value_range': stats.get('value_range'),
        'interaction_count': stats.get('interaction_count', 0),
    })
```

### 3. A/B Testing de Algoritmo

```python
# Experimentar con diferentes pesos
_WEIGHTS_EXPERIMENT = {
    "content": 0.60,      # Más énfasis en contenido
    "collaborative": 0.20, # Menos en collaborative
    "recency": 0.15,
    "diversity": 0.05,
}

recs_experiment = get_recommendations(
    user_id, leads, limit=20
)  # Modificar _WEIGHTS global temporalmente
```

## 🚦 Producción Checklist

- [ ] Inicializar DB al deploy: `init_recommendation_db()`
- [ ] Configurar job nocturno para `calculate_all_user_similarities()`
- [ ] Llamar `store_lead_embedding()` cuando lleguen leads nuevos
- [ ] Llamar `record_interaction()` en cada swipe/click/contacto
- [ ] Invalidar cache tras interacciones importantes
- [ ] Monitorear cold start users (< 5 interacciones)
- [ ] Ajustar pesos según métricas de conversión

## 📚 Futuras Mejoras

1. **Matrix Factorization**: SVD o ALS para collaborative filtering más robusto
2. **Deep Learning**: Neural networks para embeddings no lineales
3. **Contextual Bandits**: Exploración vs explotación optimizada
4. **Real-time Updates**: Streaming updates para user profiles
5. **Multi-armed Bandit**: Optimización automática de pesos
6. **Graph-based**: User-item bipartite graph para mejor collaborative filtering

---

**Autor**: AI Assistant  
**Fecha**: 2025  
**Versión**: 1.0
