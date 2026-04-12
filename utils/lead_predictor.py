"""
Predicción proactiva de leads basada en comportamiento, 
demografía y señales de mercado para identificar oportunidades
de conversión antes de que ocurran.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math

logger = logging.getLogger(__name__)


class LeadScore(Enum):
    """Niveles de score de lead."""
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class IntentSignal(Enum):
    """Señales de intención de compra."""
    PAGE_VIEW = "page_view"
    CONTENT_DOWNLOAD = "content_download"
    WEBINAR_REGISTRATION = "webinar_registration"
    DEMO_REQUEST = "demo_request"
    PRICE_CHECK = "price_check"
    COMPETITOR_RESEARCH = "competitor_research"
    CONTACT_FORM = "contact_form"
    EMAIL_OPEN = "email_open"
    EMAIL_CLICK = "email_click"
    SOCIAL_ENGAGEMENT = "social_engagement"


@dataclass
class LeadProfile:
    """Perfil completo de un lead."""
    lead_id: str
    email: str
    company: Optional[str]
    industry: Optional[str]
    company_size: Optional[str]
    job_title: Optional[str]
    location: Optional[str]
    source: str
    created_at: datetime
    last_activity: datetime
    total_interactions: int = 0
    engagement_score: float = 0.0
    conversion_probability: float = 0.0


@dataclass
class BehavioralSignal:
    """Señal de comportamiento del lead."""
    signal_type: IntentSignal
    timestamp: datetime
    weight: float
    metadata: Optional[Dict] = None


@dataclass
class LeadPrediction:
    """Resultado de predicción para un lead."""
    lead_id: str
    predicted_score: LeadScore
    conversion_probability: float
    recommended_actions: List[str]
    key_signals: List[BehavioralSignal]
    prediction_confidence: float
    predicted_conversion_date: Optional[datetime]
    generated_at: datetime


class LeadPredictor:
    """
    Predictor proactivo de leads que analiza comportamiento y señales
    para identificar oportunidades de conversión.
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Inicializa el predictor de leads con configuración opcional.

        Args:
            config: Diccionario con configuración de modelos y umbrales.
        """
        self.config = config or {}
        self._setup_default_config()
        self._lead_profiles: Dict[str, LeadProfile] = {}
        self._behavioral_signals: Dict[str, List[BehavioralSignal]] = {}

    def _setup_default_config(self):
        """Configura parámetros por defecto del predictor."""
        self.settings = {
            'min_engagement_threshold': self.config.get('min_engagement_threshold', 0.3),
            'high_intent_threshold': self.config.get('high_intent_threshold', 0.7),
            'signal_decay_hours': self.config.get('signal_decay_hours', 72),
            'prediction_horizon_days': self.config.get('prediction_horizon_days', 30),
            'enable_real_time_scoring': self.config.get('enable_real_time_scoring', True),
        }
        
        # Pesos por tipo de señal
        self.signal_weights = {
            IntentSignal.PAGE_VIEW: 0.05,
            IntentSignal.CONTENT_DOWNLOAD: 0.15,
            IntentSignal.WEBINAR_REGISTRATION: 0.2,
            IntentSignal.DEMO_REQUEST: 0.35,
            IntentSignal.PRICE_CHECK: 0.25,
            IntentSignal.COMPETITOR_RESEARCH: 0.2,
            IntentSignal.CONTACT_FORM: 0.4,
            IntentSignal.EMAIL_OPEN: 0.03,
            IntentSignal.EMAIL_CLICK: 0.08,
            IntentSignal.SOCIAL_ENGAGEMENT: 0.05,
        }

    def create_lead_profile(
        self,
        lead_id: str,
        email: str,
        company: Optional[str] = None,
        industry: Optional[str] = None,
        company_size: Optional[str] = None,
        job_title: Optional[str] = None,
        location: Optional[str] = None,
        source: str = "unknown"
    ) -> LeadProfile:
        """
        Crea un nuevo perfil de lead.

        Args:
            lead_id: ID único del lead.
            email: Email del lead.
            company: Nombre de la empresa.
            industry: Industria de la empresa.
            company_size: Tamaño de la empresa.
            job_title: Título del trabajo.
            location: Ubicación geográfica.
            source: Fuente del lead.

        Returns:
            LeadProfile creado.
        """
        now = datetime.now()
        profile = LeadProfile(
            lead_id=lead_id,
            email=email,
            company=company,
            industry=industry,
            company_size=company_size,
            job_title=job_title,
            location=location,
            source=source,
            created_at=now,
            last_activity=now
        )
        self._lead_profiles[lead_id] = profile
        self._behavioral_signals[lead_id] = []
        
        logger.info(f"Perfil de lead creado: {lead_id}")
        return profile

    def record_behavioral_signal(
        self,
        lead_id: str,
        signal_type: IntentSignal,
        metadata: Optional[Dict] = None
    ) -> bool:
        """
        Registra una señal de comportamiento para un lead.

        Args:
            lead_id: ID del lead.
            signal_type: Tipo de señal de comportamiento.
            metadata: Metadatos adicionales de la señal.

        Returns:
            True si se registró correctamente.
        """
        if lead_id not in self._lead_profiles:
            logger.warning(f"Lead {lead_id} no encontrado")
            return False
        
        signal = BehavioralSignal(
            signal_type=signal_type,
            timestamp=datetime.now(),
            weight=self.signal_weights.get(signal_type, 0.1),
            metadata=metadata
        )
        
        self._behavioral_signals[lead_id].append(signal)
        
        # Actualizar perfil
        profile = self._lead_profiles[lead_id]
        profile.total_interactions += 1
        profile.last_activity = signal.timestamp
        
        # Recalcular scores
        self._update_engagement_score(lead_id)
        
        logger.debug(f"Señal {signal_type.value} registrada para lead {lead_id}")
        return True

    def _update_engagement_score(self, lead_id: str):
        """Actualiza el score de engagement de un lead."""
        signals = self._behavioral_signals.get(lead_id, [])
        if not signals:
            return
        
        now = datetime.now()
        decay_hours = self.settings['signal_decay_hours']
        
        # Calcular score con decaimiento temporal
        weighted_sum = 0.0
        for signal in signals:
            hours_old = (now - signal.timestamp).total_seconds() / 3600
            decay_factor = math.exp(-hours_old / decay_hours)
            weighted_sum += signal.weight * decay_factor
        
        # Normalizar score (0-1)
        max_possible_score = sum(self.signal_weights.values()) * 5  # Asumiendo máx 5 señales recientes
        engagement_score = min(1.0, weighted_sum / max_possible_score)
        
        self._lead_profiles[lead_id].engagement_score = engagement_score

    def predict_lead_quality(self, lead_id: str) -> Optional[LeadPrediction]:
        """
        Predice la calidad y probabilidad de conversión de un lead.

        Args:
            lead_id: ID del lead a predecir.

        Returns:
            LeadPrediction o None si el lead no existe.
        """
        if lead_id not in self._lead_profiles:
            logger.warning(f"Lead {lead_id} no encontrado para predicción")
            return None
        
        profile = self._lead_profiles[lead_id]
        signals = self._behavioral_signals.get(lead_id, [])
        
        # Calcular probabilidad de conversión
        conversion_prob = self._calculate_conversion_probability(profile, signals)
        
        # Determinar score basado en probabilidad
        predicted_score = self._score_from_probability(conversion_prob)
        
        # Identificar señales clave
        key_signals = self._identify_key_signals(signals)
        
        # Generar acciones recomendadas
        recommended_actions = self._generate_recommendations(
            profile, predicted_score, conversion_prob
        )
        
        # Predecir fecha de conversión
        predicted_conversion_date = self._predict_conversion_date(
            conversion_prob, profile.last_activity
        )
        
        # Calcular confianza de predicción
        confidence = self._calculate_prediction_confidence(profile, signals)
        
        prediction = LeadPrediction(
            lead_id=lead_id,
            predicted_score=predicted_score,
            conversion_probability=conversion_prob,
            recommended_actions=recommended_actions,
            key_signals=key_signals,
            prediction_confidence=confidence,
            predicted_conversion_date=predicted_conversion_date,
            generated_at=datetime.now()
        )
        
        # Actualizar perfil con probabilidad
        profile.conversion_probability = conversion_prob
        
        logger.info(f"Predicción generada para lead {lead_id}: {predicted_score.value} ({conversion_prob:.2%})")
        return prediction

    def _calculate_conversion_probability(
        self,
        profile: LeadProfile,
        signals: List[BehavioralSignal]
    ) -> float:
        """Calcula probabilidad de conversión basada en perfil y señales."""
        # Factores base
        base_probability = 0.1  # 10% base
        
        # Factor de engagement
        engagement_factor = profile.engagement_score * 0.4
        
        # Factor de señales de alta intención
        high_intent_signals = [
            IntentSignal.DEMO_REQUEST,
            IntentSignal.CONTACT_FORM,
            IntentSignal.PRICE_CHECK
        ]
        recent_high_intent = sum(
            1 for s in signals
            if s.signal_type in high_intent_signals and
            (datetime.now() - s.timestamp).days <= 7
        )
        high_intent_factor = min(0.3, recent_high_intent * 0.1)
        
        # Factor de completitud de perfil
        profile_completeness = self._calculate_profile_completeness(profile)
        completeness_factor = profile_completeness * 0.1
        
        # Factor de recencia
        days_since_activity = (datetime.now() - profile.last_activity).days
        recency_factor = max(0, 0.1 * (1 - days_since_activity / 30))
        
        probability = (
            base_probability +
            engagement_factor +
            high_intent_factor +
            completeness_factor +
            recency_factor
        )
        
        return min(0.95, max(0.01, probability))

    def _calculate_profile_completeness(self, profile: LeadProfile) -> float:
        """Calcula completitud del perfil (0-1)."""
        fields = [
            profile.email,
            profile.company,
            profile.industry,
            profile.company_size,
            profile.job_title,
            profile.location
        ]
        completed = sum(1 for f in fields if f is not None and f != "")
        return completed / len(fields)

    def _score_from_probability(self, probability: float) -> LeadScore:
        """Convierte probabilidad a score categórico."""
        if probability >= 0.8:
            return LeadScore.VERY_HIGH
        elif probability >= 0.6:
            return LeadScore.HIGH
        elif probability >= 0.4:
            return LeadScore.MEDIUM
        elif probability >= 0.2:
            return LeadScore.LOW
        else:
            return LeadScore.VERY_LOW

    def _identify_key_signals(
        self,
        signals: List[BehavioralSignal]
    ) -> List[BehavioralSignal]:
        """Identifica las señales más importantes."""
        if not signals:
            return []
        
        # Ordenar por peso y recencia
        now = datetime.now()
        scored_signals = []
        for signal in signals:
            hours_old = (now - signal.timestamp).total_seconds() / 3600
            recency_bonus = 1 / (1 + hours_old / 24)  # Bonus por recencia
            score = signal.weight * recency_bonus
            scored_signals.append((signal, score))
        
        scored_signals.sort(key=lambda x: x[1], reverse=True)
        
        # Retornar top 5 señales
        return [s[0] for s in scored_signals[:5]]

    def _generate_recommendations(
        self,
        profile: LeadProfile,
        score: LeadScore,
        conversion_prob: float
    ) -> List[str]:
        """Genera acciones recomendadas basadas en el estado del lead."""
        recommendations = []
        
        if score in [LeadScore.VERY_HIGH, LeadScore.HIGH]:
            recommendations.append("Contactar inmediatamente por teléfono")
            recommendations.append("Ofrecer demo personalizada")
            recommendations.append("Preparar propuesta comercial")
        elif score == LeadScore.MEDIUM:
            recommendations.append("Enviar email de seguimiento personalizado")
            recommendations.append("Invitar a webinar o evento")
            recommendations.append("Compartir caso de éxito relevante")
        elif score == LeadScore.LOW:
            recommendations.append("Continuar nurturing con contenido educativo")
            recommendations.append("Segmentar en campaña de email marketing")
        else:
            recommendations.append("Mantener en lista de nurturing general")
            recommendations.append("Re-evaluar en 30 días")
        
        # Recomendaciones basadas en comportamiento específico
        signals = self._behavioral_signals.get(profile.lead_id, [])
        price_checks = sum(1 for s in signals if s.signal_type == IntentSignal.PRICE_CHECK)
        if price_checks >= 2:
            recommendations.append("Preparar información de precios y descuentos")
        
        demo_requests = sum(1 for s in signals if s.signal_type == IntentSignal.DEMO_REQUEST)
        if demo_requests > 0:
            recommendations.append("Agendar demo dentro de las próximas 24 horas")
        
        return recommendations

    def _predict_conversion_date(
        self,
        probability: float,
        last_activity: datetime
    ) -> Optional[datetime]:
        """Predice fecha probable de conversión."""
        if probability < 0.3:
            return None
        
        # Mayor probabilidad = menor tiempo estimado
        if probability >= 0.8:
            days_until_conversion = 3
        elif probability >= 0.6:
            days_until_conversion = 7
        elif probability >= 0.4:
            days_until_conversion = 14
        else:
            days_until_conversion = 30
        
        return last_activity + timedelta(days=days_until_conversion)

    def _calculate_prediction_confidence(
        self,
        profile: LeadProfile,
        signals: List[BehavioralSignal]
    ) -> float:
        """Calcula confianza de la predicción."""
        confidence = 0.5  # Base
        
        # Más interacciones = más confianza
        interaction_bonus = min(0.2, profile.total_interactions * 0.02)
        confidence += interaction_bonus
        
        # Más señales = más confianza
        signal_bonus = min(0.2, len(signals) * 0.03)
        confidence += signal_bonus
        
        # Perfil completo = más confianza
        completeness = self._calculate_profile_completeness(profile)
        confidence += completeness * 0.1
        
        return min(1.0, confidence)

    def batch_predict_leads(
        self,
        lead_ids: Optional[List[str]] = None,
        min_score: Optional[LeadScore] = None
    ) -> List[LeadPrediction]:
        """
        Genera predicciones para múltiples leads.

        Args:
            lead_ids: Lista de IDs de leads a predecir. Si None, todos.
            min_score: Filtrar leads por score mínimo.

        Returns:
            Lista de LeadPredictions.
        """
        if lead_ids is None:
            lead_ids = list(self._lead_profiles.keys())
        
        predictions = []
        for lead_id in lead_ids:
            prediction = self.predict_lead_quality(lead_id)
            if prediction:
                if min_score is None or self._score_meets_threshold(prediction.predicted_score, min_score):
                    predictions.append(prediction)
        
        # Ordenar por probabilidad de conversión
        predictions.sort(key=lambda p: p.conversion_probability, reverse=True)
        
        return predictions

    def _score_meets_threshold(self, score: LeadScore, threshold: LeadScore) -> bool:
        """Verifica si un score meets or exceeds threshold."""
        score_order = [
            LeadScore.VERY_LOW,
            LeadScore.LOW,
            LeadScore.MEDIUM,
            LeadScore.HIGH,
            LeadScore.VERY_HIGH
        ]
        return score_order.index(score) >= score_order.index(threshold)

    def get_high_intent_leads(
        self,
        hours_window: int = 24
    ) -> List[LeadProfile]:
        """
        Obtiene leads con señales de alta intención recientes.

        Args:
            hours_window: Ventana de tiempo en horas.

        Returns:
            Lista de perfiles de leads de alta intención.
        """
        high_intent_types = [
            IntentSignal.DEMO_REQUEST,
            IntentSignal.CONTACT_FORM,
            IntentSignal.PRICE_CHECK
        ]
        
        cutoff = datetime.now() - timedelta(hours=hours_window)
        high_intent_leads = []
        
        for lead_id, signals in self._behavioral_signals.items():
            has_recent_high_intent = any(
                s.signal_type in high_intent_types and s.timestamp >= cutoff
                for s in signals
            )
            
            if has_recent_high_intent and lead_id in self._lead_profiles:
                high_intent_leads.append(self._lead_profiles[lead_id])
        
        return high_intent_leads

    def analyze_lead_trends(
        self,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Analiza tendencias de leads en el período especificado.

        Args:
            days: Número de días hacia atrás.

        Returns:
            Diccionario con análisis de tendencias.
        """
        cutoff = datetime.now() - timedelta(days=days)
        
        # Leads creados en período
        new_leads = [
            p for p in self._lead_profiles.values()
            if p.created_at >= cutoff
        ]
        
        # Distribución de scores
        score_distribution = {score.value: 0 for score in LeadScore}
        for lead_id in self._lead_profiles:
            prediction = self.predict_lead_quality(lead_id)
            if prediction:
                score_distribution[prediction.predicted_score.value] += 1
        
        # Tasa de engagement promedio
        avg_engagement = (
            sum(p.engagement_score for p in self._lead_profiles.values()) /
            len(self._lead_profiles) if self._lead_profiles else 0
        )
        
        # Señales más comunes
        signal_counts = {}
        for signals in self._behavioral_signals.values():
            for signal in signals:
                if signal.timestamp >= cutoff:
                    signal_counts[signal.signal_type.value] = \
                        signal_counts.get(signal.signal_type.value, 0) + 1
        
        return {
            'period_days': days,
            'new_leads_count': len(new_leads),
            'total_leads_count': len(self._lead_profiles),
            'score_distribution': score_distribution,
            'average_engagement_score': round(avg_engagement, 3),
            'top_signals': sorted(signal_counts.items(), key=lambda x: x[1], reverse=True)[:5],
            'high_intent_leads_count': len(self.get_high_intent_leads())
        }
