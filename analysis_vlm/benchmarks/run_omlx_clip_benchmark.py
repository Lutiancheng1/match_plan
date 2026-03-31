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
from collections import Counter
from pathlib import Path


OMLX_BASE_URL = "http://127.0.0.1:8000/v1"
OMLX_API_KEY = "sk-1234"
DEFAULT_PROMPT = (
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
    "label 只能是 goal, red_card, penalty, dangerous_attack, celebration, replay_sequence, substitution, injury_or_stoppage, none 之一。"
)


def extract_frames(video_path: Path, output_dir: Path, fractions: list[float]) -> list[Path]:
    duration = probe_duration(video_path)
    frame_paths: list[Path] = []
    for idx, frac in enumerate(fractions, start=1):
        sec = max(0.0, duration * frac)
        frame_path = output_dir / f"{video_path.stem}__f{idx}.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{sec:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(frame_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        frame_paths.append(frame_path)
    return frame_paths


def build_contact_sheet(frame_paths: list[Path], output_dir: Path, stem: str) -> Path:
    sheet_path = output_dir / f"{stem}__contact.jpg"
    cmd = ["ffmpeg"]
    for path in frame_paths:
        cmd += ["-i", str(path)]
    cmd += [
        "-y",
        "-filter_complex",
        f"hstack=inputs={len(frame_paths)}",
        str(sheet_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sheet_path


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
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)


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


def build_messages_vlm(prompt: str, image_paths: list[Path]) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": prompt}]
    for image_path in image_paths:
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encode_image(image_path)}})
    return [
        {"role": "system", "content": "你是直播画面分析助手。严格只输出纯 JSON，不要解释。"},
        {"role": "user", "content": content},
    ]


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


def load_label(label_path: Path) -> dict:
    payload = json.loads(label_path.read_text())
    return payload.get("labels", {})


def normalize_score(label: dict) -> str:
    h = str(label.get("score_h", "")).strip()
    c = str(label.get("score_c", "")).strip()
    if h and c:
        return f"{h}-{c}"
    return ""


def normalize_clock(label: dict) -> str:
    raw = str(label.get("match_clock", "")).strip()
    if "^" in raw:
        raw = raw.split("^", 1)[1]
    return raw


def clock_minute_part(clock: str) -> str:
    clock = clock.strip()
    if not clock:
        return ""
    return clock.split(":", 1)[0]


def normalize_scene_type(value: object) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "live_play": "live_play",
        "replay": "replay",
        "scoreboard_focus": "scoreboard_focus",
        "crowd_or_bench": "crowd_or_bench",
        "stoppage": "stoppage",
        "unknown": "unknown",
        "足球比赛": "live_play",
        "soccer_match": "live_play",
        "soccer_game": "live_play",
        "football_match": "live_play",
        "match_play": "live_play",
        "直播比赛": "live_play",
        "回放": "replay",
        "比分牌特写": "scoreboard_focus",
        "记分牌特写": "scoreboard_focus",
        "观众或教练席": "crowd_or_bench",
        "停顿": "stoppage",
    }
    return mapping.get(text, "unknown")


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


