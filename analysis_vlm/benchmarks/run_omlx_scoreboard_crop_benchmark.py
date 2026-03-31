#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image


OMLX_BASE_URL = "http://127.0.0.1:8000/v1"
OMLX_API_KEY = "sk-1234"
DEFAULT_PROMPT = (
    "你现在只需要看足球直播画面顶部的记分牌/比赛时间区域。"
    "如果画面里没有清晰可见的记分牌或比赛时间条，绝对不要猜测。"
    "请只输出纯JSON，不要解释，也不要使用 markdown 代码块。"
    "字段固定为 score_detected, match_clock_detected, confidence。"
    "score_detected 必须是类似 1-0 的字符串；看不清时输出空字符串。"
    "match_clock_detected 必须是类似 45:00 的字符串；看不清时输出空字符串。"
)

SCORE_PATTERN = re.compile(r"\b\d{1,2}\s*[-:]\s*\d{1,2}\b")
CLOCK_PATTERN = re.compile(r"\b\d{1,2}[:.]\d{2}\b")
PHASE_PATTERN = re.compile(r"\b(?:1T|2T|HT|FT|1H|2H)\b", re.IGNORECASE)


def probe_duration(video_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nokey=1:noprint_wrappers=1",
        str(video_path),
    ]
    return float(subprocess.check_output(cmd, text=True).strip())


def extract_frames(video_path: Path, output_dir: Path, fractions: list[float]) -> list[Path]:
    duration = probe_duration(video_path)
    frame_paths: list[Path] = []
    for idx, frac in enumerate(fractions, start=1):
        sec = max(0.0, duration * frac)
        frame_path = output_dir / f"{video_path.stem}__score_f{idx}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{sec:.3f}", "-i", str(video_path), "-frames:v", "1", "-update", "1", str(frame_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        frame_paths.append(frame_path)
    return frame_paths


def crop_top_strip(frame_path: Path, output_dir: Path, left_ratio: float, top_ratio: float, width_ratio: float, height_ratio: float) -> Path:
    img = Image.open(frame_path)
    w, h = img.size
    left = int(w * left_ratio)
    top = int(h * top_ratio)
    right = left + int(w * width_ratio)
    bottom = top + int(h * height_ratio)
    cropped = img.crop((left, top, right, bottom))
    out = output_dir / f"{frame_path.stem}__crop.jpg"
    cropped.save(out, format="JPEG", quality=95)
    return out


def build_contact_sheet(frame_paths: list[Path], output_dir: Path, stem: str) -> Path:
    images = [Image.open(p) for p in frame_paths]
    total_width = sum(im.width for im in images)
    max_height = max(im.height for im in images)
    sheet = Image.new("RGB", (total_width, max_height))
    x = 0
    for im in images:
        sheet.paste(im, (x, 0))
        x += im.width
    out = output_dir / f"{stem}__scoreboard_contact.jpg"
    sheet.save(out, format="JPEG", quality=95)
    return out


def run_tesseract_text(image_path: Path) -> str:
    proc = subprocess.run(
        ["tesseract", str(image_path), "stdout", "--psm", "6"],
        check=False,
        capture_output=True,
    )
    return (proc.stdout or b"").decode("utf-8", errors="ignore").strip()


def looks_like_visible_scoreboard(text: str) -> bool:
    return bool(
        SCORE_PATTERN.search(text)
        or CLOCK_PATTERN.search(text)
        or PHASE_PATTERN.search(text)
    )


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


def extract_json_block(text: str) -> tuple[dict | None, str | None]:
    text = str(text or "").strip()
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


def build_messages(prompt: str, image_path: Path) -> list[dict]:
    return [
        {"role": "system", "content": "你是直播记分牌 OCR 助手。严格只输出纯 JSON。"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encode_image(image_path)}},
            ],
        },
    ]


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
                }
            )
    return clips


def load_label(path: Path) -> dict:
    payload = json.loads(path.read_text())
    return payload.get("labels", {})


def normalize_score(label: dict) -> str:
    h = str(label.get("score_h", "")).strip()
    c = str(label.get("score_c", "")).strip()
    return f"{h}-{c}" if h and c else ""


def normalize_clock(label: dict) -> str:
    raw = str(label.get("match_clock", "")).strip()
    if "^" in raw:
        raw = raw.split("^", 1)[1]
    return raw


def clock_minute_part(clock: str) -> str:
    return clock.strip().split(":", 1)[0] if ":" in clock.strip() else ""


def normalize_score_value(value: object) -> str:
    text = str(value or "").strip()
    return text if "-" in text else ""


