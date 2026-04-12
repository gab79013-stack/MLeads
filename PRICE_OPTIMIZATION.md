# Optimización Dinámica de Precios

## Descripción
Sistema avanzado de optimización de precios que ajusta dinámicamente los precios de productos y servicios basándose en demanda, competencia, costos, comportamiento del cliente y condiciones del mercado en tiempo real.

## Características Principales

### 📈 Pricing Dinámico
- Ajustes de precios en tiempo real
- Segmentación por cliente y contexto
- Estrategias múltiples configurables
- Límites y guardrails personalizables

### 🤖 Machine Learning
- Modelos predictivos de demanda
- Elasticidad de precio por segmento
- Optimización basada en reinforcement learning
- Detección de patrones estacionales

### 🎯 Estrategias Múltiples
- Cost-plus pricing
- Value-based pricing
- Competitive pricing
- Dynamic yield management
- Psychological pricing
- Bundle pricing

## Estrategias de Pricing

### 1. Basado en Demanda
```python
from utils.price_optimizer import PriceOptimizer

optimizer = PriceOptimizer()

price = optimizer.calculate_demand_based_price(
    product_id="prod_123",
    current_demand="high",
    inventory_level=0.3,  # 30% restante
    time_to_deadline_hours=48,
    historical_conversion_rate=0.15
)

# Resultado: Precio ajustado según presión de demanda
```

### 2. Basado en Competencia
```python
competitive_price = optimizer.calculate_competitive_price(
    product_id="prod_123",
    competitor_prices=[99.99, 105.00, 102.50],
    positioning_strategy="match_lowest",  # o "undercut", "premium"
    price_match_guarantee=True
)
```

### 3. Basado en Valor
```python
value_price = optimizer.calculate_value_based_price(
    product_id="prod_123",
    customer_segment="enterprise",
    perceived_value_score=8.5,
    willingness_to_pay_estimate=150.00,
    value_drivers=["time_savings", "revenue_increase", "risk_reduction"]
)
```

### 4. Optimización Multi-Objetivo
```python
optimal_price = optimizer.optimize_price(
    product_id="prod_123",
    objectives={
        "maximize_revenue": 0.4,
        "maximize_margin": 0.3,
        "maximize_market_share": 0.2,
        "maintain_customer_satisfaction": 0.1
    },
    constraints={
        "min_price": 50.00,
        "max_price": 200.00,
        "min_margin_percent": 25,
        "max_price_change_percent": 15
    }
)
```

## Factores de Ajuste

| Factor | Impacto | Ejemplo |
|--------|---------|---------|
| **Demanda Actual** | Alto | +20% si demanda > 2x promedio |
| **Inventario** | Medio-Alto | -15% si inventario > 80% |
| **Competencia** | Alto | Match o undercut según estrategia |
| **Temporada** | Medio | +10-30% en alta temporada |
| **Hora del Día** | Bajo-Medio | Variaciones por patrón de compra |
| **Historial Cliente** | Medio | Descuentos por lealtad |
| **Ubicación** | Bajo-Medio | Ajuste por poder adquisitivo regional |
| **Dispositivo** | Bajo | Diferencias mobile vs desktop |

## Elasticidad de Precio

El sistema calcula elasticidad para cada segmento:

```python
elasticity = optimizer.calculate_price_elasticity(
    product_id="prod_123",
    segment="millennials",
    price_points=[80, 90, 100, 110, 120],
    historical_quantities=[150, 130, 100, 75, 50]
)

# Resultado:
# {
#     "elasticity_coefficient": -1.85,  # Elástico
#     "optimal_price_point": 95.00,
#     "predicted_revenue_at_optimal": 12350.00,
#     "confidence_interval": [90.00, 100.00]
# }
```

### Interpretación de Elasticidad

| Coeficiente | Tipo | Estrategia Recomendada |
|-------------|------|------------------------|
| < -2.0 | Muy Elástico | Pequeñas reducciones generan grandes aumentos en volumen |
| -2.0 a -1.0 | Elástico | Reducciones moderadas aumentan revenue |
| -1.0 a -0.5 | Inelástico | Se puede aumentar precio sin perder mucho volumen |
| > -0.5 | Muy Inelástico | Aumentos de precio mejoran margen significativamente |

## Configuración

```yaml
price_optimizer:
  strategies:
    default: dynamic_competitive
    available:
      - cost_plus
      - demand_based
      - competitive
      - value_based
      - yield_management
  
  demand_sensitivity:
    high_demand_multiplier: 1.25
    low_demand_multiplier: 0.85
    demand_update_frequency_minutes: 15
  
  competition_tracking:
    enabled: true
    update_frequency_minutes: 30
    min_competitors_required: 3
    ignore_outliers: true
  
  constraints:
    global:
      min_margin_percent: 15
      max_price_change_per_day: 20
      max_discount_percent: 40
    
    by_category:
      electronics:
        min_price: 10.00
        max_price: 5000.00
      services:
        min_price: 50.00
        max_price_change_percent: 10
  
  ab_testing:
    enabled: true
    test_allocation_percent: 10
    min_sample_size: 1000
    confidence_level: 0.95
  
  machine_learning:
    model_retrain_frequency_hours: 24
    features_window_days: 90
    include_external_factors: true  # clima, eventos, etc.
```

## API Usage Avanzado

