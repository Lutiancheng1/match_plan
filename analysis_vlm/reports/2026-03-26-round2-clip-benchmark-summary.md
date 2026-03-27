# Round 2 Clip Benchmark Summary

Date: 2026-03-26

Dataset:

- Gold clips manifest:
  `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/meta/current_golden_sample_manifest.json`
- Total clips:
  `24`

Input strategy:

- Extract `3` key frames from each clip
- Merge them into a single horizontal contact sheet
- Ask the model for:
  - `scene_type`
  - `score_detected`
  - `match_clock_detected`
  - `event_candidates`
  - `confidence`
  - `explanation_short`

## Result Table

| Model | JSON valid rate | Score exact | Clock exact | Clock minute | Strong-event predicted | Avg latency |
|---|---:|---:|---:|---:|---:|---:|
| Qwen2.5-VL-7B-Instruct-4bit | 0.8750 | 0 / 24 | 0 / 24 | 0 / 24 | 21 / 24 | 4695.99 ms |
| Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK | 1.0000 | 0 / 24 | 0 / 24 | 0 / 24 | 22 / 24 | 4440.04 ms |

## Per-run directories

- Qwen2.5-VL-7B:
  `/Users/niannianshunjing/match_plan/analysis_vlm/reports/Qwen2.5-VL-7B-Instruct-4bit__clip_round2__1774525433`
- Qwen3.5-VL-35B:
  `/Users/niannianshunjing/match_plan/analysis_vlm/reports/Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK__clip_round2__1774525548`

## Interpretation

### What worked

- The round-2 runner itself is usable.
- Contact-sheet input is more compatible than sending multiple independent images.
- Both models can return scene-level judgments from short clips.

### What did not work yet

- Neither model achieved usable score extraction on this round.
- Neither model achieved usable clock extraction on this round.
- Both models strongly over-predicted event candidates:
  - many clips were tagged as if they contained notable events
  - this is too noisy for training-grade event labeling

### Model comparison

#### Qwen2.5-VL-7B-Instruct-4bit

- Still the better lightweight online baseline from round 1.
- But in round 2:
  - JSON dropped to `87.5%`
  - OCR-like fields collapsed
  - strong-event prediction became too trigger-happy

Conclusion:

- Keep for online lightweight observation only.
- Do not trust it yet for structured multi-frame short-clip labeling.

#### Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK

- Round-2 JSON stability stayed at `100%`
- Scene type outputs were more coherent than 7B
- But score/clock still failed on this contact-sheet setup

Conclusion:

- Still the current best offline reviewer candidate
- But not yet good enough to serve as automatic labeler for score/clock on short clips

## Stage Decision

Do **not** move to training yet.

The correct next step is:

1. improve label quality for round-2 tasks
2. refine prompt and extraction strategy for score/clock
3. test alternate visual layouts for multi-frame understanding
4. only after round-2 quality becomes useful, consider building training-ready clip labels

## Immediate next action

Prioritize:

- stronger label skeletons
- better OCR-oriented prompting
- possibly cropped scorebox/clock auxiliary inputs

Training is not the next bottleneck yet.
