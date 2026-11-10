"""
Runner and audit generator for Stage 3.9A Creative Thumbnail Design.
"""
import os
import json
from PIL import Image
from backend.app.rendering.engines.cinematic_thumbnail_designer import CinematicThumbnailDesigner

assets = {
    'hiroshima_cloud': 'media/images/yamaguchi/Atomic_cloud_over_Hiroshima_jpg.jpg',
    'nagasaki_cloud': 'media/images/yamaguchi/Nagasakibomb_jpg.jpg',
    'monument': 'media/images/yamaguchi/Hiroshima_Gembaku_Dome_1_jpg.jpg',
    'torii': 'media/images/yamaguchi/Nagasaki_temple_destroyed_jpg.jpg',
    'declassified_order': 'media/images/yamaguchi/Hiroshima_Bombing_Operations_Order_No_35_5_Aug_1945_jpg.jpg',
    'rubble': 'media/images/yamaguchi/Dan_A__McGovern_at_Nagasaki_ground_zero_9_September_1945_png.png',
}

designer = CinematicThumbnailDesigner()
candidates = designer.generate_all_candidates(assets)
print(f"Generated {len(candidates)} creative candidates:")

for c in candidates:
    img = Image.open(c.full_path)
    mob = Image.open(c.mobile_path)
    print(f"  - {c.id}: Full {img.size} ({os.path.getsize(c.full_path)} B), Mobile {mob.size} ({os.path.getsize(c.mobile_path)} B)")

winner, report = designer.select_best_poster(candidates)
print("\n=== WINNING CONCEPT ===")
print(f"Title:    {winner.title}")
print(f"Headline: {winner.headline}")
print(f"Score:    {winner.evaluation.composite_score} / 10.0")
print(f"Saved:    media/thumbnails/showcase_stage_3_9a_yamaguchi_poster.png")
print("\nAudit ranking complete.")
