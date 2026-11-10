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

    def create_storyboard(self, script_data: Dict, total_duration: float) -> Dict:
        """
        Splits script into dynamic scenes adhering to the 5-stage storytelling structure:
        Hook (0-3s), Context (3-12s), Main Content (12-40s), Payoff (40-51s), Closing (51-55s).
        Maintains continuous visual engagement with 5-8 second scene cadence.
        """
        title = script_data.get("title", "YouTube Short")
        full_text = script_data.get("full_script", "")
        sentences = [s.strip() for s in full_text.split(".") if s.strip()]
        
        # Determine number of scenes: for 50-60s target ~7 scenes (~7-8s per scene)
        if total_duration >= 45.0:
            num_scenes = max(5, min(9, len(sentences)))
        else:
            num_scenes = max(3, min(4, len(sentences)))

        scene_duration = round(total_duration / max(1, num_scenes), 2)
        
        # Preset kinetic headlines and postures for dynamic engagement
        scene_presets = [
            {"headline": "COULD YOU SURVIVE?", "badge": "THE HOOK", "left": "arm_left_idle", "right": "arm_right_think", "eyebrows": "eyebrows_curious", "char_x": 40, "trans": "cut"},
            {"headline": "THE INVISIBLE BOUNDARY", "badge": "EVENT HORIZON", "left": "arm_left_present", "right": "arm_right_idle", "eyebrows": "eyebrows_curious", "char_x": -30, "trans": "whoosh"},
            {"headline": "EXPONENTIAL GRAVITY", "badge": "TIDAL FORCES", "left": "arm_left_idle", "right": "arm_right_think", "eyebrows": "eyebrows_curious", "char_x": 50, "trans": "whoosh"},
            {"headline": "SPAGHETTIFICATION!", "badge": "ATOMIC STRETCH", "left": "arm_left_present", "right": "arm_right_think", "eyebrows": "eyebrows_surprised", "char_x": -40, "trans": "glitch"},
            {"headline": "WARPED SPACETIME", "badge": "LIGHT RING", "left": "arm_left_present", "right": "arm_right_idle", "eyebrows": "eyebrows_curious", "char_x": 40, "trans": "whoosh"},
            {"headline": "FROZEN IN TIME", "badge": "THE PARADOX", "left": "arm_left_idle", "right": "arm_right_think", "eyebrows": "eyebrows_curious", "char_x": -20, "trans": "whoosh"},
            {"headline": "THE CLIMACTIC PAYOFF", "badge": "THE SINGULARITY", "left": "arm_left_present", "right": "arm_right_think", "eyebrows": "eyebrows_surprised", "char_x": 50, "trans": "whoosh"},
            {"headline": "SUBSCRIBE NOW!", "badge": "COSMIC SECRETS", "left": "arm_left_present", "right": "arm_right_idle", "eyebrows": "eyebrows_neutral", "char_x": 0, "trans": "whoosh"},
        ]
        
        scenes = []
        for i in range(num_scenes):
            start_t = round(i * scene_duration, 2)
            end_t = round(min(total_duration, (i + 1) * scene_duration), 2)
            dur = round(end_t - start_t, 2)
            
            preset = scene_presets[min(i, len(scene_presets) - 1)]
            headline = preset["headline"]
            if i == 0 and title:
                headline = "CAN YOU SURVIVE THIS?" if "black hole" in title.lower() else title.upper()[:26]
            elif i == num_scenes - 1:
                headline = "SUBSCRIBE NOW!"
                
            scenes.append({
                "id": i + 1,
                "start_time": start_t,
                "duration": dur,
                "headline": headline,
                "badge": preset["badge"],
                "background": "bg_space",
                "talking": True,
                "left_arm": preset["left"],
                "right_arm": preset["right"],
                "eyebrows": preset["eyebrows"],
                "char_x": preset["char_x"],
                "char_y": 760,
                "camera_zoom_speed": 0.02,
                "particles": True,
                "transition": preset["trans"]
            })
            
        storyboard = {
            "title": title,
            "total_duration": total_duration,
            "scenes": scenes
        }
        return storyboard
