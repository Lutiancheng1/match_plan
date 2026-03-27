# Round 3 Scoreboard OCR Auxiliary Benchmark Summary

Date: 2026-03-26

Purpose:

- Test whether focusing only on the top scoreboard / match-clock strip improves OCR-like extraction.

Dataset:

- Gold clips manifest:
  `/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/meta/current_golden_sample_manifest.json`
- Sample size in this validation run:
  `4`

Input strategy:

- Extract `3` key frames from each clip
- Crop only the top strip from each frame
- Merge cropped strips into one contact sheet
- Ask the model to output only:
  - `score_detected`
  - `match_clock_detected`
  - `confidence`

## Validation Result

Model:

- `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`

Metrics:

- `json_valid_rate = 1.0`
- `score_exact_match_rate = 0.25`
- `clock_exact_match_rate = 0.0`
- `clock_minute_match_rate = 0.5`
- `avg_latency_ms = 1486.76`

Output directory:

- `/Users/niannianshunjing/match_plan/analysis_vlm/reports/Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK__scoreboard_round3__1774525929`

## Conclusion

This is the first benchmark direction that clearly improved OCR-related behavior:

- latency dropped compared with round 2
- score extraction started to become partially correct
- clock minute extraction improved materially

It is still not training-ready, but it is a better direction than:

- full-frame single image
- generic multi-frame contact sheet without scoreboard-focused cropping

## Stage Decision

Next priority should be:

1. expand this scoreboard OCR benchmark to larger sample size
2. tune crop geometry
3. test the same scoreboard OCR benchmark on:
   - `Qwen2.5-VL-7B-Instruct-4bit`
   - `InternVL3-38B-4bit`
4. keep betting/timeline data as labels and evaluation reference, not as direct model input for this pure-vision baseline stage
