"""
utils/recommendation_engine.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Motor de Recomendación Hiper-Personalizado — Sistema tipo Netflix para leads

Arquitectura:
  1. Content-Based Filtering: Features del lead (trade, valor, ubicación, tipo)
  2. Collaborative Filtering: Usuarios similares con comportamientos parecidos
  3. Hybrid Scoring: Combina ambos enfoques + contexto temporal
  4. Online Learning: Actualiza pesos con cada swipe/interacción

Features principales:
  - Perfil de usuario dinámico (preferencias explícitas + implícitas)
  - Similaridad coseno entre leads basados en embeddings
  - Factorización de matrices para collaborative filtering
  - Decay temporal (leads recientes tienen más peso)
  - Diversity boost (evita mostrar solo un tipo de lead)
  - Cold start handling (nuevos usuarios / nuevos leads)

Score final = α·content_score + β·collab_score + γ·recency + δ·diversity
"""

import logging
import math
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
import os

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "data/leads.db")


# ── Configuración de pesos ───────────────────────────────────────
_WEIGHTS = {
    "content": 0.45,      # Content-based filtering
    "collaborative": 0.35, # Collaborative filtering
    "recency": 0.12,      # Decay temporal
    "diversity": 0.08,    # Boost por diversidad
}

# Decay rate para interacciones antiguas (half-life de 7 días)
_DECAY_HALF_LIFE_DAYS = 7
_DECAY_RATE = math.log(2) / _DECAY_HALF_LIFE_DAYS


def _get_conn() -> sqlite3.Connection:
    """Obtiene conexión a la base de datos."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_recommendation_db():
    """
    Inicializa tablas para el motor de recomendación:
      - user_interactions: historial de swipes/interacciones
      - user_profiles: preferencias aprendidas por usuario
      - lead_embeddings: vectores de features para cada lead
    """
    with _get_conn() as conn:
        # Historial de interacciones (swipes, clicks, tiempo de vista)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                lead_id TEXT NOT NULL,
                interaction_type TEXT NOT NULL,  -- 'swipe_right', 'swipe_left', 'click', 'view', 'contact'
                interaction_value REAL DEFAULT 1.0,  -- intensidad (ej: tiempo de vista en segundos)
                created_at TEXT NOT NULL,
                context JSON  -- metadata adicional (ubicación, dispositivo, etc.)
            )
        """)
        
        # Índice para consultas rápidas por usuario
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_interactions_user 
            ON user_interactions(user_id, created_at)
        """)
        
        # Índice para consultas por lead
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_interactions_lead 
            ON user_interactions(lead_id)
        """)
        
        # Perfil de usuario (preferencias aprendidas)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id TEXT PRIMARY KEY,
                preferences JSON NOT NULL,
                trade_weights JSON,
                location_weights JSON,
                value_range_min REAL,
                value_range_max REAL,
                last_updated TEXT NOT NULL,
                interaction_count INTEGER DEFAULT 0
            )
        """)
        
        # Embeddings de leads (features vectorizadas)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lead_embeddings (
                lead_id TEXT PRIMARY KEY,
                embedding JSON NOT NULL,
                trade_category TEXT,
                value_bucket TEXT,
                location_cluster TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Matriz de similaridad entre usuarios (para collaborative filtering)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_similarity (
                user_id_1 TEXT NOT NULL,
                user_id_2 TEXT NOT NULL,
                similarity_score REAL NOT NULL,
                common_leads INTEGER,
                calculated_at TEXT NOT NULL,
                PRIMARY KEY (user_id_1, user_id_2)
            )
        """)
        
        # Cache de recomendaciones por usuario
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recommendation_cache (
                user_id TEXT NOT NULL,
                lead_id TEXT NOT NULL,
                score REAL NOT NULL,
                rank INTEGER,
                generated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (user_id, lead_id)
            )
        """)
        
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cache_user_score 
            ON recommendation_cache(user_id, score DESC)
        """)
        
        conn.commit()
    
    logger.info("Tablas de recomendación inicializadas")


# ── Funciones de utilidad ────────────────────────────────────────

def _parse_json(json_str: Optional[str]) -> Any:
    """Parsea string JSON a objeto Python."""
    if not json_str:
        return {}
    try:
        import json
        return json.loads(json_str)
    except:
        return {}


def _to_json(obj: Any) -> str:
    """Convierte objeto a string JSON."""
    import json
    try:
        return json.dumps(obj, default=str)
    except:
        return "{}"


