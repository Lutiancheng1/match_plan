# OMLX First Probe Results

Date: 2026-03-26

Image used:

- `/tmp/openclaw/compression_compare_5m/compare_00_05_00.jpg`

Prompt used:

```text
请只输出纯JSON，不要解释。字段固定为 scene_type, visible_score, visible_clock, confidence。
```

## Results

### InternVL3-8B-MLX-4bit

- Status: usable
- Real time: `13.95s`
- Output:

```json
{
  "scene_type": "soccer_match",
  "visible_score": null,
  "visible_clock": "05:00",
  "confidence": 0.95
}
```

Assessment:

- JSON output is stable enough for benchmark entry.
- First probe did not recover the visible score, but clock extraction worked.
- Good online benchmark candidate.

### InternVL3-38B-4bit

- Status: usable
- Real time: `14.03s`
- Output:

```json
{
  "scene_type": "soccer_game",
  "visible_score": "0-0",
  "visible_clock": "05:00",
  "confidence": 0.95
}
```

Assessment:

- JSON output is stable enough for benchmark entry.
- Recovered both visible clock and visible score on the same test image.
- Good offline reviewer benchmark candidate.

### MiniCPM-V-4_5-int4

- Status: blocked
- Real time to failure: `0.18s`
- Error: `HTTP 500 Internal Server Error`

Assessment:

- Model directory is visible to OMLX.
- Runtime compatibility is currently not usable for benchmark.
- Keep installed, but exclude from the first benchmark round until compatibility is fixed.

## Conclusion

Current recommendation for the first benchmark round:

- Online baseline / candidate:
  - `Qwen2.5-VL-7B-Instruct-4bit`
  - `InternVL3-8B-MLX-4bit`
- Offline reviewer candidate:
  - `Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK`
  - `InternVL3-38B-4bit`

Do not include `MiniCPM-V-4_5-int4` in the first round until its OMLX runtime issue is resolved.
