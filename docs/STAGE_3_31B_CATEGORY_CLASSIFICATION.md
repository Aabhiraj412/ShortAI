# Stage 3.31B: Category Classification Repair

**Status:** implemented and tested. **Disabled** (flag `category_classifier_v2` defaults to `off`; `shadow` and `on` are supported). Not deployed; not committed.

## Problem (audit §11)

`CreativeProductionPolicy.classify_topic` (v1) uses plain substring matching:
- `"era"` matches "several", `"war"` matches "award".
- Ties go to the first registered category, which is History.
- With no evidence at all it falls back to Everyday Science.

As a result, 13 of 42 produced topics were labelled History, including a supernova story and a dwarf-star study.

**v1 was left untouched.** Historical categories are not rewritten.

## What was built: `backend/app/content/category_classifier.py` (new)

`CategoryClassifierV2.classify(title, text, hint_key=None) -> ClassificationResult`.

**Evidence types, strongest first:**

1. **Definition context (entity context).** The subject type is read from the opening "X is/was a(n) …" sentence, cut at relative clauses. For example, "is a stratovolcano" points to Geography and "is a proposed space probe" to Space. "Is a song by", "is a genre of fiction", "is a footballer", "is a political theorist" or "terrorist attacks" mark the subject as out of scope.
2. **Anchors.** A short, category-specific list of unambiguous terms (supernova, galaxy, light-years, strait, excavation, …). It **supplements** the existing policy keywords and does not replace them.
3. **Policy keywords.** The existing `CategorySpec.keywords`, matched on word boundaries with plural handling (galaxy / galaxies).

**Weighting rules:**
- Weight by position: title 3.0, lead (first two sentences) 1.5, body 0.5. Each term counts at most twice.
- Phrases are weighted ×1.25.
- **Ambiguous terms** ("era", "war", "cell", "orbit", "bank", "engine", "species", …) count at 25% outside the title. "War" in "World War I" still counts in full, while "war" in a body sentence barely does.
- **Masking.** Phrases containing a keyword but meaning something else are removed before matching: "science fiction", "rock star", "political science", "cell phone", …

**Outcomes:**

| Status | Meaning |
|---|---|
| `CLASSIFIED` | One category is clearly ahead. |
| `AMBIGUOUS` | The runner-up has ≥ 85% of the winner's score. A deterministic tie-break picks one: score, then title evidence, then distinct terms, then the discovery hint, then the category key alphabetically. **History is never a default.** |
| `UNKNOWN` (needs review) | Top score below 3.0. |
| `OUT_OF_SCOPE` | Music, entertainment, sport or politics, with no stronger in-scope evidence. |

**Discovery hint.** The category a search was run for (`wikipedia_search:<key>`), the Phys.org feed's category, or "History" for On-This-Day events adds 2.0. It is evidence, not a verdict: an out-of-scope definition still wins.

## Engine integration (`CuriosityTopicEngine._apply_category`)

| Flag | Behaviour |
|---|---|
| `off` (default) | Nothing runs; the v1 category is used exactly as before. |
| `shadow` | v2 runs and its result is stored on the candidate (`category_v2`), in score metadata and in the candidate ledger. The v1 category is still used for selection. |
| `on` | v2 assigns the category. If no in-scope category is found, the discovery hint is used. If there is no hint, or the subject is out of scope, the candidate gets `category_invalid` and is rejected before ranking as `INVALID_CATEGORY` / `category_gate`. |

**Risk of `on`.** Rejecting UNKNOWN subjects with no hint shrinks the pool. Examples: "Pakistan", and Wikipedia most-read or featured-article items. This is why exhaustion recovery (3.31D) should be enabled before, or together with, the classifier. Shadow mode first is recommended to measure how many candidates would be affected.

## Results on real cases

Replayed offline on the audited titles and texts:

| Topic | v1 | v2 |
|---|---|---|
| "This supernova is 50 million light-years away…" | History | CLASSIFIED Space and Astronomy |
| "Astronomers track a flaring dwarf-star duo" | History | CLASSIFIED Space and Astronomy |
| Mark Satin (political theorist, "award", "era") | History | OUT_OF_SCOPE politics |
| Wei Jingsheng (dissident) | History | OUT_OF_SCOPE politics |
| "Can't Let Go (Mariah Carey song)" | Everyday Science | OUT_OF_SCOPE music |
| Science fiction (genre) | Science | OUT_OF_SCOPE entertainment |
| November 2015 Paris attacks | History | OUT_OF_SCOPE politics |
| Aucanquilcha (stratovolcano, "era") | History | CLASSIFIED Geography |
| Nares Strait | Geography | CLASSIFIED Geography |
| World War I | History | CLASSIFIED History |
| Interstellar Probe (spacecraft) | Space | CLASSIFIED Space and Astronomy |
| DESI weighs the Milky Way | Everyday Science (no evidence) | CLASSIFIED Space and Astronomy |
| Regulus (Turner), a painting | History | UNKNOWN (no art category exists) |
| Pakistan | Everyday Science | UNKNOWN |

## Tests: `backend/tests/test_category_classifier_331b.py` (26 tests)

The 17 parameterized regression cases cover every requested type:

| Requested case | Example used |
|---|---|
| Science containing "era" | inflationary era |
| "award" | chemistry award |
| Supernova and astronomy | supernova, dwarf-star duo |
| Science fiction | the genre article |
| Historical wars | World War I, Battle of Hastings |
| Political / geopolitical | Mark Satin, Wei Jingsheng, Paris attacks |
| Music | a song article |
| Sport | a footballer |
| Geography / natural history | volcano, strait, woodcocks |
| Ambiguous / no evidence | "Thing", a painting |

Other tests:
- Word boundaries and plurals.
- v1 is unchanged, and the v1 substring failure is fixed in v2.
- Determinism, and the tie-break that never defaults to History.
- The hint is evidence, not a verdict.
- Definition context.
- Engine `off` / `shadow` / `on` behaviour, with the hint fallback.
- An out-of-scope candidate is rejected end to end with `INVALID_CATEGORY` recorded in the ledger.

## Proposed (not done): read-only historical recategorization report

A script could run v2 over the 42 `content_ledger` and 62 `creative_identity_registry` rows and print v1 versus v2 side by side, without writing anything. It would show the size of the History over-count. It was not produced, because the brief asks for a proposal first.

## Known limitations

- The lexicons are hand-curated and English-only. Subjects outside the 20 policy categories (art, religion, law) come out UNKNOWN.
- Country, city and person articles without a story hook come out UNKNOWN or OUT_OF_SCOPE. That is correct for labelling, but story suitability itself is 3.31f.
