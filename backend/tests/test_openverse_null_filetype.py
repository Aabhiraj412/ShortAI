"""
Openverse returns "filetype": null for some images. `.get('filetype', 'jpeg')` returns None for a
present-but-null key, `.lower()` raised, and the exception discarded the whole result batch, so a
single null field cost every image of that query (found 2026-11-13 while testing the Pop Info engine).
"""
import io
import json
from unittest.mock import patch

from backend.app.media.providers.openverse_provider import OpenverseProvider


def _item(i, filetype):
    return {"id": f"id{i}", "url": f"https://example.org/{i}.jpg", "foreign_landing_url": f"https://example.org/p{i}",
            "license": "by", "license_version": "4.0", "creator": "c", "title": f"t{i}", "filetype": filetype,
            "width": 1200, "height": 1600}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_null_filetype_no_longer_drops_the_batch():
    body = json.dumps({"results": [_item(1, None), _item(2, "png"), _item(3, "JPG")]}).encode()
    with patch("urllib.request.urlopen", return_value=_Resp(body)):
        assets = OpenverseProvider()._sync_fetch("bridges", 3)
    assert len(assets) == 3
    assert [a.mime_type for a in assets] == ["image/jpeg", "image/png", "image/jpeg"]
