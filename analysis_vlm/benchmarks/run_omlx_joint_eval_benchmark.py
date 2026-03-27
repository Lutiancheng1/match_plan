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
OMLX_CTL = Path("/Users/niannianshunjing/match_plan/analysis_vlm/tools/omlx_server_ctl.py")
DEFAULT_MANIFEST = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/05_eval_sets/event_odds_joint_eval_inputs/manifests/current_event_odds_joint_eval_manifest.json"
)
DEFAULT_OUTPUT_DIR = Path("/Users/niannianshunjing/match_plan/analysis_vlm/reports")
DEFAULT_PROMPT = (
    "你是直播盘口重定价分析助手。"
    "你会看到一张由同一 clip 的三帧关键画面拼成的图，以及该时间点前后的盘口变化摘要。"
    "你的任务不是解说比赛，而是判断该片段是否可能触发盘口重定价。"
    "严格只输出纯JSON，不要解释，不要 markdown。"
    "字段固定为 repricing_expected, repricing_direction, repricing_strength, first_leg_side, "
    "first_leg_urgency, hedge_window_expected_sec, edge_rationale_short。"
    "repricing_expected 必须是 true 或 false，不能输出 unclear。"
    "repricing_direction 只能是 home_price_down, home_price_up, away_price_down, away_price_up, unclear。"
    "repricing_strength 只能是 weak, medium, strong, very_strong, unclear。"
    "first_leg_side 只能是 home, away, none。"
    "first_leg_urgency 只能是 immediate, soon, watch, none。"
    "如果没有明确强事件，且赔率窗口变化不够显著，请输出 repricing_expected=false。"
    "不要仅凭主队压制、控球、比赛进入末段这类泛化描述就输出 true。"
    "只有当可见事件或赔率窗口变化共同支持重定价时，才输出 repricing_expected=true。"
    "当 repricing_expected=false 时，repricing_direction 必须输出 unclear，first_leg_side 必须输出 none。"
    "如果证据不足，请保守输出 false / unclear / none，而不是猜测。"
)


def load_json(path: Path):
    return json.loads(path.read_text())


def load_manifest(path: Path) -> list[dict]:
    payload = load_json(path)
    records: list[dict] = []
    for match in payload.get("matches", []):
        for item in match.get("records", []):
            records.append(
                {
                    "teams": match.get("teams"),
                    "clip_id": item.get("clip_id"),
                    "record_path": item.get("output_path"),
                    "clip_path": item.get("clip_path"),
                    "source_strong_event_path": item.get("source_strong_event_path"),
                }
            )
    return records


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


