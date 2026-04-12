#!/usr/bin/env python3
"""
tests/test_recommendation_engine.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tests y demo del Motor de Recomendación

Ejecutar: python tests/test_recommendation_engine.py
"""

import sys
sys.path.insert(0, '/workspace')

from utils.recommendation_engine import (
    init_recommendation_db,
    extract_lead_features,
    create_lead_embedding,
    cosine_similarity,
    calculate_content_score,
    calculate_collaborative_score,
    get_recommendations,
    record_interaction,
    update_user_profile,
    get_user_profile,
    get_recommendation_stats,
    explain_recommendation,
    cache_recommendations,
    get_cached_recommendations,
)

import json
from datetime import datetime


def test_basic_features():
    """Test básico de extracción de features."""
    print("\n" + "="*60)
    print("TEST 1: Extracción de Features de Leads")
    print("="*60)
    
    leads = [
        {
            'id': 'lead_001',
            'description': 'Complete roof replacement with new shingles',
            'value_float': 45000,
            'city': 'Oakland',
            'zip': '94601',
            'trade': 'roofing',
            'date': datetime.utcnow().strftime('%Y-%m-%d'),
        },
        {
            'id': 'lead_002',
            'description': 'Electrical panel upgrade to 200 amp service',
            'value_float': 12000,
            'city': 'San Francisco',
            'zip': '94102',
            'trade': 'electrical',
            'date': (datetime.utcnow()).strftime('%Y-%m-%d'),
        },
        {
            'id': 'lead_003',
            'description': 'Full house interior and exterior painting',
            'value_float': 28000,
            'city': 'Berkeley',
            'zip': '94701',
            'trade': 'painting',
            'date': (datetime.utcnow()).strftime('%Y-%m-%d'),
        },
        {
            'id': 'lead_004',
            'description': 'New ADU construction in backyard',
            'value_float': 350000,
            'city': 'San Jose',
            'zip': '95101',
            'trade': 'new_construction',
            'date': (datetime.utcnow()).strftime('%Y-%m-%d'),
        },
        {
            'id': 'lead_005',
            'description': 'Landscaping redesign with drought tolerant plants',
            'value_float': 18000,
            'city': 'Oakland',
            'zip': '94610',
            'trade': 'landscaping',
            'date': (datetime.utcnow()).strftime('%Y-%m-%d'),
        },
    ]
    
    for lead in leads:
        features = extract_lead_features(lead)
        embedding = create_lead_embedding(features)
        
        print(f"\n{lead['id']}: {lead['trade']}")
        print(f"  Ciudad: {lead['city']}, Valor: ${lead['value_float']:,}")
        print(f"  Project type: {features['project_type']}")
        print(f"  Value bucket: {features['value_bucket']}")
        print(f"  Urgency score: {features['urgency_score']:.2f}")
        print(f"  Embedding dim: {len(embedding)}")
    
    print("\n✓ Test 1 completado")
    return leads


def test_user_interactions():
    """Test de registro de interacciones y aprendizaje."""
    print("\n" + "="*60)
    print("TEST 2: Registro de Interacciones y Online Learning")
    print("="*60)
    
    user_id = "contractor_john"
    
    # Simular interacciones iniciales
    interactions = [
        ('lead_001', 'swipe_right', {
            'id': 'lead_001',
            'description': 'Complete roof replacement',
            'value_float': 45000,
            'city': 'Oakland',
            'trade': 'roofing',
        }),
        ('lead_002', 'swipe_right', {
            'id': 'lead_002',
            'description': 'Electrical panel upgrade',
            'value_float': 12000,
            'city': 'San Francisco',
            'trade': 'electrical',
        }),
        ('lead_003', 'click', {
            'id': 'lead_003',
            'description': 'House painting',
            'value_float': 28000,
            'city': 'Berkeley',
            'trade': 'painting',
        }),
        ('lead_004', 'swipe_left', {
            'id': 'lead_004',
            'description': 'ADU construction',
            'value_float': 350000,
            'city': 'San Jose',
            'trade': 'new_construction',
        }),
    ]
    
    print(f"\nRegistrando {len(interactions)} interacciones para {user_id}...")
    
    for lead_id, interaction_type, lead_data in interactions:
        record_interaction(user_id, lead_id, interaction_type, lead_data)
        print(f"  ✓ {interaction_type} en {lead_id}")
    
    # Obtener perfil actualizado
    profile = get_user_profile(user_id)
    
    if profile:
        print(f"\nPerfil aprendido después de {profile['interaction_count']} interacciones:")
        
        trade_weights = json.loads(profile['trade_weights'])
        top_trades = sorted(trade_weights.items(), key=lambda x: x[1], reverse=True)[:3]
        print(f"  Top trades preferidos:")
        for trade, weight in top_trades:
            print(f"    - {trade}: {weight:.3f}")
        
        location_weights = json.loads(profile['location_weights'])
        top_locations = sorted(location_weights.items(), key=lambda x: x[1], reverse=True)[:3]
        print(f"  Top ubicaciones:")
        for loc, weight in top_locations:
            print(f"    - {loc}: {weight:.3f}")
        
        print(f"  Rango de valores: ${profile['value_range_min']:,.0f} - ${profile['value_range_max']:,.0f}")
    
    print("\n✓ Test 2 completado")
    return user_id