### Batch Pricing Update
```python
updates = optimizer.batch_optimize(
    product_ids=["prod_1", "prod_2", "prod_3"],
    context={
        "timestamp": "2024-01-15T14:30:00Z",
        "market_condition": "bullish",
        "season": "holiday"
    },
    apply_immediately=False  # Para revisión previa
)

# Revisar y aprobar cambios
for update in updates:
    print(f"{update['product_id']}: ${update['old_price']} → ${update['new_price']}")
    print(f"  Expected impact: {update['predicted_revenue_change']}%")

optimizer.apply_price_updates(updates)
```

### Simulación de Escenarios
```python
simulation = optimizer.simulate_scenario(
    product_id="prod_123",
    scenarios=[
        {"name": "conservative", "price_change": 0.05},
        {"name": "moderate", "price_change": 0.10},
        {"name": "aggressive", "price_change": 0.20}
    ],
    simulation_days=30
)

# Comparar resultados proyectados
for scenario in simulation['results']:
    print(f"{scenario['name']}: Revenue=${scenario['predicted_revenue']:.2f}")
```

### Reglas Personalizadas
```python
optimizer.add_pricing_rule(
    name="weekend_flash_sale",
    condition="day_of_week in [5, 6] and hour >= 18",
    action="apply_discount",
    value=0.15,  # 15% descuento
    priority=10,
    stackable=False
)
```

## Métricas de Rendimiento

| Métrica | Fórmula | Objetivo |
|---------|---------|----------|
| **Revenue Lift** | (Revenue_actual - Revenue_baseline) / Revenue_baseline | +10-25% |
| **Margin Improvement** | Margen_post - Margen_pre | +3-8 pp |
| **Price Realization** | Precio_real / Precio_óptimo_teorico | >95% |
| **Competitive Position** | % productos en rango objetivo | >90% |
| **Elasticity Accuracy** | Predicción vs realidad | >85% |
| **Update Frequency** | Cambios de precio por día | Según categoría |

## Dashboard de Control

El sistema incluye dashboards con:

- 📊 **Evolución de Precios**: Histórico y proyecciones
- 💰 **Revenue Impact**: Atribución de cambios a optimización
- 🏆 **Market Position**: Ranking competitivo por categoría
- ⚡ **Alertas en Tiempo Real**: Anomalías y oportunidades
- 🧪 **Resultados A/B Tests**: Significancia estadística
- 📈 **Elasticidad por Segmento**: Heatmaps y tendencias

## Integraciones

- ✅ E-commerce Platforms (Shopify, WooCommerce, Magento)
- ✅ ERP Systems (SAP, Oracle, NetSuite)
- ✅ PIM Systems (Informatica, Akeneo)
- ✅ Analytics (Google Analytics, Adobe Analytics)
- ✅ Competitor Price Tracking (Prisync, Competera)
- ✅ Payment Gateways (Stripe, PayPal)
- ✅ CRM (Salesforce, HubSpot)
- ✅ BI Tools (Tableau, PowerBI, Looker)

## Casos de Uso

### 🛒 Retail E-commerce
**Escenario**: 10,000 SKUs con competencia intensa.
**Implementación**: Pricing competitivo + demand-based.
**Resultado**: +18% revenue, +5% margen, +12% conversión.

### 🏨 Hospitality
**Escenario**: Hotel con ocupación variable.
**Implementación**: Yield management dinámico.
**Resultado**: +22% RevPAR, +15% ocupación promedio.

### 🎫 Event Ticketing
**Escenario**: Ventas de entradas con fecha límite.
**Implementación**: Time-decay pricing + demanda.
**Resultado**: +30% revenue total, 98% ocupación.

### ✈️ Aerolínea
**Escenario**: Asientos perecederos, demanda fluctuante.
**Implementación**: Multi-class yield optimization.
**Resultado**: +14% yield, mejor distribución por clase.

## Gobernanza de Precios

### Approval Workflows
```yaml
approval_rules:
  - condition: "price_change_percent > 20"
    approvers: ["pricing_manager", "finance_director"]
  
  - condition: "category in ['luxury', 'flagship']"
    approvers: ["brand_manager"]
  
  - condition: "new_price < cost * 1.15"
    approvers: ["cfo"]
    auto_reject: true
```

### Audit Trail
- Todos los cambios registrados con timestamp
- Razón del cambio (automático vs manual)
- Usuario/sistema responsable
- Impacto proyectado vs real

## Seguridad y Compliance

- 🔒 Prevención de colusión algorítmica
- 🔒 Cumplimiento leyes de competencia
- 🔒 Transparencia de criterios de pricing
- 🔒 No discriminación por protected classes
- 🔒 Logging completo para auditoría

## Mejores Prácticas

1. **Start conservative**: Comenzar con límites estrechos
2. **Monitor closely**: Revisar diariamente las primeras semanas
3. **Communicate changes**: Informar stakeholders de ajustes
4. **Test continuously**: A/B testing permanente
5. **Review seasonality**: Ajustar modelos por temporalidad
6. **Competitor intelligence**: Mantener tracking actualizado
7. **Customer feedback**: Monitorear percepción de precios

## Roadmap

- [ ] Pricing personalizado por usuario individual
- [ ] Integración con datos macroeconómicos en tiempo real
- [ ] Optimización de precios bundles dinámica
- [ ] Predicción de reacciones competitivas
- [ ] Pricing ético con fairness constraints
- [ ] Natural language interface para reglas de pricing

## Soporte

- Documentación: `/docs/price-optimizer`
- Playbooks: `/playbooks/pricing-strategies`
- Slack: #pricing-optimization
- Email: pricing-team@company.com
