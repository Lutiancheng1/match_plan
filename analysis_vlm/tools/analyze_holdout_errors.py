#!/usr/bin/env python3
"""Analyze holdout evaluation errors by comparing model output vs ground truth."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

DEFAULT_HOLDOUT_ROOT = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/04_holdout_eval"
)
DEFAULT_REPORTS_DIR = Path("/Users/niannianshunjing/match_plan/analysis_vlm/reports")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze holdout eval errors.")
    parser.add_argument("--results-csv", type=Path, required=True, help="Path to holdout eval results.csv")
    parser.add_argument("--holdout-root", type=Path, default=DEFAULT_HOLDOUT_ROOT)
    return parser.parse_args()


def load_ground_truth(holdout_root: Path) -> dict[str, dict]:
    """Load ground truth from holdout records keyed by frame_id."""
    gt = {}
    records_root = holdout_root / "frame_observation/records"
    for p in sorted(records_root.rglob("*.json")):
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        fid = r.get("frame_id", p.stem)
        obs = r.get("observation", {})
        if obs:
            gt[fid] = obs
    return gt


def main() -> int:
    args = parse_args()
    gt = load_ground_truth(args.holdout_root)
    print(f"Ground truth records: {len(gt)}")

    rows = []
    with args.results_csv.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)
    print(f"Eval results: {len(rows)}")

    errors = {
        "scene_type_mismatch": [],
        "score_missed": [],
        "score_wrong": [],
        "clock_missed": [],
        "clock_wrong": [],
        "json_invalid": [],
    }
    scene_confusion = Counter()

    for row in rows:
        fid = row.get("frame_id", "")
        truth = gt.get(fid, {})
        if not truth:
            continue

        if row.get("json_valid") != "True":
            errors["json_invalid"].append(fid)
            continue

        # Scene type
        pred_scene = (row.get("scene_type") or "").strip()
        true_scene = (truth.get("scene_type") or "").strip()
        if pred_scene and true_scene and pred_scene != true_scene:
            errors["scene_type_mismatch"].append({"frame_id": fid, "pred": pred_scene, "true": true_scene})
            scene_confusion[(true_scene, pred_scene)] += 1

        # Score
        true_score = (truth.get("score_detected") or "").strip()
        pred_score = (row.get("score_detected") or "").strip()
        if true_score and "-" in true_score:
            if not pred_score or "-" not in pred_score:
                errors["score_missed"].append(fid)
            elif pred_score != true_score:
                errors["score_wrong"].append({"frame_id": fid, "pred": pred_score, "true": true_score})

        # Clock
        true_clock = (truth.get("match_clock_detected") or "").strip()
        pred_clock = (row.get("match_clock_detected") or "").strip()
        if true_clock and ":" in true_clock:
            if not pred_clock or ":" not in pred_clock:
                errors["clock_missed"].append(fid)
            elif pred_clock != true_clock:
                errors["clock_wrong"].append({"frame_id": fid, "pred": pred_clock, "true": true_clock})

    report = {
        "total_frames": len(rows),
        "matched_with_gt": sum(1 for r in rows if r.get("frame_id", "") in gt),
        "error_counts": {k: len(v) for k, v in errors.items()},
        "scene_confusion_matrix": {f"{k[0]}->{k[1]}": v for k, v in scene_confusion.most_common(20)},
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