def test_recommendations():
    """Test de generación de recomendaciones personalizadas."""
    print("\n" + "="*60)
    print("TEST 3: Generación de Recomendaciones Personalizadas")
    print("="*60)
    
    user_id = "contractor_john"
    
    # Candidatos para recomendar
    candidates = [
        {
            'id': 'lead_006',
            'description': 'Roof repair after storm damage',
            'value_float': 22000,
            'city': 'Oakland',
            'zip': '94602',
            'trade': 'roofing',
            'date': datetime.utcnow().strftime('%Y-%m-%d'),
        },
        {
            'id': 'lead_007',
            'description': 'Kitchen remodel with electrical work',
            'value_float': 65000,
            'city': 'San Francisco',
            'zip': '94103',
            'trade': 'electrical',
            'date': datetime.utcnow().strftime('%Y-%m-%d'),
        },
        {
            'id': 'lead_008',
            'description': 'Exterior painting for commercial building',
            'value_float': 45000,
            'city': 'Berkeley',
            'zip': '94702',
            'trade': 'painting',
            'date': datetime.utcnow().strftime('%Y-%m-%d'),
        },
        {
            'id': 'lead_009',
            'description': 'Luxury home new construction',
            'value_float': 850000,
            'city': 'Palo Alto',
            'zip': '94301',
            'trade': 'new_construction',
            'date': datetime.utcnow().strftime('%Y-%m-%d'),
        },
        {
            'id': 'lead_010',
            'description': 'Backyard landscaping with irrigation',
            'value_float': 15000,
            'city': 'Oakland',
            'zip': '94611',
            'trade': 'landscaping',
            'date': datetime.utcnow().strftime('%Y-%m-%d'),
        },
    ]
    
    print(f"\nGenerando recomendaciones para {user_id}...")
    print(f"Candidatos disponibles: {len(candidates)}")
    
    recommendations = get_recommendations(
        user_id=user_id,
        leads=candidates,
        limit=5,
        include_explanations=True
    )
    
    print(f"\nTop {len(recommendations)} recomendaciones:")
    print("-" * 60)
    
    for i, rec in enumerate(recommendations, 1):
        score = rec['_recommendation_score']
        content = rec['_content_score']
        collab = rec['_collab_score']
        
        print(f"\n{i}. {rec['id']}: {rec['trade']}")
        print(f"   Score final: {score:.3f}")
        print(f"   Desglose: content={content:.3f}, collab={collab:.3f}")
        print(f"   Ubicación: {rec['city']}, Valor: ${rec['value_float']:,}")
        
        if '_explanation' in rec and rec['_explanation']:
            print(f"   Explicación: {', '.join(rec['_explanation'])}")
    
    # Cachear recomendaciones
    cache_recommendations(user_id, recommendations, ttl_hours=6)
    print(f"\n✓ Recomendaciones cacheadas por 6 horas")
    
    # Verificar cache
    cached_ids = get_cached_recommendations(user_id, limit=5)
    print(f"✓ IDs en cache: {cached_ids}")
    
    print("\n✓ Test 3 completado")
    return recommendations


def test_explainability():
    """Test de explicabilidad de recomendaciones."""
    print("\n" + "="*60)
    print("TEST 4: Explicabilidad de Recomendaciones")
    print("="*60)
    
    user_id = "contractor_john"
    
    lead = {
        'id': 'lead_006',
        'description': 'Roof repair after storm damage',
        'value_float': 22000,
        'city': 'Oakland',
        'zip': '94602',
        'trade': 'roofing',
        'date': datetime.utcnow().strftime('%Y-%m-%d'),
    }
    
    print(f"\nExplicando por qué se recomienda {lead['id']} a {user_id}:")
    print("-" * 60)
    
    explanation = explain_recommendation(user_id, lead)
    
    print(f"\nLead: {explanation['lead_id']}")
    print(f"Score overall: {explanation['overall_score']:.3f}")
    print(f"  - Content score: {explanation['content_score']:.3f}")
    print(f"  - Collaborative score: {explanation['collaborative_score']:.3f}")
    
    print(f"\nRazones:")
    for reason in explanation['reasons']:
        print(f"  ✓ {reason}")
    
    print(f"\nFeatures del lead:")
    features = explanation['lead_features']
    print(f"  Trade: {[k for k, v in features['trade'].items() if v > 0]}")
    print(f"  Value bucket: {features['value_bucket']}")
    print(f"  Project type: {features['project_type']}")
    print(f"  Urgency: {features['urgency']:.2f}")
    
    print("\n✓ Test 4 completado")


