#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


DEFAULT_MANIFEST = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/04_golden_samples/strong_event_labels/manifests/current_strong_event_label_manifest.json"
)
DEFAULT_OUTPUT_ROOT = Path(
    "/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/05_eval_sets/event_odds_joint_eval_inputs"
)
DEFAULT_SCHEMA = Path(
    "/Users/niannianshunjing/match_plan/analysis_vlm/schemas/event_odds_repricing_eval.schema.json"
)

PRIMARY_HOME_FIELD = "ior_reh"
PRIMARY_AWAY_FIELD = "ior_rec"
WINDOW_OFFSETS = (-15, 0, 15, 30, 60)
KNOWN_GTYPES = {"FT", "BK", "ES", "TN", "VB", "BM", "TT", "BS", "SK", "OP"}


def load_json(path: Path):
    return json.loads(path.read_text())


def parse_gtypes(raw: str) -> set[str]:
    return {item.strip().upper() for item in str(raw or "").split(",") if item.strip()}


def prefixed_gtype(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for candidate in (Path(text).name, Path(text).stem, text):
        prefix = candidate.split("_", 1)[0].upper()
        if prefix in KNOWN_GTYPES:
            return prefix
    return ""


def infer_record_gtype(match: dict, record_stub: dict | None = None, strong_event_record: dict | None = None) -> str:
    record_stub = record_stub or {}
    strong_event_record = strong_event_record or {}
    for value in (
        strong_event_record.get("gtype"),
        match.get("gtype"),
        record_stub.get("gtype"),
        strong_event_record.get("clip_path"),
        record_stub.get("clip_path"),
        record_stub.get("output_path"),
        match.get("teams"),
    ):
        gtype = prefixed_gtype(value)
        if gtype:
            return gtype
    return "UNKNOWN"


def ensure_schema_exists(schema_path: Path) -> None:
    if not schema_path.exists():
        raise FileNotFoundError(f"Missing schema: {schema_path}")
    json.loads(schema_path.read_text())


def load_timeline_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    for row in rows:
        try:
            row["_elapsed_float"] = float(str(row.get("elapsed_sec", "")).strip() or "0")
        except ValueError:
            row["_elapsed_float"] = 0.0
    return rows


def nearest_row(rows: list[dict], target_elapsed: float) -> dict:
    return min(rows, key=lambda row: abs(row.get("_elapsed_float", 0.0) - target_elapsed))


def snapshot_from_row(row: dict) -> dict:
    return {
        "elapsed_sec": row.get("_elapsed_float"),
        "elapsed_hms": str(row.get("elapsed_hms", "")).strip(),
        "timestamp_utc": str(row.get("timestamp_utc", "")).strip(),
        "score_h": str(row.get("score_h", "")).strip(),
        "score_c": str(row.get("score_c", "")).strip(),
        "match_clock": str(row.get("match_clock", "")).strip(),
        "game_phase": str(row.get("game_phase", "")).strip(),
        "redcard_h": str(row.get("redcard_h", "")).strip(),
        "redcard_c": str(row.get("redcard_c", "")).strip(),
        "ratio_re": str(row.get("ratio_re", "")).strip(),
        "ior_reh": str(row.get("ior_reh", "")).strip(),
        "ior_rec": str(row.get("ior_rec", "")).strip(),
        "ratio_rouo": str(row.get("ratio_rouo", "")).strip(),
        "ior_rouh": str(row.get("ior_rouh", "")).strip(),
        "ior_rouc": str(row.get("ior_rouc", "")).strip(),
        "ior_rmh": str(row.get("ior_rmh", "")).strip(),
        "ior_rmn": str(row.get("ior_rmn", "")).strip(),
        "ior_rmc": str(row.get("ior_rmc", "")).strip(),
    }


def parse_float(value) -> float | None:
    try:
        raw = str(value).strip()
        if not raw:
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def compute_repricing_ground_truth(window_rows: dict[str, dict]) -> dict:
    pivot = window_rows.get("t_plus_0") or {}
    plus_15 = window_rows.get("t_plus_15") or {}
    plus_30 = window_rows.get("t_plus_30") or {}
    plus_60 = window_rows.get("t_plus_60") or {}

    pivot_home = parse_float(pivot.get(PRIMARY_HOME_FIELD))
    pivot_away = parse_float(pivot.get(PRIMARY_AWAY_FIELD))
    future_home_candidates = [
        parse_float(plus_15.get(PRIMARY_HOME_FIELD)),
        parse_float(plus_30.get(PRIMARY_HOME_FIELD)),
        parse_float(plus_60.get(PRIMARY_HOME_FIELD)),
    ]
    future_away_candidates = [
        parse_float(plus_15.get(PRIMARY_AWAY_FIELD)),
        parse_float(plus_30.get(PRIMARY_AWAY_FIELD)),
        parse_float(plus_60.get(PRIMARY_AWAY_FIELD)),
    ]
    future_home = next((x for x in future_home_candidates if x is not None), None)
    future_away = next((x for x in future_away_candidates if x is not None), None)

    direction = "unclear"
    strength = "unclear"
    repricing_expected = False
    first_leg_side = "none"
    first_leg_urgency = "none"
    hedge_window_expected_sec = 0
    rationale = "No stable repricing signal computed yet."
    trigger_family = "strong_event"
    suggested_side = "none"
    entry_window_open = False
    hedge_watch_open = False
    voice_text = ""

    deltas: dict[str, float] = {}
    if pivot_home is not None and future_home is not None:
        deltas["home"] = future_home - pivot_home
    if pivot_away is not None and future_away is not None:
        deltas["away"] = future_away - pivot_away

    if deltas:
        side = max(deltas, key=lambda key: abs(deltas[key]))
        delta = deltas[side]
        abs_delta = abs(delta)
        if abs_delta >= 0.03:
            repricing_expected = True
            if side == "home":
                direction = "home_price_up" if delta > 0 else "home_price_down"
            else:
                direction = "away_price_up" if delta > 0 else "away_price_down"
            if abs_delta >= 0.15:
                strength = "very_strong"
            elif abs_delta >= 0.08:
                strength = "strong"
            elif abs_delta >= 0.05:
                strength = "medium"
            else:
                strength = "weak"
            if direction.startswith("home_"):
                first_leg_side = "home"
            elif direction.startswith("away_"):
                first_leg_side = "away"
            suggested_side = first_leg_side
            first_leg_urgency = "immediate" if abs_delta >= 0.08 else "soon"
            hedge_window_expected_sec = 30 if abs_delta >= 0.08 else 60
            entry_window_open = True
            hedge_watch_open = True
            rationale = (
                f"Primary handicap odds moved on {side} side by {delta:+.3f} "
                f"between pivot and later evaluation window."
            )
            if first_leg_side in {"home", "away"}:
                voice_text = (
                    f"{first_leg_side} 方向进入观察窗口，"
                    f"预计 {hedge_window_expected_sec} 秒内关注反手价。"
                )

    return {
        "repricing_expected": repricing_expected,
        "repricing_direction": direction,
        "repricing_strength": strength,
        "first_leg_side": first_leg_side,
        "first_leg_urgency": first_leg_urgency,
        "hedge_window_expected_sec": hedge_window_expected_sec,
        "trigger_family": trigger_family,
        "suggested_side": suggested_side,
        "entry_window_open": entry_window_open,
        "hedge_watch_open": hedge_watch_open,
        "voice_text": voice_text,
        "edge_rationale_short": rationale,
        "ground_truth_delta": {
            "home_ior_reh_delta": deltas.get("home"),
            "away_ior_rec_delta": deltas.get("away"),
        },
    }


def build_joint_eval_record(strong_event_record: dict) -> dict:
    meta_path = Path(strong_event_record["source_meta_path"])
    meta_payload = load_json(meta_path)
    timeline_path = Path(meta_payload["source_timeline"])
    rows = load_timeline_rows(timeline_path)
    pivot_elapsed = float(meta_payload.get("pivot_elapsed") or strong_event_record.get("pivot_elapsed_sec") or 0.0)

    window_rows: dict[str, dict] = {}
    for offset in WINDOW_OFFSETS:
        label = f"t_plus_{offset}" if offset >= 0 else f"t_minus_{abs(offset)}"
        target_elapsed = max(0.0, pivot_elapsed + float(offset))
        row = nearest_row(rows, target_elapsed)
        window_rows[label] = snapshot_from_row(row)

    ground_truth = compute_repricing_ground_truth(window_rows)
    record = {
        "clip_id": strong_event_record["clip_id"],
        "gtype": strong_event_record.get("gtype", "UNKNOWN"),
        "teams": strong_event_record.get("teams", ""),
        "quality_tier": strong_event_record.get("quality_tier", "gold"),
        "clip_path": strong_event_record.get("clip_path", ""),
        "source_video": strong_event_record.get("source_video", ""),
        "source_timeline": meta_payload.get("source_timeline", ""),
        "source_viewer": meta_payload.get("source_viewer", ""),
        "source_strong_event_label": strong_event_record.get("strong_event_observation", {}),
        "source_annotation": strong_event_record.get("annotation", {}),
        "pivot_elapsed_sec": pivot_elapsed,
        "clip_window": {
            "start_sec": meta_payload.get("start_sec"),
            "end_sec": meta_payload.get("end_sec"),
            "duration_sec": meta_payload.get("duration_sec"),
        },
        "event_context": {
            "bootstrap": strong_event_record.get("bootstrap", {}),
            "source_snapshot": strong_event_record.get("source_snapshot", {}),
        },
        "odds_windows": {
            "t_minus_15": window_rows["t_minus_15"],
            "t_plus_0": window_rows["t_plus_0"],
            "t_plus_15": window_rows["t_plus_15"],
            "t_plus_30": window_rows["t_plus_30"],
            "t_plus_60": window_rows["t_plus_60"],
        },
        "joint_eval_ground_truth": ground_truth,
        "joint_eval_annotation": {
            "manual_review_status": "pending",
            "needs_human_review": True,
            "review_notes": "",
            "annotator": "",
            "reviewed_at": "",
        },
        "joint_eval_model_target": {
            "repricing_expected": None,
            "repricing_direction": "",
            "repricing_strength": "",
            "first_leg_side": "",
            "first_leg_urgency": "",
            "hedge_window_expected_sec": None,
            "trigger_family": "strong_event",
            "suggested_side": "none",
            "entry_window_open": False,
            "hedge_watch_open": False,
            "voice_text": "",
            "edge_rationale_short": "",
        },
        "realtime_trade_alert_stub": {
            "alert_type": "watch",
            "trigger_family": "strong_event",
            "suggested_side": "none",
            "entry_window_open": False,
            "hedge_watch_open": False,
            "voice_text": "",
            "rationale_short": "",
        },
    }
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Phase-2 event+odds joint-eval inputs from strong-event labels.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--gtypes", default="FT")
    args = parser.parse_args()

    ensure_schema_exists(args.schema)
    payload = load_json(args.manifest)

    output_root = args.output_root
    labels_root = output_root / "records"
    manifests_root = output_root / "manifests"
    labels_root.mkdir(parents=True, exist_ok=True)
    manifests_root.mkdir(parents=True, exist_ok=True)

    manifest_records: list[dict] = []
    created = 0
    allowed_gtypes = parse_gtypes(args.gtypes)

    for match in payload.get("matches", []):
        match_gtype = infer_record_gtype(match)
        if allowed_gtypes and match_gtype not in allowed_gtypes:
            continue
        teams = match.get("teams", "unknown_match")
        safe_teams = teams.replace(" ", "_").replace("/", "_")
        match_dir = labels_root / safe_teams
        match_dir.mkdir(parents=True, exist_ok=True)
        per_match: list[dict] = []
        for record_stub in match.get("records", []):
            strong_event_path = Path(record_stub["output_path"])
            strong_event_record = load_json(strong_event_path)
            record_gtype = infer_record_gtype(match, record_stub, strong_event_record)
            if allowed_gtypes and record_gtype not in allowed_gtypes:
                continue
            strong_event_record["gtype"] = record_gtype
            joint_record = build_joint_eval_record(strong_event_record)
            out_path = match_dir / f"{record_stub['clip_id']}.json"
            out_path.write_text(json.dumps(joint_record, ensure_ascii=False, indent=2))
            created += 1
            per_match.append(
                {
                    "clip_id": record_stub["clip_id"],
                    "gtype": record_gtype,
                    "output_path": str(out_path),
                    "clip_path": record_stub["clip_path"],
                    "source_strong_event_path": str(strong_event_path),
                }
            )

        match_manifest = {
            "gtype": match_gtype,
            "teams": teams,
            "clip_count": len(per_match),
            "records": per_match,
        }
        manifest_path = manifests_root / f"{safe_teams}.json"
        manifest_path.write_text(json.dumps(match_manifest, ensure_ascii=False, indent=2))
        manifest_records.append(match_manifest)

    current_manifest = {
        "source_manifest": str(args.manifest),
        "gtypes": sorted(allowed_gtypes) if allowed_gtypes else [],
        "match_count": len(manifest_records),
        "record_count": created,
        "matches": manifest_records,
    }
    current_manifest_path = manifests_root / "current_event_odds_joint_eval_manifest.json"
    current_manifest_path.write_text(json.dumps(current_manifest, ensure_ascii=False, indent=2))

    print(
        json.dumps(
            {
                "match_count": len(manifest_records),
                "record_count": created,
                "records_root": str(labels_root),
                "manifest_path": str(current_manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
