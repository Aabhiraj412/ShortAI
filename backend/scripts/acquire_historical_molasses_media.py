"""
Curated Historical Media Acquisition Script for The Great Molasses Flood of 1919.
Downloads authentic public-domain archival photographs from Wikimedia Commons and Boston Public Library.
"""

import json
import logging
import os
import urllib.request
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("media_acquisition")

HIST_DIR = r"D:\Agents\CrazzyDocs\media\images\historical"
MANIFEST_PATH = os.path.join(HIST_DIR, "attribution_manifest.json")
os.makedirs(HIST_DIR, exist_ok=True)

CURATED_IMAGES = [
    {
        "id": "hist_tank_01",
        "scene": 1,
        "title": "Purity Distilling North End Molasses Tank (1915-1919)",
        "url": "https://upload.wikimedia.org/wikipedia/commons/5/59/North_End_molasses_tank.jpg",
        "source_page": "https://commons.wikimedia.org/wiki/File:North_End_molasses_tank.jpg",
        "creator": "Leslie Jones (1886-1967) / Boston Public Library Collection",
        "license": "Public Domain (Published in US before 1928)",
        "attribution_required": False,
        "relevance": "Authentic historical photograph of the 50-foot cylindrical steel molasses storage tank prior to catastrophic rupture.",
        "filename": "scene_01_original_tank.jpg"
    },
    {
        "id": "hist_layout_02",
        "scene": 2,
        "title": "Boston Commercial Street Molasses Tank Detail Map and Structural Blueprint",
        "url": "https://upload.wikimedia.org/wikipedia/commons/2/2e/Boston_molasses_detail_map.png",
        "source_page": "https://commons.wikimedia.org/wiki/File:Boston_molasses_detail_map.png",
        "creator": "Historic cartographic archive / Wikimedia Commons contributors",
        "license": "Public Domain",
        "attribution_required": False,
        "relevance": "Detailed schematic map showing tank placement, harbor wharf, and surrounding North End street grid.",
        "filename": "scene_02_tank_layout.png"
    },
    {
        "id": "hist_rupture_03",
        "scene": 3,
        "title": "Catastrophic Rupture: Collapsed Molasses Tank Structure and Twisted Steel",
        "url": "https://upload.wikimedia.org/wikipedia/commons/7/70/BostonMolassesDisaster.jpg",
        "source_page": "https://commons.wikimedia.org/wiki/File:BostonMolassesDisaster.jpg",
        "creator": "Boston Public Library / Leslie Jones Collection",
        "license": "Public Domain (Published in US 1919)",
        "attribution_required": False,
        "relevance": "Iconic archival photograph of the crumpled 2.3M-gallon steel tank plates and sea of syrup.",
        "filename": "scene_03_collapsed_tank.jpg"
    },
    {
        "id": "hist_damage_04",
        "scene": 4,
        "title": "Boston Elevated Railway Structure Sheared by Molasses Wave",
        "url": "https://upload.wikimedia.org/wikipedia/commons/6/6f/Boston_Elevated_Railway_after_the_Molasses_Flood%2C_January_1919%2C_from_the_City_of_Boston_Archives_-_9800-018-023.jpg",
        "source_page": "https://commons.wikimedia.org/wiki/File:Boston_Elevated_Railway_after_the_Molasses_Flood,_January_1919,_from_the_City_of_Boston_Archives_-_9800-018-023.jpg",
        "creator": "City of Boston Archives (Record 9800-018-023)",
        "license": "Public Domain (Official Government Archive / Published 1919)",
        "attribution_required": False,
        "relevance": "Direct evidence of the wave's kinetic devastation: elevated train stanchions bent and shattered.",
        "filename": "scene_04_railway_wreckage.jpg"
    },
    {
        "id": "hist_investigation_05",
        "scene": 5,
        "title": "Wreckage Under Elevated Tracks with Rescuers and Inquiry Auditors",
        "url": "https://upload.wikimedia.org/wikipedia/commons/1/10/GreatMolassesFlood_1919-Wreckage_under_the_elevated_tracks.jpg",
        "source_page": "https://commons.wikimedia.org/wiki/File:GreatMolassesFlood_1919-Wreckage_under_the_elevated_tracks.jpg",
        "creator": "Boston Public Library / Leslie Jones Collection",
        "license": "Public Domain (Published 1919)",
        "attribution_required": False,
        "relevance": "Shows waist-deep syrup wreckage and initial recovery operations that exposed the flawed 0.67-inch steel plates.",
        "filename": "scene_05_investigation_wreckage.jpg"
    },
    {
        "id": "hist_legacy_06",
        "scene": 6,
        "title": "Boston Daily Globe Headline: Massive Wave Devastation & Legal Inquest",
        "url": "https://upload.wikimedia.org/wikipedia/commons/4/49/Boston_Daily_Globe_Jan._16%2C_1919.png",
        "source_page": "https://commons.wikimedia.org/wiki/File:Boston_Daily_Globe_Jan._16%2C_1919.png",
        "creator": "Boston Daily Globe (January 16, 1919 edition)",
        "license": "Public Domain in US (Published January 16, 1919 without copyright renewal)",
        "attribution_required": False,
        "relevance": "Front-page newspaper record documenting the tragedy that spurred the class-action lawsuit and modern PE stamp laws.",
        "filename": "scene_06_newspaper_legacy.png"
    }
]


def download_and_verify_all():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CrazzyDocsStudio/1.0"}
    manifest = []

    for item in CURATED_IMAGES:
        dest = os.path.join(HIST_DIR, item["filename"])
        logger.info("Fetching %s from %s...", item["id"], item["url"][:60])
        req = urllib.request.Request(item["url"], headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=12) as resp, open(dest, "wb") as f_out:
                f_out.write(resp.read())

            im = Image.open(dest)
            item["width"], item["height"] = im.size
            item["size_bytes"] = os.path.getsize(dest)
            item["local_path"] = dest
            manifest.append(item)
            logger.info("Saved %s: %s (%dx%d, %d bytes)", item["id"], item["filename"], im.size[0], im.size[1], item["size_bytes"])
        except Exception as e:
            logger.error("Download failed for %s: %s", item["id"], e)
            raise e

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Manifest successfully written to %s with %d verified historical assets.", MANIFEST_PATH, len(manifest))
    return manifest


if __name__ == "__main__":
    download_and_verify_all()
