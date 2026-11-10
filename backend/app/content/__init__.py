"""
Content Intelligence Package.

Provides topic discovery, evaluation, research, primary source fact-verification,
duplicate detection, and 50-60 second production script generation for YouTube Shorts.
"""

from backend.app.content.duplicate_detector import DuplicateDetector, normalize_title, extract_keywords
from backend.app.content.fact_verifier import FactVerifier, ResearchDossier, FactualClaim, ClaimType
from backend.app.content.topic_engine import TopicEngine, TOPIC_CATEGORIES
from backend.app.content.script_generator import ScriptGenerator, ProductionScript, ScriptSceneBeat

__all__ = [
    "DuplicateDetector",
    "normalize_title",
    "extract_keywords",
    "FactVerifier",
    "ResearchDossier",
    "FactualClaim",
    "ClaimType",
    "TopicEngine",
    "TOPIC_CATEGORIES",
    "ScriptGenerator",
    "ProductionScript",
    "ScriptSceneBeat",
]
