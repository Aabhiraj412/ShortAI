"""
Search and verify authentic public domain historical imagery for Tsutomu Yamaguchi.
"""
import urllib.request
import json
import os

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
headers = {"User-Agent": "CrazzyDocs/1.0 (CuratedHistoricalMedia; contact@crazzydocs.dev)"}

filenames = [
    "Atomic_cloud_over_Hiroshima.jpg",
    "Nagasaki_1945_-_Present_Image_-_Nagasaki_journey_-_the_bombing_of_Nagasaki.jpg",
    "Hiroshima_aftermath.jpg",
    "Tsutomu_Yamaguchi.jpg",
    "Hiroshima_Gembaku_Dome_1.jpg",
    "Fat_man.jpg",
    "Little_Boy_replica.jpg",
    "Nagasaki_temple_destroyed.jpg",
    "Yamaguchi_Tsutomu.jpg",
    "Tsutomu_Yamaguchi_holding_documents.jpg"
]

for fn in filenames:
    url = f"{WIKIMEDIA_API}?action=query&titles=File:{fn}&prop=imageinfo&iiprop=url|size|extmetadata&format=json"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        pages = data.get("query", {}).get("pages", {})
        for pid, page in pages.items():
            if int(pid) > 0 and "imageinfo" in page:
                info = page["imageinfo"][0]
                print(f"[FOUND] {fn}: {info['width']}x{info['height']} -> {info['url']}")
            else:
                print(f"[NOT FOUND] {fn}")
    except Exception as e:
        print(f"[ERROR] {fn}: {e}")
