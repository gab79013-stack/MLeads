"""
Análisis competitivo que monitorea y analiza actividades, 
precios y estrategias de competidores en el mercado.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


class CompetitorSize(Enum):
    """Tamaño del competidor."""
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    ENTERPRISE = "enterprise"


class MarketPosition(Enum):
    """Posición en el mercado."""
    LEADER = "leader"
    CHALLENGER = "challenger"
    FOLLOWER = "follower"
    NICHE = "niche"


class ThreatLevel(Enum):
    """Nivel de amenaza competitiva."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class CompetitorProfile:
    """Perfil completo de un competidor."""
    competitor_id: str
    name: str
    website: str
    size: CompetitorSize
    market_position: MarketPosition
    headquarters: Optional[str]
    founded_year: Optional[int]
    employee_count: Optional[int]
    estimated_revenue: Optional[float]
    target_segments: List[str]
    key_products: List[str]
    strengths: List[str]
    weaknesses: List[str]
    last_updated: datetime


@dataclass
class PricePoint:
    """Punto de precio de producto competitivo."""
    product_name: str
    price: float
    currency: str
    pricing_model: str  # 'subscription', 'one_time', 'freemium', etc.
    features_included: List[str]
    last_observed: datetime


@dataclass
class MarketingActivity:
    """Actividad de marketing observada."""
    activity_type: str
    channel: str
    description: str
    observed_date: datetime
    estimated_budget: Optional[float]
    target_audience: Optional[str]
    effectiveness_score: Optional[float]


@dataclass
class CompetitiveInsight:
    """Insight competitivo generado."""
    insight_id: str
    competitor_id: str
    insight_type: str
    severity: ThreatLevel
    title: str
    description: str
    recommended_actions: List[str]
    supporting_data: Dict[str, Any]
    generated_at: datetime


