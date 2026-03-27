#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_MANIFEST = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/meta/current_golden_sample_manifest.json")
DEFAULT_OUTPUT_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/strong_event_labels")
DEFAULT_SCHEMA = Path("/Users/niannianshunjing/match_plan/analysis_vlm/schemas/strong_event_observation.schema.json")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def normalize_clock(raw: str) -> str:
    raw = str(raw or "").strip()
    if "^" in raw:
        _, tail = raw.split("^", 1)
        return tail.strip()
    return raw


def bootstrap_hint(kind: str, reason: str, label: dict, meta: dict) -> dict:
    nearest = meta.get("nearest_timeline_row", {})
    return {
        "clip_kind": kind or "",
        "clip_reason": reason or "",
        "game_phase": label.get("game_phase", ""),
        "score": {
            "home": str(label.get("score_h", "")).strip(),
            "away": str(label.get("score_c", "")).strip(),
        },
        "match_clock": normalize_clock(label.get("match_clock", "")),
        "red_cards": {
            "home": str(nearest.get("redcard_h", "")).strip(),
            "away": str(nearest.get("redcard_c", "")).strip(),
        },
        "pricing_snapshot": {
            "ratio_re": str(nearest.get("ratio_re", "")).strip(),
            "ior_reh": str(nearest.get("ior_reh", "")).strip(),
            "ior_rec": str(nearest.get("ior_rec", "")).strip(),
            "ratio_rouo": str(nearest.get("ratio_rouo", "")).strip(),
            "ior_rouh": str(nearest.get("ior_rouh", "")).strip(),
            "ior_rouc": str(nearest.get("ior_rouc", "")).strip(),
            "ior_rmh": str(nearest.get("ior_rmh", "")).strip(),
            "ior_rmn": str(nearest.get("ior_rmn", "")).strip(),
            "ior_rmc": str(nearest.get("ior_rmc", "")).strip(),
        },
    }


def build_record(match: dict, clip: dict) -> dict:
    label_path = Path(clip["label_path"])
    meta_path = Path(clip["meta_path"])
    label_payload = load_json(label_path)
    meta_payload = load_json(meta_path)
    label = label_payload.get("labels", {})
    nearest = meta_payload.get("nearest_timeline_row", {})

    record = {
        "clip_id": clip["clip_id"],
        "teams": match.get("teams", ""),
        "quality_tier": match.get("quality_tier", "gold"),
        "source_match_id": label_payload.get("source_match_id", ""),
        "clip_path": clip["clip_path"],
        "source_label_path": clip["label_path"],
        "source_meta_path": clip["meta_path"],
        "source_video": meta_payload.get("source_video", ""),
        "pivot_elapsed_sec": meta_payload.get("pivot_elapsed"),
        "bootstrap": bootstrap_hint(
            clip.get("kind", ""),
            clip.get("reason", ""),
            label,
            meta_payload,
        ),
        "strong_event_observation": {
            "primary_event_label": "none",
            "secondary_event_labels": [],
            "severity": "low",
            "affected_side": "unknown",
            "stoppage_seconds_estimate": 0,
            "expected_pricing_impact_direction": "unclear",
            "expected_pricing_impact_confidence": 0.0,
            "rationale_short": "",
        },
        "annotation": {
            "manual_review_status": "pending",
            "needs_human_review": True,
            "review_notes": "",
            "annotator": "",
            "reviewed_at": "",
        },
        "joint_eval_stub": {
            "repricing_expected": None,
            "repricing_direction": "",
            "repricing_strength": "",
            "first_leg_side": "",
            "first_leg_urgency": "",
            "hedge_window_expected_sec": None,
            "edge_rationale_short": "",
        },
        "arbitrage_score_stub": {
            "opportunity_grade": "",
            "should_enter_first_leg": None,
            "first_leg_side": "",
            "first_leg_confidence": None,
            "expected_repricing_direction": "",
            "expected_repricing_strength": "",
            "expected_hedge_window_sec": None,
            "expected_arb_feasibility": "",
            "max_risk_flag": "",
            "rationale_short": "",
        },
        "source_snapshot": {
            "league": str(nearest.get("league", "")).strip(),
            "team_h": str(nearest.get("team_h", "")).strip(),
            "team_c": str(nearest.get("team_c", "")).strip(),
            "timestamp_utc": str(nearest.get("timestamp_utc", "")).strip(),
        },
    }
    return record


def ensure_schema_exists(schema_path: Path) -> None:
    if not schema_path.exists():
        raise FileNotFoundError(f"Missing schema: {schema_path}")
    json.loads(schema_path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description="Build strong-event label skeletons for current Gold clips.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()

    ensure_schema_exists(args.schema)
    payload = load_json(args.manifest)
    output_root = args.output_root
    labels_root = output_root / "labels"
    manifests_root = output_root / "manifests"
    labels_root.mkdir(parents=True, exist_ok=True)
    manifests_root.mkdir(parents=True, exist_ok=True)

    manifest_records: list[dict] = []
    created = 0

    for match in payload.get("matches", []):
        teams = match.get("teams", "unknown_match")
        safe_teams = teams.replace(" ", "_").replace("/", "_")
        match_dir = labels_root / safe_teams
        match_dir.mkdir(parents=True, exist_ok=True)
        per_match: list[dict] = []
        for clip in match.get("clips", []):
            record = build_record(match, clip)
            out_path = match_dir / f"{clip['clip_id']}.json"
            out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            created += 1
            per_match.append(
                {
                    "clip_id": clip["clip_id"],
                    "output_path": str(out_path),
                    "clip_path": clip["clip_path"],
                    "source_label_path": clip["label_path"],
                    "source_meta_path": clip["meta_path"],
                }
            )
        match_manifest = {
            "teams": teams,
            "clip_count": len(per_match),
            "records": per_match,
        }
        manifest_path = manifests_root / f"{safe_teams}.json"
        manifest_path.write_text(json.dumps(match_manifest, ensure_ascii=False, indent=2))
        manifest_records.append(match_manifest)

    current_manifest = {
        "source_manifest": str(args.manifest),
        "match_count": len(manifest_records),
        "record_count": created,
        "matches": manifest_records,
    }
    current_manifest_path = manifests_root / "current_strong_event_label_manifest.json"
    current_manifest_path.write_text(json.dumps(current_manifest, ensure_ascii=False, indent=2))
    print(json.dumps({
        "match_count": len(manifest_records),
        "record_count": created,
        "labels_root": str(labels_root),
        "manifest_path": str(current_manifest_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
