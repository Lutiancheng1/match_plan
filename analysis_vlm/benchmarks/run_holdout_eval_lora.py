#!/usr/bin/env python3
"""Run holdout evaluation using mlx-vlm with LoRA adapter applied."""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

DEFAULT_MODEL = "/Users/niannianshunjing/.omlx/models/Qwen3.5-VL-9B-8bit-MLX-CRACK"
DEFAULT_ADAPTER = "/Users/niannianshunjing/match_plan/analysis_vlm/training/adapters/football_obs_vlora_20260330_102325"
DEFAULT_HOLDOUT_ROOT = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/04_holdout_eval"
)
DEFAULT_OUTPUT_DIR = Path("/Users/niannianshunjing/match_plan/analysis_vlm/reports")

PROMPT = (
    "请只输出纯JSON，不要解释，也不要使用 markdown 代码块。"
    "字段固定为 scene_type, score_detected, match_clock_detected, scoreboard_visibility, "
    "replay_risk, tradeability, event_candidates, confidence, explanation_short。"
    "scene_type 只能是 live_play, replay, scoreboard_focus, crowd_or_bench, stoppage, unknown 之一。"
    "score_detected 必须是类似 1-0 的字符串；看不清时输出空字符串。"
    "match_clock_detected 必须是类似 45:00 的字符串；看不清时输出空字符串。"
    "scoreboard_visibility 只能是 clear, partial, hidden, unknown。"
    "replay_risk 只能是 low, medium, high。"
    "tradeability 只能是 tradeable, watch_only, ignore。"
    "event_candidates 必须是数组；每个元素是对象，字段固定为 label 和 confidence。"
    "label 只能是 goal, red_card, penalty, dangerous_attack, celebration, "
    "replay_sequence, substitution, injury_or_stoppage, none 之一。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Holdout eval with LoRA adapter via mlx-vlm.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--adapter-path", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--holdout-root", type=Path, default=DEFAULT_HOLDOUT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=0, help="Limit frames (0=all)")
    parser.add_argument("--max-tokens", type=int, default=512)
    return parser.parse_args()


def extract_json_block(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        for part in parts:
            stripped = part.strip()
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                text = stripped
                break
    if not (text.startswith("{") and text.endswith("}")):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


def collect_holdout_frames(holdout_root: Path) -> list[dict]:
    frames = []
    records_root = holdout_root / "frame_observation/records"
    images_root = holdout_root / "frame_observation/images"
    for record_path in sorted(records_root.rglob("*.json")):
        record = json.loads(record_path.read_text())
        image_path = Path(record.get("image_path", ""))
        if not image_path.exists():
            match_slug = record_path.parent.name
            frame_id = record_path.stem
            alt = images_root / match_slug / f"{frame_id}.jpg"
            if alt.exists():
                image_path = alt
            else:
                continue
        frames.append({
            "frame_id": record.get("frame_id", record_path.stem),
            "teams": record.get("teams", ""),
            "image_path": str(image_path),
        })
    return frames


def main() -> int:
    args = parse_args()

    from mlx_vlm import load as load_model
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.trainer import apply_lora_layers

    print(f"Loading model: {args.model}")
    model, processor = load_model(args.model)

    if args.adapter_path.exists():
        print(f"Applying LoRA adapter: {args.adapter_path}")
        model = apply_lora_layers(model, str(args.adapter_path))
    else:
        print("No adapter found, running base model.")

    frames = collect_holdout_frames(args.holdout_root)
    print(f"Collected {len(frames)} holdout frames")

    items = frames[:args.limit] if args.limit > 0 else frames
    adapter_name = args.adapter_path.name if args.adapter_path.exists() else "base"
    run_id = f"9B_lora_{adapter_name}__holdout__{int(time.time())}"
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for idx, frame in enumerate(items, 1):
        image_path = frame["image_path"]
        start = time.perf_counter()

        try:
            formatted_prompt = apply_chat_template(
                processor, config=model.config, prompt=PROMPT,
                images=[image_path], num_images=1,
            )
            result = generate(
                model, processor,
                prompt=formatted_prompt,
                image=[image_path],
                max_tokens=args.max_tokens,
                verbose=False,
            )
            output = result.text if hasattr(result, 'text') else str(result)
            elapsed = time.perf_counter() - start
            parsed = extract_json_block(output)
        except Exception as exc:
            elapsed = time.perf_counter() - start
            output = str(exc)
            parsed = None

        row = {
            "frame_id": frame["frame_id"],
            "teams": frame["teams"],
            "json_valid": parsed is not None,
            "scene_type": (parsed or {}).get("scene_type"),
            "score_detected": (parsed or {}).get("score_detected"),
            "match_clock_detected": (parsed or {}).get("match_clock_detected"),
            "latency_ms": round(elapsed * 1000, 1),
            "raw_output": output[:500],
        }
        rows.append(row)

        if idx % 50 == 0:
            valid = sum(1 for r in rows if r["json_valid"])
            print(f"  [{idx}/{len(items)}] json_valid={valid}/{idx}")

    # Write CSV
    csv_path = run_dir / "results.csv"
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    # Summary
    total = len(rows)
    json_valid = sum(1 for r in rows if r["json_valid"])
    score_non_null = sum(1 for r in rows if r.get("score_detected"))
    clock_non_null = sum(1 for r in rows if r.get("match_clock_detected"))
    avg_latency = sum(r["latency_ms"] for r in rows) / total if total else 0

    summary = {
        "run_id": run_id,
        "model": args.model,
        "adapter": str(args.adapter_path),
        "frame_count": total,
        "json_valid_count": json_valid,
        "json_valid_rate": round(json_valid / total, 4) if total else 0,
        "score_non_null": score_non_null,
        "clock_non_null": clock_non_null,
        "score_extract_rate": round(score_non_null / total, 4) if total else 0,
        "clock_extract_rate": round(clock_non_null / total, 4) if total else 0,
        "avg_latency_ms": round(avg_latency, 2),
        "output_csv": str(csv_path),
        "output_dir": str(run_dir),
    }

    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
