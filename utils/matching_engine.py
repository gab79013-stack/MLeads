"""
Matching Engine Module

Provides functionality for matching transactions, orders, or entities
based on configurable criteria and algorithms.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, Callable
from datetime import datetime
from enum import Enum
import hashlib


class MatchStatus(Enum):
    """Status of a match operation."""
    PENDING = "pending"
    MATCHED = "matched"
    PARTIAL = "partial"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class MatchType(Enum):
    """Type of matching algorithm."""
    EXACT = "exact"
    FUZZY = "fuzzy"
    RANGE = "range"
    CUSTOM = "custom"


@dataclass
class MatchResult:
    """Represents the result of a match operation."""
    entity_a_id: str
    entity_b_id: str
    score: float
    status: MatchStatus
    match_type: MatchType
    matched_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "entity_a_id": self.entity_a_id,
            "entity_b_id": self.entity_b_id,
            "score": self.score,
            "status": self.status.value,
            "match_type": self.match_type.value,
            "matched_at": self.matched_at.isoformat(),
            "metadata": self.metadata
        }


@dataclass
class MatchConfig:
    """Configuration for matching operations."""
    match_type: MatchType = MatchType.EXACT
    threshold: float = 0.8
    max_matches: int = 10
    time_window_seconds: Optional[int] = None
    custom_criteria: Optional[Callable] = None
    case_sensitive: bool = False
    ignore_whitespace: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "match_type": self.match_type.value,
            "threshold": self.threshold,
            "max_matches": self.max_matches,
            "time_window_seconds": self.time_window_seconds,
            "case_sensitive": self.case_sensitive,
            "ignore_whitespace": self.ignore_whitespace
        }


class MatchingEngine:
    """
    Engine for matching entities based on configurable criteria.
    
    Supports multiple matching algorithms including exact, fuzzy, 
    range-based, and custom matching strategies.
    """
    
    def __init__(self, config: Optional[MatchConfig] = None):
        """
        Initialize the matching engine.
        
        Args:
            config: Configuration for matching operations
        """
        self.config = config or MatchConfig()
        self._pending_entities: Dict[str, Dict[str, Any]] = {}
        self._matches: List[MatchResult] = []
        self._match_history: List[Dict[str, Any]] = []
    
    def add_entity(self, entity_id: str, entity_data: Dict[str, Any]) -> None:
        """
        Add an entity to the pending pool for matching.
        
        Args:
            entity_id: Unique identifier for the entity
            entity_data: Dictionary containing entity attributes
        """
        self._pending_entities[entity_id] = {
            "data": entity_data,
            "added_at": datetime.now(),
            "status": MatchStatus.PENDING
        }
    
    def remove_entity(self, entity_id: str) -> bool:
        """
        Remove an entity from the pending pool.
        
        Args:
            entity_id: ID of the entity to remove
            
        Returns:
            True if entity was removed, False if not found
        """
        if entity_id in self._pending_entities:
            del self._pending_entities[entity_id]
            return True
        return False
    
    def find_matches(
        self, 
        entity_id: str, 
        config: Optional[MatchConfig] = None
    ) -> List[MatchResult]:
        """
        Find matches for a specific entity.
        
        Args:
            entity_id: ID of the entity to find matches for
            config: Optional configuration override
            
        Returns:
            List of MatchResult objects
        """
        config = config or self.config
        
        if entity_id not in self._pending_entities:
            return []
        
        target_entity = self._pending_entities[entity_id]["data"]
        matches = []
        
        for candidate_id, candidate_info in self._pending_entities.items():
            if candidate_id == entity_id:
                continue
            
            candidate_data = candidate_info["data"]
            
            # Check time window if configured
            if config.time_window_seconds:
                time_diff = abs(
                    (datetime.now() - candidate_info["added_at"]).total_seconds()
                )
                if time_diff > config.time_window_seconds:
                    continue
            
            # Calculate match score based on match type
            score = self._calculate_score(
                target_entity, 
                candidate_data, 
                config
            )
            
            if score >= config.threshold:
                status = MatchStatus.MATCHED if score == 1.0 else MatchStatus.PARTIAL
                
                match_result = MatchResult(
                    entity_a_id=entity_id,
                    entity_b_id=candidate_id,
                    score=score,
                    status=status,
                    match_type=config.match_type,
                    metadata={
                        "matched_fields": self._get_matched_fields(
                            target_entity, 
                            candidate_data, 
                            config
                        )
                    }
                )
                
                matches.append(match_result)
                
                if len(matches) >= config.max_matches:
                    break
        
        # Sort by score descending
        matches.sort(key=lambda x: x.score, reverse=True)
        
        return matches
    
    def match_all(self, config: Optional[MatchConfig] = None) -> List[MatchResult]:
        """
        Find matches for all pending entities.
        
        Args:
            config: Optional configuration override
            
        Returns:
            List of all MatchResult objects
        """
        config = config or self.config
        all_matches = []
        processed_pairs = set()
        
        for entity_id in self._pending_entities.keys():
            matches = self.find_matches(entity_id, config)
            
            for match in matches:
                # Avoid duplicate pairs (A-B and B-A)
                pair_key = tuple(sorted([match.entity_a_id, match.entity_b_id]))
                if pair_key not in processed_pairs:
                    processed_pairs.add(pair_key)
                    all_matches.append(match)
                    self._matches.append(match)
        
        self._match_history.append({
            "timestamp": datetime.now(),
            "match_count": len(all_matches),
            "config": config.to_dict()
        })
        
        return all_matches
    
    def _calculate_score(
        self, 
        entity_a: Dict[str, Any], 
        entity_b: Dict[str, Any], 
        config: MatchConfig
    ) -> float:
        """
        Calculate match score between two entities.
        
        Args:
            entity_a: First entity data
            entity_b: Second entity data
            config: Match configuration
            
        Returns:
            Score between 0.0 and 1.0
        """
        if config.match_type == MatchType.EXACT:
            return self._exact_match_score(entity_a, entity_b, config)
        elif config.match_type == MatchType.FUZZY:
            return self._fuzzy_match_score(entity_a, entity_b, config)
        elif config.match_type == MatchType.RANGE:
            return self._range_match_score(entity_a, entity_b, config)
        elif config.match_type == MatchType.CUSTOM:
            if config.custom_criteria:
                return config.custom_criteria(entity_a, entity_b)
            return 0.0
        
        return 0.0
    
    def _exact_match_score(
        self, 
        entity_a: Dict[str, Any], 
        entity_b: Dict[str, Any], 
        config: MatchConfig
    ) -> float:
        """Calculate exact match score."""
        common_keys = set(entity_a.keys()) & set(entity_b.keys())
        
        if not common_keys:
            return 0.0
        
        matches = 0
        for key in common_keys:
            val_a = entity_a[key]
            val_b = entity_b[key]
            
            # Handle string comparison
            if isinstance(val_a, str) and isinstance(val_b, str):
                if config.ignore_whitespace:
                    val_a = val_a.strip()
                    val_b = val_b.strip()
                if not config.case_sensitive:
                    val_a = val_a.lower()
                    val_b = val_b.lower()
            
            if val_a == val_b:
                matches += 1
        
        return matches / len(common_keys)
    
    def _fuzzy_match_score(
        self, 
        entity_a: Dict[str, Any], 
        entity_b: Dict[str, Any], 
        config: MatchConfig
    ) -> float:
        """Calculate fuzzy match score using simple similarity."""
        common_keys = set(entity_a.keys()) & set(entity_b.keys())
        
        if not common_keys:
            return 0.0
        
        total_similarity = 0.0
        
        for key in common_keys:
            val_a = str(entity_a[key])
            val_b = str(entity_b[key])
            
            if config.ignore_whitespace:
                val_a = val_a.strip()
                val_b = val_b.strip()
            if not config.case_sensitive:
                val_a = val_a.lower()
                val_b = val_b.lower()
            
            similarity = self._string_similarity(val_a, val_b)
            total_similarity += similarity
        
        return total_similarity / len(common_keys)
    
    def _range_match_score(
        self, 
        entity_a: Dict[str, Any], 
        entity_b: Dict[str, Any], 
        config: MatchConfig
    ) -> float:
        """Calculate range-based match score for numeric values."""
        common_keys = set(entity_a.keys()) & set(entity_b.keys())
        
        if not common_keys:
            return 0.0
        
        matches = 0
        numeric_keys = 0
        
        for key in common_keys:
            val_a = entity_a[key]
            val_b = entity_b[key]
            
            # Only consider numeric values for range matching
            if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
                numeric_keys += 1
                # Consider within 10% range as a match
                if val_a != 0:
                    diff_ratio = abs(val_a - val_b) / abs(val_a)
                    if diff_ratio <= 0.1:
                        matches += 1
                elif val_a == val_b:
                    matches += 1
        
        return matches / numeric_keys if numeric_keys > 0 else 0.0
    
    def _string_similarity(self, s1: str, s2: str) -> float:
        """
        Calculate string similarity using Levenshtein distance ratio.
        
        Args:
            s1: First string
            s2: Second string
            
        Returns:
            Similarity score between 0.0 and 1.0
        """
        if s1 == s2:
            return 1.0
        
        if not s1 or not s2:
            return 0.0
        
        # Simple character-based similarity
        len1, len2 = len(s1), len(s2)
        max_len = max(len1, len2)
        
        if max_len == 0:
            return 1.0
        
        # Count common characters
        common = sum(1 for c1, c2 in zip(s1, s2) if c1 == c2)
        
        return common / max_len
    
    def _get_matched_fields(
        self, 
        entity_a: Dict[str, Any], 
        entity_b: Dict[str, Any], 
        config: MatchConfig
    ) -> List[str]:
        """Get list of fields that matched between entities."""
        matched = []
        
        for key in set(entity_a.keys()) & set(entity_b.keys()):
            val_a = entity_a[key]
            val_b = entity_b[key]
            
            if isinstance(val_a, str) and isinstance(val_b, str):
                if config.ignore_whitespace:
                    val_a = val_a.strip()
                    val_b = val_b.strip()
                if not config.case_sensitive:
                    val_a = val_a.lower()
                    val_b = val_b.lower()
            
            if val_a == val_b:
                matched.append(key)
        
        return matched
    
    def get_matches(self) -> List[MatchResult]:
        """Get all recorded matches."""
        return self._matches.copy()
    
    def get_match_history(self) -> List[Dict[str, Any]]:
        """Get match operation history."""
        return self._match_history.copy()
    
    def clear(self) -> None:
        """Clear all pending entities and matches."""
        self._pending_entities.clear()
        self._matches.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the matching engine."""
        return {
            "pending_entities": len(self._pending_entities),
            "total_matches": len(self._matches),
            "match_operations": len(self._match_history),
            "config": self.config.to_dict()
        }


