# Matching Engine - GC y Subcontratistas

## Descripción
Sistema inteligente de emparejamiento entre Gerentes de Cuenta (GC) y subcontratistas, optimizando la asignación de recursos basada en habilidades, disponibilidad, historial de desempeño y compatibilidad de perfiles.

## Características Principales

### 🎯 Matching Inteligente
- Algoritmo multi-criterio ponderado
- Aprendizaje de preferencias históricas
- Adaptación dinámica a cambios de disponibilidad
- Scoring de compatibilidad en tiempo real

### 👥 Perfiles Detallados
- **GC**: Especialización, carga actual, estilo de gestión
- **Subcontratistas**: Habilidades, certificaciones, ratings, ubicación
- **Proyectos**: Requisitos, complejidad, timeline, presupuesto

### 📊 Optimización Global
- Balanceo de carga de trabajo
- Minimización de costos operativos
- Maximización de satisfacción del cliente
- Consideración de restricciones legales/compliance

## Algoritmo de Matching

### Factores de Compatibilidad

| Factor | Peso Default | Descripción |
|--------|--------------|-------------|
| **Habilidades Técnicas** | 30% | Match entre skills requeridos y disponibles |
| **Experiencia en Industria** | 20% | Años en sector específico del proyecto |
| **Disponibilidad** | 15% | Capacidad horaria vs demanda del proyecto |
| **Historial de Desempeño** | 15% | Ratings previos con GC similares |
| **Ubicación Geográfica** | 10% | Proximidad para reuniones presenciales |
| **Idioma** | 5% | Compatibilidad lingüística |
| **Cultura de Trabajo** | 5% | Compatibilidad de estilos comunicacionales |

### Fórmula de Scoring

```python
score = (
    skills_match * 0.30 +
    industry_experience * 0.20 +
    availability_score * 0.15 +
    performance_history * 0.15 +
    location_proximity * 0.10 +
    language_compatibility * 0.05 +
    culture_fit * 0.05
) * 100
```

## API Usage

### Búsqueda de Subcontratistas para GC

```python
from utils.matching_engine import MatchingEngine

engine = MatchingEngine()

# Encontrar mejores matches para un GC
matches = engine.find_contractors_for_gc(
    gc_id="gc_789",
    project_requirements={
        "skills": ["react", "nodejs", "aws"],
        "industry": "fintech",
        "min_experience_years": 5,
        "language": "es",
        "location_preference": "LATAM"
    },
    limit=10
)

for match in matches:
    print(f"{match['contractor_id']}: {match['compatibility_score']}%")
    print(f"  Skills match: {match['skills_overlap']}")
    print(f"  Disponible desde: {match['available_from']}")
```

### Asignación Óptima Múltiple

```python
# Optimizar asignaciones para múltiples proyectos
assignments = engine.optimize_multiple_assignments(
    projects=[
        {"id": "proj_1", "priority": "high", "budget": 50000},
        {"id": "proj_2", "priority": "medium", "budget": 30000},
        {"id": "proj_3", "priority": "low", "budget": 20000}
    ],
    available_contractors=["cont_1", "cont_2", "cont_3", "cont_4"],
    constraints={
        "max_contractors_per_project": 3,
        "max_projects_per_contractor": 2,
        "min_total_score": 75
    }
)

print(f"Asignaciones óptimas: {assignments}")
```

### Análisis de Gap de Habilidades

```python
gap_analysis = engine.analyze_skills_gap(
    project_requirements=["kubernetes", "terraform", "python"],
    available_pool=["cont_1", "cont_2", "cont_3"]
)

# Resultado:
# {
#     "covered_skills": ["python"],
#     "missing_skills": ["kubernetes", "terraform"],
#     "coverage_percentage": 33.3,
#     "recommended_training": ["AWS Certified Kubernetes"],
#     "recommended_hiring": 2
# }
```

## Tipos de Matching

### 1. Matching por Proyecto
Asignación específica para un proyecto con requisitos definidos.

```python
project_match = engine.match_for_project(
    project_id="proj_abc",
    mode="best_fit"  # o "fastest_available", "lowest_cost"
)
```

### 2. Matching por Habilidad
Encontrar recursos con skills específicos.

```python
skill_matches = engine.match_by_skill(
    required_skills=["machine_learning", "pytorch"],
    proficiency_level="advanced",
    include_certifications=True
)
```

### 3. Matching por Disponibilidad
Priorizar recursos disponibles inmediatamente.

```python
urgent_matches = engine.match_by_availability(
    needed_from="2024-01-20",
    duration_weeks=8,
    commitment="full_time"
)
```

