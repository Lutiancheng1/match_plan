#!/usr/bin/env python3
"""Auto-prelabel frame observation records using a local oMLX VLM."""
from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OMLX_BASE_URL = "http://127.0.0.1:8000/v1"
OMLX_API_KEY = "sk-1234"

DEFAULT_RECORDS_ROOT = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/01_frame_observation/records"
)
DEFAULT_MODEL = "Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK"

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


SCENE_TYPES = {"live_play", "replay", "scoreboard_focus", "crowd_or_bench", "stoppage", "unknown"}
VISIBILITY = {"clear", "partial", "hidden", "unknown"}
RISK = {"low", "medium", "high"}
TRADE = {"tradeable", "watch_only", "ignore"}


def normalize(value: object, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else fallback


def prelabel_one(image_path: Path, model: str, timeout: int, max_tokens: int) -> dict | None:
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
    content = ((response.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    parsed = extract_json_block(content)
    if not parsed:
        return None
    return {
        "scene_type": normalize(parsed.get("scene_type"), SCENE_TYPES, "unknown"),
        "score_detected": str(parsed.get("score_detected") or ""),
        "match_clock_detected": str(parsed.get("match_clock_detected") or ""),
        "scoreboard_visibility": normalize(parsed.get("scoreboard_visibility"), VISIBILITY, "unknown"),
        "replay_risk": normalize(parsed.get("replay_risk"), RISK, "high"),
        "tradeability": normalize(parsed.get("tradeability"), TRADE, "watch_only"),
        "event_candidates": parsed.get("event_candidates", []),
        "confidence": parsed.get("confidence", 0.0),
        "explanation_short": str(parsed.get("explanation_short") or ""),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-prelabel frame observation records via oMLX.")
    parser.add_argument("--records-root", type=Path, default=DEFAULT_RECORDS_ROOT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-tokens", type=int, default=300)
    parser.add_argument("--skip-labeled", action="store_true", default=True,
                        help="Skip records already auto_prelabeled.")
    parser.add_argument("--limit", type=int, default=0, help="Max records to process (0=all).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    record_files = sorted(args.records_root.rglob("*.json"))
    total = len(record_files)
    labeled = 0
    skipped = 0
    errors = 0

    for idx, record_path in enumerate(record_files, 1):
        if args.limit and labeled >= args.limit:
            break

        record = json.loads(record_path.read_text())
        if args.skip_labeled and record.get("annotation", {}).get("manual_review_status") == "auto_prelabeled":
            skipped += 1
            continue

        image_path = Path(record.get("image_path", ""))
        if not image_path.exists():
            errors += 1
            continue

        try:
            obs = prelabel_one(image_path, args.model, args.timeout, args.max_tokens)
        except Exception as exc:
            print(f"[{idx}/{total}] ERROR {record_path.name}: {exc}")
            errors += 1
            continue

        if obs is None:
            print(f"[{idx}/{total}] PARSE_FAIL {record_path.name}")
            errors += 1
            continue

        record["observation"] = obs
        record.setdefault("annotation", {})["manual_review_status"] = "auto_prelabeled"
        record["annotation"]["prelabel_model"] = args.model
        record["annotation"]["prelabeled_at"] = datetime.now(timezone.utc).isoformat()
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        labeled += 1

        if labeled % 50 == 0:
            print(f"[{idx}/{total}] labeled={labeled} skipped={skipped} errors={errors}")

    print(json.dumps({
        "total_records": total,
        "labeled": labeled,
        "skipped": skipped,
        "errors": errors,
        "model": args.model,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