def _decay_factor(created_at: str) -> float:
    """
    Calcula factor de decay exponencial basado en la antigüedad.
    Half-life de 7 días: una interacción de hace 7 días vale 50%.
    """
    try:
        if isinstance(created_at, datetime):
            dt = created_at
        else:
            dt = datetime.fromisoformat(created_at.replace('Z', '+00:00').replace('+00:00', ''))
        
        days_ago = (datetime.utcnow() - dt).days
        return math.exp(-_DECAY_RATE * days_ago)
    except:
        return 0.5  # Default si no se puede parsear


# ── Extracción de features de leads ─────────────────────────────

def extract_lead_features(lead: Dict) -> Dict[str, Any]:
    """
    Extrae features normalizadas de un lead para crear su embedding.
    
    Features:
      - trade_onehot: one-hot encoding del trade/categoría
      - value_normalized: valor del proyecto normalizado (0-1)
      - location_encoded: codificación de ubicación (city/zip)
      - project_type: tipo de proyecto (new, remodel, repair)
      - urgency signals: señales de urgencia
    """
    features = {}
    
    # Trade categories (one-hot encoding simplificado)
    trade_categories = [
        'roofing', 'electrical', 'plumbing', 'hvac', 'painting',
        'drywall', 'landscaping', 'flooring', 'kitchen', 'bathroom',
        'addition', 'new_construction', 'demolition', 'solar',
        'general_contractor', 'other'
    ]
    
    desc = ((lead.get("description") or "") + " " +
            (lead.get("permit_type") or "") + " " +
            (lead.get("service_type") or "") + " " +
            (lead.get("trade") or "")).lower()
    
    trade_onehot = {}
    for category in trade_categories:
        trade_onehot[category] = 1.0 if category.replace('_', ' ') in desc or category in desc else 0.0
    
    # Asegurar que al menos una categoría tenga valor
    if sum(trade_onehot.values()) == 0:
        trade_onehot['other'] = 1.0
    
    features['trade_onehot'] = trade_onehot
    
    # Valor normalizado (log-scale para manejar outliers)
    value = lead.get("value_float", 0) or 0
    if value > 0:
        # Normalizar con log: valores típicos 10k-1M → 0-1
        value_normalized = min(math.log10(value + 1) / 6.0, 1.0)
    else:
        value_normalized = 0.5  # Valor desconocido → medio
    
    features['value_normalized'] = value_normalized
    
    # Bucket de valor para agrupación
    if value >= 500000:
        features['value_bucket'] = 'very_high'
    elif value >= 200000:
        features['value_bucket'] = 'high'
    elif value >= 50000:
        features['value_bucket'] = 'medium'
    elif value > 0:
        features['value_bucket'] = 'low'
    else:
        features['value_bucket'] = 'unknown'
    
    # Ubicación (city/zip codificado)
    city = (lead.get("city") or "unknown").lower().replace(' ', '_')
    features['location_city'] = city
    
    zip_code = lead.get("zip", "")
    if zip_code and len(zip_code) >= 3:
        # Usar primeros 3 dígitos para regionalización
        features['location_region'] = f"region_{zip_code[:3]}"
    else:
        features['location_region'] = 'unknown'
    
    # Tipo de proyecto
    project_type_keywords = {
        'new_construction': ['new construction', 'new build', 'ground up'],
        'remodel': ['remodel', 'renovation', 'upgrade'],
        'repair': ['repair', 'fix', 'replacement'],
        'addition': ['addition', 'adu', 'extension'],
        'maintenance': ['maintenance', 'inspection'],
    }
    
    project_type = 'other'
    for ptype, keywords in project_type_keywords.items():
        if any(kw in desc for kw in keywords):
            project_type = ptype
            break
    
    features['project_type'] = project_type
    
    # Señales de urgencia
    urgency_signals = 0
    if lead.get("next_scheduled_inspection_date"):
        urgency_signals += 2
    
    ai_urgency = lead.get("_urgency", "")
    if ai_urgency == "HIGH":
        urgency_signals += 3
    elif ai_urgency == "MEDIUM":
        urgency_signals += 1
    
    # Recencia del lead
    date_str = lead.get("date") or lead.get("issued_date") or ""
    if date_str:
        try:
            lead_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
            days_ago = (datetime.utcnow() - lead_date).days
            if days_ago <= 7:
                urgency_signals += 2
            elif days_ago <= 14:
                urgency_signals += 1
        except:
            pass
    
    features['urgency_score'] = min(urgency_signals, 10) / 10.0  # Normalizar 0-1
    
    # Score existente (si ya fue calculado)
    features['lead_score'] = (lead.get("_score") or lead.get("score") or 50) / 100.0
    
    return features


