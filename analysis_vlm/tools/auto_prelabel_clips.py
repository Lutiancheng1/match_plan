#!/usr/bin/env python3
"""Auto-prelabel clip observation records using a local oMLX VLM with contact-sheet input."""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OMLX_BASE_URL = "http://127.0.0.1:8000/v1"
OMLX_API_KEY = "sk-1234"

DEFAULT_RECORDS_ROOT = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/02_clip_observation/records"
)
DEFAULT_MODEL = "Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK"

PROMPT = (
    "请把这段足球直播片段的多张关键帧综合理解后，只输出纯JSON，不要解释，也不要使用 markdown 代码块。"
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


def probe_duration(video_path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", str(video_path)],
        text=True,
    ).strip()
    return float(out)


def extract_frames(video_path: Path, output_dir: Path, fractions: list[float]) -> list[Path]:
    duration = probe_duration(video_path)
    frame_paths: list[Path] = []
    for idx, frac in enumerate(fractions, 1):
        sec = max(0.0, duration * frac)
        frame_path = output_dir / f"{video_path.stem}__f{idx}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{sec:.3f}", "-i", str(video_path),
             "-frames:v", "1", str(frame_path)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        frame_paths.append(frame_path)
    return frame_paths


def build_contact_sheet(frame_paths: list[Path], output_dir: Path, stem: str) -> Path:
    sheet_path = output_dir / f"{stem}__contact.jpg"
    cmd = ["ffmpeg"]
    for path in frame_paths:
        cmd += ["-i", str(path)]
    cmd += ["-y", "-filter_complex", f"hstack=inputs={len(frame_paths)}", str(sheet_path)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sheet_path


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


def prelabel_one(clip_path: Path, tmp_dir: Path, model: str, timeout: int, max_tokens: int) -> dict | None:
    frames = extract_frames(clip_path, tmp_dir, [0.15, 0.50, 0.85])
    sheet = build_contact_sheet(frames, tmp_dir, clip_path.stem)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是直播画面分析助手。严格只输出纯 JSON，不要解释。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encode_image(sheet)}},
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
    parser = argparse.ArgumentParser(description="Auto-prelabel clip observation records via oMLX.")
    parser.add_argument("--records-root", type=Path, default=DEFAULT_RECORDS_ROOT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-tokens", type=int, default=300)
    parser.add_argument("--skip-labeled", action="store_true", default=True)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    record_files = sorted(args.records_root.rglob("*.json"))
    total = len(record_files)
    labeled = 0
    skipped = 0
    errors = 0

    with tempfile.TemporaryDirectory(prefix="clip_prelabel_") as tmpdir:
        tmp_path = Path(tmpdir)
        for idx, record_path in enumerate(record_files, 1):
            if args.limit and labeled >= args.limit:
                break

            record = json.loads(record_path.read_text())
            if args.skip_labeled and record.get("annotation", {}).get("manual_review_status") == "auto_prelabeled":
                skipped += 1
                continue

            clip_path = Path(record.get("clip_path", ""))
            if not clip_path.exists():
                errors += 1
                continue

            try:
                obs = prelabel_one(clip_path, tmp_path, args.model, args.timeout, args.max_tokens)
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

            if labeled % 20 == 0:
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
