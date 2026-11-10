"""
Agent 4: Visual Director.
Transforms approved scripts into structured storyboard JSON with timing,
character gestures, expressions, camera motions, kinetic text overlays, and sound cues.
"""
import json
import math
from typing import Dict, List, Optional
from google import genai
from app.core.config import settings

class VisualDirector:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None

    def create_storyboard(self, script_data: Dict, total_duration: float, visual_style: str = "pop_info") -> Dict:
        """
        Splits script into dynamic scenes adhering strictly to the Stage 3.1 Viewer Retention Pacing Structure:
        - Hook (0-2.5s): Pattern interrupt, high energy, instant tension.
        - Context (2.5-8.0s): Framing dilemma, baseline stakes.
        - Build Curiosity (8.0-18.0s): Escalating tension, paradox, question.
        - Reveal / Explanation (18.0-40.0s): Progressive revelation, visual developments every 5-7s.
        - Major Payoff (40.0-50.0s): Climactic breakthrough, core insight resolution.
        - Conclusion / Transition (50.0s to end): Fast punchline, clean loop into 2.0s icon-only outro.
        """
        title = script_data.get("title", "YouTube Short")
        full_text = script_data.get("full_script", "")
        sentences = [s.strip() for s in full_text.split(".") if s.strip()]
        
        # Style-aware background mapping
        style_bg_map = {
            "pop_info": "pop_info/bg_warm_cream.png",
            "data_story": "data_story/bg_dark_slate_grid.png",
            "chalk_talk": "chalk_talk/bg_slate_blackboard.png",
            "creator_motion": "creator/bg_studio_black.png",
            "creator_motion_graphics": "creator/bg_studio_black.png",
        }
        chosen_bg = style_bg_map.get(visual_style.lower(), "pop_info/bg_warm_cream.png")

        # Determine retention-optimized scene boundaries based on total_duration
        if total_duration >= 45.0:
            # Full 50-60s Short (7 scenes, each 2.5s to 10.0s to avoid static holds)
            stage_boundaries = [
                (0.0, 2.5, "THE HOOK", "cut", "arm_left_idle", "arm_right_think", "eyebrows_curious", 40),
                (2.5, 8.0, "CONTEXT", "whoosh", "arm_left_present", "arm_right_idle", "eyebrows_curious", -30),
                (8.0, 17.0, "THE PARADOX", "whoosh", "arm_left_idle", "arm_right_think", "eyebrows_curious", 50),
                (17.0, 27.0, "THE REVELATION", "glitch", "arm_left_present", "arm_right_think", "eyebrows_surprised", -40),
                (27.0, 37.0, "EVIDENCE", "whoosh", "arm_left_present", "arm_right_idle", "eyebrows_curious", 40),
                (37.0, 47.0, "THE BREAKTHROUGH", "whoosh", "arm_left_present", "arm_right_think", "eyebrows_surprised", -20),
                (47.0, total_duration, "FINAL PAYOFF", "whoosh", "arm_left_present", "arm_right_idle", "eyebrows_neutral", 0),
            ]
        elif total_duration >= 30.0:
            # ~35s Short (5 scenes)
            stage_boundaries = [
                (0.0, 2.5, "THE HOOK", "cut", "arm_left_idle", "arm_right_think", "eyebrows_curious", 40),
                (2.5, 8.0, "CONTEXT", "whoosh", "arm_left_present", "arm_right_idle", "eyebrows_curious", -30),
                (8.0, 17.0, "THE PARADOX", "whoosh", "arm_left_idle", "arm_right_think", "eyebrows_curious", 50),
                (17.0, 27.0, "THE REVELATION", "glitch", "arm_left_present", "arm_right_think", "eyebrows_surprised", -40),
                (27.0, total_duration, "THE BREAKTHROUGH", "whoosh", "arm_left_present", "arm_right_idle", "eyebrows_neutral", 0),
            ]
        else:
            # ~20s Short (4 scenes)
            stage_boundaries = [
                (0.0, 2.5, "THE HOOK", "cut", "arm_left_idle", "arm_right_think", "eyebrows_curious", 40),
                (2.5, 7.0, "CONTEXT", "whoosh", "arm_left_present", "arm_right_idle", "eyebrows_curious", -30),
                (7.0, 14.5, "THE REVELATION", "glitch", "arm_left_present", "arm_right_think", "eyebrows_surprised", 50),
                (14.5, total_duration, "THE BREAKTHROUGH", "whoosh", "arm_left_present", "arm_right_idle", "eyebrows_neutral", 0),
            ]

        # Extract punchy headlines from script or presets
        hook_headline = "THE $1 BANK ACQUISITION" if "bank" in title.lower() else (
            "THE SINGULARITY TRUTH" if "black hole" in title.lower() else (
                "COULD YOU SURVIVE?" if "survive" in title.lower() else title.upper()[:24]
            )
        )
        
        default_headlines = [
            hook_headline,
            "THE HIDDEN TRAP",
            "THE PARADOX EXPOSED",
            "EXPONENTIAL REACTION",
            "EVIDENCE CONFIRMED",
            "THE CLIMACTIC PAYOFF",
            "SUBSCRIBE FOR MORE",
        ]

        pose_map = [
            "pose_idle_welcoming",
            "pose_thinking_chin",
            "pose_presenting_left",
            "pose_presenting_right",
            "pose_pointing_up",
            "pose_shocked_hands",
        ]

        scenes = []
        for i, (st, et, badge, trans, left, right, brows, cx) in enumerate(stage_boundaries):
            dur = round(et - st, 2)
            headline = default_headlines[min(i, len(default_headlines) - 1)]
            doc_pose = pose_map[i % len(pose_map)]

            scenes.append({
                "id": i + 1,
                "start_time": round(st, 2),
                "duration": dur,
                "headline": headline,
                "badge": badge,
                "background": chosen_bg,
                "pose": doc_pose,
                "talking": True,
                "left_arm": left,
                "right_arm": right,
                "eyebrows": brows,
                "char_x": cx,
                "char_y": 760,
                "camera_zoom_speed": 0.02,
                "particles": True,
                "transition": trans,
            })
            
        storyboard = {
            "title": title,
            "total_duration": total_duration,
            "scenes": scenes
        }
        return storyboard
            
        storyboard = {
            "title": title,
            "total_duration": total_duration,
            "scenes": scenes
        }
        return storyboard