def create_lead_embedding(features: Dict) -> List[float]:
    """
    Convierte features en un vector embedding plano.
    Este embedding se usa para calcular similaridad coseno.
    """
    embedding = []
    
    # Trade one-hot (16 dimensiones)
    trade_cats = ['roofing', 'electrical', 'plumbing', 'hvac', 'painting',
                  'drywall', 'landscaping', 'flooring', 'kitchen', 'bathroom',
                  'addition', 'new_construction', 'demolition', 'solar',
                  'general_contractor', 'other']
    
    trade_onehot = features.get('trade_onehot', {})
    for cat in trade_cats:
        embedding.append(trade_onehot.get(cat, 0.0))
    
    # Valor normalizado (1 dimensión)
    embedding.append(features.get('value_normalized', 0.5))
    
    # Project type one-hot (5 dimensiones)
    project_types = ['new_construction', 'remodel', 'repair', 'addition', 'other']
    ptype = features.get('project_type', 'other')
    for pt in project_types:
        embedding.append(1.0 if pt == ptype else 0.0)
    
    # Urgency score (1 dimensión)
    embedding.append(features.get('urgency_score', 0.0))
    
    # Lead score (1 dimensión)
    embedding.append(features.get('lead_score', 0.5))
    
    # Location hash (2 dimensiones simplificadas)
    # En producción, usaríamos geohash o coordenadas reales
    city = features.get('location_city', 'unknown')
    region = features.get('location_region', 'unknown')
    location_hash = [hash(city) % 100 / 100.0, hash(region) % 100 / 100.0]
    embedding.extend(location_hash)
    
    return embedding  # Total: 16 + 1 + 5 + 1 + 1 + 2 = 26 dimensiones


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Calcula similaridad coseno entre dos vectores."""
    if len(vec_a) != len(vec_b):
        return 0.0
    
    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return dot_product / (norm_a * norm_b)


# ── Content-Based Filtering ─────────────────────────────────────

def calculate_content_score(user_id: str, lead: Dict, user_profile: Optional[Dict] = None) -> float:
    """
    Calcula score basado en contenido (preferencias del usuario vs features del lead).
    
    Componentes:
      - Trade preference match: ¿Coincide con trades que el usuario prefiere?
      - Value range match: ¿Está en el rango de valores que el usuario suele interactuar?
      - Location match: ¿Es en zona que el usuario prefiere?
      - Project type match: ¿Tipo de proyecto alineado con preferencias?
    """
    if user_profile is None:
        user_profile = get_user_profile(user_id)
    
    if not user_profile:
        # Cold start: retornar score neutral basado solo en calidad del lead
        features = extract_lead_features(lead)
        return 0.5 + (features.get('lead_score', 0.5) * 0.3)
    
    score = 0.0
    weights_sum = 0.0
    
    # 1. Trade preference match (peso: 0.4)
    trade_weights = _parse_json(user_profile.get('trade_weights'))
    if trade_weights:
        lead_features = extract_lead_features(lead)
        lead_trade_onehot = lead_features.get('trade_onehot', {})
        
        trade_match = 0.0
        for trade, weight in trade_weights.items():
            if lead_trade_onehot.get(trade, 0.0) > 0:
                trade_match += weight
        
        # Normalizar por número de trades activos en el lead
        active_trades = sum(1 for v in lead_trade_onehot.values() if v > 0)
        if active_trades > 0:
            trade_match /= active_trades
        
        score += trade_match * 0.4
        weights_sum += 0.4
    
    # 2. Value range match (peso: 0.25)
    value_min = user_profile.get('value_range_min', 0)
    value_max = user_profile.get('value_range_max', 1000000)
    lead_value = lead.get('value_float', 0) or 0
    
    if value_max > value_min:
        if value_min <= lead_value <= value_max:
            # Dentro del rango preferido
            value_score = 1.0
        elif lead_value < value_min:
            # Por debajo: penalización suave
            value_score = max(0.3, 1.0 - (value_min - lead_value) / value_min)
        else:
            # Por encima: penalización suave
            value_score = max(0.3, 1.0 - (lead_value - value_max) / lead_value)
        
        score += value_score * 0.25
        weights_sum += 0.25
    
    # 3. Location match (peso: 0.25)
    location_weights = _parse_json(user_profile.get('location_weights'))
    if location_weights:
        lead_city = (lead.get('city') or 'unknown').lower()
        lead_zip = lead.get('zip', '')[:3] if lead.get('zip') else ''
        
        location_score = 0.0
        if lead_city in location_weights:
            location_score = max(location_score, location_weights[lead_city])
        if lead_zip and f'region_{lead_zip}' in location_weights:
            location_score = max(location_score, location_weights[f'region_{lead_zip}'])
        
        # Si no hay match específico, usar score base
        if location_score == 0:
            location_score = 0.5  # Neutral
        
        score += location_score * 0.25
        weights_sum += 0.25
    
    # 4. Project type preference (peso: 0.1)
    # Si el usuario ha mostrado preferencia por tipos específicos
    interaction_count = user_profile.get('interaction_count', 0)
    if interaction_count > 10:
        # Usuario con historial: aplicar bonus por consistencia
        score += 0.1
        weights_sum += 0.1
    
    # Normalizar score final
    if weights_sum > 0:
        score /= weights_sum
    
    return min(max(score, 0.0), 1.0)


# ── Collaborative Filtering ─────────────────────────────────────

def find_similar_users(user_id: str, limit: int = 10) -> List[Tuple[str, float]]:
    """
    Encuentra usuarios similares basado en historial de interacciones.
    Retorna lista de (user_id, similarity_score).
    """
    with _get_conn() as conn:
        # Buscar usuarios con interacciones en leads similares
        rows = conn.execute("""
            SELECT user_id_2, similarity_score
            FROM user_similarity
            WHERE user_id_1 = ?
            ORDER BY similarity_score DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()
        
        if rows:
            return [(row[0], row[1]) for row in rows]
    
    # Si no hay similaridad pre-calculada, calcular on-the-fly
    return calculate_user_similarity_on_demand(user_id, limit)