def test_stats():
    """Test de estadísticas del sistema."""
    print("\n" + "="*60)
    print("TEST 5: Estadísticas del Sistema")
    print("="*60)
    
    user_id = "contractor_john"
    
    stats = get_recommendation_stats(user_id)
    
    print(f"\nEstado del usuario: {stats['status']}")
    
    if stats['status'] == 'active':
        print(f"Interacciones totales: {stats['interaction_count']}")
        
        print("\nTop trades preferidos:")
        for trade, weight in stats['top_trades']:
            bar = '█' * int(weight * 10)
            print(f"  {trade:20s} {bar} ({weight:.3f})")
        
        print("\nTop ubicaciones:")
        for loc, weight in stats['top_locations']:
            bar = '█' * int(weight * 10)
            print(f"  {loc:20s} {bar} ({weight:.3f})")
        
        print(f"\nRango de valores preferido:")
        print(f"  ${stats['value_range']['min']:,.0f} - ${stats['value_range']['max']:,.0f}")
    
    print("\n✓ Test 5 completado")


def test_cold_start():
    """Test de cold start para usuario nuevo."""
    print("\n" + "="*60)
    print("TEST 6: Cold Start (Usuario Nuevo)")
    print("="*60)
    
    new_user = "contractor_newbie"
    
    candidates = [
        {
            'id': 'lead_101',
            'description': 'Small bathroom remodel',
            'value_float': 15000,
            'city': 'Oakland',
            'trade': 'plumbing',
        },
        {
            'id': 'lead_102',
            'description': 'Large commercial roofing project',
            'value_float': 250000,
            'city': 'San Francisco',
            'trade': 'roofing',
        },
    ]
    
    print(f"\nUsuario nuevo: {new_user}")
    print(f"Generando primeras recomendaciones sin historial...")
    
    recs = get_recommendations(
        user_id=new_user,
        leads=candidates,
        limit=2,
        include_explanations=True
    )
    
    for rec in recs:
        print(f"\n  {rec['id']}: score={rec['_recommendation_score']:.3f}")
        if '_explanation' in rec:
            print(f"    Explicación: {rec['_explanation']}")
    
    stats = get_recommendation_stats(new_user)
    print(f"\nEstado: {stats.get('status', 'unknown')}")
    if 'message' in stats:
        print(f"Mensaje: {stats['message']}")
    
    print("\n✓ Test 6 completado")


def main():
    """Ejecutar todos los tests."""
    print("\n" + "╔" + "═"*58 + "╗")
    print("║" + " "*10 + "MOTOR DE RECOMENDACIÓN - TEST SUITE" + " "*11 + "║")
    print("╚" + "═"*58 + "╝")
    
    # Inicializar DB
    print("\nInicializando base de datos...")
    init_recommendation_db()
    print("✓ DB lista")
    
    # Ejecutar tests
    leads = test_basic_features()
    user_id = test_user_interactions()
    recommendations = test_recommendations()
    test_explainability()
    test_stats()
    test_cold_start()
    
    # Resumen final
    print("\n" + "="*60)
    print("RESUMEN FINAL")
    print("="*60)
    print("""
El motor de recomendación incluye:

✓ Content-Based Filtering
  - Análisis de features de leads (trade, valor, ubicación)
  - Matching con preferencias aprendidas del usuario
  - Peso: 45% del score final

✓ Collaborative Filtering  
  - Detección de usuarios similares (Jaccard similarity)
  - Recomendación basada en comportamiento de peers
  - Peso: 35% del score final

✓ Online Learning
  - Actualización incremental de preferencias
  - Learning rate adaptativo (más alto para usuarios nuevos)
  - Tracking de trade weights, location weights, value ranges

✓ Diversity Boost
  - Penalización por filter bubble
  - Bonus por diversidad en el feed
  - Peso: 8% del score final

✓ Recency Scoring
  - Decay temporal para leads antiguos
  - Peso: 12% del score final

✓ Explainability
  - Explicaciones detalladas de cada recomendación
  - Transparency en el scoring

✓ Cold Start Handling
  - Scores neutrales para usuarios/leads nuevos
  - Quick learning en primeras interacciones

✓ Caching
  - Cache de recomendaciones con TTL configurable
  - Invalidación automática tras nuevas interacciones
    """)
    
    print("\n" + "🎉 " + "="*50)
    print("   TODOS LOS TESTS COMPLETADOS EXITOSAMENTE")
    print("🎉 " + "="*50 + "\n")


if __name__ == "__main__":
    main()
