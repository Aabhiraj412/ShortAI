"""
Permanent Shared Creative Intelligence & Visual Acquisition Policy for CrazzyDocs.
Phase 5, Stage 3.21M Standard.

Guarantees IDENTICAL creative standards, multi-category discovery, 8-dimension scoring,
visual acquisition hierarchy (Priority A -> B -> C -> D), licensing rules, storyboard
intelligence, and QC criteria across BOTH ExecutionContext.VALIDATION and
ExecutionContext.PRODUCTION. Only operational permissions differ between contexts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.app.core.execution_context import ExecutionContext
from backend.app.core.paths import PROJECT_ROOT_STR as WORKSPACE_ROOT

DEFAULT_POLICY_CONFIG_PATH = os.path.join(WORKSPACE_ROOT, "backend", "data", "creative_policy_config.json")
DEFAULT_CATEGORY_DIST_PATH = os.path.join(WORKSPACE_ROOT, "backend", "data", "category_distribution.json")


@dataclass
class CategorySpec:
    """Extensible definition for a topic category, its discovery queries, and storytelling arc."""
    key: str
    display_name: str
    category_code: str
    search_queries: List[str]
    keywords: List[str]
    preferred_providers: List[str]
    narrative_arc: List[str]
    visual_affinities: List[str]
    accent_rgb: Tuple[int, int, int] = (56, 189, 248)


@dataclass
class TopicScoringWeights:
    """Configurable weights for transparent 8-dimension topic scoring (sum normalized to 1.0)."""
    curiosity: float = 0.18
    novelty: float = 0.14
    storytelling: float = 0.15
    factual_reliability: float = 0.14
    visual_availability: float = 0.14
    audience_relevance: float = 0.09
    short_form_suitability: float = 0.08
    freshness: float = 0.08

    def normalized(self) -> Dict[str, float]:
        raw = {
            "curiosity": max(0.0, float(self.curiosity)),
            "novelty": max(0.0, float(self.novelty)),
            "storytelling": max(0.0, float(self.storytelling)),
            "factual_reliability": max(0.0, float(self.factual_reliability)),
            "visual_availability": max(0.0, float(self.visual_availability)),
            "audience_relevance": max(0.0, float(self.audience_relevance)),
            "short_form_suitability": max(0.0, float(self.short_form_suitability)),
            "freshness": max(0.0, float(self.freshness)),
        }
        tot = sum(raw.values()) or 1.0
        return {k: round(v / tot, 6) for k, v in raw.items()}


@dataclass
class VisualAcquisitionPolicy:
    """Identical visual sourcing hierarchy and licensing policy across all execution contexts."""
    priority_order: Tuple[str, ...] = (
        "A_AUTHENTIC_EXISTING",
        "B_OPEN_SOURCE_ANIMATION",
        "C_ANIMATED_AUTHENTIC",
        "D_SELECTIVE_AI_GENERATION",
    )
    max_image_reuse: int = 2
    min_distinct_assets: int = 12
    min_authentic_or_open_anim_when_available: int = 4
    require_illustrative_label_for_ai: bool = True
    allow_share_alike: bool = False
    allow_non_commercial: bool = False
    allow_no_derivatives: bool = False
    reject_unknown_licenses: bool = True
    prefer_authentic_over_generation: bool = True


@dataclass
class OperationalPermissions:
    """Operational permissions partitioned strictly by ExecutionContext (never creative rules)."""
    execution_context: str
    allow_youtube_upload: bool
    allow_production_ledger_writes: bool
    allow_production_notifications: bool
    allow_scheduler_dispatch: bool
    allow_backlog_recovery: bool


DEFAULT_CATEGORY_SPECS: List[CategorySpec] = [
    CategorySpec(
        key="history",
        display_name="History",
        category_code="HIST",
        search_queries=["pivotal historical event archival record", "forgotten expedition disaster history"],
        keywords=["century", "war", "treaty", "emperor", "kingdom", "expedition", "historical", "era", "revolution"],
        preferred_providers=["Wikimedia Commons", "Internet Archive", "Openverse"],
        narrative_arc=["incident", "context", "escalation", "consequence"],
        visual_affinities=["authentic_photo", "map", "newspaper_archive", "animated_authentic"],
        accent_rgb=(255, 82, 82),
    ),
    CategorySpec(
        key="science",
        display_name="Science",
        category_code="SCI",
        search_queries=["breakthrough physics experiment discovery", "unexpected scientific phenomenon"],
        keywords=["physics", "quantum", "chemistry", "molecule", "experiment", "hypothesis", "laboratory", "particle", "thermodynamics", "radiation"],
        preferred_providers=["Wikimedia Commons", "Openverse", "Internet Archive"],
        narrative_arc=["question", "scientific_principle", "experiment_evidence", "revelation"],
        visual_affinities=["scientific_diagram", "authentic_photo", "existing_animation", "animated_authentic"],
        accent_rgb=(56, 189, 248),
    ),
    CategorySpec(
        key="technology",
        display_name="Technology",
        category_code="TECH",
        search_queries=["computing hardware semiconductor breakthrough", "communication network invention history"],
        keywords=["transistor", "semiconductor", "microchip", "computing", "laser", "fiber optic", "radar", "hardware", "telemetry", "sensor"],
        preferred_providers=["Wikimedia Commons", "Openverse", "Internet Archive"],
        narrative_arc=["problem", "innovation", "mechanism", "impact"],
        visual_affinities=["authentic_photo", "diagram", "animated_authentic", "existing_animation"],
        accent_rgb=(56, 189, 248),
    ),
    CategorySpec(
        key="artificial_intelligence",
        display_name="Artificial Intelligence",
        category_code="AI",
        search_queries=["neural network transformer machine learning", "deep learning computer vision milestone"],
        keywords=["artificial intelligence", "neural network", "machine learning", "deep learning", "transformer", "algorithm", "alphago", "language model", "perceptron"],
        preferred_providers=["Wikimedia Commons", "Openverse"],
        narrative_arc=["problem", "innovation", "mechanism", "impact"],
        visual_affinities=["diagram", "animated_diagram", "existing_animation", "generated_illustration"],
        accent_rgb=(168, 139, 250),
    ),
    CategorySpec(
        key="psychology",
        display_name="Psychology",
        category_code="PSY",
        search_queries=["cognitive bias perception experiment psychology", "human memory illusion psychology study"],
        keywords=["psychology", "cognitive", "perception", "memory", "placebo", "bystander", "conditioning", "illusion", "neuroscience", "behavior"],
        preferred_providers=["Wikimedia Commons", "Openverse"],
        narrative_arc=["relatable_phenomenon", "explanation", "evidence", "implication"],
        visual_affinities=["scientific_visualization", "diagram", "authentic_photo", "generated_illustration"],
        accent_rgb=(168, 139, 250),
    ),
    CategorySpec(
        key="human_biology",
        display_name="Human Biology",
        category_code="BIO",
        search_queries=["human anatomy physiology cellular discovery", "immune system circadian rhythm biology"],
        keywords=["cell", "dna", "genome", "neuron", "synapse", "mitochondria", "anatomy", "circadian", "hemoglobin", "physiology"],
        preferred_providers=["Wikimedia Commons", "Openverse"],
        narrative_arc=["question", "biological_mechanism", "evidence", "revelation"],
        visual_affinities=["scientific_diagram", "existing_animation", "authentic_photo", "animated_authentic"],
        accent_rgb=(34, 211, 153),
    ),
    CategorySpec(
        key="medical_science",
        display_name="Medical Science",
        category_code="MED",
        search_queries=["vaccine antibiotic surgical medical discovery", "medical imaging diagnostic breakthrough"],
        keywords=["vaccine", "antibiotic", "penicillin", "surgery", "pathology", "clinical", "epidemiology", "mri", "insulin", "medicine"],
        preferred_providers=["Wikimedia Commons", "Internet Archive", "Openverse"],
        narrative_arc=["medical_challenge", "discovery", "clinical_evidence", "lifesaving_impact"],
        visual_affinities=["authentic_photo", "medical_illustration", "existing_animation", "animated_authentic"],
        accent_rgb=(34, 211, 153),
    ),
    CategorySpec(
        key="nature_and_wildlife",
        display_name="Nature and Wildlife",
        category_code="NAT",
        search_queries=["animal migration bioluminescence adaptation", "deep sea creature extremism biology"],
        keywords=["species", "migration", "bioluminescence", "octopus", "predator", "coral", "ecosystem", "evolution", "whale", "insect"],
        preferred_providers=["Wikimedia Commons", "Openverse", "Internet Archive"],
        narrative_arc=["unusual_behavior", "biological_mechanism", "surprising_adaptation", "ecological_payoff"],
        visual_affinities=["authentic_photo", "map", "existing_animation", "animated_authentic"],
        accent_rgb=(34, 211, 153),
    ),
    CategorySpec(
        key="space_and_astronomy",
        display_name="Space and Astronomy",
        category_code="SPACE",
        search_queries=["nasa space telescope exoplanet black hole", "voyager cassini planetary probe mission"],
        keywords=["nasa", "telescope", "exoplanet", "black hole", "pulsar", "nebula", "voyager", "orbit", "spacecraft", "apollo", "mars", "jupiter", "saturn"],
        preferred_providers=["NASA Image Library", "Wikimedia Commons", "Openverse"],
        narrative_arc=["discovery", "scale", "mechanism", "scientific_implication"],
        visual_affinities=["authentic_photo", "scientific_visualization", "animated_map", "existing_animation"],
        accent_rgb=(56, 189, 248),
    ),
    CategorySpec(
        key="engineering",
        display_name="Engineering",
        category_code="ENG",
        search_queries=["structural engineering suspension bridge dam design", "aerospace propulsion turbine mechanics"],
        keywords=["turbine", "suspension bridge", "cantilever", "hydraulic", "propulsion", "structural", "resonance", "aerodynamics", "engine", "gyroscope"],
        preferred_providers=["Wikimedia Commons", "Internet Archive", "NASA Image Library"],
        narrative_arc=["constraint", "design_principle", "stress_test", "engineering_outcome"],
        visual_affinities=["engineering_diagram", "authentic_photo", "animated_authentic", "animated_diagram"],
        accent_rgb=(245, 194, 66),
    ),
    CategorySpec(
        key="geography",
        display_name="Geography",
        category_code="GEO",
        search_queries=["extreme geography rift valley trench canyon", "ocean current strait plate tectonics"],
        keywords=["trench", "rift valley", "strait", "plateau", "glacier", "tectonic", "archipelago", "basin", "desert", "latitude"],
        preferred_providers=["Wikimedia Commons", "NASA Image Library", "Openverse"],
        narrative_arc=["location", "geophysical_force", "scale", "global_effect"],
        visual_affinities=["map", "satellite_imagery", "animated_map", "animated_authentic"],
        accent_rgb=(34, 211, 153),
    ),
    CategorySpec(
        key="business_and_economics",
        display_name="Business and Economics",
        category_code="BIZ",
        search_queries=["industrial monopoly logistics innovation market shift", "financial crisis currency collapse case study"],
        keywords=["economy", "currency", "inflation", "supply chain", "semiconductor market", "bank", "exchange", "corporation", "trade", "patent"],
        preferred_providers=["Wikimedia Commons", "Internet Archive", "Openverse"],
        narrative_arc=["decision", "opportunity", "mistake_or_shift", "consequence"],
        visual_affinities=["authentic_photo", "data_visualization", "timeline", "animated_authentic"],
        accent_rgb=(245, 194, 66),
    ),
    CategorySpec(
        key="archaeology",
        display_name="Archaeology",
        category_code="ARCH",
        search_queries=["ancient artifact excavation decipherment discovery", "lidar archaeological site sunken city"],
        keywords=["archaeology", "excavation", "inscription", "cuneiform", "artifact", "bronze age", "antiquity", "ruins", "lidar", "fossil"],
        preferred_providers=["Wikimedia Commons", "Internet Archive", "Openverse"],
        narrative_arc=["artifact", "mystery", "excavation_clue", "historical_revelation"],
        visual_affinities=["authentic_photo", "map", "animated_authentic", "timeline"],
        accent_rgb=(245, 194, 66),
    ),
    CategorySpec(
        key="inventions_and_discoveries",
        display_name="Inventions and Discoveries",
        category_code="INV",
        search_queries=["accidental scientific invention serendipity discovery", "industrial patent mechanism breakthrough"],
        keywords=["invented", "patent", "accidental discovery", "prototype", "compass", "printing press", "vulcanized", "x-ray", "telemetry"],
        preferred_providers=["Wikimedia Commons", "Internet Archive", "Openverse"],
        narrative_arc=["need_or_accident", "insight", "prototype", "world_impact"],
        visual_affinities=["authentic_photo", "diagram", "animated_authentic", "timeline"],
        accent_rgb=(245, 194, 66),
    ),
    CategorySpec(
        key="cybersecurity",
        display_name="Cybersecurity",
        category_code="CYB",
        search_queries=["cryptography cipher zero day network vulnerability", "encryption public key security incident"],
        keywords=["cryptography", "encryption", "cipher", "vulnerability", "zero-day", "malware", "firewall", "rsa", "ransomware", "exploit"],
        preferred_providers=["Wikimedia Commons", "Openverse"],
        narrative_arc=["threat_vector", "technical_flaw", "exploit_or_defense", "security_lesson"],
        visual_affinities=["diagram", "animated_diagram", "authentic_photo", "generated_illustration"],
        accent_rgb=(255, 82, 82),
    ),
    CategorySpec(
        key="mathematics",
        display_name="Mathematics",
        category_code="MATH",
        search_queries=["mathematical paradox prime number theorem geometry", "game theory chaos probability paradox"],
        keywords=["theorem", "paradox", "fractal", "prime number", "topology", "probability", "fibonacci", "euclidean", "calculus", "bayes"],
        preferred_providers=["Wikimedia Commons", "Openverse"],
        narrative_arc=["paradox_or_puzzle", "intuition_trap", "proof_principle", "real_world_payoff"],
        visual_affinities=["diagram", "existing_animation", "data_visualization", "generated_illustration"],
        accent_rgb=(168, 139, 250),
    ),
    CategorySpec(
        key="everyday_science",
        display_name="Everyday Science",
        category_code="EVSCI",
        search_queries=["physics of everyday objects fluid dynamics sound", "materials science glass soap acoustic resonance"],
        keywords=["surface tension", "refraction", "doppler", "friction", "evaporation", "acoustics", "fermentation", "microwave", "crystallization"],
        preferred_providers=["Wikimedia Commons", "Openverse"],
        narrative_arc=["familiar_observation", "hidden_physics", "demonstration", "surprising_takeaway"],
        visual_affinities=["authentic_photo", "existing_animation", "animated_diagram", "generated_illustration"],
        accent_rgb=(56, 189, 248),
    ),
    CategorySpec(
        key="unusual_phenomena",
        display_name="Unusual Phenomena",
        category_code="PHEN",
        search_queries=["rare atmospheric lightning sprite optical phenomenon", "unusual acoustic geomagnetic anomaly"],
        keywords=["sprite", "aurora", "ball lightning", "mirage", "rogue wave", "bioluminescent", "resonance", "acoustic anomaly", "meteorite"],
        preferred_providers=["Wikimedia Commons", "NASA Image Library", "Openverse"],
        narrative_arc=["strange_observation", "initial_doubt", "scientific_measurement", "explanation"],
        visual_affinities=["authentic_photo", "scientific_visualization", "animated_authentic", "map"],
        accent_rgb=(168, 139, 250),
    ),
    CategorySpec(
        key="architecture_and_megastructures",
        display_name="Architecture and Megastructures",
        category_code="ARCHM",
        search_queries=["supertall engineering tuned mass damper tunnel aqueduct", "underground particle collider observatory construction"],
        keywords=["skyscraper", "damper", "aqueduct", "viaduct", "dome", "tunnel boring", "hydroelectric", "megastructure", "foundation"],
        preferred_providers=["Wikimedia Commons", "Internet Archive", "Openverse"],
        narrative_arc=["scale_challenge", "structural_innovation", "construction_feat", "lasting_legacy"],
        visual_affinities=["authentic_photo", "engineering_diagram", "animated_authentic", "map"],
        accent_rgb=(245, 194, 66),
    ),
    CategorySpec(
        key="environment_and_climate",
        display_name="Environment and Climate",
        category_code="ENV",
        search_queries=["atmospheric jet stream ice core permafrost glacier", "ocean carbon sink ozone layer recovery"],
        keywords=["climate", "ice core", "permafrost", "ozone", "albedo", "jet stream", "coral bleaching", "carbon cycle", "methane", "monsoon"],
        preferred_providers=["NASA Image Library", "Wikimedia Commons", "Openverse"],
        narrative_arc=["environmental_signal", "mechanism", "measurement_record", "planetary_impact"],
        visual_affinities=["satellite_imagery", "map", "data_visualization", "animated_authentic"],
        accent_rgb=(34, 211, 153),
    ),
]


class CategoryDistributionTracker:
    """
    Persists recent category selections across validation and production runs so discovery
    avoids category repetition without enforcing rigid equal quotas.
    """

    def __init__(self, store_path: str = DEFAULT_CATEGORY_DIST_PATH):
        if store_path == DEFAULT_CATEGORY_DIST_PATH and os.environ.get("PYTEST_CURRENT_TEST"):
            import tempfile
            store_path = os.path.join(tempfile.gettempdir(), f"cd_pytest_cat_dist_{os.getpid()}.json")
        self.store_path = store_path

    def load_history(self) -> List[Dict[str, Any]]:
        if not os.path.isfile(self.store_path):
            return []
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("selections"), list):
                return data["selections"]
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    def record_selection(self, category: str, topic_title: str, execution_context: str, run_id: str) -> None:
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        history = self.load_history()
        history.append({
            "category": category,
            "topic_title": topic_title,
            "execution_context": execution_context,
            "run_id": run_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        history = history[-100:]
        tmp_path = f"{self.store_path}.{os.getpid()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"selections": history, "updated_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)
        os.replace(tmp_path, self.store_path)

    def recent_categories(self, window: int = 12) -> List[str]:
        return [str(item.get("category", "")) for item in self.load_history()[-window:] if item.get("category")]

    def get_distribution_counts(self, window: int = 20) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in self.load_history()[-window:]:
            cat = str(item.get("category", ""))
            if cat:
                counts[cat] = counts.get(cat, 0) + 1
        return counts

    def compute_soft_recency_penalty(self, category: str, recent_override: Optional[List[str]] = None) -> float:
        """
        Soft recency penalty (0.0 to 0.45 on a 0..10 scale) preventing clustering while letting
        exceptionally strong topics win regardless of category.
        """
        recent = recent_override if recent_override is not None else self.recent_categories(window=6)
        if not recent:
            return 0.0
        cat_norm = (category or "").strip().lower()
        penalty = 0.0
        for offset, past_cat in enumerate(reversed(recent)):
            if past_cat.strip().lower() == cat_norm:
                if offset == 0:
                    penalty += 0.35
                elif offset == 1:
                    penalty += 0.22
                else:
                    penalty += 0.10
        return round(min(0.45, penalty), 3)


class CreativeProductionPolicy:
    """Shared permanent creative policy governing topic discovery, visuals, storyboards, and QC."""

    def __init__(
        self,
        categories: Optional[List[CategorySpec]] = None,
        scoring_weights: Optional[TopicScoringWeights] = None,
        visual_policy: Optional[VisualAcquisitionPolicy] = None,
        config_path: str = DEFAULT_POLICY_CONFIG_PATH,
    ):
        self.config_path = config_path
        self.categories: Dict[str, CategorySpec] = {c.key: c for c in (categories or DEFAULT_CATEGORY_SPECS)}
        self.scoring_weights = scoring_weights or TopicScoringWeights()
        self.visual_policy = visual_policy or VisualAcquisitionPolicy()
        self._load_external_overrides()

    def _load_external_overrides(self) -> None:
        if not self.config_path or not os.path.isfile(self.config_path):
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            weights = cfg.get("scoring_weights")
            if isinstance(weights, dict):
                self.scoring_weights = TopicScoringWeights(**{
                    k: float(weights[k]) for k in TopicScoringWeights.__dataclass_fields__ if k in weights
                })
            for extra in cfg.get("custom_categories", []):
                if isinstance(extra, dict) and extra.get("display_name"):
                    self.register_category(CategorySpec(
                        key=extra.get("key") or re.sub(r"[^a-z0-9]+", "_", extra["display_name"].lower()).strip("_"),
                        display_name=extra["display_name"],
                        category_code=extra.get("category_code", "CUST"),
                        search_queries=list(extra.get("search_queries") or [extra["display_name"]]),
                        keywords=list(extra.get("keywords") or [extra["display_name"].lower()]),
                        preferred_providers=list(extra.get("preferred_providers") or ["Wikimedia Commons", "Openverse"]),
                        narrative_arc=list(extra.get("narrative_arc") or ["hook", "mechanism", "evidence", "impact"]),
                        visual_affinities=list(extra.get("visual_affinities") or ["authentic_photo", "diagram"]),
                        accent_rgb=tuple(extra.get("accent_rgb", (56, 189, 248)))[:3],
                    ))
        except Exception:
            pass

    def register_category(self, spec: CategorySpec) -> None:
        """Allows registering additional categories dynamically without touching core discovery code."""
        self.categories[spec.key] = spec

    def list_categories(self) -> List[CategorySpec]:
        return list(self.categories.values())

    def list_category_names(self) -> List[str]:
        return [c.display_name for c in self.categories.values()]

    def get_category(self, name_or_key: str) -> CategorySpec:
        norm = re.sub(r"[^a-z0-9]+", "_", (name_or_key or "").lower()).strip("_")
        if norm in self.categories:
            return self.categories[norm]
        for c in self.categories.values():
            if c.display_name.lower() == (name_or_key or "").strip().lower() or c.category_code.lower() == norm:
                return c
        # Legacy category aliases
        alias_map = {
            "science_technology": "science",
            "strange_but_true_history": "history",
            "corporate_financial_stories": "business_and_economics",
            "mysteries_unexplained_events": "unusual_phenomena",
            "unbelievable_real_life_stories": "human_biology",
        }
        mapped = alias_map.get(norm)
        if mapped and mapped in self.categories:
            return self.categories[mapped]
        return self.categories["science"]

    def classify_topic(self, title: str, text: str) -> CategorySpec:
        """Scores all registered categories against title/text without hardcoded historical bias."""
        title_l = (title or "").lower()
        body_l = (text or "").lower()
        best_spec = self.categories["science"]
        best_score = -1.0
        for spec in self.categories.values():
            hits = 0.0
            for kw in spec.keywords:
                kw_l = kw.lower()
                if kw_l in title_l:
                    hits += 3.0
                elif kw_l in body_l:
                    hits += 1.0
            if spec.display_name.lower() in f"{title_l} {body_l}":
                hits += 2.5
            if hits > best_score:
                best_score = hits
                best_spec = spec
        if best_score <= 0:
            # Neutral fallback rotates by content characteristics rather than always choosing History
            if any(w in body_l for w in ("research", "scientists", "study", "experiment", "university")):
                return self.categories["science"]
            return self.categories["everyday_science"]
        return best_spec

    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            "policy_version": "3.21M",
            "supported_categories_count": len(self.categories),
            "supported_categories": [c.display_name for c in self.categories.values()],
            "scoring_weights": self.scoring_weights.normalized(),
            "scoring_disclaimer": "Estimates storytelling and engagement suitability; does not predict or guarantee viral view counts.",
            "visual_priority_order": list(self.visual_policy.priority_order),
            "visual_policy": asdict(self.visual_policy),
            "target_duration_sec": [50.0, 58.0],
        }


_SHARED_POLICY = CreativeProductionPolicy()


def get_creative_production_policy(execution_context: Optional[ExecutionContext] = None) -> CreativeProductionPolicy:
    """
    Returns the EXACT SAME shared creative policy regardless of ExecutionContext
    (VALIDATION and PRODUCTION share identical creative requirements).
    """
    _ = execution_context
    return _SHARED_POLICY


def get_operational_permissions(execution_context: ExecutionContext) -> OperationalPermissions:
    """Only operational permissions (publishing, ledger mutations, alerts, scheduling) vary by context."""
    is_prod = execution_context == ExecutionContext.PRODUCTION
    return OperationalPermissions(
        execution_context=execution_context.value,
        allow_youtube_upload=is_prod,
        allow_production_ledger_writes=is_prod,
        allow_production_notifications=is_prod,
        allow_scheduler_dispatch=is_prod,
        allow_backlog_recovery=is_prod,
    )