def calculate_user_similarity_on_demand(user_id: str, limit: int = 10) -> List[Tuple[str, float]]:
    """
    Calcula similaridad entre usuarios on-demand usando Jaccard similarity
    sobre leads con los que han interactuado positivamente.
    """
    with _get_conn() as conn:
        # Obtener leads con swipe_right del usuario actual
        user_leads = set(row[0] for row in conn.execute("""
            SELECT DISTINCT lead_id
            FROM user_interactions
            WHERE user_id = ? AND interaction_type IN ('swipe_right', 'click', 'contact')
        """, (user_id,)).fetchall())
        
        if not user_leads:
            return []
        
        # Encontrar otros usuarios que interactuaron con los mismos leads
        other_users = defaultdict(set)
        for lead_id in user_leads:
            rows = conn.execute("""
                SELECT user_id
                FROM user_interactions
                WHERE lead_id = ? AND user_id != ? 
                AND interaction_type IN ('swipe_right', 'click', 'contact')
            """, (lead_id, user_id)).fetchall()
            
            for row in rows:
                other_users[row[0]].add(lead_id)
        
        # Calcular Jaccard similarity
        similarities = []
        for other_user, their_leads in other_users.items():
            intersection = len(user_leads & their_leads)
            union = len(user_leads | their_leads)
            
            if union > 0:
                jaccard = intersection / union
                
                # Bonus por número de leads en común
                bonus = min(intersection / 10.0, 0.2)
                
                similarities.append((other_user, jaccard + bonus))
        
        # Ordenar y retornar top N
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:limit]


def calculate_collaborative_score(user_id: str, lead: Dict) -> float:
    """
    Calcula score basado en collaborative filtering.
    ¿Usuarios similares interactuaron positivamente con este lead?
    """
    similar_users = find_similar_users(user_id, limit=20)
    
    if not similar_users:
        # Cold start: no hay usuarios similares
        return 0.5  # Score neutral
    
    with _get_conn() as conn:
        lead_id = lead.get('id') or lead.get('lead_id') or lead.get('property_address')
        if not lead_id:
            return 0.5
        
        total_weighted_score = 0.0
        total_weight = 0.0
        
        for similar_user, similarity in similar_users:
            # Verificar interacción de este usuario similar con el lead
            row = conn.execute("""
                SELECT interaction_type, interaction_value, created_at
                FROM user_interactions
                WHERE user_id = ? AND lead_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (similar_user, lead_id)).fetchone()
            
            if row:
                interaction_type, interaction_value, created_at = row
                
                # Mapear tipo de interacción a score
                type_scores = {
                    'contact': 1.0,
                    'swipe_right': 0.9,
                    'click': 0.7,
                    'view': 0.4,
                    'swipe_left': 0.1,
                }
                
                base_score = type_scores.get(interaction_type, 0.5)
                
                # Aplicar decay temporal
                decay = _decay_factor(created_at)
                
                # Weighted score: similaridad × tipo_interacción × decay
                weighted_score = similarity * base_score * decay
                total_weighted_score += weighted_score
                total_weight += similarity * decay
        
        if total_weight > 0:
            collab_score = total_weighted_score / total_weight
            return min(max(collab_score, 0.0), 1.0)
    
    return 0.5  # No se encontró interacción


# ── User Profile Management ─────────────────────────────────────

def get_user_profile(user_id: str) -> Optional[Dict]:
    """Obtiene perfil de usuario de la base de datos."""
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT user_id, preferences, trade_weights, location_weights,
                   value_range_min, value_range_max, last_updated, interaction_count
            FROM user_profiles
            WHERE user_id = ?
        """, (user_id,)).fetchone()
        
        if row:
            return {
                'user_id': row[0],
                'preferences': _parse_json(row[1]),
                'trade_weights': row[2],
                'location_weights': row[3],
                'value_range_min': row[4],
                'value_range_max': row[5],
                'last_updated': row[6],
                'interaction_count': row[7],
            }
    
    return None


