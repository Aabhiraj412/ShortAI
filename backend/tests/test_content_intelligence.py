"""
Automated Test Suite for Stage 1: Content Intelligence Subsystem.

Validates:
1. Topic Discovery across categories with multi-dimensional scoring.
2. Duplicate and semantic similarity detection against history.
3. Fact verification, primary source cross-checking, and speculative claim rejection.
4. 50-60 second production script generation (120-135 words, 5 narrative stages).
5. Script validation (word count, duration standard, intro fluff prohibition).
6. Offline resilience (zero API key dependency, deterministic curated fallbacks).
"""

import asyncio
import unittest

from backend.app.content.duplicate_detector import DuplicateDetector, normalize_title, extract_keywords
from backend.app.content.fact_verifier import FactVerifier, ClaimType
from backend.app.content.topic_engine import TopicEngine, TOPIC_CATEGORIES
from backend.app.content.script_generator import ScriptGenerator, ProductionScript, FORBIDDEN_INTRO_PHRASES
from backend.app.rendering.styles.style_system import VisualStyle
from backend.app.rendering.duration_standards import (
    PRODUCTION_MAX_DURATION_SEC,
    PRODUCTION_MIN_DURATION_SEC,
    TARGET_SCRIPT_WORD_COUNT_MAX,
    TARGET_SCRIPT_WORD_COUNT_MIN,
)


class TestDuplicateDetector(unittest.TestCase):
    """Tests semantic duplicate detection and normalization."""

    def setUp(self):
        self.detector = DuplicateDetector(similarity_threshold=0.60)

    def test_exact_match(self):
        title = "What Happens If You Fall Into a Black Hole?"
        sim = self.detector.compute_similarity(title, title)
        self.assertEqual(sim, 1.0)
        is_dup, max_sim, match = self.detector.is_duplicate(title, [title])
        self.assertTrue(is_dup)
        self.assertEqual(max_sim, 1.0)

    def test_near_duplicate_detection(self):
        title1 = "Why Milk Is Always at the Back of the Grocery Store"
        title2 = "Why Grocery Stores Put Milk in the Far Back"
        is_dup, sim, match = self.detector.is_duplicate(title1, [title2])
        self.assertTrue(is_dup, f"Expected duplicate detection, got sim: {sim}")
        self.assertGreaterEqual(sim, 0.55)

    def test_distinct_topics_pass(self):
        title1 = "What Happens If You Fall Into a Black Hole?"
        title2 = "Why Solar Energy Became the Cheapest Power in History"
        is_dup, sim, _ = self.detector.is_duplicate(title1, [title2])
        self.assertFalse(is_dup)
        self.assertLess(sim, 0.25)


class TestFactVerifier(unittest.TestCase):
    """Tests research gathering, primary source verification, and speculation detection."""

    def setUp(self):
        self.verifier = FactVerifier()

    def test_authoritative_ground_truth_verification(self):
        dossier = self.verifier.research_topic(
            topic_title="Why Does the Moon Not Fall to Earth?",
            category="Space & Astrophysics",
            summary="Orbital velocity and perpetual free fall."
        )
        self.assertEqual(dossier.verification_status, "VERIFIED")
        self.assertGreaterEqual(len(dossier.claims), 2)
        # Check that primary sources are NASA / institutional
        for claim in dossier.claims:
            self.assertTrue(claim.verified)
            self.assertTrue("NASA" in claim.source_name or "NASA" in claim.institution)
            self.assertGreater(claim.confidence, 0.90)

    def test_solar_energy_cost_verification(self):
        dossier = self.verifier.research_topic(
            topic_title="Why Solar Energy Became the Cheapest Power in History",
            category="Business & Global Economics",
            summary="LCOE reduction from $359 to $38 per MWh."
        )
        self.assertEqual(dossier.verification_status, "VERIFIED")
        has_iea = any("IEA" in c.source_name or "International Energy Agency" in c.institution for c in dossier.claims)
        self.assertTrue(has_iea, "Expected IEA primary source citation in solar energy dossier.")

    def test_speculative_claim_rejection(self):
        dossier = self.verifier.research_topic(
            topic_title="Secret Alien Coverup: Miracle Energy Free from the Government",
            category="Bizarre Science & Physics",
            summary="Conspiracy that defies all laws of physics."
        )
        self.assertEqual(dossier.verification_status, "REJECTED")
        self.assertIn("speculative", dossier.verification_notes.lower())

    def test_db_persistence(self):
        dossier = self.verifier.research_topic(
            topic_title="The Ocean Trapped Inside Earth's Mantle",
            category="Mindblowing Earth Facts",
            summary="Ringwoodite water reservoir."
        )
        rec_id = self.verifier.save_research_record(dossier)
        self.assertIsNotNone(rec_id)
        self.assertIsInstance(rec_id, int)