def normalize_simple_text(value: object, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else fallback


def normalize_event_candidates(value: object) -> list[dict]:
    allowed = {
        "goal",
        "red_card",
        "penalty",
        "dangerous_attack",
        "celebration",
        "replay_sequence",
        "substitution",
        "injury_or_stoppage",
        "none",
    }
    alias = {
        "进球": "goal",
        "红牌": "red_card",
        "点球": "penalty",
        "危险进攻": "dangerous_attack",
        "庆祝": "celebration",
        "回放": "replay_sequence",
        "换人": "substitution",
        "伤停": "injury_or_stoppage",
        "球员庆祝": "celebration",
        "传球": "none",
        "防守": "none",
        "进攻": "dangerous_attack",
    }
    out: list[dict] = []
    if not isinstance(value, list):
        return out
    for item in value:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("type") or "").strip()
            conf = item.get("confidence", 0.5)
        else:
            label = str(item).strip()
            conf = 0.5
        label = alias.get(label, label)
        if label not in allowed:
            label = "none"
        try:
            conf = float(conf)
        except Exception:  # noqa: BLE001
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        out.append({"label": label, "confidence": conf})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a multi-frame clip benchmark through local OMLX.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-tokens", type=int, default=384)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--fractions", default="0.15,0.5,0.85")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    clips = load_manifest(manifest_path)
    if args.limit > 0:
        clips = clips[: args.limit]
    fractions = [float(x) for x in args.fractions.split(",") if x.strip()]

    run_id = f"{args.model}__clip_round2__{int(time.time())}"
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "results.csv"
    json_path = run_dir / "summary.json"

    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="omlx_clip_bench_", dir="/tmp") as tmpdir:
        tmp_path = Path(tmpdir)
        for item in clips:
            clip_path = Path(item["clip_path"])
            label = load_label(Path(item["label_path"]))
            expected_score = normalize_score(label)
            expected_clock = normalize_clock(label)
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
                "confidence": None,
                "event_candidates_json": None,
                "explanation_short": None,
                "expected_score": expected_score,
                "expected_clock": expected_clock,
                "score_exact_match": False,
                "clock_exact_match": False,
                "clock_minute_match": False,
                "predicted_strong_event": False,
                "error": None,
                "raw_content": None,
            }
            try:
                frame_paths = extract_frames(clip_path, tmp_path, fractions)
                contact_sheet_path = build_contact_sheet(frame_paths, tmp_path, clip_path.stem)
                payload = {
                    "model": args.model,
                    "messages": build_messages_vlm(args.prompt, [contact_sheet_path]),
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
                    score_detected = normalize_score_value(parsed.get("score_detected", ""))
                    clock_detected = normalize_clock_value(parsed.get("match_clock_detected", ""))
                    event_candidates = normalize_event_candidates(parsed.get("event_candidates", []))
                    predicted_labels = [
                        ev.get("label")
                        for ev in event_candidates
                        if isinstance(ev, dict) and ev.get("label") and ev.get("label") != "none"
                    ]
                    result["scene_type"] = normalize_scene_type(parsed.get("scene_type"))
                    result["score_detected"] = score_detected
                    result["match_clock_detected"] = clock_detected
                    result["scoreboard_visibility"] = normalize_simple_text(
                        parsed.get("scoreboard_visibility"),
                        {"clear", "partial", "hidden", "unknown"},
                        "unknown",
                    )
                    result["replay_risk"] = normalize_simple_text(parsed.get("replay_risk"), {"low", "medium", "high"}, "high")
                    result["tradeability"] = normalize_simple_text(
                        parsed.get("tradeability"),
                        {"tradeable", "watch_only", "ignore"},
                        "watch_only",
                    )
                    result["confidence"] = parsed.get("confidence")
                    result["event_candidates_json"] = json.dumps(event_candidates, ensure_ascii=False)
                    result["explanation_short"] = parsed.get("explanation_short")
                    result["score_exact_match"] = bool(expected_score) and score_detected == expected_score
                    result["clock_exact_match"] = bool(expected_clock) and clock_detected == expected_clock
                    result["clock_minute_match"] = bool(expected_clock) and clock_minute_part(clock_detected) == clock_minute_part(expected_clock)
                    result["predicted_strong_event"] = bool(predicted_labels)
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
        "confidence",
        "event_candidates_json",
        "explanation_short",
        "expected_score",
        "expected_clock",
        "score_exact_match",
        "clock_exact_match",
        "clock_minute_match",
        "predicted_strong_event",
        "error",
        "raw_content",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    valid_rows = [row for row in rows if row["json_valid"]]
    latencies = [float(row["latency_ms"]) for row in rows if row["latency_ms"] is not None]
    scene_counter = Counter(row["scene_type"] or "null" for row in valid_rows)
    score_exact = sum(1 for row in rows if row["score_exact_match"])
    clock_exact = sum(1 for row in rows if row["clock_exact_match"])
    clock_minute = sum(1 for row in rows if row["clock_minute_match"])
    strong_event = sum(1 for row in rows if row["predicted_strong_event"])
    summary = {
        "run_id": run_id,
        "model": args.model,
        "manifest": str(manifest_path),
        "clip_count": len(rows),
        "json_valid_count": len(valid_rows),
        "json_valid_rate": round(len(valid_rows) / len(rows), 4) if rows else 0.0,
        "score_exact_match_count": score_exact,
        "score_exact_match_rate": round(score_exact / len(rows), 4) if rows else 0.0,
        "clock_exact_match_count": clock_exact,
        "clock_exact_match_rate": round(clock_exact / len(rows), 4) if rows else 0.0,
        "clock_minute_match_count": clock_minute,
        "clock_minute_match_rate": round(clock_minute / len(rows), 4) if rows else 0.0,
        "predicted_strong_event_count": strong_event,
        "predicted_strong_event_rate": round(strong_event / len(rows), 4) if rows else 0.0,
        "scene_type_distribution": dict(scene_counter),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "output_csv": str(csv_path),
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