def update_user_profile(user_id: str, interaction: Dict):
    """
    Actualiza perfil de usuario basado en nueva interacción.
    Usa online learning para ajustar preferencias incrementalmente.
    """
    lead_id = interaction.get('lead_id')
    interaction_type = interaction.get('interaction_type', 'view')
    lead_data = interaction.get('lead_data', {})
    
    with _get_conn() as conn:
        # Obtener perfil existente
        profile = get_user_profile(user_id)
        
        if profile is None:
            # Crear nuevo perfil
            profile = {
                'user_id': user_id,
                'preferences': {},
                'trade_weights': {},
                'location_weights': {},
                'value_range_min': 0,
                'value_range_max': 1000000,
                'last_updated': datetime.utcnow().isoformat(),
                'interaction_count': 0,
            }
            
            conn.execute("""
                INSERT INTO user_profiles 
                (user_id, preferences, trade_weights, location_weights,
                 value_range_min, value_range_max, last_updated, interaction_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                user_id, '{}', '{}', '{}', 0, 1000000,
                profile['last_updated']
            ))
        
        # Factor de aprendizaje (más alto para usuarios nuevos)
        learning_rate = 0.3 if profile['interaction_count'] < 20 else 0.1
        
        # Peso de la interacción (swipe_right > click > view)
        interaction_weights = {
            'contact': 1.0,
            'swipe_right': 0.9,
            'click': 0.6,
            'view': 0.2,
            'swipe_left': -0.5,  # Negativo para dislikes
        }
        
        weight = interaction_weights.get(interaction_type, 0.2)
        
        # Actualizar trade weights
        lead_features = extract_lead_features(lead_data)
        trade_onehot = lead_features.get('trade_onehot', {})
        
        current_trade_weights = _parse_json(profile['trade_weights'])
        for trade, present in trade_onehot.items():
            if present > 0:
                current_value = current_trade_weights.get(trade, 0.5)
                new_value = current_value + learning_rate * weight * (1 - current_value)
                current_trade_weights[trade] = min(max(new_value, 0.0), 1.0)
        
        # Actualizar location weights
        city = (lead_data.get('city') or 'unknown').lower()
        current_location_weights = _parse_json(profile['location_weights'])
        current_value = current_location_weights.get(city, 0.5)
        new_value = current_value + learning_rate * weight * (1 - current_value)
        current_location_weights[city] = min(max(new_value, 0.0), 1.0)
        
        # Actualizar value range
        lead_value = lead_data.get('value_float', 0) or 0
        if lead_value > 0 and weight > 0:
            current_min = profile['value_range_min']
            current_max = profile['value_range_max']
            
            # Expandir rango para incluir nuevo valor
            if lead_value < current_min or current_min == 0:
                new_min = lead_value * 0.8  # Margen del 20%
                profile['value_range_min'] = new_min
            elif lead_value > current_max:
                new_max = lead_value * 1.2
                profile['value_range_max'] = new_max
        
        # Incrementar contador
        profile['interaction_count'] += 1
        profile['trade_weights'] = _to_json(current_trade_weights)
        profile['location_weights'] = _to_json(current_location_weights)
        profile['last_updated'] = datetime.utcnow().isoformat()
        
        # Guardar cambios
        conn.execute("""
            UPDATE user_profiles
            SET preferences = ?, trade_weights = ?, location_weights = ?,
                value_range_min = ?, value_range_max = ?,
                last_updated = ?, interaction_count = ?
            WHERE user_id = ?
        """, (
            _to_json(profile['preferences']),
            profile['trade_weights'],
            profile['location_weights'],
            profile['value_range_min'],
            profile['value_range_max'],
            profile['last_updated'],
            profile['interaction_count'],
            user_id
        ))
        
        # Registrar interacción
        conn.execute("""
            INSERT INTO user_interactions 
            (user_id, lead_id, interaction_type, interaction_value, created_at, context)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            lead_id,
            interaction_type,
            weight,
            datetime.utcnow().isoformat(),
            _to_json(interaction.get('context', {}))
        ))
        
        conn.commit()
    
    logger.info(f"Perfil actualizado para usuario {user_id}: {profile['interaction_count']} interacciones")


# ── Diversity Boost ─────────────────────────────────────────────

