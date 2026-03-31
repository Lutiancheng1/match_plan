#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_GOLD_MATCHES = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/01_gold_matches/current_gold_matches.json")
DEFAULT_OUTPUT_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/01_frame_observation")
DEFAULT_SCHEMA = Path("/Users/niannianshunjing/match_plan/analysis_vlm/schemas/live_frame_observation.schema.json")


def load_json(path: Path):
    return json.loads(path.read_text())


def slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug[:120] or "unknown_match"


def ensure_schema_exists(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing schema: {path}")
    json.loads(path.read_text())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build frame-observation teaching samples from football Gold matches.")
    parser.add_argument("--gold-matches", type=Path, default=DEFAULT_GOLD_MATCHES)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--gtype", default="FT")
    parser.add_argument("--sample-every-sec", type=float, default=15.0)
    parser.add_argument("--max-frames-per-match", type=int, default=80)
    parser.add_argument("--min-duration-sec", type=float, default=45.0)
    return parser.parse_args()


def build_times(duration_sec: float, sample_every_sec: float, max_frames: int) -> list[float]:
    if duration_sec <= 0:
        return []
    times: list[float] = []
    cursor = 0.0
    while cursor < duration_sec and len(times) < max_frames:
        times.append(round(cursor, 3))
        cursor += sample_every_sec
    if not times:
        times.append(0.0)
    return times


def ffmpeg_extract_frame(video_path: Path, output_path: Path, at_sec: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{at_sec:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            str(output_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    args = parse_args()
    ensure_schema_exists(args.schema)
    matches = load_json(args.gold_matches)
    output_root = args.output_root
    images_root = output_root / "images"
    records_root = output_root / "records"
    manifests_root = output_root / "manifests"
    images_root.mkdir(parents=True, exist_ok=True)
    records_root.mkdir(parents=True, exist_ok=True)
    manifests_root.mkdir(parents=True, exist_ok=True)

    manifest_matches: list[dict] = []
    total_records = 0

    for match in matches:
        if str(match.get("gtype", "")).upper() != str(args.gtype).upper():
            continue
        duration = float(match.get("duration_sec") or 0.0)
        if duration < args.min_duration_sec:
            continue

        teams = str(match.get("teams") or "")
        match_slug = slugify(teams)
        video_path = Path(match["video"])
        timeline_path = Path(match["timeline"]) if match.get("timeline") else None
        viewer_path = Path(match["viewer"]) if match.get("viewer") else None
        match_images_dir = images_root / match_slug
        match_records_dir = records_root / match_slug
        match_images_dir.mkdir(parents=True, exist_ok=True)
        match_records_dir.mkdir(parents=True, exist_ok=True)

        frame_times = build_times(duration, args.sample_every_sec, args.max_frames_per_match)
        records: list[dict] = []
        for idx, at_sec in enumerate(frame_times, start=1):
            frame_id = f"{match_slug}__frame_{idx:04d}"
            image_path = match_images_dir / f"{frame_id}.jpg"
            ffmpeg_extract_frame(video_path, image_path, at_sec)
            record = {
                "frame_id": frame_id,
                "gtype": str(match.get("gtype") or ""),
                "teams": teams,
                "match_id": str(match.get("match_id") or ""),
                "quality_tier": str(match.get("quality_tier") or "gold"),
                "source_video": str(video_path),
                "source_timeline": str(timeline_path or ""),
                "source_viewer": str(viewer_path or ""),
                "sample_at_sec": at_sec,
                "image_path": str(image_path),
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
                    "review_notes": "",
                    "annotator": "",
                    "reviewed_at": "",
                },
            }
            record_path = match_records_dir / f"{frame_id}.json"
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            records.append(
                {
                    "frame_id": frame_id,
                    "sample_at_sec": at_sec,
                    "image_path": str(image_path),
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
    current_manifest_path = manifests_root / "current_frame_observation_manifest.json"
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