def extract_frames(video_path: Path, output_dir: Path, fractions: list[float]) -> list[Path]:
    duration = probe_duration(video_path)
    frame_paths: list[Path] = []
    for idx, frac in enumerate(fractions, start=1):
        sec = max(0.0, duration * frac)
        frame_path = output_dir / f"{video_path.stem}__joint_f{idx}.jpg"
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
    sheet_path = output_dir / f"{stem}__joint_contact.jpg"
    cmd = ["ffmpeg"]
    for path in frame_paths:
        cmd += ["-i", str(path)]
    cmd += ["-y", "-filter_complex", f"hstack=inputs={len(frame_paths)}", str(sheet_path)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return sheet_path


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


def omlx_is_healthy(timeout: int = 3) -> bool:
    req = urllib.request.Request(
        f"{OMLX_BASE_URL}/models",
        headers={"Authorization": f"Bearer {OMLX_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def ensure_omlx_started() -> bool:
    if omlx_is_healthy():
        return False
    subprocess.run(
        ["python3", str(OMLX_CTL), "start"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True


def stop_omlx_if_requested(should_stop: bool) -> None:
    if not should_stop:
        return
    subprocess.run(
        ["python3", str(OMLX_CTL), "stop"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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


def build_odds_summary(record: dict) -> str:
    event_context = record.get("event_context") or {}
    bootstrap = event_context.get("bootstrap") or {}
    odds = record.get("odds_windows") or {}
    lines = [
        f"teams={record.get('teams','')}",
        f"match_clock={bootstrap.get('match_clock','')}",
        f"score={bootstrap.get('score', {}).get('home','')}:{bootstrap.get('score', {}).get('away','')}",
        f"game_phase={bootstrap.get('game_phase','')}",
        f"primary_event_label={((record.get('source_strong_event_label') or {}).get('primary_event_label') or '')}",
    ]
    for label in ("t_minus_15", "t_plus_0", "t_plus_15", "t_plus_30", "t_plus_60"):
        snap = odds.get(label) or {}
        lines.append(
            f"{label}: match_clock={snap.get('match_clock','')}, ratio_re={snap.get('ratio_re','')}, "
            f"ior_reh={snap.get('ior_reh','')}, ior_rec={snap.get('ior_rec','')}, "
            f"ratio_rouo={snap.get('ratio_rouo','')}, ior_rouh={snap.get('ior_rouh','')}, ior_rouc={snap.get('ior_rouc','')}"
        )
    def num(value):
        try:
            return float(value)
        except Exception:
            return None
    t0 = odds.get("t_plus_0") or {}
    t60 = odds.get("t_plus_60") or {}
    for field in ("ior_reh", "ior_rec", "ior_rouh", "ior_rouc"):
        a = num(t0.get(field))
        b = num(t60.get(field))
        if a is not None and b is not None:
            lines.append(f"delta_{field}_0_to_60={b-a:+.3f}")
    return "\n".join(lines)


def build_messages(prompt: str, image_path: Path, record: dict) -> list[dict]:
    summary = build_odds_summary(record)
    return [
        {"role": "system", "content": "你是盘口重定价分析助手。严格只输出纯 JSON，不要解释。"},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"{prompt}\n\n下面是对应片段的盘口窗口摘要：\n{summary}",
                },
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encode_image(image_path)}},
            ],
        },
    ]


def normalize_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return None


def normalize_text(value: object, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else fallback


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase-2 event+odds joint-eval benchmark on local OMLX models.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--auto-start-omlx", action="store_true", default=True)
    parser.add_argument("--no-auto-start-omlx", dest="auto_start_omlx", action="store_false")
    parser.add_argument("--stop-omlx-on-exit", action="store_true", default=True)
    parser.add_argument("--keep-omlx-running", dest="stop_omlx_on_exit", action="store_false")
    args = parser.parse_args()

    started_by_runner = False
    if args.auto_start_omlx:
        started_by_runner = ensure_omlx_started()

    records = load_manifest(args.manifest)
    if args.limit > 0:
        records = records[: args.limit]

    run_id = f"{args.model}__joint_eval__{int(time.time())}"
    run_dir = args.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "results.csv"
    summary_path = run_dir / "summary.json"

    rows: list[dict] = []
    try:
        with tempfile.TemporaryDirectory(prefix="omlx_joint_", dir="/tmp") as tmpdir:
            tmp_path = Path(tmpdir)
            for item in records:
                record = load_json(Path(item["record_path"]))
                clip_path = Path(record["clip_path"])
                ground_truth = record.get("joint_eval_ground_truth") or {}
                start = time.perf_counter()
                result = {
                    "model": args.model,
                    "teams": item.get("teams"),
                    "clip_id": item.get("clip_id"),
                    "record_path": item.get("record_path"),
                    "clip_path": str(clip_path),
                    "latency_ms": None,
                    "json_valid": False,
                    "repricing_expected_pred": None,
                    "repricing_direction_pred": "",
                    "repricing_strength_pred": "",
                    "first_leg_side_pred": "",
                    "first_leg_urgency_pred": "",
                    "hedge_window_expected_sec_pred": None,
                    "repricing_expected_gt": ground_truth.get("repricing_expected"),
                    "repricing_direction_gt": ground_truth.get("repricing_direction"),
                    "first_leg_side_gt": ground_truth.get("first_leg_side"),
                    "repricing_expected_match": False,
                    "repricing_direction_match": False,
                    "first_leg_side_match": False,
                    "error": "",
                    "raw_content": "",
                }
                try:
                    frames = extract_frames(clip_path, tmp_path, [0.2, 0.5, 0.8])
                    sheet = build_contact_sheet(frames, tmp_path, clip_path.stem)
                    payload = {
                        "model": args.model,
                        "messages": build_messages(args.prompt, sheet, record),
                        "temperature": args.temperature,
                        "max_tokens": args.max_tokens,
                    }
                    response = post_chat(payload, timeout=args.timeout)
                    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                    content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                    parsed, parse_error = extract_json_block(content)
                    result["latency_ms"] = elapsed_ms
                    result["raw_content"] = content
                    result["json_valid"] = parsed is not None
                    if parsed:
                        expected = normalize_bool(parsed.get("repricing_expected"))
                        direction = normalize_text(parsed.get("repricing_direction"), {"home_price_down", "home_price_up", "away_price_down", "away_price_up", "unclear"}, "unclear")
                        strength = normalize_text(parsed.get("repricing_strength"), {"weak", "medium", "strong", "very_strong", "unclear"}, "unclear")
                        side = normalize_text(parsed.get("first_leg_side"), {"home", "away", "none"}, "none")
                        urgency = normalize_text(parsed.get("first_leg_urgency"), {"immediate", "soon", "watch", "none"}, "none")
                        hedge = parsed.get("hedge_window_expected_sec")
                        try:
                            hedge = int(hedge) if hedge is not None else None
                        except Exception:
                            hedge = None
                        result["repricing_expected_pred"] = expected
                        result["repricing_direction_pred"] = direction
                        result["repricing_strength_pred"] = strength
                        result["first_leg_side_pred"] = side
                        result["first_leg_urgency_pred"] = urgency
                        result["hedge_window_expected_sec_pred"] = hedge
                        result["repricing_expected_match"] = expected == ground_truth.get("repricing_expected")
                        result["repricing_direction_match"] = direction == ground_truth.get("repricing_direction")
                        result["first_leg_side_match"] = side == ground_truth.get("first_leg_side")
                    else:
                        result["error"] = f"json_parse_error:{parse_error}"
                except Exception as exc:  # noqa: BLE001
                    result["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
                    result["error"] = str(exc)
                rows.append(result)
    finally:
        if args.stop_omlx_on_exit:
            stop_omlx_if_requested(started_by_runner)

    fieldnames = list(rows[0].keys()) if rows else [
        "model", "teams", "clip_id", "record_path", "clip_path", "latency_ms", "json_valid"
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    latencies = [row["latency_ms"] for row in rows if row["latency_ms"] is not None]
    summary = {
        "run_id": run_id,
        "model": args.model,
        "manifest": str(args.manifest),
        "record_count": len(rows),
        "json_valid_count": sum(1 for row in rows if row["json_valid"]),
        "json_valid_rate": round(sum(1 for row in rows if row["json_valid"]) / len(rows), 4) if rows else 0.0,
        "repricing_expected_match_rate": round(sum(1 for row in rows if row["repricing_expected_match"]) / len(rows), 4) if rows else 0.0,
        "repricing_direction_match_rate": round(sum(1 for row in rows if row["repricing_direction_match"]) / len(rows), 4) if rows else 0.0,
        "first_leg_side_match_rate": round(sum(1 for row in rows if row["first_leg_side_match"]) / len(rows), 4) if rows else 0.0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "output_csv": str(csv_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