def calculate_diversity_boost(user_id: str, lead: Dict, 
                               recent_recommendations: List[Dict]) -> float:
    """
    Calcula bonus por diversidad para evitar filter bubble.
    Si el lead es muy similar a recomendaciones recientes, reducir score.
    Si aporta diversidad, aumentar score.
    """
    if not recent_recommendations:
        return 1.0  # Sin penalty ni bonus
    
    lead_features = extract_lead_features(lead)
    lead_embedding = create_lead_embedding(lead_features)
    
    # Calcular similaridad promedio con recomendaciones recientes
    similarities = []
    for rec in recent_recommendations[-10:]:  # Últimas 10 recomendaciones
        rec_lead = rec.get('lead', {})
        rec_features = extract_lead_features(rec_lead)
        rec_embedding = create_lead_embedding(rec_features)
        
        sim = cosine_similarity(lead_embedding, rec_embedding)
        similarities.append(sim)
    
    if not similarities:
        return 1.0
    
    avg_similarity = sum(similarities) / len(similarities)
    
    # Si es muy similar (>0.8), aplicar penalty
    # Si es diverso (<0.5), aplicar bonus
    if avg_similarity > 0.8:
        return 0.7  # Penalty del 30%
    elif avg_similarity > 0.6:
        return 0.9  # Penalty leve
    elif avg_similarity < 0.4:
        return 1.15  # Bonus del 15% por diversidad
    else:
        return 1.0  # Neutral


# ── Main Recommendation Function ────────────────────────────────

def get_recommendations(user_id: str, 
                        leads: List[Dict],
                        limit: int = 20,
                        include_explanations: bool = False) -> List[Dict]:
    """
    Genera recomendaciones personalizadas para un usuario.
    
    Args:
        user_id: Identificador del usuario
        leads: Lista de leads candidatos
        limit: Número máximo de recomendaciones a retornar
        include_explanations: Si True, incluye explicaciones del scoring
    
    Returns:
        Lista de leads ordenados por score descendente, con metadata
    """
    if not leads:
        return []
    
    # Obtener perfil de usuario
    user_profile = get_user_profile(user_id)
    
    # Obtener últimas recomendaciones para diversity calculation
    with _get_conn() as conn:
        recent_recs = conn.execute("""
            SELECT lead_id, score
            FROM recommendation_cache
            WHERE user_id = ?
            ORDER BY generated_at DESC
            LIMIT 10
        """, (user_id,)).fetchall()
        
        recent_recommendations = [
            {'lead_id': row[0], 'score': row[1]} 
            for row in recent_recs
        ]
    
    # Calcular scores para cada lead
    scored_leads = []
    
    for lead in leads:
        lead_id = lead.get('id') or lead.get('lead_id') or lead.get('property_address')
        
        # 1. Content-based score
        content_score = calculate_content_score(user_id, lead, user_profile)
        
        # 2. Collaborative filtering score
        collab_score = calculate_collaborative_score(user_id, lead)
        
        # 3. Recency score (decaimiento temporal del lead)
        date_str = lead.get("date") or lead.get("issued_date") or ""
        recency_score = 0.5  # Default
        if date_str:
            try:
                lead_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                days_ago = (datetime.utcnow() - lead_date).days
                # Leads de hoy: 1.0, hace 30 días: 0.3
                recency_score = max(0.3, 1.0 - (days_ago / 45.0))
            except:
                pass
        
        # 4. Diversity boost
        diversity_boost = calculate_diversity_boost(
            user_id, lead, recent_recommendations
        )
        
        # Score final ponderado
        final_score = (
            _WEIGHTS['content'] * content_score +
            _WEIGHTS['collaborative'] * collab_score +
            _WEIGHTS['recency'] * recency_score
        ) * diversity_boost
        
        final_score = min(max(final_score, 0.0), 1.0)
        
        scored_lead = {
            **lead,
            '_recommendation_score': round(final_score, 4),
            '_content_score': round(content_score, 4),
            '_collab_score': round(collab_score, 4),
            '_recency_score': round(recency_score, 4),
            '_diversity_boost': round(diversity_boost, 4),
        }
        
        if include_explanations:
            explanations = []
            if content_score > 0.7:
                explanations.append("Alta coincidencia con tus preferencias")
            if collab_score > 0.7:
                explanations.append("Usuarios similares mostraron interés")
            if recency_score > 0.8:
                explanations.append("Lead muy reciente")
            if diversity_boost > 1.0:
                explanations.append("Aporta diversidad a tu feed")
            
            scored_lead['_explanation'] = explanations
        
        scored_leads.append(scored_lead)
    
    # Ordenar por score descendente
    scored_leads.sort(key=lambda x: x['_recommendation_score'], reverse=True)
    
    # Retornar top N
    return scored_leads[:limit]


