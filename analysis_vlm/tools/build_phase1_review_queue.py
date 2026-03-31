#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_CLIP_MANIFESTS = [
    Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/02_clip_observation/manifests/current_clip_observation_manifest.json"),
    Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/tests/new_match_clip_observation/manifests/current_clip_observation_manifest.json"),
]
DEFAULT_RULE_MANIFESTS = [
    Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/03_rule_teaching/manifests/current_rule_teaching_manifest.json"),
    Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/tests/new_match_rule_teaching/manifests/current_rule_teaching_manifest.json"),
]
DEFAULT_OUTPUT = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library/06_training_pool/05_reviews/current_phase1_priority_review_queue.json")


def load_json(path: Path):
    return json.loads(path.read_text())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a unified Phase 1 football review queue.")
    parser.add_argument("--clip-manifest", action="append", dest="clip_manifests", default=[])
    parser.add_argument("--rule-manifest", action="append", dest="rule_manifests", default=[])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def priority_rank(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(str(value or "").lower(), 0)


def build_rule_index(rule_manifest_paths: list[Path]) -> dict[str, str]:
    index: dict[str, str] = {}
    for path in rule_manifest_paths:
        if not path.exists():
            continue
        payload = load_json(path)
        for match in payload.get("matches", []):
            for item in match.get("records", []):
                clip_id = str(item.get("clip_id") or "")
                output_path = str(item.get("output_path") or "")
                if clip_id and output_path:
                    index[clip_id] = output_path
    return index


def main() -> int:
    args = parse_args()
    clip_manifest_paths = [Path(p) for p in args.clip_manifests] or DEFAULT_CLIP_MANIFESTS
    rule_manifest_paths = [Path(p) for p in args.rule_manifests] or DEFAULT_RULE_MANIFESTS
    rule_index = build_rule_index(rule_manifest_paths)

    records: list[dict] = []
    for clip_manifest_path in clip_manifest_paths:
        if not clip_manifest_path.exists():
            continue
        payload = load_json(clip_manifest_path)
        source_group = "main_pool"
        if "/tests/" in str(clip_manifest_path):
            source_group = "observation_test"
        for match in payload.get("matches", []):
            for item in match.get("records", []):
                clip_record = load_json(Path(item["record_path"]))
                annotation = clip_record.get("annotation", {})
                records.append(
                    {
                        "source_group": source_group,
                        "teams": clip_record.get("teams", ""),
                        "match_id": clip_record.get("match_id", ""),
                        "clip_id": clip_record.get("clip_id", ""),
                        "course_target": clip_record.get("course_target", ""),
                        "review_priority": annotation.get("review_priority", "medium"),
                        "kind": clip_record.get("kind", ""),
                        "reason": clip_record.get("reason", ""),
                        "clip_path": clip_record.get("clip_path", ""),
                        "clip_record_path": item["record_path"],
                        "rule_record_path": rule_index.get(clip_record.get("clip_id", ""), ""),
                        "fact_focus": annotation.get("fact_focus", []),
                        "rule_focus": annotation.get("rule_focus", []),
                        "teaching_tags": annotation.get("teaching_tags", []),
                        "bootstrap": clip_record.get("bootstrap", {}),
                    }
                )

    ordered = sorted(
        records,
        key=lambda item: (
            -priority_rank(item["review_priority"]),
            item["course_target"] != "contact_restart_scan",
            item["teams"],
            item["clip_id"],
        ),
    )
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "clip_manifests": [str(p) for p in clip_manifest_paths if p.exists()],
        "rule_manifests": [str(p) for p in rule_manifest_paths if p.exists()],
        "record_count": len(ordered),
        "records": ordered,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({"record_count": len(ordered), "output_path": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
