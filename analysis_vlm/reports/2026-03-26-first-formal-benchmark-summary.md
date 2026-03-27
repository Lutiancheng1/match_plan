# First Formal Benchmark Summary

Date: 2026-03-26

Dataset:

- Gold clips manifest:
  `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/meta/current_golden_sample_manifest.json`
- Total clips:
  `24`

Task:

- Single-frame extraction from each Gold clip
- Forced JSON output with fields:
  - `scene_type`
  - `visible_score`
  - `visible_clock`
  - `confidence`

## Result Table

| Model | JSON valid rate | Score non-null | Clock non-null | Avg latency |
|---|---:|---:|---:|---:|
| Qwen2.5-VL-7B-Instruct-4bit | 1.00 | 20 / 24 | 20 / 24 | 1776.50 ms |
| InternVL3-8B-MLX-4bit | 1.00 | 0 / 24 | 0 / 24 | 2344.90 ms |
| Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK | 1.00 | 24 / 24 | 24 / 24 | 2033.41 ms |
| InternVL3-38B-4bit | 1.00 | 15 / 24 | 15 / 24 | 14126.01 ms |

## Per-run directories

- Qwen2.5-VL-7B:
  `/Users/niannianshunjing/match_plan/analysis_vlm/reports/Qwen2.5-VL-7B-Instruct-4bit__1774524387`
- InternVL3-8B:
  `/Users/niannianshunjing/match_plan/analysis_vlm/reports/InternVL3-8B-MLX-4bit__1774524432`
- Qwen3.5-VL-35B:
  `/Users/niannianshunjing/match_plan/analysis_vlm/reports/Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK__1774524490`
- InternVL3-38B:
  `/Users/niannianshunjing/match_plan/analysis_vlm/reports/InternVL3-38B-4bit__1774524541`

## Interpretation

### Online primary candidate

Current best choice:

- `Qwen2.5-VL-7B-Instruct-4bit`

Reason:

- Fastest among the models that also recovered score and clock reliably.
- `20 / 24` score and clock extraction is already useful for first-stage online baseline.
- Lower latency than InternVL3-8B and much lower than InternVL3-38B.

### Offline reviewer candidate

Current best choice:

- `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`

Reason:

- Best OCR-like extraction in this round:
  - `24 / 24` score
  - `24 / 24` clock
- Latency stayed close to 2 seconds on this benchmark, which is much better than expected.
- Best current reviewer-quality candidate.

### InternVL observations

- `InternVL3-8B-MLX-4bit`
  - JSON was perfectly stable.
  - But it failed to recover visible score and clock on this benchmark set.
  - Keep as a secondary online candidate, not current primary.

- `InternVL3-38B-4bit`
  - JSON was stable and OCR worked partially.
  - But latency is far too high for online use in the current setup.
  - Keep as a secondary offline/teacher candidate, not the first reviewer choice.

### MiniCPM-V 4.5

- Still blocked for OMLX runtime use.
- Not included in this formal round.

## Current Recommendation

For the next implementation step:

- Online baseline:
  - `Qwen2.5-VL-7B-Instruct-4bit`
- Offline reviewer baseline:
  - `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
- Secondary comparison pool:
  - `InternVL3-8B-MLX-4bit`
  - `InternVL3-38B-4bit`

## Next Step

Proceed to the next benchmark layer:

- add multi-frame / short-clip tasks
- add score and clock accuracy against label skeletons
- add scene-type evaluation

Do not move to training yet.
