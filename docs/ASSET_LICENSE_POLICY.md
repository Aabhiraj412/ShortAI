# Asset License Policy & Legal Verification Standard
## Automated Copyright Compliance for YouTube Shorts Video Production

**Project Root:** `D:\AI Arts\AI\`  
**Date:** November 10, 2026  
**Module:** `backend/app/media/license_verifier.py`  
**Status:** Enforced Across All Media Providers

---

## 1. Legal Stance & Operating Principles

The studio enforces a **conservative, risk-averse automated copyright policy**. Because generated YouTube Shorts are intended for public distribution and potential monetization on YouTube, every external visual asset must hold an unambiguous, legally verifiable license permitting:
1. Commercial distribution on public platforms.
2. Modification, cropping, scaling, and adaptation into 9:16 vertical video compositions.
3. Distribution without viral copyleft obligations on composite video files.

> [!CAUTION]
> An API metadata tag is **not** definitive proof of copyright status. The system normalizes and verifies license deeds, validates original landing URLs, and cross-checks metadata for non-copyright legal restrictions.

---

## 2. Approved vs. Rejected License Taxonomy

| License Code | License Name | Status | Permitted Usage & Obligations | Rejection Rationale |
| :--- | :--- | :---: | :--- | :--- |
| **CC0 1.0** | Creative Commons Zero Universal | **APPROVED** | Free commercial reuse, adaptation, zero attribution required (courtesy credit recommended). | N/A |
| **Public Domain** | General Public Domain | **APPROVED** | Free commercial reuse and adaptation without restrictions. | N/A |
| **US Gov Work** | U.S. Federal Government Public Domain | **APPROVED** | NASA, NOAA, USGS media. Must preserve institutional credit (e.g. *"NASA/JPL"*). | N/A |
| **CC BY (1.0 - 4.0)** | Creative Commons Attribution | **APPROVED** | Commercial reuse and adaptation permitted **provided complete legal attribution** is provided in the video description. | N/A |
| **CC BY-NC** | Non-Commercial | **REJECTED** | Restricted to personal/non-profit use. | **REJECTED:** Incompatible with potential YouTube monetization and commercial distribution. |
| **CC BY-ND** | No-Derivatives | **REJECTED** | Prohibits any adaptation, editing, or transformation. | **REJECTED:** Cropping and adapting into 9:16 vertical video constitutes a derivative work. |
| **CC BY-SA** | Share-Alike | **REJECTED** | Requires downstream derivative works to be released under the identical SA license. | **REJECTED BY DEFAULT:** Imposes viral copyleft obligations on the entire final MP4 composite. |
| **Unknown / Missing** | Ambiguous or Missing | **REJECTED** | Incomplete provenance. | **REJECTED:** Assets lacking verifiable licensing terms cannot be legally cleared. |

---

## 3. Non-Copyright Legal Restrictions

Beyond copyright licenses, visual assets can be restricted by ancillary legal rights:

1. **Personality & Publicity Rights:** If metadata notes state *"personality rights apply"* or *"model release not provided"*, the asset is automatically rejected.
2. **Trademark Restrictions:** Media containing prominent trademarked logos or private property requiring commercial property releases are flagged and rejected.
3. **Editorial Use Only:** Assets restricted exclusively to newspaper/editorial reporting without commercial rights are rejected.

---

## 4. Verification Flow & Decision Matrix

```python
def verify_asset_license(raw_license, source_url, repository, creator, metadata_notes):
    # 1. Source URL Validation: Must be valid HTTP/HTTPS address
    # 2. Non-Copyright Restriction Check: Rejects "editorial use only", "personality rights"
    # 3. License Normalization: Maps variations to standard LicenseType enum
    # 4. Policy Check:
    #    - CC0, US_GOV_PD, PUBLIC_DOMAIN -> APPROVED
    #    - CC_BY -> APPROVED (logs attribution requirement)
    #    - CC_BY_NC, CC_BY_ND, CC_BY_SA, UNKNOWN -> REJECTED (with specific reason)
```

### Concrete Decision Examples from Test Suite:
- **Case 1 (NASA PIA12345):** `Public Domain (U.S. Government Work)` $\rightarrow$ **APPROVED** (Courtesy NASA/JPL).
- **Case 2 (ESO Black Hole):** `CC BY 4.0` from Openverse $\rightarrow$ **APPROVED** (Attribution: *"Supermassive Black Hole" by ESO / L. Calçada via Openverse, licensed under CC BY 4.0*).
- **Case 3 (Flickr Photo):** `CC BY-NC 4.0` from Openverse $\rightarrow$ **REJECTED** (Reason: *"Non-Commercial (NC) restriction is incompatible with public YouTube Shorts monetization"*).
- **Case 4 (Wikimedia File):** `CC BY-ND 3.0` $\rightarrow$ **REJECTED** (Reason: *"No-Derivatives (ND) license prohibits cropping, adapting, and transforming into 9:16 vertical video"*).
- **Case 5 (Wikimedia File):** `CC BY 4.0` with note *"Editorial use only: personality rights apply"* $\rightarrow$ **REJECTED** (Reason: *"Asset contains non-copyright legal restriction: 'personality rights'"*).
