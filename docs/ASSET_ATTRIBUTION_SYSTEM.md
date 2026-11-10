# Asset Attribution & Manifest System
## Automated Legal Attribution Compilation for Creative Commons and Public Domain Assets

**Project Root:** `D:\AI Arts\AI\`  
**Date:** November 10, 2026  
**Module:** `backend/app/media/attribution_generator.py`  
**Standard:** Creative Commons Legal Code (CC BY 4.0 / 3.0 / 2.0 / 1.0) & U.S. Federal Works Guidelines

---

## 1. Legal Attribution Mandates

Under Creative Commons Attribution (CC BY) licenses, reusers must provide:
1. **Title:** The name of the material.
2. **Author / Creator:** The name or pseudonym of the author.
3. **Source:** The original landing URL where the work is hosted.
4. **License:** The specific CC license deed and link.
5. **Modifications:** Explicit notice if the material was edited, transformed, cropped, or adapted.

The `AttributionGenerator` automates this requirement, generating per-video attribution manifests and ready-to-paste YouTube description blocks.

---

## 2. Structured Manifest Schema

Per-video attribution manifests are saved as JSON files in `assets/media_library/manifests/manifest_{video_id}.json`:

```json
{
  "video_id": "short_black_hole_20261110",
  "created_at": "2026-11-10T08:00:00Z",
  "total_assets": 2,
  "items": [
    {
      "title": "Supermassive Black Hole Accretion Disk",
      "creator": "European Southern Observatory / L. Calçada",
      "source_repository": "Openverse",
      "source_url": "https://www.eso.org/public/images/eso0849a/",
      "license": "CC BY 4.0",
      "license_url": "https://creativecommons.org/licenses/by/4.0/",
      "modifications": "Adapted and cropped for 9:16 vertical video",
      "verified": true
    },
    {
      "title": "Cassini Orbiting Saturn",
      "creator": "NASA/JPL-Caltech/SSI",
      "source_repository": "NASA Image Library",
      "source_url": "https://images.nasa.gov/details-PIA12345",
      "license": "Public Domain (U.S. Government Work)",
      "license_url": "https://www.nasa.gov/multimedia/guidelines/index.html",
      "modifications": "Adapted and cropped for 9:16 vertical video",
      "verified": true
    }
  ],
  "youtube_description_block": "--- \nVISUAL MEDIA CREDITS & LICENSING:\n• \"Supermassive Black Hole Accretion Disk\" by European Southern Observatory / L. Calçada via Openverse, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/).\n• \"Cassini Orbiting Saturn\" by NASA/JPL-Caltech/SSI via NASA Image Library, licensed under Public Domain (U.S. Government Work) (https://www.nasa.gov/multimedia/guidelines/index.html)."
}
```

---

## 3. YouTube Description Integration

The `youtube_description_block` is formatted to integrate into YouTube's metadata upload pipeline (`youtube_service.py`), ensuring that:
- Every uploaded video description automatically includes full attribution credits.
- Character limits ($\le 5,000$ characters) are respected.
- URLs to official CC deeds and source pages are preserved.
