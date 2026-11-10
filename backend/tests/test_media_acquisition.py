"""
Automated Test Suite for Stage 2: Openly Licensed Media Acquisition Subsystem.

Validates:
1. Provider result parsing (Openverse, Wikimedia, NASA) with mocked API payloads.
2. Strict License Verification (CC0, Public Domain, CC BY accepted; NC, ND, SA, Unknown rejected).
3. Secure Asset Downloader (Magic bytes, corrupt rejection, size limits, SHA-256 deduplication).
4. Portrait Image Processing (1080x1920 9:16 vertical adaptation without distortion).
5. Attribution Generator (AttributionManifest generation & YouTube description block).
6. SQLite Asset Registry (Persistence, deduplication, job associations).
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from backend.app.media.asset_registry import AssetRegistry
from backend.app.media.attribution_generator import AttributionGenerator
from backend.app.media.acquisition_manager import MediaAcquisitionEngine
from backend.app.media.downloader import (
    AssetDownloader,
    detect_magic_type,
    sanitize_filename,
)
from backend.app.media.image_processor import AdaptationMode, ImageProcessor
from backend.app.media.license_verifier import LicenseVerifier
from backend.app.media.models import (
    AttributionItem,
    AttributionManifest,
    LicenseStatus,
    LicenseType,
    MediaAsset,
)
from backend.app.media.providers.nasa_provider import NASAProvider
from backend.app.media.providers.openverse_provider import OpenverseProvider
from backend.app.media.providers.wikimedia_provider import WikimediaProvider
from backend.app.media.relevance_scorer import RelevanceScorer


class TestLicenseVerifier(unittest.TestCase):
    """Tests strict legal license policy evaluation."""

    def setUp(self):
        self.verifier = LicenseVerifier()

    def test_cc0_accepted(self):
        status, lic_type, lic_name, reason = self.verifier.verify_asset_license(
            raw_license="CC0 1.0",
            source_url="https://commons.wikimedia.org/wiki/File:Hubble_Deep_Field.jpg",
            repository="Wikimedia Commons",
            creator="NASA/ESA"
        )
        self.assertEqual(status, LicenseStatus.APPROVED)
        self.assertEqual(lic_type, LicenseType.CC0)
        self.assertIsNone(reason)

    def test_public_domain_accepted(self):
        status, lic_type, lic_name, reason = self.verifier.verify_asset_license(
            raw_license="Public Domain (U.S. Government Work)",
            source_url="https://images.nasa.gov/details-PIA12345",
            repository="NASA Image Library",
            creator="NASA/JPL"
        )
        self.assertEqual(status, LicenseStatus.APPROVED)
        self.assertIn(lic_type, (LicenseType.US_GOV_PD, LicenseType.PUBLIC_DOMAIN))
        self.assertIsNone(reason)

    def test_cc_by_accepted(self):
        status, lic_type, lic_name, reason = self.verifier.verify_asset_license(
            raw_license="CC BY 4.0",
            source_url="https://openverse.org/image/12345",
            repository="Openverse",
            creator="European Southern Observatory"
        )
        self.assertEqual(status, LicenseStatus.APPROVED)
        self.assertEqual(lic_type, LicenseType.CC_BY)
        self.assertIsNone(reason)

    def test_cc_by_nc_rejected_by_policy(self):
        status, lic_type, lic_name, reason = self.verifier.verify_asset_license(
            raw_license="CC BY-NC 4.0",
            source_url="https://openverse.org/image/54321",
            repository="Openverse",
            creator="Artist Name"
        )
        self.assertEqual(status, LicenseStatus.REJECTED)
        self.assertEqual(lic_type, LicenseType.CC_BY_NC)
        self.assertIn("Non-Commercial", reason)

    def test_cc_by_nd_rejected_for_adaptation(self):
        status, lic_type, lic_name, reason = self.verifier.verify_asset_license(
            raw_license="CC BY-ND 3.0",
            source_url="https://openverse.org/image/999",
            repository="Openverse",
            creator="Photographer"
        )
        self.assertEqual(status, LicenseStatus.REJECTED)
        self.assertEqual(lic_type, LicenseType.CC_BY_ND)
        self.assertIn("No-Derivatives", reason)

    def test_cc_by_sa_rejected_by_default(self):
        status, lic_type, lic_name, reason = self.verifier.verify_asset_license(
            raw_license="CC BY-SA 4.0",
            source_url="https://commons.wikimedia.org/wiki/File:Black_Hole.jpg",
            repository="Wikimedia Commons",
            creator="Author"
        )
        self.assertEqual(status, LicenseStatus.REJECTED)
        self.assertEqual(lic_type, LicenseType.CC_BY_SA)
        self.assertIn("ShareAlike", reason)

    def test_unknown_or_missing_license_rejected(self):
        status, lic_type, lic_name, reason = self.verifier.verify_asset_license(
            raw_license="",
            source_url="https://example.com/asset",
            repository="Unknown"
        )
        self.assertEqual(status, LicenseStatus.REJECTED)
        self.assertEqual(lic_type, LicenseType.UNKNOWN)
        self.assertIn("Rejected", reason)

    def test_invalid_source_url_rejected(self):
        status, lic_type, lic_name, reason = self.verifier.verify_asset_license(
            raw_license="CC0",
            source_url="not-a-valid-url",
            repository="Openverse"
        )
        self.assertEqual(status, LicenseStatus.REJECTED)
        self.assertIn("source landing url", reason.lower())

    def test_trademark_or_personality_restriction_rejected(self):
        status, lic_type, lic_name, reason = self.verifier.verify_asset_license(
            raw_license="CC BY 4.0",
            source_url="https://commons.wikimedia.org/wiki/File:Crowd.jpg",
            repository="Wikimedia Commons",
            creator="Photographer",
            metadata_notes="Editorial use only: personality rights apply, no model release provided."
        )
        self.assertEqual(status, LicenseStatus.REJECTED)
        self.assertIn("non-copyright legal restriction", reason.lower())


class TestProviderParsing(unittest.TestCase):
    """Tests metadata parsing and schema mapping across Openverse, Wikimedia, and NASA."""

    def test_openverse_parsing(self):
        provider = OpenverseProvider()
        mock_payload = {
            "results": [
                {
                    "id": "openverse_abc123",
                    "title": "Supermassive Black Hole",
                    "creator": "ESO / L. Calçada",
                    "url": "https://cdn.openverse.org/images/abc123.jpg",
                    "foreign_landing_url": "https://www.eso.org/public/images/eso0849a/",
                    "license": "by",
                    "license_version": "4.0",
                    "license_url": "https://creativecommons.org/licenses/by/4.0/",
                    "width": 3840,
                    "height": 2160,
                    "filetype": "jpg",
                    "filesize": 2048000
                }
            ]
        }
        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
            mock_url.return_value.__enter__.return_value = mock_resp

            assets = provider._sync_fetch("black hole", limit=1)
            self.assertEqual(len(assets), 1)
            a = assets[0]
            self.assertEqual(a.title, "Supermassive Black Hole")
            self.assertEqual(a.creator, "ESO / L. Calçada")
            self.assertEqual(a.license_type, LicenseType.CC_BY)
            self.assertEqual(a.verification_status, LicenseStatus.APPROVED)
            self.assertEqual(a.width, 3840)

    def test_wikimedia_parsing(self):
        provider = WikimediaProvider()
        mock_payload = {
            "query": {
                "pages": {
                    "98765": {
                        "pageid": 98765,
                        "title": "File:Moon_orbit.jpg",
                        "imageinfo": [
                            {
                                "url": "https://upload.wikimedia.org/wikipedia/commons/moon_orbit.jpg",
                                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Moon_orbit.jpg",
                                "width": 2048,
                                "height": 1536,
                                "size": 1500000,
                                "mime": "image/jpeg",
                                "extmetadata": {
                                    "ObjectName": {"value": "Lunar Orbit Geometry"},
                                    "Artist": {"value": "NASA Goddard Space Flight Center"},
                                    "LicenseShortName": {"value": "Public domain"},
                                    "LicenseUrl": {"value": "https://en.wikipedia.org/wiki/Public_domain"}
                                }
                            }
                        ]
                    }
                }
            }
        }
        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
            mock_url.return_value.__enter__.return_value = mock_resp

            assets = provider._sync_fetch("moon orbit", limit=1)
            self.assertEqual(len(assets), 1)
            a = assets[0]
            self.assertEqual(a.title, "Lunar Orbit Geometry")
            self.assertIn("NASA", a.creator)
            self.assertEqual(a.verification_status, LicenseStatus.APPROVED)
            self.assertEqual(a.license_type, LicenseType.PUBLIC_DOMAIN)

    def test_nasa_parsing(self):
        provider = NASAProvider()
        mock_payload = {
            "collection": {
                "items": [
                    {
                        "data": [
                            {
                                "nasa_id": "PIA12345",
                                "title": "Cassini Orbiting Saturn",
                                "center": "JPL",
                                "photographer": "NASA/JPL-Caltech/SSI"
                            }
                        ],
                        "links": [
                            {"href": "https://images-assets.nasa.gov/image/PIA12345/PIA12345~thumb.jpg"}
                        ]
                    }
                ]
            }
        }
        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
            mock_url.return_value.__enter__.return_value = mock_resp

            assets = provider._sync_fetch("saturn", limit=1)
            self.assertEqual(len(assets), 1)
            a = assets[0]
            self.assertEqual(a.title, "Cassini Orbiting Saturn")
            self.assertEqual(a.source_repository, "NASA Image Library")
            self.assertEqual(a.verification_status, LicenseStatus.APPROVED)
            self.assertIn("PIA12345~orig.jpg", a.download_url)


class TestAssetDownloader(unittest.TestCase):
    """Tests secure file downloads, magic bytes signature validation, and deduplication."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_media_downloader_")
        self.downloader = AssetDownloader(storage_dir=self.temp_dir)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _create_mock_image_bytes(self, fmt="JPEG", width=200, height=200) -> bytes:
        img = Image.new("RGB", (width, height), (100, 150, 200))
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        return buf.getvalue()

    def test_magic_bytes_detection(self):
        jpeg_bytes = self._create_mock_image_bytes("JPEG")
        self.assertEqual(detect_magic_type(jpeg_bytes[:16]), "image/jpeg")

        png_bytes = self._create_mock_image_bytes("PNG")
        self.assertEqual(detect_magic_type(png_bytes[:16]), "image/png")

        fake_exe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00"
        self.assertIsNone(detect_magic_type(fake_exe))

    def test_successful_download_and_hash(self):
        img_bytes = self._create_mock_image_bytes("JPEG", 400, 300)
        asset = MediaAsset(
            asset_id="test_01",
            title="Valid Image",
            creator="Tester",
            source_repository="NASA",
            source_url="https://images.nasa.gov/details-01",
            download_url="https://example.com/valid.jpg",
            license_type=LicenseType.US_GOV_PD,
            attribution_text="Credit"
        )

        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "image/jpeg"}
            mock_resp.read.side_effect = [img_bytes, b""]
            mock_url.return_value.__enter__.return_value = mock_resp

            ok, path, file_hash, err = self.downloader.download_asset(asset)
            self.assertTrue(ok)
            self.assertIsNotNone(path)
            self.assertTrue(os.path.exists(path))
            self.assertEqual(len(file_hash), 64)  # SHA-256 length
            self.assertEqual(asset.width, 400)
            self.assertEqual(asset.height, 300)

    def test_rejection_of_corrupt_non_image(self):
        bad_bytes = b"NOT_AN_IMAGE_FILE_JUST_PLAIN_TEXT_DATA_EXACTLY"
        asset = MediaAsset(
            asset_id="test_bad",
            title="Bad Data",
            creator="Tester",
            source_repository="Web",
            source_url="https://example.com/bad",
            download_url="https://example.com/bad.jpg",
            license_type=LicenseType.CC0,
            attribution_text="Credit"
        )

        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "image/jpeg"}
            mock_resp.read.side_effect = [bad_bytes, b""]
            mock_url.return_value.__enter__.return_value = mock_resp

            ok, path, file_hash, err = self.downloader.download_asset(asset)
            self.assertFalse(ok)
            self.assertIn("magic bytes", err.lower())

    def test_safe_filename_sanitization(self):
        dangerous = "../../etc/passwd!@#$%^&*()_image.jpg"
        clean = sanitize_filename(dangerous)
        self.assertNotIn("..", clean)
        self.assertNotIn("/", clean)
        self.assertNotIn("\\", clean)


