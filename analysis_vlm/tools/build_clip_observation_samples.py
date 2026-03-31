#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_GOLD_MATCHES = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/01_gold_matches/current_gold_matches.json")
DEFAULT_OUTPUT_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/02_clip_observation")
DEFAULT_SCHEMA = Path("/Users/niannianshunjing/match_plan/analysis_vlm/schemas/live_frame_observation.schema.json")

SIGNAL_FIELDS = [
    "score_h",
    "score_c",
    "redcard_h",
    "redcard_c",
    "ratio_re",
    "ior_reh",
    "ior_rec",
    "ratio_rouo",
    "ior_rouh",
    "ior_rouc",
]

COURSE_REASON_TO_TARGET = {
    "score_h": "score_change_review",
    "score_c": "score_change_review",
    "redcard_h": "discipline_event_review",
    "redcard_c": "discipline_event_review",
    "ratio_re": "market_movement_review",
    "ior_reh": "market_movement_review",
    "ior_rec": "market_movement_review",
    "ratio_rouo": "market_movement_review",
    "ior_rouh": "market_movement_review",
    "ior_rouc": "market_movement_review",
}


def load_json(path: Path):
    return json.loads(path.read_text())


def ensure_schema_exists(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing schema: {path}")
    json.loads(path.read_text())


def slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug[:120] or "unknown_match"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build clip-observation teaching samples from football Gold matches.")
    parser.add_argument("--gold-matches", type=Path, default=DEFAULT_GOLD_MATCHES)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--gtype", default="FT")
    parser.add_argument("--pre-seconds", type=float, default=1.5)
    parser.add_argument("--post-seconds", type=float, default=2.5)
    parser.add_argument("--calm-duration", type=float, default=4.0)
    parser.add_argument("--calm-gap-seconds", type=float, default=180.0)
    parser.add_argument("--dense-review-interval-sec", type=float, default=45.0)
    parser.add_argument("--dense-review-duration-sec", type=float, default=3.5)
    parser.add_argument("--max-dense-review-per-match", type=int, default=8)
    parser.add_argument("--overlap-merge-gap", type=float, default=2.0)
    parser.add_argument("--limit-per-match", type=int, default=24)
    parser.add_argument("--min-duration-sec", type=float, default=45.0)
    return parser.parse_args()


def load_timeline_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def timeline_value(row: dict[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def normalize_clock(raw: str) -> str:
    raw = str(raw or "").strip()
    if "^" in raw:
        _, tail = raw.split("^", 1)
        return tail.strip()
    return raw


def derive_course_target(reason: str, kind: str) -> str:
    if kind == "calm":
        return "steady_reference"
    if kind == "dense_review":
        return "contact_restart_scan"
    parts = [part.strip() for part in str(reason or "").split(",") if part.strip()]
    targets = {COURSE_REASON_TO_TARGET.get(part, "") for part in parts}
    targets.discard("")
    if "discipline_event_review" in targets:
        return "discipline_event_review"
    if "score_change_review" in targets:
        return "goal_or_restart_review"
    if "market_movement_review" in targets:
        return "manual_visual_review"
    return "manual_visual_review"


def derive_review_priority(course_target: str) -> str:
    if course_target in {"discipline_event_review", "goal_or_restart_review", "contact_restart_scan"}:
        return "high"
    if course_target == "manual_visual_review":
        return "medium"
    return "low"


def derive_fact_focus(course_target: str, kind: str) -> list[str]:
    if course_target == "contact_restart_scan":
        return [
            "heavy_contact_foul",
            "injury_or_stoppage",
            "ball_out_of_play",
            "corner_candidate",
        ]
    if course_target == "discipline_event_review":
        return [
            "heavy_contact_foul",
            "injury_or_stoppage",
            "replay_sequence",
        ]
    if course_target == "goal_or_restart_review":
        return [
            "ball_out_of_play",
            "corner_candidate",
            "dangerous_attack",
        ]
    if kind == "calm":
        return ["live_play_reference", "scoreboard_reference"]
    return ["manual_visual_review"]


def derive_rule_focus(course_target: str) -> list[str]:
    if course_target == "contact_restart_scan":
        return ["last_touch_side", "exit_boundary", "restart_type", "discipline_outcome"]
    if course_target == "discipline_event_review":
        return ["discipline_outcome", "time_loss_candidate"]
    if course_target == "goal_or_restart_review":
        return ["last_touch_side", "exit_boundary", "restart_type"]
    return []


def derive_teaching_tags(course_target: str, kind: str) -> list[str]:
    tags = ["football_phase1"]
    if kind == "dense_review":
        tags.append("dense_review")
    if course_target:
        tags.append(course_target)
    return tags


def detect_change_events(
    rows: list[dict[str, str]],
    *,
    duration_sec: float,
    pre_seconds: float,
    post_seconds: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    prev: dict[str, str] | None = None
    for idx, row in enumerate(rows):
        elapsed = float(row["elapsed_sec"])
        if prev is None:
            prev = row
            continue
        changes: list[str] = []
        for key in SIGNAL_FIELDS:
            if timeline_value(prev, key) != timeline_value(row, key):
                changes.append(key)
        if changes:
            start_sec = max(0.0, elapsed - pre_seconds)
            end_sec = min(duration_sec, elapsed + post_seconds)
            if end_sec - start_sec >= 2.0:
                candidates.append(
                    {
                        "kind": "event",
                        "reason": ",".join(changes),
                        "start_sec": round(start_sec, 3),
                        "end_sec": round(end_sec, 3),
                        "pivot_elapsed": round(elapsed, 3),
                        "row_index": idx,
                    }
                )
        prev = row
    return candidates


def detect_calm_windows(
    *,
    duration_sec: float,
    calm_duration: float,
    calm_gap_seconds: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    starts = [0.0]
    current = calm_gap_seconds
    while current + calm_duration <= duration_sec:
        starts.append(current)
        current += calm_gap_seconds
    for start_sec in starts:
        end_sec = min(duration_sec, start_sec + calm_duration)
        if end_sec - start_sec < 2.0:
            continue
        candidates.append(
            {
                "kind": "calm",
                "reason": "steady_reference",
                "start_sec": round(start_sec, 3),
                "end_sec": round(end_sec, 3),
                "pivot_elapsed": round(start_sec, 3),
                "row_index": 0,
            }
        )
    return candidates


def detect_dense_review_windows(
    *,
    duration_sec: float,
    dense_review_interval_sec: float,
    dense_review_duration_sec: float,
    max_dense_review_per_match: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if duration_sec <= 0 or dense_review_interval_sec <= 0 or dense_review_duration_sec <= 0:
        return candidates
    cursor = max(0.0, dense_review_interval_sec / 2.0)
    while cursor + dense_review_duration_sec <= duration_sec and len(candidates) < max_dense_review_per_match:
        start_sec = max(0.0, cursor - dense_review_duration_sec / 2.0)
        end_sec = min(duration_sec, start_sec + dense_review_duration_sec)
        if end_sec - start_sec >= 2.0:
            candidates.append(
                {
                    "kind": "dense_review",
                    "reason": "contact_restart_scan",
                    "start_sec": round(start_sec, 3),
                    "end_sec": round(end_sec, 3),
                    "pivot_elapsed": round((start_sec + end_sec) / 2.0, 3),
                    "row_index": 0,
                }
            )
        cursor += dense_review_interval_sec
    return candidates


def dedupe_candidates(candidates: list[dict[str, Any]], overlap_merge_gap: float, limit_per_match: int) -> list[dict[str, Any]]:
    priority = {"event": 3, "dense_review": 2, "calm": 1}
    ordered = sorted(candidates, key=lambda item: (item["start_sec"], -priority.get(item["kind"], 0), item["end_sec"]))
    kept: list[dict[str, Any]] = []
    last_end = -9999.0
    for candidate in ordered:
        if candidate["start_sec"] <= last_end + overlap_merge_gap:
            if kept and priority.get(candidate["kind"], 0) > priority.get(kept[-1]["kind"], 0):
                kept[-1] = candidate
                last_end = candidate["end_sec"]
            continue
        kept.append(candidate)
        last_end = candidate["end_sec"]
        if len(kept) >= limit_per_match:
            break
    return kept


def nearest_timeline_row(rows: list[dict[str, str]], pivot_elapsed: float) -> dict[str, str]:
    nearest = rows[0]
    min_gap = abs(float(nearest["elapsed_sec"]) - pivot_elapsed)
    for row in rows[1:]:
        gap = abs(float(row["elapsed_sec"]) - pivot_elapsed)
        if gap < min_gap:
            nearest = row
            min_gap = gap
    return nearest


def ffmpeg_cut(input_video: Path, output_video: Path, start_sec: float, duration_sec: float) -> None:
    output_video.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_sec:.3f}",
            "-i",
            str(input_video),
            "-t",
            f"{duration_sec:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            str(output_video),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    args = parse_args()
    ensure_schema_exists(args.schema)
    matches = load_json(args.gold_matches)
    output_root = args.output_root
    clips_root = output_root / "clips"
    records_root = output_root / "records"
    manifests_root = output_root / "manifests"
    clips_root.mkdir(parents=True, exist_ok=True)
    records_root.mkdir(parents=True, exist_ok=True)
    manifests_root.mkdir(parents=True, exist_ok=True)

    manifest_matches: list[dict] = []
    total_records = 0

    for match in matches:
        if str(match.get("gtype", "")).upper() != str(args.gtype).upper():
            continue
        duration_sec = float(match.get("duration_sec") or 0.0)
        if duration_sec < args.min_duration_sec:
            continue
        teams = str(match.get("teams") or "")
        match_slug = slugify(teams)
        video_path = Path(match["video"])
        timeline_path = Path(match["timeline"]) if match.get("timeline") else None
        viewer_path = Path(match["viewer"]) if match.get("viewer") else None
        rows = load_timeline_rows(timeline_path)

        candidates: list[dict[str, Any]] = []
        if rows:
            candidates = detect_change_events(
                rows,
                duration_sec=duration_sec,
                pre_seconds=args.pre_seconds,
                post_seconds=args.post_seconds,
            )
        candidates.extend(
            detect_calm_windows(
                duration_sec=duration_sec,
                calm_duration=args.calm_duration,
                calm_gap_seconds=args.calm_gap_seconds,
            )
        )
        candidates.extend(
            detect_dense_review_windows(
                duration_sec=duration_sec,
                dense_review_interval_sec=args.dense_review_interval_sec,
                dense_review_duration_sec=args.dense_review_duration_sec,
                max_dense_review_per_match=args.max_dense_review_per_match,
            )
        )
        if not candidates:
            continue
        candidates = dedupe_candidates(candidates, args.overlap_merge_gap, args.limit_per_match)

        match_clip_dir = clips_root / match_slug
        match_record_dir = records_root / match_slug
        match_clip_dir.mkdir(parents=True, exist_ok=True)
        match_record_dir.mkdir(parents=True, exist_ok=True)

        records: list[dict] = []
        for idx, item in enumerate(candidates, start=1):
            clip_id = f"{match_slug}__clip_{idx:03d}"
            clip_path = match_clip_dir / f"{clip_id}.mp4"
            duration = round(item["end_sec"] - item["start_sec"], 3)
            ffmpeg_cut(video_path, clip_path, item["start_sec"], duration)
            nearest = nearest_timeline_row(rows, float(item["pivot_elapsed"])) if rows else {}
            record = {
                "clip_id": clip_id,
                "gtype": str(match.get("gtype") or ""),
                "teams": teams,
                "match_id": str(match.get("match_id") or ""),
                "quality_tier": str(match.get("quality_tier") or "gold"),
                "source_video": str(video_path),
                "source_timeline": str(timeline_path or ""),
                "source_viewer": str(viewer_path or ""),
                "kind": item["kind"],
                "reason": item["reason"],
                "course_target": derive_course_target(item["reason"], item["kind"]),
                "start_sec": item["start_sec"],
                "end_sec": item["end_sec"],
                "duration_sec": duration,
                "pivot_elapsed_sec": item["pivot_elapsed"],
                "clip_path": str(clip_path),
                "bootstrap": {
                    "score_detected": f"{nearest.get('score_h', '').strip()}-{nearest.get('score_c', '').strip()}".strip("-"),
                    "match_clock_detected": normalize_clock(nearest.get("match_clock", "")),
                    "game_phase": str(nearest.get("game_phase", "")).strip(),
                },
                "observation": {
                    "scene_type": "unknown",
                    "score_detected": "",
                    "match_clock_detected": "",
                    "scoreboard_visibility": "unknown",
                    "replay_risk": "high",
                    "tradeability": "watch_only",
                    "event_candidates": [],
                    "confidence": 0.0,
                    "explanation_short": "",
                },
                "annotation": {
                    "manual_review_status": "pending",
                    "needs_human_review": True,
                    "review_priority": derive_review_priority(derive_course_target(item["reason"], item["kind"])),
                    "fact_focus": derive_fact_focus(derive_course_target(item["reason"], item["kind"]), item["kind"]),
                    "rule_focus": derive_rule_focus(derive_course_target(item["reason"], item["kind"])),
                    "teaching_tags": derive_teaching_tags(derive_course_target(item["reason"], item["kind"]), item["kind"]),
                    "review_notes": "",
                    "annotator": "",
                    "reviewed_at": "",
                },
            }
            record_path = match_record_dir / f"{clip_id}.json"
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            records.append(
                {
                    "clip_id": clip_id,
                    "kind": item["kind"],
                    "reason": item["reason"],
                    "course_target": derive_course_target(item["reason"], item["kind"]),
                    "start_sec": item["start_sec"],
                    "end_sec": item["end_sec"],
                    "clip_path": str(clip_path),
                    "record_path": str(record_path),
                }
            )
            total_records += 1

        match_manifest = {
            "teams": teams,
            "match_id": str(match.get("match_id") or ""),
            "gtype": str(match.get("gtype") or ""),
            "source_video": str(video_path),
            "record_count": len(records),
            "records": records,
        }
        manifest_path = manifests_root / f"{match_slug}.json"
        manifest_path.write_text(json.dumps(match_manifest, ensure_ascii=False, indent=2))
        manifest_matches.append(match_manifest)

    current_manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_gold_matches": str(args.gold_matches),
        "schema": str(args.schema),
        "gtype": str(args.gtype).upper(),
        "match_count": len(manifest_matches),
        "record_count": total_records,
        "matches": manifest_matches,
    }
    current_manifest_path = manifests_root / "current_clip_observation_manifest.json"
    current_manifest_path.write_text(json.dumps(current_manifest, ensure_ascii=False, indent=2))
    print(json.dumps(
        {
            "match_count": len(manifest_matches),
            "record_count": total_records,
            "manifest_path": str(current_manifest_path),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
