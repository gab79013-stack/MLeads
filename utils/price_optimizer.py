"""
Optimización dinámica de precios basada en demanda, competencia
y factores de mercado en tiempo real.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timedelta
import math

logger = logging.getLogger(__name__)


class PricingStrategy(Enum):
    """Estrategias de fijación de precios."""
    DYNAMIC = "dynamic"
    COMPETITIVE = "competitive"
    DEMAND_BASED = "demand_based"
    COST_PLUS = "cost_plus"
    VALUE_BASED = "value_based"
    PENETRATION = "penetration"
    SKIMMING = "skimming"


class DemandLevel(Enum):
    """Niveles de demanda del mercado."""
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


@dataclass
class PricePoint:
    """Representa un punto de precio con sus métricas asociadas."""
    price: float
    currency: str
    strategy: PricingStrategy
    confidence_score: float
    expected_conversion_rate: float
    expected_margin: float
    valid_from: datetime
    valid_until: datetime
    metadata: Optional[Dict] = None


@dataclass
class CompetitorPrice:
    """Información de precio de competidor."""
    competitor_id: str
    product_id: str
    price: float
    currency: str
    last_updated: datetime
    source: str


class PriceOptimizer:
    """
    Optimizador dinámico de precios que ajusta precios en tiempo real
    basado en múltiples factores de mercado.
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Inicializa el optimizador de precios con configuración opcional.

        Args:
            config: Diccionario con configuración de estrategias y umbrales.
        """
        self.config = config or {}
        self._setup_default_config()
        self._competitor_prices: Dict[str, List[CompetitorPrice]] = {}
        self._price_history: Dict[str, List[PricePoint]] = {}

    def _setup_default_config(self):
        """Configura parámetros por defecto del optimizador."""
        self.settings = {
            'min_margin_percent': self.config.get('min_margin_percent', 15),
            'max_discount_percent': self.config.get('max_discount_percent', 30),
            'price_elasticity': self.config.get('price_elasticity', -1.5),
            'competitor_weight': self.config.get('competitor_weight', 0.4),
            'demand_weight': self.config.get('demand_weight', 0.3),
            'cost_weight': self.config.get('cost_weight', 0.2),
            'value_weight': self.config.get('value_weight', 0.1),
            'update_frequency_minutes': self.config.get('update_frequency_minutes', 15),
            'currency': self.config.get('currency', 'USD'),
        }

    def calculate_optimal_price(
        self,
        product_id: str,
        base_cost: float,
        current_demand: DemandLevel,
        competitor_prices: Optional[List[CompetitorPrice]] = None,
        customer_segment: Optional[str] = None,
        inventory_level: Optional[int] = None,
        time_sensitivity: Optional[float] = None
    ) -> PricePoint:
        """
        Calcula el precio óptimo para un producto.

        Args:
            product_id: ID único del producto.
            base_cost: Costo base del producto.
            current_demand: Nivel actual de demanda.
            competitor_prices: Lista de precios de competidores.
            customer_segment: Segmento de cliente objetivo.
            inventory_level: Nivel actual de inventario.
            time_sensitivity: Sensibilidad al tiempo (0-1).

        Returns:
            PricePoint con el precio óptimo calculado.
        """
        try:
            # Calcular factores individuales
            demand_factor = self._calculate_demand_factor(current_demand)
            competitor_factor = self._calculate_competitor_factor(
                base_cost, competitor_prices
            )
            inventory_factor = self._calculate_inventory_factor(inventory_level)
            time_factor = self._calculate_time_factor(time_sensitivity)
            
            # Calcular precio base con margen mínimo
            min_price = base_cost * (1 + self.settings['min_margin_percent'] / 100)
            
            # Calcular precio ponderado por factores
            weighted_price = (
                base_cost * self.settings['cost_weight'] +
                competitor_factor * self.settings['competitor_weight'] +
                base_cost * demand_factor * self.settings['demand_weight'] +
                base_cost * inventory_factor * self.settings['cost_weight']
            )
            
            # Ajustar por sensibilidad temporal
            if time_sensitivity and time_sensitivity > 0:
                weighted_price *= (1 + time_sensitivity * 0.1)
            
            # Aplicar límites
            optimal_price = max(min_price, weighted_price)
            max_price = base_cost * (1 + self.settings['max_discount_percent'] / 100 * -1 + 0.5)
            optimal_price = min(optimal_price, max_price * 2)
            
            # Calcular métricas asociadas
            margin = ((optimal_price - base_cost) / optimal_price) * 100
            conversion_rate = self._estimate_conversion_rate(optimal_price, base_cost, current_demand)
            confidence = self._calculate_confidence_score(
                competitor_prices, current_demand, inventory_level
            )
            
            now = datetime.now()
            price_point = PricePoint(
                price=round(optimal_price, 2),
                currency=self.settings['currency'],
                strategy=PricingStrategy.DYNAMIC,
                confidence_score=confidence,
                expected_conversion_rate=conversion_rate,
                expected_margin=round(margin, 2),
                valid_from=now,
                valid_until=now + timedelta(minutes=self.settings['update_frequency_minutes']),
                metadata={
                    'demand_level': current_demand.value,
                    'competitor_count': len(competitor_prices) if competitor_prices else 0,
                    'inventory_level': inventory_level,
                    'customer_segment': customer_segment
                }
            )
            
            # Guardar en historial
            self._save_price_history(product_id, price_point)
            
            logger.info(f"Precio óptimo calculado para {product_id}: ${price_point.price}")
            return price_point
            
        except Exception as e:
            logger.error(f"Error calculando precio óptimo: {str(e)}")
            raise

    def _calculate_demand_factor(self, demand: DemandLevel) -> float:
        """Calcula factor de ajuste basado en nivel de demanda."""
        demand_factors = {
            DemandLevel.VERY_LOW: 0.8,
            DemandLevel.LOW: 0.9,
            DemandLevel.MEDIUM: 1.0,
            DemandLevel.HIGH: 1.15,
            DemandLevel.VERY_HIGH: 1.3,
        }
        return demand_factors.get(demand, 1.0)

    def _calculate_competitor_factor(
        self,
        base_cost: float,
        competitor_prices: Optional[List[CompetitorPrice]]
    ) -> float:
        """Calcula precio basado en análisis competitivo."""
        if not competitor_prices or len(competitor_prices) == 0:
            return base_cost * 1.2  # Margen por defecto sin competencia
        
        prices = [cp.price for cp in competitor_prices if cp.currency == self.settings['currency']]
        if not prices:
            return base_cost * 1.2
        
        avg_competitor_price = sum(prices) / len(prices)
        min_competitor_price = min(prices)
        
        # Estrategia: precio ligeramente por debajo del promedio pero above mínimo
        target_price = avg_competitor_price * 0.95
        target_price = max(target_price, min_competitor_price * 1.05)
        target_price = max(target_price, base_cost * (1 + self.settings['min_margin_percent'] / 100))
        
        return target_price

    def _calculate_inventory_factor(self, inventory_level: Optional[int]) -> float:
        """Calcula factor de ajuste basado en nivel de inventario."""
        if inventory_level is None:
            return 1.0
        
        if inventory_level <= 0:
            return 1.5  # Precio premium por escasez
        elif inventory_level < 10:
            return 1.2
        elif inventory_level < 50:
            return 1.0
        elif inventory_level < 100:
            return 0.95  # Descuento por exceso
        else:
            return 0.9  # Descuento agresivo

    def _calculate_time_factor(self, time_sensitivity: Optional[float]) -> float:
        """Calcula factor de ajuste basado en sensibilidad temporal."""
        if time_sensitivity is None or time_sensitivity <= 0:
            return 1.0
        return 1.0 + (time_sensitivity * 0.2)

    def _estimate_conversion_rate(
        self,
        price: float,
        base_cost: float,
        demand: DemandLevel
    ) -> float:
        """Estima tasa de conversión basada en precio y demanda."""
        base_conversion = 0.05  # 5% base
        
        # Ajustar por markup
        markup = (price - base_cost) / base_cost
        conversion_adjustment = 1 - (markup * 0.3)  # Mayor markup = menor conversión
        
        # Ajustar por demanda
        demand_multipliers = {
            DemandLevel.VERY_LOW: 0.5,
            DemandLevel.LOW: 0.75,
            DemandLevel.MEDIUM: 1.0,
            DemandLevel.HIGH: 1.3,
            DemandLevel.VERY_HIGH: 1.5,
        }
        
        demand_multiplier = demand_multipliers.get(demand, 1.0)
        
        conversion_rate = base_conversion * conversion_adjustment * demand_multiplier
        return max(0.01, min(0.5, conversion_rate))  # Entre 1% y 50%

    def _calculate_confidence_score(
        self,
        competitor_prices: Optional[List[CompetitorPrice]],
        demand: DemandLevel,
        inventory_level: Optional[int]
    ) -> float:
        """Calcula score de confianza del precio recomendado."""
        confidence = 0.5  # Base
        
        # Más datos de competidores = más confianza
        if competitor_prices:
            confidence += min(0.2, len(competitor_prices) * 0.05)
        
        # Demanda clara = más confianza
        if demand in [DemandLevel.VERY_LOW, DemandLevel.VERY_HIGH]:
            confidence += 0.15
        elif demand in [DemandLevel.LOW, DemandLevel.HIGH]:
            confidence += 0.1
        
        # Inventario conocido = más confianza
        if inventory_level is not None:
            confidence += 0.1
        
        return min(1.0, confidence)

    def _save_price_history(self, product_id: str, price_point: PricePoint):
        """Guarda punto de precio en historial."""
        if product_id not in self._price_history:
            self._price_history[product_id] = []
        
        self._price_history[product_id].append(price_point)
        
        # Mantener solo últimos 100 registros
        if len(self._price_history[product_id]) > 100:
            self._price_history[product_id] = self._price_history[product_id][-100:]

    def update_competitor_prices(
        self,
        product_id: str,
        prices: List[CompetitorPrice]
    ):
        """
        Actualiza precios de competidores para un producto.

        Args:
            product_id: ID del producto.
            prices: Lista de precios de competidores.
        """
        self._competitor_prices[product_id] = prices
        logger.info(f"Actualizados {len(prices)} precios de competidores para {product_id}")

    def get_price_history(
        self,
        product_id: str,
        days: int = 7
    ) -> List[PricePoint]:
        """
        Obtiene historial de precios para un producto.

        Args:
            product_id: ID del producto.
            days: Número de días hacia atrás.

        Returns:
            Lista de PricePoints históricos.
        """
        if product_id not in self._price_history:
            return []
        
        cutoff_date = datetime.now() - timedelta(days=days)
        return [
            pp for pp in self._price_history[product_id]
            if pp.valid_from >= cutoff_date
        ]

    def analyze_price_elasticity(
        self,
        product_id: str,
        price_changes: List[Tuple[datetime, float, float]]
    ) -> Dict[str, Any]:
        """
        Analiza elasticidad precio basada en cambios históricos.

        Args:
            product_id: ID del producto.
            price_changes: Lista de (timestamp, precio, unidades_vendidas).

        Returns:
            Diccionario con análisis de elasticidad.
        """
        if len(price_changes) < 2:
            return {'elasticity': 0, 'confidence': 'low'}
        
        # Calcular elasticidad usando cambios porcentuales
        elasticities = []
        for i in range(1, len(price_changes)):
            _, price1, qty1 = price_changes[i-1]
            _, price2, qty2 = price_changes[i]
            
            if price1 > 0 and qty1 > 0:
                price_change_pct = (price2 - price1) / price1
                qty_change_pct = (qty2 - qty1) / qty1
                
                if price_change_pct != 0:
                    elasticity = qty_change_pct / price_change_pct
                    elasticities.append(elasticity)
        
        if not elasticities:
            return {'elasticity': 0, 'confidence': 'low'}
        
        avg_elasticity = sum(elasticities) / len(elasticities)
        
        return {
            'elasticity': round(avg_elasticity, 3),
            'confidence': 'high' if len(elasticities) >= 5 else 'medium',
            'observations': len(elasticities),
            'interpretation': self._interpret_elasticity(avg_elasticity)
        }

    def _interpret_elasticity(self, elasticity: float) -> str:
        """Interpreta el valor de elasticidad."""
        if elasticity < -2:
            return "Altamente elástico - pequeños cambios en precio generan grandes cambios en demanda"
        elif elasticity < -1:
            return "Elástico - la demanda es sensible al precio"
        elif elasticity < 0:
            return "Inelástico - la demanda es relativamente insensible al precio"
        elif elasticity == 0:
            return "Perfectamente inelástico - la demanda no cambia con el precio"
        else:
            return "Elasticidad positiva - comportamiento atípico (posiblemente producto Giffen)"

    def batch_optimize_prices(
        self,
        products: List[Dict[str, Any]]
    ) -> Dict[str, PricePoint]:
        """
        Optimiza precios para múltiples productos simultáneamente.

        Args:
            products: Lista de diccionarios con información de productos.

        Returns:
            Diccionario mapping product_id a PricePoint.
        """
        results = {}
        for product in products:
            try:
                price_point = self.calculate_optimal_price(
                    product_id=product['product_id'],
                    base_cost=product['base_cost'],
                    current_demand=product.get('demand', DemandLevel.MEDIUM),
                    competitor_prices=product.get('competitor_prices'),
                    customer_segment=product.get('customer_segment'),
                    inventory_level=product.get('inventory_level'),
                    time_sensitivity=product.get('time_sensitivity')
                )
                results[product['product_id']] = price_point
            except Exception as e:
                logger.error(f"Error optimizando precio para {product.get('product_id')}: {str(e)}")
        
        return results