def match_entities(
    entities: List[Dict[str, Any]],
    config: Optional[MatchConfig] = None
) -> List[MatchResult]:
    """
    Convenience function to match a list of entities.
    
    Args:
        entities: List of entity dictionaries with 'id' key
        config: Optional match configuration
        
    Returns:
        List of MatchResult objects
    """
    engine = MatchingEngine(config)
    
    for entity in entities:
        entity_id = entity.get("id") or entity.get("entity_id")
        if entity_id:
            engine.add_entity(str(entity_id), entity)
    
    return engine.match_all()


def find_best_match(
    target: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    config: Optional[MatchConfig] = None
) -> Optional[MatchResult]:
    """
    Find the best match for a target entity among candidates.
    
    Args:
        target: Target entity dictionary
        candidates: List of candidate entity dictionaries
        config: Optional match configuration
        
    Returns:
        Best MatchResult or None if no matches found
    """
    engine = MatchingEngine(config)
    
    target_id = target.get("id") or target.get("entity_id") or "target"
    engine.add_entity(str(target_id), target)
    
    for i, candidate in enumerate(candidates):
        candidate_id = candidate.get("id") or candidate.get("entity_id") or f"candidate_{i}"
        engine.add_entity(str(candidate_id), candidate)
    
    matches = engine.find_matches(str(target_id))
    
    return matches[0] if matches else None


def generate_match_id(entity_a_id: str, entity_b_id: str) -> str:
    """
    Generate a unique ID for a match pair.
    
    Args:
        entity_a_id: First entity ID
        entity_b_id: Second entity ID
        
    Returns:
        Unique match ID string
    """
    sorted_ids = sorted([entity_a_id, entity_b_id])
    combined = f"{sorted_ids[0]}:{sorted_ids[1]}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]