def record_interaction(user_id: str, lead_id: str, interaction_type: str,
                       lead_data: Optional[Dict] = None,
                       context: Optional[Dict] = None):
    """
    Registra interacción de usuario con un lead y actualiza su perfil.
    
    Args:
        user_id: Identificador del usuario
        lead_id: Identificador del lead
        interaction_type: 'swipe_right', 'swipe_left', 'click', 'view', 'contact'
        lead_data: Datos completos del lead (para actualizar perfil)
        context: Metadata adicional (dispositivo, ubicación, etc.)
    """
    interaction = {
        'lead_id': lead_id,
        'interaction_type': interaction_type,
        'lead_data': lead_data or {},
        'context': context or {},
    }
    
    update_user_profile(user_id, interaction)
    
    # Invalidar cache de recomendaciones
    with _get_conn() as conn:
        conn.execute("""
            DELETE FROM recommendation_cache
            WHERE user_id = ?
        """, (user_id,))
        conn.commit()


def cache_recommendations(user_id: str, recommendations: List[Dict],
                          ttl_hours: int = 6):
    """Guarda recomendaciones en cache para retrieval rápido."""
    if not recommendations:
        return
    
    now = datetime.utcnow()
    expires = now + timedelta(hours=ttl_hours)
    
    with _get_conn() as conn:
        # Limpiar cache anterior
        conn.execute("""
            DELETE FROM recommendation_cache
            WHERE user_id = ?
        """, (user_id,))
        
        # Insertar nuevas recomendaciones
        for rank, rec in enumerate(recommendations):
            lead_id = rec.get('id') or rec.get('lead_id') or rec.get('property_address')
            score = rec.get('_recommendation_score', 0.5)
            
            conn.execute("""
                INSERT INTO recommendation_cache
                (user_id, lead_id, score, rank, generated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_id, lead_id, score, rank,
                now.isoformat(), expires.isoformat()
            ))
        
        conn.commit()


def get_cached_recommendations(user_id: str, limit: int = 20) -> List[str]:
    """
    Obtiene IDs de leads desde cache.
    Retorna lista de lead_ids ordenados por rank.
    """
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT lead_id
            FROM recommendation_cache
            WHERE user_id = ? AND expires_at > ?
            ORDER BY rank ASC
            LIMIT ?
        """, (user_id, datetime.utcnow().isoformat(), limit)).fetchall()
        
        return [row[0] for row in rows]


# ── Batch Processing & Training ─────────────────────────────────