### 4. Matching por Presupuesto
Optimizar dentro de restricciones financieras.

```python
budget_matches = engine.match_within_budget(
    total_budget=100000,
    currency="USD",
    include_negotiation_margin=True
)
```

## Configuración

```yaml
matching_engine:
  scoring:
    weights:
      skills: 0.30
      experience: 0.20
      availability: 0.15
      performance: 0.15
      location: 0.10
      language: 0.05
      culture: 0.05
    
    thresholds:
      min_compatibility_score: 60
      preferred_compatibility_score: 80
      excellent_compatibility_score: 90
  
  constraints:
    max_workload_hours_weekly: 45
    min_rest_days_between_projects: 2
    max_concurrent_projects: 3
    require_contract_signature: true
  
  optimization:
    algorithm: genetic_algorithm
    population_size: 100
    generations: 50
    convergence_threshold: 0.01
  
  notifications:
    notify_on_new_match: true
    notify_on_assignment_change: true
    escalation_threshold_hours: 24
```

## Métricas de Rendimiento

| Métrica | Descripción | Objetivo |
|---------|-------------|----------|
| **Tasa de Match Exitoso** | % de asignaciones que completan el proyecto | >85% |
| **Score Promedio de Compatibilidad** | Puntaje medio de matches realizados | >80 |
| **Tiempo de Asignación** | Tiempo promedio para encontrar match | <4 horas |
| **Retención de Subcontratistas** | % que continúan después del primer proyecto | >70% |
| **Satisfacción del GC** | Rating promedio de GCs sobre asignaciones | >4.2/5 |
| **Utilización de Recursos** | % de tiempo productivo de subcontratistas | >75% |

## Dashboard de Monitoreo

El sistema provee dashboards con:

- 📈 **Pipeline de Asignaciones**: Matches en progreso
- 🎯 **Tasa de Éxito por GC**: Performance histórica
- ⭐ **Top Performers**: Subcontratistas mejor rankeados
- ⚠️ **Alertas de Riesgo**: Posibles conflictos o delays
- 💰 **Análisis de Costos**: Budget vs actual por proyecto
- 🌍 **Distribución Geográfica**: Mapa de recursos activos

## Integraciones

- ✅ ATS (Applicant Tracking Systems)
- ✅ HRIS (Human Resource Information Systems)
- ✅ Project Management (Jira, Asana, Monday)
- ✅ Time Tracking (Toggl, Harvest)
- ✅ Video Conferencing (Zoom, Teams)
- ✅ Payment Systems (PayPal, Wise, Deel)
- ✅ Background Check Services
- ✅ Certification Verification APIs

## Casos de Uso

### 🚀 Startup en Crecimiento
**Problema**: Necesitan escalar equipo rápidamente sin perder calidad.
**Solución**: Matching automatizado encuentra 15 subcontratistas calificados en 48 horas.
**Resultado**: 95% de retención después de 6 meses.

### 🏢 Enterprise Multi-Proyecto
**Problema**: 50+ proyectos concurrentes con recursos limitados.
**Solución**: Optimización global balancea carga y maximiza utilización.
**Resultado**: 30% reducción en costos de sobrecapacidad.

### 🌐 Agencia Global
**Problema**: Coordinar equipos distribuidos en 12 países.
**Solución**: Matching considera zonas horarias, idiomas y culturas.
**Resultado**: 40% mejora en satisfacción de clientes.

## Seguridad y Compliance

- 🔒 Datos encriptados en reposo y tránsito
- 🔒 Acceso basado en roles (RBAC)
- 🔒 Auditoría completa de decisiones de matching
- 🔒 Cumplimiento GDPR para datos personales
- 🔒 Verificación de certificaciones con fuentes oficiales
- 🔒 Contratos digitales con validez legal

## Mejores Prácticas

1. **Actualizar perfiles regularmente**: Skills y disponibilidad cambian
2. **Recopilar feedback post-proyecto**: Mejora continua del algoritmo
3. **Revisar pesos de scoring**: Ajustar según objetivos de negocio
4. **Mantener pool de backup**: Tener alternativas para emergencias
5. **Documentar excepciones**: Registrar cuando se override el matching automático

## Roadmap

- [ ] Predicción de éxito de match con ML
- [ ] Matching basado en personalidad (Big Five)
- [ ] Simulación de escenarios "what-if"
- [ ] Integración con plataformas de freelancers externos
- [ ] Sistema de mentoría automática GC-Contractor
- [ ] Gamificación para engagement de subcontratistas

## Soporte

- Documentación API: `/docs/matching-engine/api`
- Ejemplos: `/examples/matching`
- Slack: #matching-engine
- Email: ops-team@company.com
