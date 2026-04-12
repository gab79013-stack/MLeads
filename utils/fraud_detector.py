"""
Módulo de detección de fraude para transacciones y actividades sospechosas.
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class FraudRiskLevel(Enum):
    """Niveles de riesgo de fraude."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FraudDetector:
    """
    Detector de fraude que analiza transacciones y actividades
    para identificar patrones sospechosos.
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Inicializa el detector de fraude con configuración opcional.

        Args:
            config: Diccionario con configuración de umbrales y reglas.
        """
        self.config = config or {}
        self._setup_default_thresholds()

    def _setup_default_thresholds(self):
        """Configura umbrales por defecto para la detección de fraude."""
        self.thresholds = {
            'max_amount': self.config.get('max_amount', 10000),
            'max_daily_transactions': self.config.get('max_daily_transactions', 10),
            'max_failed_attempts': self.config.get('max_failed_attempts', 3),
            'velocity_window_hours': self.config.get('velocity_window_hours', 24),
            'velocity_max_amount': self.config.get('velocity_max_amount', 5000),
            'distance_max_km': self.config.get('distance_max_km', 500),
            'time_min_minutes': self.config.get('time_min_minutes', 60),
        }

    def analyze_transaction(
        self,
        transaction: Dict,
        user_history: Optional[List[Dict]] = None
    ) -> Dict:
        """
        Analiza una transacción individual en busca de indicadores de fraude.

        Args:
            transaction: Diccionario con datos de la transacción.
            user_history: Historial opcional de transacciones del usuario.

        Returns:
            Diccionario con resultados del análisis incluyendo nivel de riesgo.
        """
        risk_score = 0
        flags = []

        # Verificación de monto
        amount = transaction.get('amount', 0)
        if amount > self.thresholds['max_amount']:
            risk_score += 30
            flags.append('high_amount')

        # Verificaciones basadas en historial
        if user_history:
            velocity_check = self._check_velocity(transaction, user_history)
            if velocity_check['suspicious']:
                risk_score += velocity_check['score']
                flags.extend(velocity_check['flags'])

            location_check = self._check_location_anomaly(transaction, user_history)
            if location_check['suspicious']:
                risk_score += location_check['score']
                flags.extend(location_check['flags'])

        # Determinar nivel de riesgo
        risk_level = self._calculate_risk_level(risk_score)

        return {
            'transaction_id': transaction.get('id'),
            'risk_score': risk_score,
            'risk_level': risk_level.value,
            'flags': flags,
            'is_suspicious': risk_score >= 50,
            'recommended_action': self._get_recommended_action(risk_level),
            'analyzed_at': datetime.utcnow().isoformat()
        }

    def _check_velocity(
        self,
        current_transaction: Dict,
        history: List[Dict]
    ) -> Dict:
        """
        Verifica anomalías en la velocidad de transacciones.

        Args:
            current_transaction: Transacción actual a analizar.
            history: Historial de transacciones previas.

        Returns:
            Diccionario con resultados de la verificación de velocidad.
        """
        result = {
            'suspicious': False,
            'score': 0,
            'flags': []
        }

        window_start = datetime.utcnow() - timedelta(
            hours=self.thresholds['velocity_window_hours']
        )

        recent_transactions = [
            t for t in history
            if datetime.fromisoformat(t.get('timestamp', '1970-01-01')) > window_start
        ]

        # Verificar frecuencia de transacciones
        if len(recent_transactions) >= self.thresholds['max_daily_transactions']:
            result['suspicious'] = True
            result['score'] += 25
            result['flags'].append('high_frequency')

        # Verificar monto acumulado
        total_amount = sum(t.get('amount', 0) for t in recent_transactions)
        if total_amount > self.thresholds['velocity_max_amount']:
            result['suspicious'] = True
            result['score'] += 20
            result['flags'].append('high_velocity_amount')

        return result

    def _check_location_anomaly(
        self,
        current_transaction: Dict,
        history: List[Dict]
    ) -> Dict:
        """
        Verifica anomalías geográficas en las transacciones.

        Args:
            current_transaction: Transacción actual a analizar.
            history: Historial de transacciones previas.

        Returns:
            Diccionario con resultados de la verificación de ubicación.
        """
        result = {
            'suspicious': False,
            'score': 0,
            'flags': []
        }

        current_location = current_transaction.get('location')
        current_time = current_transaction.get('timestamp')

        if not current_location or not current_time:
            return result

        # Buscar transacción previa más cercana en tiempo
        previous_transaction = None
        min_time_diff = float('inf')

        for t in history:
            if t.get('location') and t.get('timestamp'):
                try:
                    time_diff = abs(
                        (datetime.fromisoformat(current_time) -
                         datetime.fromisoformat(t['timestamp'])).total_seconds() / 60
                    )
                    if time_diff < min_time_diff and time_diff > 0:
                        min_time_diff = time_diff
                        previous_transaction = t
                except (ValueError, TypeError):
                    continue

        if previous_transaction:
            prev_location = previous_transaction.get('location')
            if prev_location != current_location:
                # Verificar si el tiempo es insuficiente para viajar entre ubicaciones
                if min_time_diff < self.thresholds['time_min_minutes']:
                    result['suspicious'] = True
                    result['score'] += 40
                    result['flags'].append('impossible_travel')

        return result

    def _calculate_risk_level(self, risk_score: int) -> FraudRiskLevel:
        """
        Calcula el nivel de riesgo basado en el puntaje.

        Args:
            risk_score: Puntaje de riesgo calculado.

        Returns:
            Nivel de riesgo correspondiente.
        """
        if risk_score >= 80:
            return FraudRiskLevel.CRITICAL
        elif risk_score >= 60:
            return FraudRiskLevel.HIGH
        elif risk_score >= 30:
            return FraudRiskLevel.MEDIUM
        else:
            return FraudRiskLevel.LOW

    def _get_recommended_action(self, risk_level: FraudRiskLevel) -> str:
        """
        Obtiene la acción recomendada basada en el nivel de riesgo.

        Args:
            risk_level: Nivel de riesgo detectado.

        Returns:
            Acción recomendada como string.
        """
        actions = {
            FraudRiskLevel.LOW: 'approve',
            FraudRiskLevel.MEDIUM: 'review',
            FraudRiskLevel.HIGH: 'hold_and_verify',
            FraudRiskLevel.CRITICAL: 'block_and_investigate'
        }
        return actions.get(risk_level, 'review')

    def batch_analyze(
        self,
        transactions: List[Dict],
        user_histories: Optional[Dict[str, List[Dict]]] = None
    ) -> List[Dict]:
        """
        Analiza múltiples transacciones en lote.

        Args:
            transactions: Lista de transacciones a analizar.
            user_histories: Diccionario opcional con historiales por usuario.

        Returns:
            Lista de resultados de análisis para cada transacción.
        """
        results = []
        user_histories = user_histories or {}

        for transaction in transactions:
            user_id = transaction.get('user_id')
            history = user_histories.get(user_id, [])

            result = self.analyze_transaction(transaction, history)
            results.append(result)

        return results

    def generate_report(
        self,
        analysis_results: List[Dict]
    ) -> Dict:
        """
        Genera un reporte resumen de los resultados de análisis.

        Args:
            analysis_results: Lista de resultados de análisis de transacciones.

        Returns:
            Diccionario con estadísticas y resumen del reporte.
        """
        if not analysis_results:
            return {'error': 'No hay resultados para analizar'}

        total = len(analysis_results)
        suspicious_count = sum(1 for r in analysis_results if r.get('is_suspicious'))
        
        risk_distribution = {
            'low': 0,
            'medium': 0,
            'high': 0,
            'critical': 0
        }

        flag_counts = {}

        for result in analysis_results:
            risk_level = result.get('risk_level', 'low')
            if risk_level in risk_distribution:
                risk_distribution[risk_level] += 1

            for flag in result.get('flags', []):
                flag_counts[flag] = flag_counts.get(flag, 0) + 1

        return {
            'total_analyzed': total,
            'suspicious_count': suspicious_count,
            'suspicious_percentage': round((suspicious_count / total) * 100, 2),
            'risk_distribution': risk_distribution,
            'common_flags': sorted(
                flag_counts.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10],
            'generated_at': datetime.utcnow().isoformat()
        }


# Instancia global por defecto
default_detector = FraudDetector()


def detect_fraud(
    transaction: Dict,
    user_history: Optional[List[Dict]] = None
) -> Dict:
    """
    Función conveniente para detectar fraude en una transacción.

    Args:
        transaction: Datos de la transacción a analizar.
        user_history: Historial opcional de transacciones del usuario.

    Returns:
        Resultados del análisis de fraude.
    """
    return default_detector.analyze_transaction(transaction, user_history)


def detect_batch_fraud(
    transactions: List[Dict],
    user_histories: Optional[Dict[str, List[Dict]]] = None
) -> List[Dict]:
    """
    Función conveniente para detectar fraude en múltiples transacciones.

    Args:
        transactions: Lista de transacciones a analizar.
        user_histories: Historiales opcionales por usuario.

    Returns:
        Lista de resultados de análisis.
    """
    return default_detector.batch_analyze(transactions, user_histories)
