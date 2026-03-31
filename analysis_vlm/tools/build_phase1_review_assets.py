#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps


DEFAULT_BATCH = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/05_reviews/current_phase1_high_priority_batch.json")
DEFAULT_OUTPUT_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/05_reviews/high_priority_assets")


def load_json(path: Path):
    return json.loads(path.read_text())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build preview assets for Phase 1 football review clips.")
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser.parse_args()


def ffprobe_duration(video_path: Path) -> float:
    output = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        text=True,
    ).strip()
    return float(output)


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


def build_contact_sheet(frame_paths: list[Path], output_path: Path) -> None:
    images = [Image.open(path).convert("RGB") for path in frame_paths]
    resized = [ImageOps.contain(img, (480, 270)) for img in images]
    canvas = Image.new("RGB", (960, 540), color=(12, 12, 12))
    positions = [(0, 0), (480, 0), (0, 270), (480, 270)]
    for img, pos in zip(resized, positions):
        x = pos[0] + (480 - img.width) // 2
        y = pos[1] + (270 - img.height) // 2
        canvas.paste(img, (x, y))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=92)


def build_checklist(record: dict) -> list[str]:
    checklist = [
        "先确认这段 clip 是 live 还是 replay。",
        "先写视觉事实，再写规则映射，不写交易判断。",
    ]
    fact_focus = set(record.get("fact_focus") or [])
    rule_focus = set(record.get("rule_focus") or [])
    if "heavy_contact_foul" in fact_focus:
        checklist.append("检查是否存在明显重接触，还是普通身体对抗。")
    if "injury_or_stoppage" in fact_focus:
        checklist.append("检查是否存在倒地、停表、治疗或比赛暂停候选。")
    if "ball_out_of_play" in fact_focus:
        checklist.append("检查球是否已经出界，还是仍在 live play。")
    if "corner_candidate" in fact_focus:
        checklist.append("如果接近底线，先判断是否值得继续做角球/球门球映射。")
    if "last_touch_side" in rule_focus:
        checklist.append("若球出界，尽量判断最后触球方；看不清就保留 unknown。")
    if "exit_boundary" in rule_focus:
        checklist.append("若球出界，判断更接近边线还是底线。")
    if "restart_type" in rule_focus:
        checklist.append("优先在角球 / 球门球 / 边线球之间做保守判断。")
    if "discipline_outcome" in rule_focus:
        checklist.append("接触动作是否值得标记为纪律后果候选。")
    return checklist


def main() -> int:
    args = parse_args()
    payload = load_json(args.batch)
    assets_root = args.output_root
    records_root = assets_root / "records"
    manifests_root = assets_root / "manifests"
    records_root.mkdir(parents=True, exist_ok=True)
    manifests_root.mkdir(parents=True, exist_ok=True)

    out_records: list[dict] = []
    for item in payload.get("records", []):
        clip_id = str(item.get("clip_id") or "")
        clip_path = Path(item["clip_path"])
        clip_dir = records_root / clip_id
        frames_dir = clip_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        duration = max(ffprobe_duration(clip_path), 0.1)
        timestamps = [round(duration * ratio, 3) for ratio in (0.15, 0.35, 0.6, 0.85)]
        frame_paths: list[Path] = []
        for idx, ts in enumerate(timestamps, start=1):
            frame_path = frames_dir / f"{clip_id}__frame_{idx:02d}.jpg"
            ffmpeg_extract_frame(clip_path, frame_path, ts)
            frame_paths.append(frame_path)

        contact_sheet_path = clip_dir / f"{clip_id}__contact_sheet.jpg"
        build_contact_sheet(frame_paths, contact_sheet_path)

        review_packet = {
            "clip_id": clip_id,
            "teams": item.get("teams", ""),
            "match_id": item.get("match_id", ""),
            "source_group": item.get("source_group", ""),
            "course_target": item.get("course_target", ""),
            "review_priority": item.get("review_priority", ""),
            "clip_path": str(clip_path),
            "clip_record_path": item.get("clip_record_path", ""),
            "rule_record_path": item.get("rule_record_path", ""),
            "contact_sheet_path": str(contact_sheet_path),
            "frame_paths": [str(path) for path in frame_paths],
            "checklist": build_checklist(item),
            "bootstrap": item.get("bootstrap", {}),
            "fact_focus": item.get("fact_focus", []),
            "rule_focus": item.get("rule_focus", []),
            "teaching_tags": item.get("teaching_tags", []),
        }
        packet_path = clip_dir / f"{clip_id}__review_packet.json"
        packet_path.write_text(json.dumps(review_packet, ensure_ascii=False, indent=2))
        out_records.append(
            {
                "clip_id": clip_id,
                "teams": item.get("teams", ""),
                "review_priority": item.get("review_priority", ""),
                "course_target": item.get("course_target", ""),
                "contact_sheet_path": str(contact_sheet_path),
                "review_packet_path": str(packet_path),
            }
        )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_batch": str(args.batch),
        "record_count": len(out_records),
        "records": out_records,
    }
    current_manifest = manifests_root / "current_phase1_review_assets_manifest.json"
    current_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({"record_count": len(out_records), "manifest_path": str(current_manifest)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
