#!/usr/bin/env python3
"""Build holdout evaluation dataset in the same conversation format as training data."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HOLDOUT_ROOT = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/04_holdout_eval"
)
DEFAULT_OUTPUT_DIR = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/training_data"
)

SYSTEM_PROMPT = "你是直播画面分析助手。严格只输出纯 JSON，不要解释。"

USER_PROMPT = (
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
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build holdout eval dataset.")
    parser.add_argument("--holdout-root", type=Path, default=DEFAULT_HOLDOUT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def process_frame_records(holdout_root: Path) -> list[dict]:
    records_root = holdout_root / "frame_observation/records"
    images_root = holdout_root / "frame_observation/images"
    examples: list[dict] = []
    for record_path in sorted(records_root.rglob("*.json")):
        try:
            record = json.loads(record_path.read_text())
        except Exception:
            continue
        image_path = Path(record.get("image_path", ""))
        if not image_path.exists():
            match_slug = record_path.parent.name
            alt = images_root / match_slug / f"{record_path.stem}.jpg"
            if alt.exists():
                image_path = alt
            else:
                continue

        obs = record.get("observation", {})
        target = {
            "scene_type": obs.get("scene_type", "unknown"),
            "score_detected": obs.get("score_detected", ""),
            "match_clock_detected": obs.get("match_clock_detected", ""),
            "scoreboard_visibility": obs.get("scoreboard_visibility", "unknown"),
            "replay_risk": obs.get("replay_risk", "high"),
            "tradeability": obs.get("tradeability", "watch_only"),
            "event_candidates": obs.get("event_candidates", []),
            "confidence": obs.get("confidence", 0.0),
            "explanation_short": obs.get("explanation_short", ""),
        }
        examples.append({
            "conversations": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_PROMPT, "images": [str(image_path)]},
                {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
            ],
            "metadata": {
                "source_id": record.get("frame_id", record_path.stem),
                "teams": record.get("teams", ""),
                "holdout": True,
            },
        })
    return examples


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    examples = process_frame_records(args.holdout_root)
    output_path = args.output_dir / "holdout_eval.jsonl"
    with output_path.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(json.dumps(ex, ensure_ascii=False) + "\n")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_examples": len(examples),
        "output_path": str(output_path),
    }
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