class CompetitiveAnalyzer:
    """
    Analizador competitivo que monitorea y analiza actividades
    de competidores para generar insights accionables.
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Inicializa el analizador competitivo con configuración opcional.

        Args:
            config: Diccionario con configuración de monitoreo y alertas.
        """
        self.config = config or {}
        self._setup_default_config()
        self._competitors: Dict[str, CompetitorProfile] = {}
        self._price_history: Dict[str, List[PricePoint]] = defaultdict(list)
        self._marketing_activities: Dict[str, List[MarketingActivity]] = defaultdict(list)
        self._insights: List[CompetitiveInsight] = []

    def _setup_default_config(self):
        """Configura parámetros por defecto del analizador."""
        self.settings = {
            'monitoring_frequency_hours': self.config.get('monitoring_frequency_hours', 24),
            'price_change_threshold_percent': self.config.get('price_change_threshold_percent', 5),
            'alert_on_new_activity': self.config.get('alert_on_new_activity', True),
            'track_social_media': self.config.get('track_social_media', True),
            'track_job_postings': self.config.get('track_job_postings', True),
            'track_product_updates': self.config.get('track_product_updates', True),
            'default_currency': self.config.get('default_currency', 'USD'),
        }

    def add_competitor(
        self,
        competitor_id: str,
        name: str,
        website: str,
        size: CompetitorSize = CompetitorSize.MEDIUM,
        market_position: MarketPosition = MarketPosition.FOLLOWER,
        headquarters: Optional[str] = None,
        founded_year: Optional[int] = None,
        employee_count: Optional[int] = None,
        estimated_revenue: Optional[float] = None,
        target_segments: Optional[List[str]] = None,
        key_products: Optional[List[str]] = None,
        strengths: Optional[List[str]] = None,
        weaknesses: Optional[List[str]] = None
    ) -> CompetitorProfile:
        """
        Agrega o actualiza un perfil de competidor.

        Args:
            competitor_id: ID único del competidor.
            name: Nombre del competidor.
            website: Sitio web principal.
            size: Tamaño de la empresa.
            market_position: Posición en el mercado.
            headquarters: Ubicación de oficinas principales.
            founded_year: Año de fundación.
            employee_count: Número de empleados.
            estimated_revenue: Ingresos estimados.
            target_segments: Segmentos de mercado objetivo.
            key_products: Productos principales.
            strengths: Fortalezas clave.
            weaknesses: Debilidades clave.

        Returns:
            CompetitorProfile creado/actualizado.
        """
        profile = CompetitorProfile(
            competitor_id=competitor_id,
            name=name,
            website=website,
            size=size,
            market_position=market_position,
            headquarters=headquarters,
            founded_year=founded_year,
            employee_count=employee_count,
            estimated_revenue=estimated_revenue,
            target_segments=target_segments or [],
            key_products=key_products or [],
            strengths=strengths or [],
            weaknesses=weaknesses or [],
            last_updated=datetime.now()
        )
        self._competitors[competitor_id] = profile
        
        logger.info(f"Competidor agregado/actualizado: {name} ({competitor_id})")
        return profile

    def record_price_point(
        self,
        competitor_id: str,
        product_name: str,
        price: float,
        currency: Optional[str] = None,
        pricing_model: str = "subscription",
        features_included: Optional[List[str]] = None
    ) -> bool:
        """
        Registra un punto de precio observado para un competidor.

        Args:
            competitor_id: ID del competidor.
            product_name: Nombre del producto.
            price: Precio observado.
            currency: Moneda del precio.
            pricing_model: Modelo de precios.
            features_included: Características incluidas.

        Returns:
            True si se registró correctamente.
        """
        if competitor_id not in self._competitors:
            logger.warning(f"Competidor {competitor_id} no encontrado")
            return False
        
        price_point = PricePoint(
            product_name=product_name,
            price=price,
            currency=currency or self.settings['default_currency'],
            pricing_model=pricing_model,
            features_included=features_included or [],
            last_observed=datetime.now()
        )
        
        key = f"{competitor_id}:{product_name}"
        self._price_history[key].append(price_point)
        
        # Verificar cambio significativo de precio
        self._check_price_change(competitor_id, product_name, price_point)
        
        logger.debug(f"Precio registrado para {competitor_id}/{product_name}: ${price}")
        return True

    def _check_price_change(
        self,
        competitor_id: str,
        product_name: str,
        current_price: PricePoint
    ):
        """Verifica si hay un cambio significativo de precio."""
        key = f"{competitor_id}:{product_name}"
        history = self._price_history.get(key, [])
        
        if len(history) < 2:
            return
        
        previous_price = history[-2].price
        change_percent = ((current_price.price - previous_price) / previous_price) * 100
        
        threshold = self.settings['price_change_threshold_percent']
        
        if abs(change_percent) >= threshold:
            direction = "aumento" if change_percent > 0 else "reducción"
            insight = self._create_insight(
                competitor_id=competitor_id,
                insight_type="price_change",
                severity=ThreatLevel.MEDIUM if abs(change_percent) < 15 else ThreatLevel.HIGH,
                title=f"Cambio de precio significativo en {product_name}",
                description=f"El competidor ha {direction} su precio en {abs(change_percent):.1f}%",
                recommended_actions=[
                    "Evaluar impacto en nuestra estrategia de precios",
                    "Analizar reacción del mercado",
                    "Considerar ajuste de precios propio si es necesario"
                ],
                supporting_data={
                    'product': product_name,
                    'previous_price': previous_price,
                    'current_price': current_price.price,
                    'change_percent': round(change_percent, 2)
                }
            )
            self._insights.append(insight)

    def record_marketing_activity(
        self,
        competitor_id: str,
        activity_type: str,
        channel: str,
        description: str,
        estimated_budget: Optional[float] = None,
        target_audience: Optional[str] = None
    ) -> bool:
        """
        Registra una actividad de marketing observada.

        Args:
            competitor_id: ID del competidor.
            activity_type: Tipo de actividad (campaign, launch, event, etc.).
            channel: Canal utilizado (social, email, paid, organic, etc.).
            description: Descripción de la actividad.
            estimated_budget: Presupuesto estimado.
            target_audience: Audiencia objetivo.

        Returns:
            True si se registró correctamente.
        """
        if competitor_id not in self._competitors:
            logger.warning(f"Competidor {competitor_id} no encontrado")
            return False
        
        activity = MarketingActivity(
            activity_type=activity_type,
            channel=channel,
            description=description,
            observed_date=datetime.now(),
            estimated_budget=estimated_budget,
            target_audience=target_audience,
            effectiveness_score=None  # Se calculará posteriormente
        )
        
        self._marketing_activities[competitor_id].append(activity)
        
        # Generar insight si la actividad es significativa
        if estimated_budget and estimated_budget > 50000:
            insight = self._create_insight(
                competitor_id=competitor_id,
                insight_type="major_marketing_spend",
                severity=ThreatLevel.MEDIUM,
                title=f"Inversión significativa en marketing detectada",
                description=f"Competidor invirtiendo ${estimated_budget:,} en {activity_type} vía {channel}",
                recommended_actions=[
                    "Monitorear efectividad de la campaña",
                    "Evaluar contramedidas de marketing",
                    "Analizar mensajes y posicionamiento"
                ],
                supporting_data={
                    'activity_type': activity_type,
                    'channel': channel,
                    'estimated_budget': estimated_budget
                }
            )
            self._insights.append(insight)
        
        logger.debug(f"Actividad de marketing registrada para {competitor_id}")
        return True

    def _create_insight(
        self,
        competitor_id: str,
        insight_type: str,
        severity: ThreatLevel,
        title: str,
        description: str,
        recommended_actions: List[str],
        supporting_data: Dict[str, Any]
    ) -> CompetitiveInsight:
        """Crea un nuevo insight competitivo."""
        insight_id = f"insight_{competitor_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        return CompetitiveInsight(
            insight_id=insight_id,
            competitor_id=competitor_id,
            insight_type=insight_type,
            severity=severity,
            title=title,
            description=description,
            recommended_actions=recommended_actions,
            supporting_data=supporting_data,
            generated_at=datetime.now()
        )

    def analyze_competitive_position(self) -> Dict[str, Any]:
        """
        Analiza la posición competitiva general del mercado.

        Returns:
            Diccionario con análisis de posición competitiva.
        """
        if not self._competitors:
            return {'error': 'No hay competidores registrados'}
        
        # Análisis por tamaño
        size_distribution = defaultdict(int)
        for comp in self._competitors.values():
            size_distribution[comp.size.value] += 1
        
        # Análisis por posición de mercado
        position_distribution = defaultdict(int)
        for comp in self._competitors.values():
            position_distribution[comp.market_position.value] += 1
        
        # Calcular threat level promedio
        threat_scores = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}
        total_threat = 0
        for insight in self._insights:
            total_threat += threat_scores.get(insight.severity.value, 1)
        
        avg_threat = total_threat / len(self._insights) if self._insights else 1
        
        overall_threat = ThreatLevel.LOW
        if avg_threat >= 3.5:
            overall_threat = ThreatLevel.CRITICAL
        elif avg_threat >= 2.5:
            overall_threat = ThreatLevel.HIGH
        elif avg_threat >= 1.5:
            overall_threat = ThreatLevel.MEDIUM
        
        return {
            'total_competitors': len(self._competitors),
            'size_distribution': dict(size_distribution),
            'position_distribution': dict(position_distribution),
            'total_insights': len(self._insights),
            'overall_threat_level': overall_threat.value,
            'recent_activities_count': sum(len(acts) for acts in self._marketing_activities.values()),
            'price_points_tracked': sum(len(pp) for pp in self._price_history.values())
        }

    def get_competitor_analysis(self, competitor_id: str) -> Optional[Dict[str, Any]]:
        """
        Obtiene análisis detallado de un competidor específico.

        Args:
            competitor_id: ID del competidor.

        Returns:
            Diccionario con análisis del competidor o None si no existe.
        """
        if competitor_id not in self._competitors:
            return None
        
        competitor = self._competitors[competitor_id]
        
        # Obtener precios recientes
        recent_prices = []
        for key, prices in self._price_history.items():
            if key.startswith(f"{competitor_id}:"):
                if prices:
                    recent_prices.append({
                        'product': key.split(':')[1],
                        'current_price': prices[-1].price,
                        'currency': prices[-1].currency,
                        'pricing_model': prices[-1].pricing_model
                    })
        
        # Obtener actividades recientes
        activities = self._marketing_activities.get(competitor_id, [])
        recent_activities = [
            {
                'type': act.activity_type,
                'channel': act.channel,
                'description': act.description,
                'date': act.observed_date.isoformat(),
                'budget': act.estimated_budget
            }
            for act in activities[-10:]  # Últimas 10 actividades
        ]
        
        # Obtener insights relacionados
        competitor_insights = [
            i for i in self._insights if i.competitor_id == competitor_id
        ]
        
        # Calcular score de amenaza
        threat_scores = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}
        if competitor_insights:
            avg_threat = sum(
                threat_scores.get(i.severity.value, 1) for i in competitor_insights
            ) / len(competitor_insights)
        else:
            avg_threat = 1
        
        return {
            'profile': {
                'name': competitor.name,
                'website': competitor.website,
                'size': competitor.size.value,
                'market_position': competitor.market_position.value,
                'headquarters': competitor.headquarters,
                'employee_count': competitor.employee_count,
                'target_segments': competitor.target_segments,
                'key_products': competitor.key_products,
                'strengths': competitor.strengths,
                'weaknesses': competitor.weaknesses
            },
            'pricing': recent_prices,
            'recent_activities': recent_activities,
            'insights_count': len(competitor_insights),
            'threat_score': round(avg_threat, 2),
            'threat_level': self._score_to_threat_level(avg_threat)
        }

    def _score_to_threat_level(self, score: float) -> str:
        """Convierte score numérico a nivel de amenaza."""
        if score >= 3.5:
            return ThreatLevel.CRITICAL.value
        elif score >= 2.5:
            return ThreatLevel.HIGH.value
        elif score >= 1.5:
            return ThreatLevel.MEDIUM.value
        else:
            return ThreatLevel.LOW.value

    def compare_competitors(
        self,
        competitor_ids: List[str]
    ) -> Dict[str, Any]:
        """
        Compara múltiples competidores entre sí.

        Args:
            competitor_ids: Lista de IDs de competidores a comparar.

        Returns:
            Diccionario con comparación detallada.
        """
        comparison = {
            'competitors': [],
            'price_comparison': {},
            'activity_comparison': {},
            'strengths_weaknesses': {}
        }
        
        for comp_id in competitor_ids:
            if comp_id not in self._competitors:
                continue
            
            comp = self._competitors[comp_id]
            comparison['competitors'].append({
                'id': comp_id,
                'name': comp.name,
                'size': comp.size.value,
                'market_position': comp.market_position.value
            })
            
            # Comparación de fortalezas/debilidades
            comparison['strengths_weaknesses'][comp_id] = {
                'strengths': comp.strengths,
                'weaknesses': comp.weaknesses
            }
            
            # Comparación de actividades
            activities = self._marketing_activities.get(comp_id, [])
            comparison['activity_comparison'][comp_id] = len(activities)
        
        # Comparación de precios
        for comp_id in competitor_ids:
            comp_prices = {}
            for key, prices in self._price_history.items():
                if key.startswith(f"{comp_id}:") and prices:
                    product = key.split(':')[1]
                    comp_prices[product] = prices[-1].price
            if comp_prices:
                comparison['price_comparison'][comp_id] = comp_prices
        
        return comparison

    def get_recent_insights(
        self,
        limit: int = 10,
        min_severity: ThreatLevel = ThreatLevel.LOW
    ) -> List[Dict[str, Any]]:
        """
        Obtiene insights recientes ordenados por severidad.

        Args:
            limit: Número máximo de insights a retornar.
            min_severity: Severidad mínima para filtrar.

        Returns:
            Lista de insights en formato diccionario.
        """
        severity_order = {
            ThreatLevel.LOW: 0,
            ThreatLevel.MEDIUM: 1,
            ThreatLevel.HIGH: 2,
            ThreatLevel.CRITICAL: 3
        }
        
        min_severity_value = severity_order.get(min_severity, 0)
        
        filtered_insights = [
            i for i in self._insights
            if severity_order.get(i.severity, 0) >= min_severity_value
        ]
        
        # Ordenar por severidad (desc) y fecha (desc)
        filtered_insights.sort(
            key=lambda x: (severity_order.get(x.severity, 0), x.generated_at),
            reverse=True
        )
        
        return [
            {
                'insight_id': i.insight_id,
                'competitor_id': i.competitor_id,
                'competitor_name': self._competitors.get(i.competitor_id, {}).name if hasattr(self._competitors.get(i.competitor_id, {}), 'name') else i.competitor_id,
                'type': i.insight_type,
                'severity': i.severity.value,
                'title': i.title,
                'description': i.description,
                'recommended_actions': i.recommended_actions,
                'generated_at': i.generated_at.isoformat()
            }
            for i in filtered_insights[:limit]
        ]

    def generate_competitive_report(self) -> Dict[str, Any]:
        """
        Genera un reporte competitivo completo.

        Returns:
            Diccionario con reporte competitivo detallado.
        """
        now = datetime.now()
        
        return {
            'report_generated_at': now.isoformat(),
            'executive_summary': self._generate_executive_summary(),
            'market_overview': self.analyze_competitive_position(),
            'competitor_profiles': [
                self.get_competitor_analysis(comp_id)
                for comp_id in self._competitors.keys()
            ],
            'top_insights': self.get_recent_insights(limit=5, min_severity=ThreatLevel.MEDIUM),
            'recommendations': self._generate_strategic_recommendations(),
            'next_monitoring_date': (now + timedelta(hours=self.settings['monitoring_frequency_hours'])).isoformat()
        }

    def _generate_executive_summary(self) -> Dict[str, Any]:
        """Genera resumen ejecutivo del panorama competitivo."""
        total_competitors = len(self._competitors)
        total_insights = len(self._insights)
        
        high_severity_insights = [
            i for i in self._insights
            if i.severity in [ThreatLevel.HIGH, ThreatLevel.CRITICAL]
        ]
        
        most_active_competitor = max(
            self._marketing_activities.items(),
            key=lambda x: len(x[1]),
            default=(None, [])
        )
        
        return {
            'total_competitors_tracked': total_competitors,
            'total_insights_generated': total_insights,
            'high_severity_issues': len(high_severity_insights),
            'most_active_competitor': most_active_competitor[0],
            'requires_immediate_attention': len(high_severity_insights) > 0
        }

    def _generate_strategic_recommendations(self) -> List[str]:
        """Genera recomendaciones estratégicas basadas en análisis."""
        recommendations = []
        
        # Analizar amenazas críticas
        critical_insights = [
            i for i in self._insights
            if i.severity == ThreatLevel.CRITICAL
        ]
        
        if critical_insights:
            recommendations.append(
                "Priorizar respuesta a amenazas críticas identificadas"
            )
        
        # Analizar brechas de precios
        price_changes = [
            i for i in self._insights
            if i.insight_type == 'price_change'
        ]
        
        if len(price_changes) >= 3:
            recommendations.append(
                "Revisar estrategia de precios ante movimientos competitivos frecuentes"
            )
        
        # Analizar actividad de marketing
        total_activities = sum(len(acts) for acts in self._marketing_activities.values())
        if total_activities > 20:
            recommendations.append(
                "Incrementar inversión en marketing para mantener visibilidad"
            )
        
        if not recommendations:
            recommendations.append(
                "Continuar monitoreo regular del panorama competitivo"
            )
        
        return recommendations
