"""
Agente conversacional multi-modal para interacciones con usuarios
a través de texto, voz e imágenes.
"""

import logging
from typing import Dict, List, Optional, Any, Union
from enum import Enum
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


class ModalityType(Enum):
    """Tipos de modalidad soportadas."""
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    VIDEO = "video"


class IntentType(Enum):
    """Tipos de intenciones del usuario."""
    INQUIRY = "inquiry"
    SUPPORT = "support"
    SALES = "sales"
    COMPLAINT = "complaint"
    FEEDBACK = "feedback"
    GENERAL = "general"


@dataclass
class Message:
    """Representa un mensaje en la conversación."""
    content: str
    modality: ModalityType
    timestamp: datetime
    sender: str  # 'user' o 'agent'
    metadata: Optional[Dict] = None


@dataclass
class ConversationContext:
    """Contexto de la conversación actual."""
    conversation_id: str
    user_id: str
    messages: List[Message]
    current_intent: Optional[IntentType]
    sentiment_score: float
    created_at: datetime
    updated_at: datetime


class MultimodalAgent:
    """
    Agente conversacional multi-modal que procesa y responde
    a través de múltiples canales (texto, voz, imagen).
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Inicializa el agente multi-modal con configuración opcional.

        Args:
            config: Diccionario con configuración de modelos y umbrales.
        """
        self.config = config or {}
        self._setup_default_config()
        self._conversations: Dict[str, ConversationContext] = {}

    def _setup_default_config(self):
        """Configura parámetros por defecto del agente."""
        self.settings = {
            'max_context_length': self.config.get('max_context_length', 10),
            'sentiment_threshold': self.config.get('sentiment_threshold', 0.3),
            'response_timeout_seconds': self.config.get('response_timeout_seconds', 30),
            'enable_voice_processing': self.config.get('enable_voice_processing', True),
            'enable_image_analysis': self.config.get('enable_image_analysis', True),
            'language': self.config.get('language', 'es'),
        }

    def process_message(
        self,
        conversation_id: str,
        user_id: str,
        content: str,
        modality: ModalityType = ModalityType.TEXT,
        metadata: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Procesa un mensaje entrante y genera una respuesta.

        Args:
            conversation_id: ID único de la conversación.
            user_id: ID del usuario.
            content: Contenido del mensaje.
            modality: Tipo de modalidad del mensaje.
            metadata: Metadatos adicionales del mensaje.

        Returns:
            Diccionario con la respuesta y metadatos de procesamiento.
        """
        try:
            # Crear o actualizar contexto de conversación
            context = self._get_or_create_context(conversation_id, user_id)
            
            # Crear mensaje del usuario
            user_message = Message(
                content=content,
                modality=modality,
                timestamp=datetime.now(),
                sender='user',
                metadata=metadata
            )
            
            # Analizar intención y sentimiento
            intent = self._detect_intent(content, modality)
            sentiment = self._analyze_sentiment(content)
            
            # Actualizar contexto
            context.messages.append(user_message)
            context.current_intent = intent
            context.sentiment_score = sentiment
            context.updated_at = datetime.now()
            
            # Generar respuesta
            response_content = self._generate_response(content, intent, sentiment, context)
            
            # Crear mensaje de respuesta
            agent_message = Message(
                content=response_content,
                modality=modality,
                timestamp=datetime.now(),
                sender='agent',
                metadata={'intent': intent.value, 'sentiment': sentiment}
            )
            context.messages.append(agent_message)
            
            # Mantener límite de mensajes en contexto
            self._trim_context(context)
            
            logger.info(f"Procesado mensaje en conversación {conversation_id}")
            
            return {
                'success': True,
                'response': response_content,
                'intent': intent.value,
                'sentiment': sentiment,
                'conversation_id': conversation_id
            }
            
        except Exception as e:
            logger.error(f"Error procesando mensaje: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'response': "Lo siento, hubo un error procesando tu mensaje."
            }

    def _get_or_create_context(
        self,
        conversation_id: str,
        user_id: str
    ) -> ConversationContext:
        """Obtiene o crea un contexto de conversación."""
        if conversation_id in self._conversations:
            return self._conversations[conversation_id]
        
        context = ConversationContext(
            conversation_id=conversation_id,
            user_id=user_id,
            messages=[],
            current_intent=None,
            sentiment_score=0.0,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        self._conversations[conversation_id] = context
        return context

    def _detect_intent(self, content: str, modality: ModalityType) -> IntentType:
        """
        Detecta la intención del mensaje.
        
        Args:
            content: Contenido del mensaje.
            modality: Modalidad del mensaje.
            
        Returns:
            IntentType detectado.
        """
        content_lower = content.lower()
        
        # Palabras clave para detección básica de intención
        inquiry_keywords = ['pregunta', 'información', 'cómo', 'qué', 'cuándo', 'dónde']
        support_keywords = ['ayuda', 'soporte', 'problema', 'error', 'no funciona']
        sales_keywords = ['comprar', 'precio', 'cotización', 'oferta', 'venta']
        complaint_keywords = ['queja', 'reclamo', 'malo', 'pésimo', 'insatisfecho']
        feedback_keywords = ['opinión', 'comentario', 'sugerencia', 'recomendación']
        
        for keyword in inquiry_keywords:
            if keyword in content_lower:
                return IntentType.INQUIRY
        
        for keyword in support_keywords:
            if keyword in content_lower:
                return IntentType.SUPPORT
        
        for keyword in sales_keywords:
            if keyword in content_lower:
                return IntentType.SALES
        
        for keyword in complaint_keywords:
            if keyword in content_lower:
                return IntentType.COMPLAINT
        
        for keyword in feedback_keywords:
            if keyword in content_lower:
                return IntentType.FEEDBACK
        
        return IntentType.GENERAL

    def _analyze_sentiment(self, content: str) -> float:
        """
        Analiza el sentimiento del mensaje.
        
        Args:
            content: Contenido del mensaje.
            
        Returns:
            Score de sentimiento entre -1.0 (negativo) y 1.0 (positivo).
        """
        positive_words = ['bueno', 'excelente', 'genial', 'feliz', 'satisfecho', 
                         'gracias', 'perfecto', 'increíble', 'maravilloso']
        negative_words = ['malo', 'terrible', 'horrible', 'triste', 'enojado',
                         'problema', 'error', 'pésimo', 'decepcionado']
        
        content_lower = content.lower()
        
        positive_count = sum(1 for word in positive_words if word in content_lower)
        negative_count = sum(1 for word in negative_words if word in content_lower)
        
        total = positive_count + negative_count
        if total == 0:
            return 0.0
        
        score = (positive_count - negative_count) / total
        return max(-1.0, min(1.0, score))

    def _generate_response(
        self,
        content: str,
        intent: IntentType,
        sentiment: float,
        context: ConversationContext
    ) -> str:
        """
        Genera una respuesta basada en el contenido, intención y sentimiento.
        
        Args:
            content: Contenido del mensaje original.
            intent: Intención detectada.
            sentiment: Score de sentimiento.
            context: Contexto de la conversación.
            
        Returns:
            Respuesta generada.
        """
        # Respuestas basadas en intención
        responses = {
            IntentType.INQUIRY: "Gracias por tu pregunta. Estoy analizando la información para brindarte la mejor respuesta.",
            IntentType.SUPPORT: "Entiendo que necesitas ayuda. Voy a asistirte para resolver este problema lo antes posible.",
            IntentType.SALES: "¡Excelente interés! Permíteme proporcionarte información detallada sobre nuestras opciones.",
            IntentType.COMPLAINT: "Lamento escuchar sobre tu experiencia negativa. Tomaré acciones inmediatas para resolverlo.",
            IntentType.FEEDBACK: "Agradezco mucho tus comentarios. Son muy valiosos para mejorar nuestro servicio.",
            IntentType.GENERAL: "Gracias por tu mensaje. ¿En qué más puedo ayudarte hoy?",
        }
        
        base_response = responses.get(intent, responses[IntentType.GENERAL])
        
        # Ajustar tono basado en sentimiento
        if sentiment < -0.5:
            base_response = "Entiendo tu frustración. " + base_response
        elif sentiment > 0.5:
            base_response = "¡Me alegra saber eso! " + base_response
        
        return base_response

    def _trim_context(self, context: ConversationContext):
        """Mantiene el contexto dentro del límite máximo de mensajes."""
        max_messages = self.settings['max_context_length'] * 2  # Mensajes de usuario + agente
        if len(context.messages) > max_messages:
            context.messages = context.messages[-max_messages:]

    def get_conversation_history(
        self,
        conversation_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Obtiene el historial de una conversación.
        
        Args:
            conversation_id: ID de la conversación.
            
        Returns:
            Lista de mensajes o None si no existe.
        """
        if conversation_id not in self._conversations:
            return None
        
        context = self._conversations[conversation_id]
        return [
            {
                'content': msg.content,
                'modality': msg.modality.value,
                'timestamp': msg.timestamp.isoformat(),
                'sender': msg.sender,
                'metadata': msg.metadata
            }
            for msg in context.messages
        ]

    def end_conversation(self, conversation_id: str) -> bool:
        """
        Finaliza una conversación y libera recursos.
        
        Args:
            conversation_id: ID de la conversación.
            
        Returns:
            True si se eliminó correctamente, False si no existía.
        """
        if conversation_id in self._conversations:
            del self._conversations[conversation_id]
            logger.info(f"Conversación {conversation_id} finalizada")
            return True
        return False

    def process_voice_input(self, audio_data: bytes) -> str:
        """
        Procesa entrada de voz y la convierte a texto.
        
        Args:
            audio_data: Datos de audio en bytes.
            
        Returns:
            Texto transcrito.
        """
        if not self.settings['enable_voice_processing']:
            raise ValueError("Procesamiento de voz deshabilitado")
        
        # Placeholder para integración con servicio de speech-to-text
        logger.info("Procesando entrada de voz...")
        return "[Audio transcrito]"

    def analyze_image(self, image_data: bytes) -> Dict[str, Any]:
        """
        Analiza una imagen y extrae información relevante.
        
        Args:
            image_data: Datos de imagen en bytes.
            
        Returns:
            Diccionario con análisis de la imagen.
        """
        if not self.settings['enable_image_analysis']:
            raise ValueError("Análisis de imágenes deshabilitado")
        
        # Placeholder para integración con servicio de visión computacional
        logger.info("Analizando imagen...")
        return {
            'labels': [],
            'text_detected': '',
            'objects': []
        }