def normalize_clock_value(value: object) -> str:
    text = str(value or "").strip()
    return text if ":" in text else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a scoreboard/clock OCR auxiliary benchmark through local OMLX.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--fractions", default="0.15,0.5,0.85")
    parser.add_argument("--crop-left-ratio", type=float, default=0.0)
    parser.add_argument("--crop-top-ratio", type=float, default=0.0)
    parser.add_argument("--crop-width-ratio", type=float, default=1.0)
    parser.add_argument("--crop-height-ratio", type=float, default=0.18)
    parser.add_argument("--require-visible-scoreboard", action="store_true", default=True)
    parser.add_argument("--skip-visibility-gate", dest="require_visible_scoreboard", action="store_false")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    clips = load_manifest(manifest_path)
    if args.limit > 0:
        clips = clips[: args.limit]
    fractions = [float(x) for x in args.fractions.split(",") if x.strip()]

    run_id = f"{args.model}__scoreboard_round3__{int(time.time())}"
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "results.csv"
    json_path = run_dir / "summary.json"

    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="omlx_scoreboard_bench_", dir="/tmp") as tmpdir:
        tmp_path = Path(tmpdir)
        for item in clips:
            label = load_label(Path(item["label_path"]))
            expected_score = normalize_score(label)
            expected_clock = normalize_clock(label)
            start = time.perf_counter()
            result = {
                **item,
                "model": args.model,
                "latency_ms": None,
                "prompt_tokens": None,
                "completion_tokens": None,
                "json_valid": False,
                "score_detected": "",
                "match_clock_detected": "",
                "confidence": None,
                "expected_score": expected_score,
                "expected_clock": expected_clock,
                "score_exact_match": False,
                "clock_exact_match": False,
                "clock_minute_match": False,
                "error": None,
                "raw_content": "",
            }
            try:
                clip_path = Path(item["clip_path"])
                frames = extract_frames(clip_path, tmp_path, fractions)
                crops = [
                    crop_top_strip(
                        frame,
                        tmp_path,
                        args.crop_left_ratio,
                        args.crop_top_ratio,
                        args.crop_width_ratio,
                        args.crop_height_ratio,
                    )
                    for frame in frames
                ]
                ocr_texts = [run_tesseract_text(crop) for crop in crops]
                visible = any(looks_like_visible_scoreboard(text) for text in ocr_texts)
                result["scoreboard_visible"] = visible
                result["ocr_hint_text"] = " | ".join(text for text in ocr_texts if text)[:500]
                if args.require_visible_scoreboard and not visible:
                    result["error"] = "scoreboard_not_visible"
                    rows.append(result)
                    continue
                sheet = build_contact_sheet(crops, tmp_path, clip_path.stem)
                payload = {
                    "model": args.model,
                    "messages": build_messages(args.prompt, sheet),
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
                    score = normalize_score_value(parsed.get("score_detected", ""))
                    clock = normalize_clock_value(parsed.get("match_clock_detected", ""))
                    result["score_detected"] = score
                    result["match_clock_detected"] = clock
                    result["confidence"] = parsed.get("confidence")
                    result["score_exact_match"] = bool(expected_score) and score == expected_score
                    result["clock_exact_match"] = bool(expected_clock) and clock == expected_clock
                    result["clock_minute_match"] = bool(expected_clock) and clock_minute_part(clock) == clock_minute_part(expected_clock)
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
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "json_valid",
        "score_detected",
        "match_clock_detected",
        "confidence",
        "expected_score",
        "expected_clock",
        "score_exact_match",
        "clock_exact_match",
        "clock_minute_match",
        "scoreboard_visible",
        "ocr_hint_text",
        "error",
        "raw_content",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    valid_rows = [r for r in rows if r["json_valid"]]
    latencies = [float(r["latency_ms"]) for r in rows if r["latency_ms"] is not None]
    score_exact = sum(1 for r in rows if r["score_exact_match"])
    clock_exact = sum(1 for r in rows if r["clock_exact_match"])
    clock_minute = sum(1 for r in rows if r["clock_minute_match"])
    visible_rows = [r for r in rows if r.get("scoreboard_visible")]
    evaluated_rows = [r for r in rows if r.get("error") != "scoreboard_not_visible" and r.get("scoreboard_visible")]
    summary = {
        "run_id": run_id,
        "model": args.model,
        "manifest": str(manifest_path),
        "clip_count": len(rows),
        "visible_scoreboard_count": len(visible_rows),
        "visible_scoreboard_rate": round(len(visible_rows) / len(rows), 4) if rows else 0.0,
        "evaluated_clip_count": len(evaluated_rows),
        "json_valid_count": len(valid_rows),
        "json_valid_rate": round(len(valid_rows) / len(evaluated_rows), 4) if evaluated_rows else 0.0,
        "score_exact_match_count": score_exact,
        "score_exact_match_rate": round(score_exact / len(evaluated_rows), 4) if evaluated_rows else 0.0,
        "clock_exact_match_count": clock_exact,
        "clock_exact_match_rate": round(clock_exact / len(evaluated_rows), 4) if evaluated_rows else 0.0,
        "clock_minute_match_count": clock_minute,
        "clock_minute_match_rate": round(clock_minute / len(evaluated_rows), 4) if evaluated_rows else 0.0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "output_csv": str(csv_path),
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