class TestImageProcessor(unittest.TestCase):
    """Tests 1080x1920 portrait adaptation modes."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_image_processor_")
        self.processor = ImageProcessor(output_dir=self.temp_dir)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _create_sample_landscape_file(self, width=1920, height=1080) -> str:
        path = os.path.join(self.temp_dir, f"sample_{width}x{height}.jpg")
        img = Image.new("RGB", (width, height), (30, 80, 150))
        img.save(path, "JPEG")
        return path

    def test_fit_blurred_background_adaptation(self):
        landscape_path = self._create_sample_landscape_file(1920, 1080)
        portrait_path = self.processor.adapt_image_to_portrait(
            input_path=landscape_path,
            mode=AdaptationMode.FIT_BLURRED_BACKGROUND
        )
        self.assertTrue(os.path.exists(portrait_path))
        with Image.open(portrait_path) as out:
            self.assertEqual(out.size, (1080, 1920))

    def test_center_crop_adaptation(self):
        landscape_path = self._create_sample_landscape_file(1600, 1200)
        portrait_path = self.processor.adapt_image_to_portrait(
            input_path=landscape_path,
            mode=AdaptationMode.CENTER_CROP
        )
        self.assertTrue(os.path.exists(portrait_path))
        with Image.open(portrait_path) as out:
            self.assertEqual(out.size, (1080, 1920))

    def test_smart_focal_crop_adaptation(self):
        landscape_path = self._create_sample_landscape_file(2048, 1152)
        portrait_path = self.processor.adapt_image_to_portrait(
            input_path=landscape_path,
            mode=AdaptationMode.SMART_FOCAL_CROP
        )
        self.assertTrue(os.path.exists(portrait_path))
        with Image.open(portrait_path) as out:
            self.assertEqual(out.size, (1080, 1920))


class TestAttributionAndRegistry(unittest.TestCase):
    """Tests attribution manifest compilation and SQLite registry persistence."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_attribution_")
        self.attr_gen = AttributionGenerator(manifests_dir=self.temp_dir)
        self.registry = AssetRegistry()

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_attribution_manifest_and_youtube_description(self):
        asset1 = MediaAsset(
            asset_id="asset_01",
            title="Black Hole Accretion Disk",
            creator="European Southern Observatory",
            source_repository="Openverse",
            source_url="https://www.eso.org/public/images/eso0849a/",
            download_url="https://cdn.eso.org/eso0849a.jpg",
            license_type=LicenseType.CC_BY,
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution_text="Credit line",
            verification_status=LicenseStatus.APPROVED
        )
        asset2 = MediaAsset(
            asset_id="asset_02",
            title="Earth from Apollo 8",
            creator="NASA",
            source_repository="NASA Image Library",
            source_url="https://images.nasa.gov/details-AS08-14-2383",
            download_url="https://images-assets.nasa.gov/AS08-14-2383~orig.jpg",
            license_type=LicenseType.US_GOV_PD,
            license_url="https://www.nasa.gov/multimedia/guidelines/index.html",
            attribution_text="Credit line",
            verification_status=LicenseStatus.APPROVED
        )

        manifest = self.attr_gen.generate_manifest(
            video_id="test_video_100",
            assets=[asset1, asset2]
        )
        self.assertEqual(manifest.total_assets, 2)
        self.assertIn("VISUAL MEDIA CREDITS", manifest.youtube_description_block)
        self.assertIn("European Southern Observatory", manifest.youtube_description_block)
        self.assertIn("NASA", manifest.youtube_description_block)

        # Save manifest to disk
        saved_path = self.attr_gen.save_manifest(manifest)
        self.assertTrue(os.path.exists(saved_path))
        with open(saved_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data["video_id"], "test_video_100")
            self.assertEqual(len(data["items"]), 2)

    def test_sqlite_asset_registry_crud(self):
        asset = MediaAsset(
            asset_id="reg_test_001",
            title="Saturn Aurora",
            creator="NASA/JPL",
            source_repository="NASA Image Library",
            source_url="https://images.nasa.gov/details-PIA123",
            download_url="https://images-assets.nasa.gov/PIA123~orig.jpg",
            license_type=LicenseType.US_GOV_PD,
            attribution_text="Credit",
            asset_hash="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            width=1920,
            height=1080,
            verification_status=LicenseStatus.APPROVED
        )

        db_id = self.registry.register_asset(asset, job_id="job_unit_test")
        self.assertIsNotNone(db_id)

        # Retrieve by ID
        fetched = self.registry.get_by_asset_id("reg_test_001")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.title, "Saturn Aurora")

        # Retrieve by hash
        by_hash = self.registry.get_by_hash(asset.asset_hash)
        self.assertIsNotNone(by_hash)
        self.assertEqual(by_hash.asset_id, "reg_test_001")