def calculate_all_user_similarities(batch_size: int = 100):
    """
    Calcula matriz de similaridad entre todos los usuarios activos.
    Ejecutar periódicamente (ej: cada noche).
    """
    with _get_conn() as conn:
        # Obtener usuarios activos (con >5 interacciones)
        active_users = conn.execute("""
            SELECT user_id, COUNT(*) as interaction_count
            FROM user_interactions
            GROUP BY user_id
            HAVING interaction_count > 5
            LIMIT ?
        """, (batch_size,)).fetchall()
        
        user_ids = [row[0] for row in active_users]
    
    logger.info(f"Calculando similaridades para {len(user_ids)} usuarios")
    
    # Calcular similaridades pairwise
    updates = []
    for i, user_1 in enumerate(user_ids):
        similar_users = calculate_user_similarity_on_demand(user_1, limit=50)
        
        for user_2, similarity in similar_users:
            if user_2 in user_ids:  # Solo si ambos están en el batch
                # Contar leads en común
                with _get_conn() as conn:
                    common = conn.execute("""
                        SELECT COUNT(DISTINCT a.lead_id)
                        FROM user_interactions a
                        JOIN user_interactions b ON a.lead_id = b.lead_id
                        WHERE a.user_id = ? AND b.user_id = ?
                        AND a.interaction_type IN ('swipe_right', 'click', 'contact')
                        AND b.interaction_type IN ('swipe_right', 'click', 'contact')
                    """, (user_1, user_2)).fetchone()[0]
                
                updates.append((
                    user_1, user_2, similarity, common,
                    datetime.utcnow().isoformat()
                ))
    
    # Guardar en base de datos
    with _get_conn() as conn:
        for user_1, user_2, similarity, common, timestamp in updates:
            conn.execute("""
                INSERT OR REPLACE INTO user_similarity
                (user_id_1, user_id_2, similarity_score, common_leads, calculated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_1, user_2, similarity, common, timestamp))
        
        conn.commit()
    
    logger.info(f"Similaridades actualizadas: {len(updates)} pares")


def store_lead_embedding(lead_id: str, lead_data: Dict):
    """Calcula y guarda embedding de un lead en la base de datos."""
    features = extract_lead_features(lead_data)
    embedding = create_lead_embedding(features)
    
    now = datetime.utcnow().isoformat()
    
    with _get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO lead_embeddings
            (lead_id, embedding, trade_category, value_bucket, 
             location_cluster, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            lead_id,
            _to_json(embedding),
            lead_data.get('trade') or lead_data.get('service_type'),
            features.get('value_bucket'),
            features.get('location_region'),
            now, now
        ))
        conn.commit()


# ── API Helper Functions ────────────────────────────────────────

def get_recommendation_stats(user_id: str) -> Dict:
    """Retorna estadísticas de recomendaciones para un usuario."""
    profile = get_user_profile(user_id)
    
    if not profile:
        return {
            'status': 'cold_start',
            'message': 'Usuario nuevo. Interactúa con leads para personalizar.',
        }
    
    trade_weights = _parse_json(profile.get('trade_weights'))
    location_weights = _parse_json(profile.get('location_weights'))
    
    # Top trades
    top_trades = sorted(
        [(k, v) for k, v in trade_weights.items() if v > 0.5],
        key=lambda x: x[1], reverse=True
    )[:5]
    
    # Top locations
    top_locations = sorted(
        [(k, v) for k, v in location_weights.items() if v > 0.5],
        key=lambda x: x[1], reverse=True
    )[:5]
    
    return {
        'status': 'active',
        'interaction_count': profile.get('interaction_count', 0),
        'top_trades': top_trades,
        'top_locations': top_locations,
        'value_range': {
            'min': profile.get('value_range_min', 0),
            'max': profile.get('value_range_max', 1000000),
        },
        'last_updated': profile.get('last_updated'),
    }


def explain_recommendation(user_id: str, lead: Dict) -> Dict:
    """
    Genera explicación detallada de por qué se recomendó este lead.
    Útil para transparencia y debugging.
    """
    user_profile = get_user_profile(user_id)
    
    content_score = calculate_content_score(user_id, lead, user_profile)
    collab_score = calculate_collaborative_score(user_id, lead)
    
    lead_features = extract_lead_features(lead)
    
    reasons = []
    
    # Trade match
    if user_profile:
        trade_weights = _parse_json(user_profile.get('trade_weights'))
        for trade, weight in trade_weights.items():
            if lead_features.get('trade_onehot', {}).get(trade, 0) > 0 and weight > 0.6:
                reasons.append(f"Coincide con tu interés en {trade.replace('_', ' ')}")
    
    # Collaborative signal
    if collab_score > 0.6:
        reasons.append("Profesionales similares mostraron interés en este lead")
    
    # Recency
    date_str = lead.get("date") or lead.get("issued_date") or ""
    if date_str:
        try:
            lead_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
            days_ago = (datetime.utcnow() - lead_date).days
            if days_ago <= 3:
                reasons.append(f"Lead muy reciente ({days_ago} días)")
        except:
            pass
    
    # Value range
    if user_profile:
        lead_value = lead.get('value_float', 0) or 0
        v_min = user_profile.get('value_range_min', 0)
        v_max = user_profile.get('value_range_max', 1000000)
        
        if v_min <= lead_value <= v_max:
            reasons.append(f"Valor del proyecto (${lead_value:,}) en tu rango habitual")
    
    return {
        'lead_id': lead.get('id') or lead.get('property_address'),
        'overall_score': round((content_score * 0.45 + collab_score * 0.35 + 0.2), 4),
        'content_score': round(content_score, 4),
        'collaborative_score': round(collab_score, 4),
        'reasons': reasons,
        'lead_features': {
            'trade': lead_features.get('trade_onehot', {}),
            'value_bucket': lead_features.get('value_bucket'),
            'project_type': lead_features.get('project_type'),
            'urgency': lead_features.get('urgency_score', 0),
        }
    }


# ── Initialization ──────────────────────────────────────────────

if __name__ == "__main__":
    # Test básico del motor
    print("Inicializando motor de recomendación...")
    init_recommendation_db()
    print("✓ Tablas creadas exitosamente")
    
    # Ejemplo de uso
    print("\nEjemplo de features extraídas:")
    test_lead = {
        'id': 'test_001',
        'description': 'Roof replacement for single family home',
        'value_float': 45000,
        'city': 'Oakland',
        'zip': '94601',
        'trade': 'roofing',
    }
    
    features = extract_lead_features(test_lead)
    embedding = create_lead_embedding(features)
    
    print(f"  Trade: {features.get('trade_onehot', {})}")
    print(f"  Valor normalizado: {features.get('value_normalized', 0):.3f}")
    print(f"  Embedding dimensions: {len(embedding)}")
    print(f"  Project type: {features.get('project_type')}")
    
    print("\n✓ Motor de recomendación listo para usar")
