# CrazzyDocs Story Completion & Duration Policy

## 1. Overview & Problem Definition

In earlier pipeline iterations, short-form video finalization attempted to enforce a rigid target duration (e.g., exactly 55.0 seconds) by trimming the narrative video track:
$$\text{Cut Narrative Track to 52.5s} + 2.5\text{s Outro} = 55.0\text{s}$$

This produced a critical production flaw:
- The conclusion, payoff, or final spoken sentence was frequently cut off mid-word or mid-sentence.
- The narrative transition into the outro felt abrupt and unnatural.

## 2. The Additive Outro Policy (Strict Invariant)

Under the **Additive Outro Policy**, the main video story is **never trimmed** to fit an arbitrary target. The CrazzyDocs branded outro is strictly additive:

$$\text{Final Video Duration} = \text{Complete Main Video Duration} + \text{Outro Duration (2.0s)}$$

### Core Rules:
1. **Zero Narrative Truncation**: Every spoken word, sound effect, and animation storyboard beat must be fully rendered and resolved to its natural conclusion.
2. **Planning-Time Duration Calibration**: The 50–60 second Shorts duration window is achieved through **script word count calibration at generation time**, not destructive cutting at render time.
3. **Zero Subtitle Outro Leakage**: All on-screen dialogue and subtitle events must terminate prior to the outro transition.

---

## 3. Planning-Time Word Count Calibration

The `ScriptGenerator` (`backend/app/content/script_generator.py`) calibrates script length based on empirical speech rates:

$$\text{Target Spoken Duration} = \frac{\text{Word Count}}{\text{Average Words Per Second (2.3 wps)}}$$

| Parameter | Target Range | Spoken Duration | With 2.0s Outro | YouTube Shorts Standard |
|---|---|---|---|---|
| **Underlength** | $< 115$ words | $< 50\text{s}$ | $< 52\text{s}$ | Rejected by validation |
| **Optimal Production** | **118–128 words** | **51–55s** | **53–57s** | **Full Compliance (100%)** |
| **Overlength** | $> 135$ words | $> 58\text{s}$ | $> 60\text{s}$ | Rejected (exceeds 60s Shorts limit) |

---

## 4. Universal Compositor 8-Point Validation Standard

The `UniversalCompositor.append_branded_outro` method in `backend/app/rendering/compositing/compositor.py` executes an 8-point automated validation before finalizing any video:

```
                  +-----------------------------------+
                  |  1. Main Video Exists (>= 1.0s)   |
                  +-----------------+-----------------+
                                    |
                  +-----------------v-----------------+
                  |  2. Main Video Stream Validation  |
                  +-----------------+-----------------+
                                    |
                  +-----------------v-----------------+
                  |  3. Outro Exists (2.0s +/- 0.25s) |
                  +-----------------+-----------------+
                                    |
                  +-----------------v-----------------+
                  |  4. Subtitle Zero Leakage Check   |
                  +-----------------+-----------------+
                                    |
                  +-----------------v-----------------+
                  |  5. Normalized Stream Concat      |
                  +-----------------+-----------------+
                                    |
                  +-----------------v-----------------+
                  |  6. Non-Empty Output File Check   |
                  +-----------------+-----------------+
                                    |
                  +-----------------v-----------------+
                  |  7. Duration Invariant (|Δ|<=0.35)|
                  +-----------------+-----------------+
                                    |
                  +-----------------v-----------------+
                  |  8. Stream Integrity (1080x1920)  |
                  +-----------------------------------+
```

### Validation Point Details:
1. **Main Video Existence & Minimum Duration**: Ensures the narrative video exists and duration $\ge 1.0\text{s}$.
2. **Video Stream Validity**: Probes width and height via JSON metadata (`1080x1920`).
3. **Outro Duration Range**: Asserts outro duration is calibrated to $2.0 \pm 0.25\text{s}$.
4. **Zero Subtitle Leakage**: Parses all dialogue end times in the ASS subtitle file. Raises `CompositorValidationError` if any dialogue event extends past $\text{main\_dur} + 0.15\text{s}$.
5. **Concat Execution**: Uses FFmpeg filter graph `concat=n=2:v=1:a=1` with SAR and FPS normalization (`setsar=1, fps=30, scale=1080:1920, aformat=sample_rates=48000:channel_layouts=stereo`).
6. **File Existence & Integrity**: Asserts final file size $> 0$ bytes.
7. **Duration Invariant Verification**: Checks $|\text{final\_dur} - (\text{main\_dur} + \text{outro\_dur})| \le 0.35\text{s}$.
8. **Final Stream Check**: Confirms resolution is $1080\times 1920$, constant 30 FPS, and stereo audio stream is active.
