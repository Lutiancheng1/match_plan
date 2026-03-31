#!/usr/bin/env python3
"""Run holdout evaluation benchmark across multiple models on frame observations."""
from __future__ import annotations

import argparse
import base64
import csv
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OMLX_BASE_URL = "http://127.0.0.1:8000/v1"
OMLX_API_KEY = "sk-1234"

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

DEFAULT_MODELS = [
    "Qwen3.5-VL-9B-8bit-MLX-CRACK",
    "Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK",
]


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def post_chat(payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        f"{OMLX_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OMLX_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


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
    """Collect frame images and their records from holdout eval directory."""
    frames: list[dict] = []
    records_root = holdout_root / "frame_observation/records"
    images_root = holdout_root / "frame_observation/images"
    for record_path in sorted(records_root.rglob("*.json")):
        record = json.loads(record_path.read_text())
        image_path = Path(record.get("image_path", ""))
        if not image_path.exists():
            # Try to find image in holdout images dir
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
            "match_id": record.get("match_id", ""),
            "image_path": str(image_path),
            "record_path": str(record_path),
            "sample_at_sec": record.get("sample_at_sec", 0),
        })
    return frames


def eval_model(model: str, frames: list[dict], output_dir: Path, timeout: int, max_tokens: int, limit: int) -> dict:
    """Evaluate a single model on holdout frames."""
    run_id = f"{model}__holdout_eval__{int(time.time())}"
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    items = frames[:limit] if limit > 0 else frames

    for idx, frame in enumerate(items, 1):
        image_path = Path(frame["image_path"])
        start = time.perf_counter()
        result = {
            "frame_id": frame["frame_id"],
            "teams": frame["teams"],
            "model": model,
            "json_valid": False,
            "scene_type": None,
            "score_detected": None,
            "match_clock_detected": None,
            "scoreboard_visibility": None,
            "replay_risk": None,
            "event_candidates_count": 0,
            "confidence": None,
            "latency_ms": None,
            "error": None,
        }
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是直播画面分析助手。严格只输出纯 JSON，不要解释。"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encode_image(image_path)}},
                        ],
                    },
                ],
                "temperature": 0.1,
                "max_tokens": max_tokens,
            }
            response = post_chat(payload, timeout=timeout)
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            content = ((response.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            parsed = extract_json_block(content)

            result["latency_ms"] = elapsed_ms
            result["json_valid"] = parsed is not None
            if parsed:
                result["scene_type"] = str(parsed.get("scene_type", ""))
                result["score_detected"] = str(parsed.get("score_detected", ""))
                result["match_clock_detected"] = str(parsed.get("match_clock_detected", ""))
                result["scoreboard_visibility"] = str(parsed.get("scoreboard_visibility", ""))
                result["replay_risk"] = str(parsed.get("replay_risk", ""))
                result["event_candidates_count"] = len(parsed.get("event_candidates", []))
                result["confidence"] = parsed.get("confidence")
        except Exception as exc:
            result["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
            result["error"] = str(exc)

        rows.append(result)
        if idx % 50 == 0:
            valid = sum(1 for r in rows if r["json_valid"])
            print(f"  [{idx}/{len(items)}] json_valid={valid}/{idx}")

    # Write CSV
    csv_path = run_dir / "results.csv"
    fieldnames = list(rows[0].keys()) if rows else []
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Compute summary
    valid_rows = [r for r in rows if r["json_valid"]]
    latencies = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    score_non_null = sum(1 for r in valid_rows if r.get("score_detected") and "-" in str(r["score_detected"]))
    clock_non_null = sum(1 for r in valid_rows if r.get("match_clock_detected") and ":" in str(r["match_clock_detected"]))

    summary = {
        "run_id": run_id,
        "model": model,
        "frame_count": len(rows),
        "json_valid_count": len(valid_rows),
        "json_valid_rate": round(len(valid_rows) / len(rows), 4) if rows else 0.0,
        "score_non_null": score_non_null,
        "clock_non_null": clock_non_null,
        "score_extract_rate": round(score_non_null / len(rows), 4) if rows else 0.0,
        "clock_extract_rate": round(clock_non_null / len(rows), 4) if rows else 0.0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "output_csv": str(csv_path),
        "output_dir": str(run_dir),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run holdout eval benchmark.")
    parser.add_argument("--holdout-root", type=Path, default=DEFAULT_HOLDOUT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=300)
    parser.add_argument("--limit", type=int, default=0, help="Max frames per model (0=all)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frames = collect_holdout_frames(args.holdout_root)
    print(f"Collected {len(frames)} holdout frames")

    if not frames:
        print("No holdout frames found.")
        return 1

    summaries: list[dict] = []
    for model in args.models:
        print(f"\n=== Evaluating {model} ===")
        summary = eval_model(model, frames, args.output_dir, args.timeout, args.max_tokens, args.limit)
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    # Write combined report
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_root": str(args.holdout_root),
        "frame_count": len(frames),
        "models": summaries,
    }
    report_path = args.output_dir / "holdout_eval_combined.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nCombined report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
