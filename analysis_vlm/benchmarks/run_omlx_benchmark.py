#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


OMLX_BASE_URL = "http://127.0.0.1:8000/v1"
OMLX_API_KEY = "sk-1234"
DEFAULT_PROMPT = (
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


def extract_first_frame(video_path: Path, output_dir: Path) -> Path:
    frame_path = output_dir / f"{video_path.stem}__frame.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1", str(frame_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return frame_path


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def post_chat(payload: dict, timeout: int) -> dict:
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


def build_messages_vlm(prompt: str, image_path: Path) -> list[dict]:
    return [
        {"role": "system", "content": "你是直播画面分析助手。严格只输出纯 JSON，不要解释。"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encode_image(image_path)}},
            ],
        },
    ]


def extract_json_block(text: str) -> tuple[dict | None, str | None]:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        for part in parts:
            stripped = part.strip()
            if not stripped:
                continue
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
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def normalize_score_value(value: object) -> str:
    if value in (False, None):
        return ""
    text = str(value).strip()
    return text if "-" in text else ""


def normalize_clock_value(value: object) -> str:
    if value in (False, None):
        return ""
    text = str(value).strip()
    return text if ":" in text else ""


def normalize_text(value: object, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else fallback


def load_manifest(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    clips: list[dict] = []
    for match in payload.get("matches", []):
        for clip in match.get("clips", []):
            clips.append(
                {
                    "teams": match.get("teams"),
                    "clip_id": clip.get("clip_id"),
                    "clip_path": clip.get("clip_path"),
                    "label_path": clip.get("label_path"),
                    "meta_path": clip.get("meta_path"),
                    "kind": clip.get("kind"),
                    "reason": clip.get("reason"),
                    "start_sec": clip.get("start_sec"),
                    "end_sec": clip.get("end_sec"),
                }
            )
    return clips


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a first-pass OMLX benchmark on golden clips.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    clips = load_manifest(manifest_path)
    if args.limit > 0:
        clips = clips[: args.limit]

    run_id = f"{args.model}__{int(time.time())}"
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "results.csv"
    json_path = run_dir / "summary.json"

    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="omlx_bench_", dir="/tmp") as tmpdir:
        tmp_path = Path(tmpdir)
        for item in clips:
            clip_path = Path(item["clip_path"])
            start = time.perf_counter()
            result: dict = {
                **item,
                "model": args.model,
                "latency_ms": None,
                "prompt_tokens": None,
                "completion_tokens": None,
                "json_valid": False,
                "scene_type": None,
                "score_detected": None,
                "match_clock_detected": None,
                "scoreboard_visibility": None,
                "replay_risk": None,
                "tradeability": None,
                "event_candidates_json": None,
                "confidence": None,
                "explanation_short": None,
                "error": None,
                "raw_content": None,
            }
            try:
                frame_path = extract_first_frame(clip_path, tmp_path)
                payload = {
                    "model": args.model,
                    "messages": build_messages_vlm(args.prompt, frame_path),
                    "temperature": args.temperature,
                    "max_tokens": args.max_tokens,
                }
                response = post_chat(payload, timeout=args.timeout)
                elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                choice = ((response.get("choices") or [{}])[0].get("message") or {})
                usage = response.get("usage") or {}
                content = choice.get("content", "")
                parsed, parse_error = extract_json_block(content)

                result["latency_ms"] = elapsed_ms
                result["prompt_tokens"] = usage.get("prompt_tokens")
                result["completion_tokens"] = usage.get("completion_tokens")
                result["raw_content"] = content
                result["json_valid"] = parsed is not None
                if parsed:
                    result["scene_type"] = normalize_text(
                        parsed.get("scene_type"),
                        {"live_play", "replay", "scoreboard_focus", "crowd_or_bench", "stoppage", "unknown"},
                        "unknown",
                    )
                    result["score_detected"] = normalize_score_value(parsed.get("score_detected", ""))
                    result["match_clock_detected"] = normalize_clock_value(parsed.get("match_clock_detected", ""))
                    result["scoreboard_visibility"] = normalize_text(
                        parsed.get("scoreboard_visibility"),
                        {"clear", "partial", "hidden", "unknown"},
                        "unknown",
                    )
                    result["replay_risk"] = normalize_text(parsed.get("replay_risk"), {"low", "medium", "high"}, "high")
                    result["tradeability"] = normalize_text(
                        parsed.get("tradeability"),
                        {"tradeable", "watch_only", "ignore"},
                        "watch_only",
                    )
                    result["event_candidates_json"] = json.dumps(parsed.get("event_candidates", []), ensure_ascii=False)
                    result["confidence"] = parsed.get("confidence")
                    result["explanation_short"] = parsed.get("explanation_short")
                else:
                    result["error"] = f"json_parse_error:{parse_error}"
            except Exception as exc:  # noqa: BLE001
                result["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
                result["error"] = str(exc)
            rows.append(result)

    fieldnames = [
        "model",
        "teams",
        "clip_id",
        "clip_path",
        "label_path",
        "meta_path",
        "kind",
        "reason",
        "start_sec",
        "end_sec",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "json_valid",
        "scene_type",
        "score_detected",
        "match_clock_detected",
        "scoreboard_visibility",
        "replay_risk",
        "tradeability",
        "event_candidates_json",
        "confidence",
        "explanation_short",
        "error",
        "raw_content",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    valid_rows = [row for row in rows if row["json_valid"]]
    latencies = [row["latency_ms"] for row in rows if row["latency_ms"] is not None]
    summary = {
        "run_id": run_id,
        "model": args.model,
        "manifest": str(manifest_path),
        "clip_count": len(rows),
        "json_valid_count": len(valid_rows),
        "json_valid_rate": round(len(valid_rows) / len(rows), 4) if rows else 0.0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "output_csv": str(csv_path),
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
