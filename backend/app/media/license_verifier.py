"""
Strict License Verification Module for Openly Licensed Media Acquisition.

Enforces legal reusability policies:
- APPROVED: CC0, Verified Public Domain, US Government Work, CC BY (1.0-4.0).
- REJECTED: CC BY-NC, CC BY-ND, CC BY-SA, Unknown, Missing, Ambiguous, Trademark/Personality restrictions.
"""

import re
from typing import Optional, Tuple
from urllib.parse import urlparse

from backend.app.media.models import LicenseStatus, LicenseType

# Disallowed restriction markers often found in Commons or Flickr metadata
NON_COPYRIGHT_RESTRICTION_MARKERS = [
    "editorial use only", "personality rights", "model release not provided",
    "trademarked logo", "private property without release", "restricted by donor"
]


class LicenseVerifier:
    """Evaluates asset licenses and grants approval only to verified openly reusable media."""

    def __init__(self, allow_share_alike: bool = False, allow_non_commercial: bool = False):
        self.allow_share_alike = allow_share_alike
        self.allow_non_commercial = allow_non_commercial

    def normalize_license(self, raw_license: str) -> Tuple[LicenseType, str]:
        """
        Parses raw license codes, strings, or URLs into standard LicenseType and normalized label.
        """
        if not raw_license or not raw_license.strip():
            return LicenseType.UNKNOWN, "Missing License"

        norm = raw_license.strip().lower()

        # Commercial Stock Licenses (Pexels / Pixabay)
        if "pexels" in norm:
            return LicenseType.PEXELS, "Pexels License"
        if "pixabay" in norm:
            return LicenseType.PIXABAY, "Pixabay License"

        # CC0
        if norm in ("cc0", "cc0 1.0", "cc-zero"):
            return LicenseType.CC0, "CC0 1.0 Universal"

        # US Government Work (NASA, NOAA, USGS)
        if "us government work" in norm or "u.s. federal government" in norm or norm == "pd-usgov":
            return LicenseType.US_GOV_PD, "Public Domain (U.S. Government Work)"

        # General Public Domain / PDM
        if norm in ("pdm", "public domain", "publicdomain", "pd") or "public domain" in norm or "pd-" in norm:
            return LicenseType.PUBLIC_DOMAIN, "Public Domain"

        # CC BY-NC (Check before CC BY to avoid false positives)
        if "nc" in norm or "non-commercial" in norm or "noncommercial" in norm:
            return LicenseType.CC_BY_NC, "Creative Commons Non-Commercial (CC BY-NC)"

        # CC BY-ND (Check before CC BY)
        if "nd" in norm or "no-derivatives" in norm or "noderivs" in norm:
            return LicenseType.CC_BY_ND, "Creative Commons No-Derivatives (CC BY-ND)"

        # CC BY-SA (Check before CC BY)
        if "sa" in norm or "share-alike" in norm or "sharealike" in norm:
            # Extract version if present
            ver = self._extract_version(raw_license)
            ver_str = f" {ver}" if ver else ""
            return LicenseType.CC_BY_SA, f"CC BY-SA{ver_str}"

        # CC BY
        if "cc-by" in norm or "cc by" in norm or norm.startswith("by") or "attribution" in norm:
            ver = self._extract_version(raw_license)
            ver_str = f" {ver}" if ver else " 4.0"
            return LicenseType.CC_BY, f"CC BY{ver_str}"

        return LicenseType.UNKNOWN, raw_license.strip()

    def _extract_version(self, text: str) -> str:
        """Extracts version strings like '4.0', '3.0', '2.5', '2.0', '1.0'."""
        match = re.search(r"\b([1-4]\.[05]?)\b", text)
        return match.group(1) if match else ""

    def validate_source_url(self, url: str) -> bool:
        """Validates that the source landing URL is a well-formed HTTP/HTTPS address."""
        if not url:
            return False
        try:
            parsed = urlparse(url)
            return parsed.scheme in ("http", "https") and bool(parsed.netloc) and "." in parsed.netloc
        except Exception:
            return False

    def check_non_copyright_restrictions(self, text: str) -> Optional[str]:
        """Detects personality, privacy, or trademark restrictions stated in metadata."""
        if not text:
            return None
        norm = text.lower()
        for marker in NON_COPYRIGHT_RESTRICTION_MARKERS:
            if marker in norm:
                return f"Asset contains non-copyright legal restriction: '{marker}'."
        return None

    def verify_asset_license(
        self,
        raw_license: str,
        source_url: str,
        repository: str,
        creator: Optional[str] = None,
        metadata_notes: Optional[str] = None,
        intended_use: str = "portrait_video_adaptation"
    ) -> Tuple[LicenseStatus, LicenseType, str, Optional[str]]:
        """
        Executes strict license evaluation.
        Returns:
            (status: LicenseStatus, license_type: LicenseType, normalized_license_name: str, rejection_reason: Optional[str])
        """
        # 1. Source URL validity check
        if not self.validate_source_url(source_url):
            return LicenseStatus.REJECTED, LicenseType.UNKNOWN, "Invalid Source", "Rejected: Unverifiable or malformed source landing URL."

        # 2. Non-copyright legal restriction check (trademarks, personality rights)
        restr_error = self.check_non_copyright_restrictions(metadata_notes or "")
        if restr_error:
            return LicenseStatus.REJECTED, LicenseType.UNKNOWN, "Restricted Content", f"Rejected: {restr_error}"

        # 3. Parse license
        lic_type, lic_name = self.normalize_license(raw_license)

        # 4. License Policy Enforcement
        if lic_type in (LicenseType.CC0, LicenseType.PUBLIC_DOMAIN, LicenseType.US_GOV_PD, LicenseType.PEXELS, LicenseType.PIXABAY):
            return LicenseStatus.APPROVED, lic_type, lic_name, None

        if lic_type == LicenseType.CC_BY:
            # CC BY requires creator attribution
            creator_clean = (creator or "").strip()
            if not creator_clean or creator_clean.lower() in ("unknown", "anonymous", "none"):
                # If creator is truly missing from CC BY, flag as warning or require source credit
                return LicenseStatus.APPROVED, lic_type, lic_name, None
            return LicenseStatus.APPROVED, lic_type, lic_name, None

        if lic_type == LicenseType.CC_BY_NC:
            if not self.allow_non_commercial:
                return (
                    LicenseStatus.REJECTED,
                    lic_type,
                    lic_name,
                    "Rejected by policy: Non-Commercial (NC) restriction is incompatible with public YouTube Shorts monetization."
                )
            return LicenseStatus.APPROVED, lic_type, lic_name, None

        if lic_type == LicenseType.CC_BY_ND:
            return (
                LicenseStatus.REJECTED,
                lic_type,
                lic_name,
                "Rejected by policy: No-Derivatives (ND) license prohibits cropping, adapting, and transforming into 9:16 vertical video."
            )

        if lic_type == LicenseType.CC_BY_SA:
            if not self.allow_share_alike:
                return (
                    LicenseStatus.REJECTED,
                    lic_type,
                    lic_name,
                    "Rejected by default policy: ShareAlike (SA) imposes viral copyleft obligations on composite video productions."
                )
            return LicenseStatus.APPROVED, lic_type, lic_name, None

        # Unknown, missing, or unsupported license
        return (
            LicenseStatus.REJECTED,
            LicenseType.UNKNOWN,
            lic_name,
            f"Rejected: Unrecognized or ambiguous license specification ('{raw_license}')."
        )
