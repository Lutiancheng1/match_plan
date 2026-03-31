#!/usr/bin/env python3
"""Convert auto-prelabeled frame/clip records into MLX LoRA fine-tuning conversation format."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_FRAME_RECORDS = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/01_frame_observation/records"
)
DEFAULT_CLIP_RECORDS = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/02_clip_observation/records"
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
    parser = argparse.ArgumentParser(description="Build MLX LoRA training dataset from prelabeled records.")
    parser.add_argument("--frame-records", type=Path, default=DEFAULT_FRAME_RECORDS)
    parser.add_argument("--clip-records", type=Path, default=DEFAULT_CLIP_RECORDS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--require-prelabeled", action="store_true", default=True,
                        help="Only include auto_prelabeled or human_verified records.")
    return parser.parse_args()


def record_to_conversation(record: dict, image_field: str) -> dict | None:
    """Convert a single record into a conversation training example."""
    obs = record.get("observation", {})
    annotation = record.get("annotation", {})
    status = annotation.get("manual_review_status", "")

    # Only use records that have been labeled
    if status not in ("auto_prelabeled", "human_verified", "human_corrected"):
        return None

    # Check observation has meaningful content
    if obs.get("scene_type", "unknown") == "unknown" and not obs.get("score_detected"):
        return None

    image_path = record.get(image_field, "")
    if not image_path or not Path(image_path).exists():
        return None

    # Build the target JSON that the model should learn to output
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

    return {
        "conversations": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_PROMPT,
                "images": [image_path],
            },
            {
                "role": "assistant",
                "content": json.dumps(target, ensure_ascii=False),
            },
        ],
        "metadata": {
            "source_id": record.get("frame_id") or record.get("clip_id", ""),
            "teams": record.get("teams", ""),
            "prelabel_model": annotation.get("prelabel_model", ""),
            "review_status": status,
        },
    }


def process_records(records_root: Path, image_field: str) -> list[dict]:
    """Process all records in a directory tree."""
    examples: list[dict] = []
    for record_path in sorted(records_root.rglob("*.json")):
        try:
            record = json.loads(record_path.read_text())
        except Exception:
            continue
        conv = record_to_conversation(record, image_field)
        if conv:
            examples.append(conv)
    return examples


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Process frames
    frame_examples = process_records(args.frame_records, "image_path")
    frame_output = args.output_dir / "frame_conversations.jsonl"
    with frame_output.open("w", encoding="utf-8") as fh:
        for ex in frame_examples:
            fh.write(json.dumps(ex, ensure_ascii=False) + "\n")

    # Process clips (use clip_path as image reference - will need contact sheet at train time)
    clip_examples = process_records(args.clip_records, "clip_path")
    clip_output = args.output_dir / "clip_conversations.jsonl"
    with clip_output.open("w", encoding="utf-8") as fh:
        for ex in clip_examples:
            fh.write(json.dumps(ex, ensure_ascii=False) + "\n")

    # Combined
    all_examples = frame_examples + clip_examples
    combined_output = args.output_dir / "all_conversations.jsonl"
    with combined_output.open("w", encoding="utf-8") as fh:
        for ex in all_examples:
            fh.write(json.dumps(ex, ensure_ascii=False) + "\n")

    # Manifest
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "frame_examples": len(frame_examples),
        "clip_examples": len(clip_examples),
        "total_examples": len(all_examples),
        "frame_output": str(frame_output),
        "clip_output": str(clip_output),
        "combined_output": str(combined_output),
    }
    (args.output_dir / "training_data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
