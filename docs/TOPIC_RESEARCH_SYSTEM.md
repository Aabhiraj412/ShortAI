# Topic Research & Evaluation System
## Autonomous Topic Discovery, Semantic Deduplication, and Primary Source Fact Verification

**Project Root:** `D:\AI Arts\AI\`  
**Date:** November 10, 2026  
**Module:** `backend/app/content/` (`topic_engine.py`, `duplicate_detector.py`, `fact_verifier.py`)  
**Standard:** Local-First, Zero-Marginal-Cost Architecture

---

## 1. Topic Discovery Architecture

The Topic Discovery Engine identifies, scores, and ranks candidate subjects across 10 educational categories without requiring paid third-party search APIs:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   TOPIC DISCOVERY ENGINE CHANNELS                      │
├────────────────────────────────┬───────────────────────────────────────┤
│ 1. Accessible Scientific RSS   │ Phys.org, ScienceDaily, NASA Feeds    │
│    (Live Signal Discovery)     │ Timeouts guarded at 1.0s failover     │
├────────────────────────────────┼───────────────────────────────────────┤
│ 2. Curated Evergreen Knowledge │ 50+ deep educational topics across    │
│    (Zero-Cost Offline Pool)    │ Physics, Astronomy, Economics, AI     │
├────────────────────────────────┼───────────────────────────────────────┤
│ 3. Gemini Flash Content Intel  │ High-performing viral topic discovery │
│    (Optional AI Acceleration)  │ Active when GEMINI_API_KEY configured │
└────────────────────────────────┴───────────────────────────────────────┘
```

### 1.1 The 10 Target Categories
1. **Space & Astrophysics:** Black holes, orbital mechanics, planetary geology, gravitational lensing.
2. **Cutting-Edge Technology & AI:** Neural networks, quantum computing, semiconductor lithography.
3. **Bizarre Science & Physics:** Quantum mechanics, thermodynamic paradoxes, fluid dynamics.
4. **Computer Science & Algorithms:** Hash tables, graph traversal, cryptographic hashing, sorting.
5. **Ancient History & Mysteries:** Antikythera mechanism, Roman concrete, Archimedes' screw.
6. **Human Psychology & Paradoxes:** Infantile amnesia, cognitive biases, optical illusions.
7. **Business & Global Economics:** Swanson's Law, cost curves, market deflation, network effects.
8. **Engineering Marvels:** Aerogels, space telescopes, suspension bridges, deep-sea cables.
9. **Mindblowing Earth Facts:** Mantle oceans, magnetic pole reversals, subterranean geology.
10. **General Educational Curiosities:** Everyday psychology, retail staging, consumer habits.

---

## 2. Multi-Dimensional Scoring Function

Every topic candidate is evaluated against six quantitative criteria on a $1.0 - 10.0$ scale:

| Scoring Dimension | Weight | Evaluation Objective |
| :--- | :---: | :--- |
| **Freshness** | $0.20$ | Timeliness or recent scientific consensus updates. |
| **Search Interest** | $0.20$ | General public curiosity and persistent search volume. |
| **Virality Potential** | $0.20$ | Psychological hook strength and counter-intuitive nature. |
| **50–60s Feasibility** | $0.20$ | Ability to completely explain the core mechanism in 55 seconds without superficial rushing. |
| **Storytelling Potential**| $0.10$ | Natural fit into the 5-stage storytelling progression (Hook $\rightarrow$ Payoff). |
| **Uniqueness** | $0.10$ | Distinctiveness compared to common YouTube Shorts tropes. |

$$\text{Composite Score} = 0.20(\text{Fr}) + 0.20(\text{SI}) + 0.20(\text{Vi}) + 0.20(\text{Fe}) + 0.10(\text{St}) + 0.10(\text{Un})$$

Candidates must achieve a composite score $\ge 7.50$ to qualify for production consideration.

---

## 3. Semantic Deduplication Algorithm

To prevent repeating topics or generating near-duplicate variations of recent shorts, `DuplicateDetector` applies a multi-layered lexical analysis:

1. **Title Normalization:** Converts to lowercase, strips non-alphanumeric punctuation, standardizes whitespace.
2. **Token Filtering & Stemming:** Strips common English stop words (`"the"`, `"why"`, `"what"`, `"is"`, `"how"`) and filters tokens with length $\le 2$.
3. **Prefix / Stem Matching:** Compares word stems and prefixes for words with length $\ge 4$ (e.g. `"store"` vs `"stores"`).
4. **Hybrid Overlap Metrics:**
   - **Stemmed Jaccard:** Concept overlap across the token union:
     $$\text{Jaccard} = \frac{|K_1 \cap_{\text{stem}} K_2|}{|K_1 \cup_{\text{stem}} K_2|}$$
   - **Subset Containment:** Ratio of shared concepts relative to the shorter title:
     $$\text{Subset Overlap} = \frac{|K_1 \cap_{\text{stem}} K_2|}{\min(|K_1|, |K_2|)}$$
   - **Sequence Edit Distance:** Character-level `SequenceMatcher` ratio.
   - **Composite Metric:**
     $$\text{Similarity} = \max\left(0.50 \cdot \text{Jaccard} + 0.35 \cdot \text{Subset} + 0.15 \cdot \text{Sequence},\; 0.85 \cdot \text{Subset}\right)$$

> [!IMPORTANT]
> If $\text{Similarity} \ge 0.60$ against any topic in the SQLite database or active production batch, the candidate is **automatically rejected as a duplicate**.

---

## 4. Fact Verification & Primary Source Provenance

The system enforces factual integrity by requiring every factual claim to be anchored in primary literature:

### 4.1 Authoritative Primary Repositories
- **Space & Astrophysics:** NASA (`nasa.gov`), ESA (`esa.int`), Jet Propulsion Laboratory (`jpl.nasa.gov`).
- **Peer-Reviewed Scientific Literature:** *Nature* (`nature.com`), *Science* (`science.org`), arXiv (`arxiv.org`), ScienceDirect.
- **Institutional & Global Data:** International Energy Agency (`iea.org`), USGS (`usgs.gov`), NOAA (`noaa.gov`), CERN (`cern.ch`).
- **Academic Institutions:** MIT, Stanford, Harvard, Cambridge, Oxford, Caltech.

### 4.2 Claim Taxonomy
- `ESTABLISHED_FACT`: Empirically validated scientific consensus or documented historical artifact.
- `SCIENTIFIC_MEASUREMENT`: Measured numerical value with specific units and published source (e.g. solar LCOE $\$38/\text{MWh}$).
- `HISTORICAL_RECORD`: Documented event with archeological or archival provenance.
- `THEORETICAL_MODEL`: Established physical hypothesis with mathematical formulation (e.g. Hawking radiation).
- `UNVERIFIED_CLAIM`: Speculative conjecture lacking peer-reviewed evidence (triggers immediate rejection).

### 4.3 SQLite Research Dossier Persistence
Every approved topic generates a structured `ResearchDossier` persisted to SQLite table `research_records`:
```sql
CREATE TABLE research_records (
    id INTEGER PRIMARY KEY,
    topic_title VARCHAR(255) NOT NULL,
    category VARCHAR(100),
    factual_claims_json TEXT,  -- Array of claims with sources, URLs, institutions, confidence
    sources_json TEXT,         -- Primary citation metadata
    verification_status VARCHAR(50), -- 'VERIFIED', 'PARTIAL', 'REJECTED'
    verification_notes TEXT,
    created_at DATETIME
);
```
