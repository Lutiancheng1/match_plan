#!/usr/bin/env python3
"""Sample prelabeled records for quality audit."""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

DEFAULT_FRAME_RECORDS = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/01_frame_observation/records"
)
DEFAULT_CLIP_RECORDS = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/02_clip_observation/records"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sample prelabeled records for audit.")
    parser.add_argument("--frame-records", type=Path, default=DEFAULT_FRAME_RECORDS)
    parser.add_argument("--clip-records", type=Path, default=DEFAULT_CLIP_RECORDS)
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def collect_prelabeled(records_root: Path) -> list[dict]:
    results = []
    for p in sorted(records_root.rglob("*.json")):
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        if r.get("annotation", {}).get("manual_review_status") == "auto_prelabeled":
            results.append({"path": str(p), "record": r})
    return results


def audit_record(rec: dict) -> dict:
    obs = rec.get("observation", {})
    issues = []

    scene = obs.get("scene_type", "")
    if scene == "unknown":
        issues.append("scene_type=unknown")

    score = obs.get("score_detected", "")
    if score and "-" not in score:
        issues.append(f"score_format_bad={score}")

    clock = obs.get("match_clock_detected", "")
    if clock and ":" not in clock:
        issues.append(f"clock_format_bad={clock}")

    vis = obs.get("scoreboard_visibility", "")
    if vis == "unknown":
        issues.append("scoreboard_visibility=unknown")

    events = obs.get("event_candidates", [])
    if not isinstance(events, list):
        issues.append("event_candidates_not_list")
    for ev in events if isinstance(events, list) else []:
        if not isinstance(ev, dict) or "label" not in ev:
            issues.append("event_missing_label")
            break

    conf = obs.get("confidence")
    if conf is not None and (not isinstance(conf, (int, float)) or conf < 0 or conf > 1):
        issues.append(f"confidence_out_of_range={conf}")

    return {
        "scene_type": scene,
        "score_detected": score,
        "match_clock_detected": clock,
        "scoreboard_visibility": vis,
        "event_count": len(events) if isinstance(events, list) else -1,
        "confidence": conf,
        "issues": issues,
        "issue_count": len(issues),
    }


def run_audit(records_root: Path, sample_size: int, rng: random.Random, label: str) -> dict:
    all_records = collect_prelabeled(records_root)
    if not all_records:
        return {"label": label, "total_prelabeled": 0, "sampled": 0, "error_rate": None}

    sample = rng.sample(all_records, min(sample_size, len(all_records)))
    audits = []
    for item in sample:
        a = audit_record(item["record"])
        a["path"] = item["path"]
        audits.append(a)

    with_issues = sum(1 for a in audits if a["issue_count"] > 0)
    issue_types: dict[str, int] = {}
    for a in audits:
        for iss in a["issues"]:
            key = iss.split("=")[0]
            issue_types[key] = issue_types.get(key, 0) + 1

    return {
        "label": label,
        "total_prelabeled": len(all_records),
        "sampled": len(sample),
        "with_issues": with_issues,
        "error_rate": round(with_issues / len(sample), 4) if sample else 0,
        "issue_distribution": issue_types,
        "samples": audits,
    }


def main() -> int:
    args = parse_args()
    rng = random.Random(args.seed)

    frame_audit = run_audit(args.frame_records, args.sample_size, rng, "frame_observation")
    clip_audit = run_audit(args.clip_records, args.sample_size, rng, "clip_observation")

    report = {
        "frame_audit": {k: v for k, v in frame_audit.items() if k != "samples"},
        "clip_audit": {k: v for k, v in clip_audit.items() if k != "samples"},
        "pass": (
            (frame_audit.get("error_rate") or 0) < 0.15
            and (clip_audit.get("error_rate") or 0) < 0.15
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # Also print sample details
    for audit in [frame_audit, clip_audit]:
        if audit.get("samples"):
            print(f"\n=== {audit['label']} samples with issues ===")
            for s in audit["samples"]:
                if s["issue_count"] > 0:
                    print(f"  {Path(s['path']).name}: {s['issues']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
