#!/usr/bin/env python3
"""Benchmark score/clock extraction: full image vs cropped scoreboard, across models."""
from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.request
from io import BytesIO
from pathlib import Path

OMLX_BASE_URL = "http://127.0.0.1:8000/v1"
OMLX_API_KEY = "sk-1234"

DEFAULT_HOLDOUT_ROOT = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/04_holdout_eval"
)

PROMPT_FULL = (
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

PROMPT_CROP = (
    "这是一张足球直播记分牌区域的裁剪图。请只输出纯JSON，不要解释。"
    "字段固定为 score_detected, match_clock_detected, scoreboard_visibility。"
    "score_detected 必须是类似 1-0 的字符串；看不清时输出空字符串。"
    "match_clock_detected 必须是类似 45:00 的字符串；看不清时输出空字符串。"
    "scoreboard_visibility 只能是 clear, partial, hidden, unknown。"
)


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def crop_scoreboard(image_path: Path, top_ratio: float = 0.22) -> str:
    """Crop top portion of image and return base64."""
    from PIL import Image
    img = Image.open(image_path)
    w, h = img.size
    cropped = img.crop((0, 0, w, int(h * top_ratio)))
    buf = BytesIO()
    cropped.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def post_chat(payload: dict, timeout: int = 60) -> dict:
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
    frames = []
    records_root = holdout_root / "frame_observation/records"
    for p in sorted(records_root.rglob("*.json")):
        r = json.loads(p.read_text())
        image_path = Path(r.get("image_path", ""))
        if not image_path.exists():
            continue
        obs = r.get("observation", {})
        frames.append({
            "frame_id": r.get("frame_id", p.stem),
            "image_path": str(image_path),
            "gt_score": (obs.get("score_detected") or "").strip(),
            "gt_clock": (obs.get("match_clock_detected") or "").strip(),
        })
    return frames


def run_one(model: str, image_b64: str, prompt: str, max_tokens: int = 300) -> dict | None:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是直播画面分析助手。严格只输出纯 JSON，不要解释。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            },
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    resp = post_chat(payload)
    content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    return extract_json_block(content)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout-root", type=Path, default=DEFAULT_HOLDOUT_ROOT)
    parser.add_argument("--models", nargs="+", default=[
        "Qwen3.5-VL-9B-8bit-MLX-CRACK",
        "Qwen3.5-VL-4B-JANG_4S-CRACK",
    ])
    parser.add_argument("--modes", nargs="+", default=["full", "crop"],
                        help="full=whole image, crop=scoreboard crop")
    parser.add_argument("--limit", type=int, default=100, help="Frames to test (0=all)")
    parser.add_argument("--output-dir", type=Path,
                        default=Path("analysis_vlm/reports"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frames = collect_holdout_frames(args.holdout_root)
    if args.limit > 0:
        frames = frames[:args.limit]
    print(f"Testing {len(frames)} holdout frames")

    gt_has_score = sum(1 for f in frames if f["gt_score"] and "-" in f["gt_score"])
    gt_has_clock = sum(1 for f in frames if f["gt_clock"] and ":" in f["gt_clock"])
    print(f"Ground truth: {gt_has_score} have score, {gt_has_clock} have clock")

    results = {}

    for model in args.models:
        for mode in args.modes:
            key = f"{model}__{mode}"
            print(f"\n=== {key} ===")
            prompt = PROMPT_CROP if mode == "crop" else PROMPT_FULL
            score_hit = 0
            score_miss = 0
            clock_hit = 0
            clock_miss = 0
            json_valid = 0
            errors = 0
            latencies = []

            for idx, frame in enumerate(frames, 1):
                image_path = Path(frame["image_path"])
                try:
                    if mode == "crop":
                        img_b64 = crop_scoreboard(image_path)
                    else:
                        img_b64 = encode_image(image_path)

                    t0 = time.perf_counter()
                    parsed = run_one(model, img_b64, prompt)
                    elapsed = time.perf_counter() - t0
                    latencies.append(elapsed)
                except Exception as exc:
                    errors += 1
                    if idx <= 3:
                        print(f"  [{idx}] ERROR: {exc}")
                    continue

                if parsed is None:
                    errors += 1
                    continue

                json_valid += 1
                pred_score = (parsed.get("score_detected") or "").strip()
                pred_clock = (parsed.get("match_clock_detected") or "").strip()
                gt_s = frame["gt_score"]
                gt_c = frame["gt_clock"]

                if gt_s and "-" in gt_s:
                    if pred_score and "-" in pred_score:
                        score_hit += 1
                    else:
                        score_miss += 1

                if gt_c and ":" in gt_c:
                    if pred_clock and ":" in pred_clock:
                        clock_hit += 1
                    else:
                        clock_miss += 1

                if idx % 25 == 0:
                    print(f"  [{idx}/{len(frames)}] score_hit={score_hit} clock_hit={clock_hit} errors={errors}")

            total_with_score = score_hit + score_miss
            total_with_clock = clock_hit + clock_miss
            avg_lat = sum(latencies) / len(latencies) if latencies else 0

            summary = {
                "model": model,
                "mode": mode,
                "frames_tested": len(frames),
                "json_valid": json_valid,
                "errors": errors,
                "score_hit": score_hit,
                "score_miss": score_miss,
                "score_rate": round(score_hit / total_with_score, 4) if total_with_score else 0,
                "clock_hit": clock_hit,
                "clock_miss": clock_miss,
                "clock_rate": round(clock_hit / total_with_clock, 4) if total_with_clock else 0,
                "avg_latency_s": round(avg_lat, 2),
            }
            results[key] = summary
            print(f"  RESULT: score={score_hit}/{total_with_score} ({summary['score_rate']:.1%}), "
                  f"clock={clock_hit}/{total_with_clock} ({summary['clock_rate']:.1%}), "
                  f"latency={avg_lat:.2f}s")

    # Save results
    run_id = f"scoreboard_ocr_benchmark__{int(time.time())}"
    out_dir = args.output_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2))

    print(f"\n{'='*60}")
    print("COMPARISON TABLE")
    print(f"{'Model+Mode':<50} {'Score%':>8} {'Clock%':>8} {'Latency':>8}")
    print("-" * 76)
    for key, s in results.items():
        print(f"{key:<50} {s['score_rate']:>7.1%} {s['clock_rate']:>7.1%} {s['avg_latency_s']:>7.2f}s")

    print(f"\nResults saved to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