class TestMediaAcquisitionEngine(unittest.IsolatedAsyncioTestCase):
    """Tests the master MediaAcquisitionEngine coordinator."""

    def setUp(self):
        self.temp_storage = tempfile.mkdtemp(prefix="test_engine_storage_")
        self.temp_portrait = tempfile.mkdtemp(prefix="test_engine_portrait_")
        self.temp_manifest = tempfile.mkdtemp(prefix="test_engine_manifest_")
        self.downloader = AssetDownloader(storage_dir=self.temp_storage)
        self.processor = ImageProcessor(output_dir=self.temp_portrait)
        self.attr_gen = AttributionGenerator(manifests_dir=self.temp_manifest)
        self.registry = AssetRegistry()

    def tearDown(self):
        for d in (self.temp_storage, self.temp_portrait, self.temp_manifest):
            if os.path.exists(d):
                shutil.rmtree(d)

    def _create_mock_image_bytes(self) -> bytes:
        img = Image.new("RGB", (800, 600), (40, 80, 120))
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        return buf.getvalue()

    async def test_end_to_end_acquisition(self):
        mock_provider = MagicMock()
        mock_provider.provider_name = "Mock NASA"
        asset1 = MediaAsset(
            asset_id="mock_01",
            title="Black Hole Event Horizon",
            creator="NASA/EHT",
            source_repository="Mock NASA",
            source_url="https://images.nasa.gov/details-01",
            download_url="https://example.com/bh.jpg",
            license_type=LicenseType.US_GOV_PD,
            attribution_text="Credit",
            verification_status=LicenseStatus.APPROVED
        )

        async def mock_search(query, limit=5):
            return [asset1]

        mock_provider.search_media = mock_search

        engine = MediaAcquisitionEngine(
            providers=[mock_provider],
            downloader=self.downloader,
            processor=self.processor,
            registry=self.registry,
            attribution_gen=self.attr_gen
        )

        img_bytes = self._create_mock_image_bytes()
        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Type": "image/jpeg"}
            mock_resp.read.side_effect = [img_bytes, b""]
            mock_url.return_value.__enter__.return_value = mock_resp

            acquired, manifest = await engine.acquire_scene_assets(
                topic_title="What Happens If You Fall Into a Black Hole?",
                category="Space & Astrophysics",
                job_id="test_e2e_job",
                max_assets=1
            )

            self.assertEqual(len(acquired), 1)
            a = acquired[0]
            self.assertIsNotNone(a.local_path)
            self.assertTrue(os.path.exists(a.local_path))
            self.assertIsNotNone(a.portrait_path)
            self.assertTrue(os.path.exists(a.portrait_path))
            with Image.open(a.portrait_path) as out:
                self.assertEqual(out.size, (1080, 1920))
            self.assertEqual(manifest.total_assets, 1)
            self.assertIn("NASA/EHT", manifest.youtube_description_block)


if __name__ == "__main__":
    unittest.main()
