# Stage 3.32C: Confidence and Priority Model

**Source:** `backend/app/autonomy/recommendations/scoring.py`. Every recommendation stores both scores with all their components (`scoring` in the API, "Details" in the dashboard), so any number can be recomputed by hand.

## 1. Evidence type: observation versus hypothesis

Every recommendation has a factual **observation** (`description`) and a separate **root-cause hypothesis** with a strength. The dashboard shows the strength as one of three labels.

| Strength | Label shown | Used when |
|---|---|---|
| CONFIRMED | **Confirmed observation** | The data itself shows the mechanism. Examples: the FFmpeg message states "padded dimensions cannot be smaller than input"; the API logged `insufficient_scope`; Google set a quota block. |
| STRONG | **Strongly supported hypothesis** | A clear concentration or correlation in the data. Examples: ≥ 60% of failures at one stage; one provider holds ≥ 60% of accepted candidates. |
| WEAK | **Weak hypothesis** | Plausible, but the data cannot attribute it. Example: expirations whose stage was not recorded. |

**A suspected cause is never stated as fact.** WEAK hypotheses say what the data cannot show.

## 2. Confidence (0–1)

```
confidence = strength_base × evidence_factor × consistency_factor × completeness
  strength_base      CONFIRMED 0.95 · STRONG 0.75 · WEAK 0.45
  evidence_factor    0.5 + 0.5 × min(1, sample_size / (2 × min_sample))
  consistency_factor 0.7 + 0.3 × consistency          (consistency 0..1, e.g. the dominant pattern's share)
  completeness       1.0 actual · 0.8 partial · 0 unavailable (the 3.32A status of the sources used)
labels: HIGH ≥ 0.70 · MEDIUM ≥ 0.45 · LOW < 0.45
```

| Component | Why it is there |
|---|---|
| strength_base | Ties confidence to how directly the data shows the cause |
| evidence_factor | More evidence than the rule's minimum raises confidence, up to twice the minimum |
| consistency_factor | Uniform evidence (same message, same stage) is more convincing than scattered evidence |
| completeness | Partial source data lowers confidence; unavailable data produces no recommendation at all |

**Worked examples (real data, scratch copy):**

| Recommendation | Calculation | Result |
|---|---|---|
| FFmpeg padding, 3 occurrences (min 2) | 0.95 × (0.5 + 0.5 × min(1, 3/4) = 0.875) × 1.0 × 1.0 = 0.831 | **HIGH** |
| Recovery expirations, WEAK, 83 finished (min 10), consistency = min(1, 42/50) = 0.84 | 0.45 × 1.0 × 0.952 × 1.0 = 0.429 | **LOW** |
| `videos.update insufficient_scope`, CONFIRMED, n = 2 (min 1) | 0.95 × 1.0 × 1.0 × 1.0 = 0.95 | **HIGH** |

## 3. Priority (0–100)

```
priority = 35·severity + 20·impact + 15·frequency + 15·recency + 10·confidence + 5·affected_jobs
  severity       critical 1.0 · high 0.75 · medium 0.5 · low 0.25 (set by the rule from its thresholds)
  impact         0..1 production impact declared by the rule (lost cycles 1.0 … wording/classifier 0.2)
  frequency      min(1, log2(1 + occurrences) / log2(21))      (20+ occurrences = 1.0)
  recency        1.0 if last seen ≤ 6 h ago, falling linearly to 0.2 at the end of the window; 0.5 if unknown
  confidence     the confidence score above
  affected_jobs  min(1, jobs / 10)
labels: CRITICAL ≥ 80 · HIGH ≥ 60 · MEDIUM ≥ 40 · LOW < 40
```

**Priority and confidence are deliberately separate.** "Many cycles exhaust their recovery windows" is **CRITICAL priority (94)** but **LOW confidence (0.43)**: the problem is large and certain, but its cause is not known. The dashboard shows both side by side.

## 4. Tests (`test_recommendation_engine_332c.py`)

- **Confidence:** CONFIRMED > STRONG > WEAK; more samples, higher consistency and full completeness each raise it; labels match the thresholds; the components are returned.
- **Priority:** the critical and low examples land in their bands; recency is 1.0 within 6 h, below 0.5 after 6 days and 0.5 when unknown; high > medium severity; the score equals the weighted sum of its components.
