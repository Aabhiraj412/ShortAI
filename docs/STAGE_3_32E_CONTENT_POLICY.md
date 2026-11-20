# Stage 3.32E: Content Policy

## 1. Sources (what a post may say)

A post states only facts that appear in a **source corpus**:

| Source | Corpus | Use |
|---|---|---|
| Completed production, last 30 days | Research dossier: title, summary, extract, factual bullets | All formats |
| Stage 3.31 candidate (`ELIGIBLE`, last 14 days) | Candidate title and summary sentences | Formats that state one fact |
| CrazzyDocs process facts (fixed) | Three true statements about how the channel works (licensed images credited, research-checked numbers, local rendering) | Behind-the-scenes format |
| Attribution manifest | Number of licensed images and repositories | Behind-the-scenes format, **public** videos only |

**Facts are rejected when they:**
- are shorter than 25 or longer than 260 characters;
- contain a URL;
- start with "Source";
- end in an ellipsis (truncated in the dossier);
- contain an unbalanced quotation mark.

## 2. Topic selection

**Never chosen:**
- a topic used by a Community draft in the last **60 days**;
- a category outside the channel's own categories (those in `content_ledger` and `creative_identity_registry`);
- a category listed in `excluded_categories`.

**Score:**

| Component | Weight |
|---|---|
| Dossier or candidate score | 0.4 |
| Recency | 0.3 |
| Production (vs candidate) | 0.2 (candidate: 0.05) |
| Public video | 0.1 |
| Same category as the last draft | −0.25 |
| Category in the last three drafts | −0.1 |

## 3. Formats and rotation

| Format | Needs | Shape |
|---|---|---|
| Interesting fact | A fact | Opening, fact, question, optional invitation to reply |
| Educational question | A fact | "What do you know about X?", one detail from the research, a question |
| Multiple choice | A fact with a whole number or year | Fill-in-the-blank with A/B/C options; the answer is shown to the operator only |
| Behind the scenes | Always | A true process fact, plus licensed-image counts for a public video |
| Video follow-up | A **public** video | One more fact plus a link to our own Short |
| History / technology trivia | A trivia-type category and a numeric fact | Category trivia line and a question |
| Short curiosity | A fact or hook | One line and a short question |
| Audience engagement | A topic title | A question inviting replies, no factual claim |

**Rotation rules:**
- Formats rotate in the configured order, starting after the last used one.
- A format used on either of the last two drafts is skipped while another feasible format exists.
- Openings, questions and calls to action rotate from per-format pools, avoiding variants used in the last 14 drafts.

## 4. Quality bar for every draft

- A clear opening, one central idea, and an engagement element (a question or an invitation to reply).
- Calls to action are natural ("Tell us in the comments"). Like, subscribe, bell and click-bait wording are refused.

## 5. Validation (text)

| Check | Severity |
|---|---|
| Empty; length outside 40–500 characters | error |
| URL other than our own **public** Short | error |
| Spam wording (subscribe, smash, link in bio, giveaway, "100%", "!!", ...) | error |
| Inappropriate words | error |
| Identical text, or ≥ 60% trigram overlap with a draft in the last 180 days | error |
| A number, or a spelled-out quantity, not in the research (quiz options and the image credit excepted) | review |
| Sensitive subject (religion, ethnicity, ancestry, politics, war, terrorism, suicide, ...) | review |
| Same topic as a draft in the last 60 days | review |
| Sensational or absolute wording not in the research (shocking, first-ever, proven, cure, ...) | warning |
| Generic opener or filler (the channel's `hook_evaluator` rules) | warning |
| More than 30% capitals; more than 3 emoji; repeated sentence or word run | warning |
| No question or invitation to respond | warning |
| ≥ 45% overlap; same opening or question this week; same call to action within 3 days | warning |

## 6. Validation (image)

| Check | Severity |
|---|---|
| File missing or outside the image folder; not JPEG or PNG; cannot be decoded | error |
| Smallest side < 600 or largest side > 4096; aspect ratio not 1:1 (± 1%) | error |
| Size outside 8 KB–5 MB | error |
| SHA-256 differs from the record (tampered); same image as another recent draft | error |
| Licence not accepted; CC BY credit missing from the text | error |
| Provenance not recorded | review |
| Near-duplicate (difference hash ≤ 6 bits); almost blank | warning |

## 7. Outcome

- Any error: **REJECTED**. The draft goes to `VALIDATION_FAILED` and cannot be approved.
- Otherwise any review item: **NEEDS_OPERATOR_REVIEW**.
- Otherwise any warning: **VALID_WITH_WARNINGS**.
- Otherwise: **VALID**.

Only VALID approves normally. The other two need an explicit acknowledgement and a recorded reason; a warning is never treated as success.

## 8. Accepted image licences

**Accepted:** CC0, Public Domain (PDM, "no known copyright restrictions", NASA), and CC BY 2.0, 3.0 and 4.0 (attribution required).

**Excluded:** share-alike, non-commercial and no-derivatives licences, and anything unrecognised.

**Credit format:** `Image: <title> by <creator> (CC BY, via <repository>)`, with HTML entities decoded and length-limited.