class TestTopicEngine(unittest.IsolatedAsyncioTestCase):
    """Tests topic discovery, scoring, and duplicate filtering."""

    def setUp(self):
        self.engine = TopicEngine(api_key=None)  # Test deterministic offline mode

    async def test_topic_discovery_returns_ranked_candidates(self):
        candidates = await self.engine.discover_topics(enable_rss=False)
        self.assertGreaterEqual(len(candidates), 5)
        # Verify scores are sorted descending
        scores = [c["score"] for c in candidates]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # Verify all scores are in valid range
        for c in candidates:
            self.assertGreaterEqual(c["score"], 6.0)
            self.assertLessEqual(c["score"], 10.0)
            self.assertIn(c["category"], TOPIC_CATEGORIES + ["Curated Selection"])

    async def test_manual_override_topic(self):
        manual_title = "The Physics of Fusion Energy in Tokamaks"
        candidates = await self.engine.discover_topics(manual_override_title=manual_title, enable_rss=False)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"], manual_title)
        self.assertEqual(candidates[0]["score"], 10.0)


class TestScriptGenerator(unittest.IsolatedAsyncioTestCase):
    """Tests 50-60s script generation, narrative structure, and validation rules."""

    def setUp(self):
        self.generator = ScriptGenerator(api_key=None)
        self.verifier = FactVerifier()

    async def test_moon_orbit_script_conforms_to_production_standard(self):
        topic = {
            "title": "Why Does the Moon Not Fall to Earth?",
            "category": "Space & Astrophysics"
        }
        dossier = self.verifier.research_topic(topic["title"], topic["category"])
        script = await self.generator.generate_script(topic, dossier)

        # 1. Visual Style selection
        self.assertEqual(script.visual_style, VisualStyle.CHALK_TALK)

        # 2. Production duration & word count standards
        self.assertGreaterEqual(script.word_count, TARGET_SCRIPT_WORD_COUNT_MIN)
        self.assertLessEqual(script.word_count, TARGET_SCRIPT_WORD_COUNT_MAX)
        self.assertGreaterEqual(script.estimated_duration_sec, PRODUCTION_MIN_DURATION_SEC)
        self.assertLessEqual(script.estimated_duration_sec, PRODUCTION_MAX_DURATION_SEC)

        # 3. 5-Stage Storytelling Structure
        self.assertTrue(bool(script.hook))
        self.assertTrue(bool(script.context))
        self.assertTrue(bool(script.main_explanation))
        self.assertTrue(bool(script.payoff))
        self.assertTrue(bool(script.closing))
        self.assertEqual(len(script.scenes), 5)

        # 4. Zero Intro Fluff
        for banned in FORBIDDEN_INTRO_PHRASES:
            self.assertNotIn(banned, script.hook.lower())

        # 5. Validation Passed
        self.assertTrue(script.validation_passed)
        self.assertEqual(len(script.validation_errors), 0)

    async def test_solar_energy_script_selects_data_story(self):
        topic = {
            "title": "Why Solar Energy Became the Cheapest Power in History",
            "category": "Business & Global Economics"
        }
        dossier = self.verifier.research_topic(topic["title"], topic["category"])
        script = await self.generator.generate_script(topic, dossier)

        self.assertEqual(script.visual_style, VisualStyle.DATA_STORY)
        self.assertGreaterEqual(script.word_count, TARGET_SCRIPT_WORD_COUNT_MIN)
        self.assertLessEqual(script.word_count, TARGET_SCRIPT_WORD_COUNT_MAX)
        self.assertTrue(script.validation_passed)

    async def test_infantile_amnesia_script_selects_pop_info(self):
        topic = {
            "title": "Why You Can't Remember Being a Baby",
            "category": "Human Psychology & Paradoxes"
        }
        dossier = self.verifier.research_topic(topic["title"], topic["category"])
        script = await self.generator.generate_script(topic, dossier)

        self.assertEqual(script.visual_style, VisualStyle.POP_INFO)
        self.assertGreaterEqual(script.word_count, TARGET_SCRIPT_WORD_COUNT_MIN)
        self.assertLessEqual(script.word_count, TARGET_SCRIPT_WORD_COUNT_MAX)
        self.assertTrue(script.validation_passed)

    def test_validation_rejects_underlength_script(self):
        # Create deliberately short script (40 words)
        short_script = ProductionScript(
            title="Short Test",
            topic_title="Short Test",
            category="Science",
            visual_style=VisualStyle.DATA_STORY,
            style_rationale="Test",
            hook="Look at this short test.",
            context="Just context.",
            main_explanation="Here is a brief fact.",
            payoff="And it is done.",
            closing="Bye.",
            full_script="Look at this short test. Just context. Here is a brief fact. And it is done. Bye.",
            word_count=18,
            estimated_duration_sec=7.8
        )
        valid, errors = self.generator.validate_script(short_script)
        self.assertFalse(valid)
        self.assertTrue(any("word count" in e for e in errors))
        self.assertTrue(any("duration" in e for e in errors))

    def test_validation_rejects_intro_fluff(self):
        fluff_script = ProductionScript(
            title="Fluff Test",
            topic_title="Fluff Test",
            category="Science",
            visual_style=VisualStyle.CHALK_TALK,
            style_rationale="Test",
            hook="Hey guys! Welcome back to my channel! In this video we explore black holes.",
            context="Black holes are dense objects in space.",
            main_explanation=" " * 100,  # simulate length
            payoff="They swallow light.",
            closing="Subscribe for more.",
            full_script=" ".join(["word"] * 125),
            word_count=125,
            estimated_duration_sec=54.3
        )
        valid, errors = self.generator.validate_script(fluff_script)
        self.assertFalse(valid)
        self.assertTrue(any("fluff" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
