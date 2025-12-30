"""Semantic rule matching using sentence-transformers."""
import re
from pathlib import Path
from typing import List, Dict, Optional
import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False


class RuleMatcher:
    """Match coding rules semantically to affected symbols/files."""
    
    def __init__(self, rules_path: str, model_name: str = "all-MiniLM-L6-v2"):
        self.rules_path = Path(rules_path)
        self.rules: List[Dict] = []
        self.embeddings: Optional[np.ndarray] = None
        
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            self.model = SentenceTransformer(model_name)
        else:
            self.model = None
        
        self._load_rules()
    
    def _load_rules(self):
        """Load and parse rules from markdown file."""
        if not self.rules_path.exists():
            return
        
        content = self.rules_path.read_text()
        
        # Parse markdown rules - supports multiple formats
        # Format 1: Numbered rules (1. Rule text)
        # Format 2: Bullet rules (- Rule text)
        # Format 3: Header sections (## Section \n content)
        
        lines = content.split("\n")
        current_rule = None
        rule_id = 0
        
        for line in lines:
            line = line.strip()
            
            # Skip empty lines
            if not line:
                if current_rule:
                    self.rules.append(current_rule)
                    current_rule = None
                continue
            
            # Numbered rule
            num_match = re.match(r'^(\d+)\.\s+(.+)', line)
            if num_match:
                if current_rule:
                    self.rules.append(current_rule)
                rule_id = int(num_match.group(1))
                current_rule = {
                    "id": rule_id,
                    "text": num_match.group(2),
                    "keywords": self._extract_keywords(num_match.group(2))
                }
                continue
            
            # Bullet rule
            bullet_match = re.match(r'^[-*]\s+(.+)', line)
            if bullet_match:
                if current_rule:
                    self.rules.append(current_rule)
                rule_id += 1
                current_rule = {
                    "id": rule_id,
                    "text": bullet_match.group(1),
                    "keywords": self._extract_keywords(bullet_match.group(1))
                }
                continue
            
            # Continue previous rule
            if current_rule and not line.startswith("#"):
                current_rule["text"] += " " + line
        
        if current_rule:
            self.rules.append(current_rule)
        
        # Build embeddings
        if self.model and self.rules:
            rule_texts = [r["text"] for r in self.rules]
            self.embeddings = self.model.encode(rule_texts, convert_to_numpy=True)
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extract important keywords from rule text."""
        # Common code-related terms to look for
        keywords = []
        text_lower = text.lower()
        
        # File patterns
        file_patterns = re.findall(r'\b(\w+\.py)\b', text_lower)
        keywords.extend(file_patterns)
        
        # Class/function patterns
        code_patterns = re.findall(r'`([^`]+)`', text)
        keywords.extend(code_patterns)
        
        # Important terms
        important = ["payment", "user", "auth", "database", "api", "test", 
                    "security", "atomic", "transaction", "validation", "error"]
        for term in important:
            if term in text_lower:
                keywords.append(term)
        
        return keywords
    
    def match_rules(self, context: Dict, top_k: int = 5, threshold: float = 0.3) -> List[Dict]:
        """Find rules relevant to the given context."""
        if not self.rules:
            return []
        
        # Build query from context
        query_parts = []
        
        if "symbol" in context:
            query_parts.append(context["symbol"])
        
        if "affected_files" in context:
            query_parts.extend(context["affected_files"])
        
        if "dependents" in context:
            query_parts.extend(context["dependents"][:5])
        
        query = " ".join(query_parts)
        
        if self.model and self.embeddings is not None:
            return self._semantic_match(query, top_k, threshold)
        else:
            return self._keyword_match(context, top_k)
    
    def _semantic_match(self, query: str, top_k: int, threshold: float) -> List[Dict]:
        """Use embeddings for semantic matching."""
        query_embedding = self.model.encode([query], convert_to_numpy=True)[0]
        
        # Cosine similarity
        similarities = np.dot(self.embeddings, query_embedding) / (
            np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query_embedding)
        )
        
        # Get top matches above threshold
        top_indices = np.argsort(similarities)[::-1][:top_k]
        
        matched = []
        for idx in top_indices:
            if similarities[idx] >= threshold:
                rule = self.rules[idx].copy()
                rule["relevance_score"] = float(similarities[idx])
                matched.append(rule)
        
        return matched
    
    def _keyword_match(self, context: Dict, top_k: int) -> List[Dict]:
        """Fallback keyword matching."""
        context_text = str(context).lower()
        
        scored_rules = []
        for rule in self.rules:
            score = 0
            for keyword in rule.get("keywords", []):
                if keyword.lower() in context_text:
                    score += 1
            if score > 0:
                rule_copy = rule.copy()
                rule_copy["relevance_score"] = score / max(len(rule.get("keywords", [])), 1)
                scored_rules.append(rule_copy)
        
        scored_rules.sort(key=lambda x: x["relevance_score"], reverse=True)
        return scored_rules[:top_k]
    
    def get_all_rules(self) -> List[Dict]:
        """Return all loaded rules."""
        return self.rules
