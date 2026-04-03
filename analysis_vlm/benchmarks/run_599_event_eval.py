#!/usr/bin/env python3
"""Evaluate 9B base model zero-shot event detection on 599 test set.

Sends each test frame to oMLX, parses event_candidates, compares to GT.
Outputs per-class precision/recall/F1 and confusion matrix.

Usage:
    python run_599_event_eval.py                    # full test set
    python run_599_event_eval.py --max-frames 50    # quick test
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from observation_postprocess import parse_model_output

OMLX_URL = "http://127.0.0.1:8000/v1"
OMLX_KEY = "sk-1234"
MODEL = "Qwen3.5-VL-9B-8bit-MLX-CRACK"

TEST_JSONL = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/07_599_event_training/training_data/599_event_conversations_test.jsonl")
OUTPUT_DIR = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/07_599_event_training/eval_results")

ALL_LABELS = [
    "goal", "red_card", "penalty", "corner_candidate", "dangerous_attack",
    "celebration", "replay_sequence", "substitution", "heavy_contact_foul",
    "foul", "offside", "injury_or_stoppage", "none",
]


def call_omlx(image_path: str, system_prompt: str, user_prompt: str) -> str | None:
    """Send image to oMLX and get raw text response."""
    import urllib.request

    img_path = Path(image_path)
    if not img_path.exists():
        return None

    img_b64 = base64.b64encode(img_path.read_bytes()).decode()
    suffix = img_path.suffix.lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(
        suffix.lstrip("."), "image/jpeg"
    )

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                    {"type": "text", "text": user_prompt},
                ],
            },
        ],
        "max_tokens": 1024,
        "temperature": 0.0,
    }

    req = urllib.request.Request(
        f"{OMLX_URL}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OMLX_KEY}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        return None


def extract_top_event(obs: dict) -> str:
    """Extract the top event label from observation."""
    candidates = obs.get("event_candidates", [])
    if not candidates:
        return "none"
    # Sort by confidence, take highest
    best = max(candidates, key=lambda c: c.get("confidence", 0))
    label = best.get("label", "none")
    if label not in ALL_LABELS:
        return "none"
    return label


def extract_gt_label(record: dict) -> str:
    """Extract ground truth label from test record metadata."""
    return record.get("metadata", {}).get("event_label", "none")


def compute_metrics(gt_labels: list[str], pred_labels: list[str]) -> dict:
    """Compute per-class precision, recall, F1."""
    labels_in_data = sorted(set(gt_labels + pred_labels))
    metrics = {}

    for label in labels_in_data:
        tp = sum(1 for g, p in zip(gt_labels, pred_labels) if g == label and p == label)
        fp = sum(1 for g, p in zip(gt_labels, pred_labels) if g != label and p == label)
        fn = sum(1 for g, p in zip(gt_labels, pred_labels) if g == label and p != label)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        metrics[label] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "support": tp + fn,
        }

    # Overall accuracy
    correct = sum(1 for g, p in zip(gt_labels, pred_labels) if g == p)
    metrics["_overall"] = {
        "accuracy": round(correct / len(gt_labels), 3) if gt_labels else 0,
        "total": len(gt_labels),
        "correct": correct,
    }

    return metrics


LABEL_ZH = {
    "goal": "进球", "red_card": "红牌", "penalty": "点球",
    "corner_candidate": "角球", "dangerous_attack": "射门/危险进攻",
    "celebration": "庆祝", "replay_sequence": "VAR回看",
    "substitution": "换人", "heavy_contact_foul": "黄牌",
    "foul": "普通犯规", "offside": "越位",
    "injury_or_stoppage": "伤停", "none": "无事件",
}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--test-jsonl", type=Path, default=TEST_JSONL)
    p.add_argument("--max-frames", type=int, default=0, help="0=all")
    p.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return p.parse_args()


def main():
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Load test records
    records = []
    with open(args.test_jsonl, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    if args.max_frames > 0:
        records = records[:args.max_frames]

    print(f"Evaluating {len(records)} frames on {MODEL}")
    print(f"{'='*70}")

    gt_labels = []
    pred_labels = []
    raw_results = []
    errors = 0
    t0 = time.time()

    for i, rec in enumerate(records):
        gt = extract_gt_label(rec)
        image_path = rec["conversations"][1]["images"][0]
        system_prompt = rec["conversations"][0]["content"]
        user_prompt = rec["conversations"][1]["content"]

        raw = call_omlx(image_path, system_prompt, user_prompt)
        if raw is None:
            errors += 1
            pred = "none"
        else:
            obs = parse_model_output(raw)
            pred = extract_top_event(obs)

        gt_labels.append(gt)
        pred_labels.append(pred)
        raw_results.append({
            "index": i,
            "image": image_path,
            "gt": gt,
            "pred": pred,
            "correct": gt == pred,
            "raw_output": raw[:200] if raw else None,
        })

        if (i + 1) % 20 == 0 or i < 5:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta = (len(records) - i - 1) / rate if rate > 0 else 0
            correct_so_far = sum(1 for r in raw_results if r["correct"])
            acc = correct_so_far / (i + 1)
            gt_zh = LABEL_ZH.get(gt, gt)
            pred_zh = LABEL_ZH.get(pred, pred)
            mark = "O" if gt == pred else "X"
            print(f"  [{i+1:>4}/{len(records)}] {mark} GT={gt_zh:<10} PRED={pred_zh:<10} acc={acc:.1%} ETA={eta/60:.1f}min")

    elapsed = time.time() - t0

    # Compute metrics
    metrics = compute_metrics(gt_labels, pred_labels)

    # Print results
    print(f"\n{'='*70}")
    print(f"结果总览 ({len(records)} 帧, {elapsed/60:.1f}分钟, {errors} 错误)")
    print(f"{'='*70}")
    print(f"  总体准确率: {metrics['_overall']['accuracy']:.1%} ({metrics['_overall']['correct']}/{metrics['_overall']['total']})")
    print()
    print(f"  {'标签':<16} {'中文':<12} {'Precision':>9} {'Recall':>7} {'F1':>7} {'TP':>5} {'FP':>5} {'FN':>5} {'支持数':>6}")
    print(f"  {'-'*85}")
    for label in ALL_LABELS:
        if label in metrics:
            m = metrics[label]
            zh = LABEL_ZH.get(label, label)
            print(f"  {label:<16} {zh:<12} {m['precision']:>8.1%} {m['recall']:>6.1%} {m['f1']:>6.1%} {m['tp']:>5} {m['fp']:>5} {m['fn']:>5} {m['support']:>6}")

    # Confusion matrix (top predictions for each GT class)
    print(f"\n  混淆分析（GT→最常预测）:")
    for gt_label in ALL_LABELS:
        gt_indices = [i for i, g in enumerate(gt_labels) if g == gt_label]
        if not gt_indices:
            continue
        pred_dist = Counter(pred_labels[i] for i in gt_indices)
        top3 = pred_dist.most_common(3)
        zh = LABEL_ZH.get(gt_label, gt_label)
        parts = [f"{LABEL_ZH.get(p,p)}={c}" for p, c in top3]
        print(f"    {zh:<10} (n={len(gt_indices):>4}): {', '.join(parts)}")

    # Save results
    results_path = args.output_dir / "eval_results.json"
    results_path.write_text(json.dumps({
        "model": MODEL,
        "test_set": str(args.test_jsonl),
        "total_frames": len(records),
        "elapsed_sec": round(elapsed, 1),
        "errors": errors,
        "metrics": metrics,
        "predictions": raw_results,
    }, ensure_ascii=False, indent=2))
    print(f"\n  详细结果: {results_path}")


if __name__ == "__main__":
    main()
