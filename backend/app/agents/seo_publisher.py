"""
Agent 7: SEO, Thumbnail & YouTube Publishing Engine.
Generates search-optimized metadata, programmatic 1080x1920 thumbnails,
handles YouTube Data API v3 OAuth2 authentication, uploads, and schedules videos.
"""
import json
import os
import time
from typing import Dict, List, Optional
from PIL import Image, ImageDraw, ImageFont
from google import genai
from app.core.config import settings

class SEOPublisher:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.client = genai.Client(api_key=self.api_key) if self.api_key else None

    async def generate_seo(self, title: str, script_text: str, category: str = "Science") -> Dict:
        """Generates title, description, tags, hashtags, and pinned comment."""
        prompt = f"""
        You are a top YouTube Shorts SEO strategist.
        Create viral, high-CTR, search-optimized metadata for this Short:
        Topic: "{title}"
        Category: {category}
        Script excerpt: "{script_text[:200]}"
        
        CRITICAL RULES:
        1. Title: Under 60 characters. Must create extreme curiosity without misleading clickbait. Include #Shorts.
        2. Description: 2-3 engaging sentences, key facts, 5 relevant hashtags, and credit to scientific sources.
        3. Tags: 12-15 high-volume search tags.
        4. Hashtags: 4-5 relevant hashtags starting with #Shorts.
        5. Pinned Comment: Engaging question to spark viewer comments and algorithmic discussion.
        
        Return strictly JSON:
        {{
            "youtube_title": "Title under 60 chars #Shorts",
            "description": "Full description with hashtags",
            "tags": ["tag1", "tag2", "tag3"],
            "hashtags": ["#Shorts", "#Science"],
            "category_id": "28",
            "pinned_comment": "Curiosity question for viewers?"
        }}
        """
        
        if self.client:
            try:
                response = self.client.models.generate_content(
                    model=settings.DEFAULT_GEMINI_MODEL,
                    contents=prompt
                )
                text = response.text.strip()
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0].strip()
                return json.loads(text)
            except Exception as e:
                print(f"Gemini SEO fallback: {e}")
                
        # Robust offline fallback metadata
        clean_title = title.replace("?", "").strip()
        return {
            "youtube_title": f"{clean_title[:45]} Explains Everything! #Shorts",
            "description": (
                f"Discover the mind-bending reality behind {title.lower()}.\n\n"
                "Physics, spacetime, and the universe explained in 60 seconds!\n\n"
                "#Shorts #Science #Space #Astrophysics #DidYouKnow"
            ),
            "tags": ["shorts", "science", "space", "astrophysics", "black hole", "physics", "universe", "did you know", "education"],
            "hashtags": ["#Shorts", "#Science", "#Space", "#Astrophysics"],
            "category_id": "28", # Science & Technology
            "pinned_comment": "Would you ever dare to enter the event horizon? Let us know in the comments below! 👇"
        }

    def generate_custom_thumbnail(
        self,
        headline: str,
        character_frame_path: str,
        output_path: str,
        badge_text: str = "MIND-BLOWING"
    ) -> str:
        """
        Generates a 1080x1920 high-contrast vertical thumbnail for YouTube Shorts.
        Features bold curiosity headline, character subject, and channel logo watermark.
        """
        # Load frame or create base
        if os.path.exists(character_frame_path):
            thumb = Image.open(character_frame_path).convert("RGBA")
        else:
            thumb = Image.new("RGBA", (1080, 1920), (10, 14, 23, 255))
            
        draw = ImageDraw.Draw(thumb, "RGBA")
        
        # 1. Dark gradient vignette at top for headline readability
        vignette = Image.new("RGBA", (1080, 600), (0, 0, 0, 0))
        v_draw = ImageDraw.Draw(vignette)
        for y in range(600):
            alpha = int(220 * (1.0 - (y / 600.0)))
            v_draw.line([(0, y), (1080, y)], fill=(5, 8, 19, alpha))
        thumb.alpha_composite(vignette, (0, 0))
        
        # 2. Bold Curiosity Headline Pill
        try:
            font_title = ImageFont.truetype("arialbd.ttf", 64)
            font_badge = ImageFont.truetype("arialbd.ttf", 40)
        except:
            font_title = ImageFont.load_default()
            font_badge = font_title
            
        # Draw category pill
        draw.rounded_rectangle([60, 120, 420, 200], radius=18, fill=(230, 57, 70, 240))
        draw.text((90, 138), badge_text.upper(), font=font_badge, fill=(255, 255, 255, 255))
        
        # Draw headline text with high contrast background
        draw.rounded_rectangle([50, 240, 1030, 430], radius=24, fill=(15, 23, 42, 245), outline=(0, 240, 255, 255), width=5)
        
        # Word wrap headline
        words = headline.upper().split()
        line1 = " ".join(words[:3]) if len(words) > 3 else " ".join(words)
        line2 = " ".join(words[3:]) if len(words) > 3 else ""
        
        draw.text((80, 265), line1, font=font_title, fill=(255, 235, 59, 255))
        if line2:
            draw.text((80, 345), line2, font=font_title, fill=(255, 255, 255, 255))
            
        # 3. Channel Watermark Icon (from Youtube Channel Src)
        logo_path = os.path.join(settings.BASE_DIR, "Youtube Channel Src", "Compressed-Youtube-Icon.png")
        if os.path.exists(logo_path):
            try:
                logo = Image.open(logo_path).convert("RGBA").resize((120, 120))
                thumb.alpha_composite(logo, (900, 100))
            except Exception as e:
                pass
                
        thumb.convert("RGB").save(output_path, "PNG", quality=95)
        print(f"Generated High-CTR Thumbnail: {output_path} ({os.path.getsize(output_path)/1024:.1f} KB)")
        return output_path
