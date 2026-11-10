# Media Provider Integration Specification
## Official Non-Scraping API Integrations for Openverse, Wikimedia Commons, and NASA

**Project Root:** `D:\AI Arts\AI\`  
**Date:** November 10, 2026  
**Module:** `backend/app/media/providers/`  
**Interface:** `BaseMediaProvider`

---

## 1. Provider Architecture & Common Interface

All media providers inherit from `BaseMediaProvider` (`backend/app/media/providers/base_provider.py`), guaranteeing an extensible, unified contract:

```python
class BaseMediaProvider(ABC):
    def __init__(self, provider_name: str, timeout_sec: float = 6.0): ...

    @abstractmethod
    async def search_media(self, query: str, limit: int = 10) -> List[MediaAsset]: ...
```

---

## 2. Integrated Provider Details

### 2.1 NASA Image & Video Library (`NASAProvider`)
- **Endpoint:** `https://images-api.nasa.gov/search`
- **Method:** `GET`
- **Authentication:** None required (Public Access).
- **Target Content:** Astronomy, planetary exploration, spacecraft, astronomical phenomena.
- **Parameters:** `q={query}&media_type=image&page_size={limit}`
- **Asset URL Resolution:** Derives original full-resolution master files (`~orig.jpg` or `~large.jpg`) from thumbnail manifest pointers.
- **Licensing:** Works of the U.S. Federal Government, Public Domain. Credit lines standardized as `"courtesy NASA/[Center]"`.

### 2.2 Wikimedia Commons (`WikimediaProvider`)
- **Endpoint:** `https://commons.wikimedia.org/w/api.php`
- **Method:** `GET`
- **Authentication:** None required; identification via standard `User-Agent`.
- **Target Content:** Scientific diagrams, historical records, geographical archives.
- **Parameters:**
  ```
  action=query
  generator=search
  gsrsearch=File:{query}
  gsrnamespace=6
  gsrlimit={limit}
  prop=imageinfo
  iiprop=url|size|extmetadata|mime
  format=json
  ```
- **Metadata Extraction:** Parses `extmetadata` for `Artist`, `LicenseShortName`, `LicenseUrl`, `Credit`, and `Restrictions`. Strips HTML markup from contributor fields.

### 2.3 Openverse (`OpenverseProvider`)
- **Endpoint:** `https://api.openverse.org/v1/images/`
- **Method:** `GET`
- **Authentication:** None required (Open Access Catalog).
- **Target Content:** Peer-curated Creative Commons photography and vector illustrations.
- **Parameters:** `q={query}&license=cc0,pdm,by&page_size={limit}&mature=false`
- **Metadata Extraction:** Extracts direct source landing URL (`foreign_landing_url`), high-res download URL, pre-formatted attribution string, and license version.

---

## 3. Resilience, Timeouts & Failover

1. **Non-Blocking Execution:** Network calls run in thread pools via `asyncio.to_thread()`, preventing blocking of the FastAPI event loop or render worker.
2. **Strict Timeouts:** Every provider request has an independent $6.0\text{s}$ timeout guard. If a provider is slow or offline, `asyncio.gather(..., return_exceptions=True)` collects results from healthy providers without failing the entire acquisition task.
3. **Graceful Degradation:** If all external providers fail or network is unavailable, the studio falls back to locally rendered procedural graphics and existing background plates.
