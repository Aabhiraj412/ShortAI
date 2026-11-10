# CrazzyDocs Content Diversity & Deduplication System

## 1. Overview & Objective

Autonomous video generation systems run the critical risk of content repetition—reusing previously covered historical events, recycling opening hooks, or selecting near-identical topics across consecutive runs.

The **CrazzyDocs Content Diversity & Deduplication System** enforces strict uniqueness through:
1. A **persistent SQLite and JSON ledger** of all generated content.
2. A **multi-dimensional collision detector** measuring fingerprint, entity, title, and hook similarity.
3. An **objective multi-hook evaluation engine** testing 3 distinct curiosity angles per topic.

---

## 2. Persistent Content Ledger Architecture

Every approved video production is recorded in:
- SQLite table `content_ledger` in `backend/studio.db`
- Mirror JSON registry in `backend/app/content/topic_history.json`

### Record Schema (`ContentRecord`)

```python
@dataclass
class ContentRecord:
    topic_id: str                # e.g., "topic_stage_3_2_marathon"
    topic_title: str             # e.g., "The 1904 Olympic Marathon: The Wildest Race in History"
    opening_hook: str            # e.g., "[Curious] In 1904, an Olympic runner won gold using rat poison and brandy."
    main_subject: str            # e.g., "1904 Olympic Marathon"
    key_entities: List[str]      # e.g., ["1904 Olympics", "Thomas Hicks", "Strychnine", "Felix Carvajal"]
    script_fingerprint: str      # SHA-256 hash of normalized script text
    category: str                # e.g., "Ancient History & Mysteries"
    visual_style: str            # e.g., "pop_info"
    created_at: str              # ISO-8601 UTC timestamp
    publication_status: str      # "approved"
```

---

## 3. Multi-Dimensional Collision Detector

When a candidate topic is evaluated by `TopicLedger.check_content_collision`, it is assessed across 4 independent dimensions:

1. **Exact Script Fingerprint (SHA-256)**:
   - Strips tags, normalizes whitespace and lowercase.
   - Any identical fingerprint is immediately rejected ($1.00$ duplicate score).
2. **Key Entity Jaccard Overlap**:
   - Calculates $J(A, B) = \frac{|A \cap B|}{|A \cup B|}$.
   - Overlap $\ge 0.50$ is rejected (e.g., attempting to generate another Barings Bank Short).
3. **Title Semantic Overlap**:
   - Word-level intersection excluding common stopwords.
   - Similarity $\ge 0.55$ triggers duplicate rejection.
4. **Opening Hook Overlap**:
   - Compares the first 2-second hook against all recorded hooks.
   - Similarity $\ge 0.55$ triggers duplicate rejection.

### Validation Example:
- **Historical Bank Story**: `duplicate=True, score=0.75` $\rightarrow$ **BLOCKED**.
- **1904 Olympic Marathon**: `duplicate=False, score=0.00` $\rightarrow$ **ADMITTED**.

---

## 4. Multi-Hook Generation & Objective Evaluation

For each vetted topic, the `HookEvaluator` (`backend/app/content/hook_evaluator.py`) generates and evaluates 3 distinct opening hook archetypes:

```
                          [ Candidate Generation ]
                                     |
    +--------------------------------+-------------------------------+
    |                                |                               |
[ 1. Paradox / Contradiction ]  [ 2. Shocking Event / Fact ]   [ 3. High-Stakes Question ]
    |                                |                               |
    +--------------------------------+-------------------------------+
                                     |
                        [ 6-Criteria Scoring Matrix ]
               (Curiosity, Surprise, Clarity, Accuracy, Visual, Payoff)
                                     |
                        [ Integrity Gate: Accuracy >= 9.0 ]
                                     |
                           [ Highest Score Selected ]
```

### 4.1 Evaluation Criteria & Weights

$$\text{Composite Score} = 0.25 C + 0.20 S + 0.20 Cl + 0.15 A + 0.10 V + 0.10 P$$

- **Curiosity ($C$, weight 0.25)**: Strength of the curiosity gap opened in the first 2.0 seconds.
- **Surprise ($S$, weight 0.20)**: Counter-intuitive or shocking nature of the premise.
- **Clarity ($Cl$, weight 0.20)**: Immediacy of comprehension without convoluted phrasing.
- **Factual Accuracy ($A$, weight 0.15)**: Strict veracity; sensationalized falsehoods receive $<9.0$ and are disqualified.
- **Visual Potential ($V$, weight 0.10)**: Feasibility of punchy visual depiction in the opening frames.
- **Payoff Promise ($P$, weight 0.10)**: Believable resolution guaranteed by the narrative.

### 4.2 Case Study: 1904 Olympic Marathon Evaluation Report

| Candidate | Archetype | Text | Word Count | Score | Status |
|---|---|---|---|---|---|
| **Hook 1** | **Paradox / Contradiction** | `[Curious] In 1904, an Olympic runner won gold using rat poison and brandy.` | **12 words** | **9.63 / 10** | **SELECTED** |
| Hook 2 | Shocking Event | `[Surprised] The 1904 Olympic marathon was the most chaotic race in human history.` | 12 words | 8.86 / 10 | Alternate |
| Hook 3 | High-Stakes Question | `[Curious] How did a runner get chased into a cornfield by wild dogs and still almost win an Olympic medal?` | 19 words | 9.17 / 10 | Alternate |

**Selection Rationale**:
Candidate 1 maximizes immediate curiosity ($9.8/10$) and surprise ($9.6/10$) within 12 words while maintaining 100% factual accuracy ($10.0/10$).
