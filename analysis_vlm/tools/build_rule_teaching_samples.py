#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_CLIP_MANIFEST = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/02_clip_observation/manifests/current_clip_observation_manifest.json")
DEFAULT_OUTPUT_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/03_rule_teaching")


def load_json(path: Path):
    return json.loads(path.read_text())


def slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug[:120] or "unknown_match"


def rule_template(course_target: str) -> dict:
    if course_target == "contact_restart_scan":
        return {
            "teaching_task": "restart_or_contact_rule_mapping",
            "target_rule_question": "这段画面里是否存在出界、底线、最后触球方或重接触导致的规则后果？",
            "expected_fact_focus": [
                "heavy_contact_foul",
                "injury_or_stoppage",
                "ball_out_of_play",
                "corner_candidate",
            ],
            "expected_rule_focus": [
                "last_touch_side",
                "exit_boundary",
                "restart_type",
                "discipline_outcome",
            ],
        }
    if course_target == "discipline_event_review":
        return {
            "teaching_task": "discipline_rule_mapping",
            "target_rule_question": "这段画面是否只构成普通犯规，还是可能对应黄牌、红牌或长时间治疗？",
            "expected_fact_focus": [
                "heavy_contact_foul",
                "injury_or_stoppage",
                "replay_sequence",
            ],
            "expected_rule_focus": [
                "discipline_outcome",
                "time_loss_candidate",
            ],
        }
    if course_target == "goal_or_restart_review":
        return {
            "teaching_task": "restart_rule_mapping",
            "target_rule_question": "这段画面更像角球、球门球、边线球，还是只是危险进攻候选？",
            "expected_fact_focus": [
                "ball_out_of_play",
                "corner_candidate",
                "dangerous_attack",
            ],
            "expected_rule_focus": [
                "last_touch_side",
                "exit_boundary",
                "restart_type",
            ],
        }
    return {
        "teaching_task": "pending_rule_mapping",
        "target_rule_question": "先描述这段画面里的视觉事实，再判断有没有需要继续映射的规则结果。",
        "expected_fact_focus": [],
        "expected_rule_focus": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build rule-teaching stubs from clip-observation records.")
    parser.add_argument("--clip-manifest", type=Path, default=DEFAULT_CLIP_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    payload = load_json(args.clip_manifest)
    output_root = args.output_root
    records_root = output_root / "records"
    manifests_root = output_root / "manifests"
    records_root.mkdir(parents=True, exist_ok=True)
    manifests_root.mkdir(parents=True, exist_ok=True)

    manifest_matches: list[dict] = []
    total_records = 0

    for match in payload.get("matches", []):
        teams = str(match.get("teams") or "")
        match_slug = slugify(teams or Path(str(match.get("source_video") or "")).stem)
        match_dir = records_root / match_slug
        match_dir.mkdir(parents=True, exist_ok=True)
        out_records: list[dict] = []
        for item in match.get("records", []):
            clip_record = load_json(Path(item["record_path"]))
            course_target = str(clip_record.get("course_target") or item.get("course_target") or "")
            template = rule_template(course_target)
            rule_record = {
                "clip_id": clip_record["clip_id"],
                "gtype": clip_record.get("gtype", "FT"),
                "teams": clip_record.get("teams", ""),
                "match_id": clip_record.get("match_id", ""),
                "source_clip_path": clip_record.get("clip_path", ""),
                "source_clip_record_path": str(item["record_path"]),
                "course_target": course_target,
                "bootstrap": clip_record.get("bootstrap", {}),
                "fact_observation_ref": clip_record.get("observation", {}),
                "rule_teaching": {
                    "teaching_task": template["teaching_task"],
                    "target_rule_question": template["target_rule_question"],
                    "expected_fact_focus": template["expected_fact_focus"],
                    "expected_rule_focus": template["expected_rule_focus"],
                    "visual_fact_summary": "",
                    "last_touch_side": "unknown",
                    "exit_boundary": "unknown",
                    "restart_type": "unknown",
                    "discipline_outcome": "unknown",
                    "time_loss_candidate": False,
                    "rule_rationale_short": "",
                },
                "annotation": {
                    "manual_review_status": "pending",
                    "needs_human_review": True,
                    "review_priority": clip_record.get("annotation", {}).get("review_priority", "medium"),
                    "fact_focus": clip_record.get("annotation", {}).get("fact_focus", []),
                    "rule_focus": clip_record.get("annotation", {}).get("rule_focus", []),
                    "teaching_tags": clip_record.get("annotation", {}).get("teaching_tags", []),
                    "review_notes": "",
                    "annotator": "",
                    "reviewed_at": "",
                },
            }
            out_path = match_dir / f"{clip_record['clip_id']}.json"
            out_path.write_text(json.dumps(rule_record, ensure_ascii=False, indent=2))
            out_records.append(
                {
                    "clip_id": clip_record["clip_id"],
                    "course_target": course_target,
                    "output_path": str(out_path),
                    "source_clip_record_path": str(item["record_path"]),
                }
            )
            total_records += 1

        match_manifest = {
            "teams": teams,
            "match_id": str(match.get("match_id") or ""),
            "gtype": str(match.get("gtype") or "FT"),
            "record_count": len(out_records),
            "records": out_records,
        }
        manifest_path = manifests_root / f"{match_slug}.json"
        manifest_path.write_text(json.dumps(match_manifest, ensure_ascii=False, indent=2))
        manifest_matches.append(match_manifest)

    current_manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_clip_manifest": str(args.clip_manifest),
        "match_count": len(manifest_matches),
        "record_count": total_records,
        "matches": manifest_matches,
    }
    current_manifest_path = manifests_root / "current_rule_teaching_manifest.json"
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
