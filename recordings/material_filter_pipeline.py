#!/usr/bin/env python3
"""Build and refresh the long-term qualified materials library."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_RECORDINGS_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_recordings")
DEFAULT_DATASET_ROOT = Path("/Volumes/990 PRO PCIe 4T/match_plan_dataset_library")
DEFAULT_DOC_OUTPUT = Path(
    "/Users/niannianshunjing/match_plan/docs/plans/"
    "2026-03-26-material-filtering-and-dataset-storage-standard.md"
)

DATASET_DIRS = [
    "00_docs",
    "01_gold_matches",
    "02_silver_review_queue",
    "03_rejected_materials",
    "04_golden_samples/clips",
    "04_golden_samples/labels",
    "04_golden_samples/meta",
    "05_eval_sets",
    "06_training_pool",
    "07_benchmarks",
    "08_model_outputs",
    "09_reviews",
    "10_manifests",
]


@dataclass
class MaterialRow:
    session: str
    session_dir: str
    teams: str
    match_id: str
    status: str
    binding: str
    matched_rows: int
    duration_sec: float
    video: str
    timeline: str
    viewer: str
    analysis: str
    timeline_rows: int
    timeline_last_elapsed: float | None
    coverage_ratio: float
    recording_note: str
    quality_tier: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "session_dir": self.session_dir,
            "teams": self.teams,
            "match_id": self.match_id,
            "status": self.status,
            "binding": self.binding,
            "matched_rows": self.matched_rows,
            "duration_sec": self.duration_sec,
            "video": self.video,
            "timeline": self.timeline,
            "viewer": self.viewer,
            "analysis": self.analysis,
            "timeline_rows": self.timeline_rows,
            "timeline_last_elapsed": self.timeline_last_elapsed,
            "coverage_ratio": self.coverage_ratio,
            "recording_note": self.recording_note,
            "quality_tier": self.quality_tier,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh qualified-material manifests and dataset-library structure."
    )
    parser.add_argument(
        "--recordings-root",
        default=str(DEFAULT_RECORDINGS_ROOT),
        help="Root of raw recording sessions.",
    )
    parser.add_argument(
        "--dataset-root",
        default=str(DEFAULT_DATASET_ROOT),
        help="Root of long-term dataset library.",
    )
    parser.add_argument(
        "--doc-output",
        default=str(DEFAULT_DOC_OUTPUT),
        help="Output markdown doc for filtering rules.",
    )
    parser.add_argument(
        "--gold-threshold",
        type=float,
        default=0.95,
        help="Minimum timeline coverage ratio for Gold.",
    )
    parser.add_argument(
        "--silver-threshold",
        type=float,
        default=0.60,
        help="Minimum timeline coverage ratio for Silver Review.",
    )
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


def read_timeline_stats(timeline_path: Path) -> tuple[int, float | None]:
    if not timeline_path.exists():
        return 0, None
    with timeline_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return 0, None
    last = rows[-1].get("elapsed_sec")
    return len(rows), float(last) if last else None


def classify_tier(
    *,
    status: str,
    binding: str,
    matched_rows: int,
    timeline: str,
    viewer: str,
    coverage_ratio: float,
    gold_threshold: float,
    silver_threshold: float,
) -> str:
    base_ok = status == "completed" and binding == "bound" and matched_rows > 0
    has_sync_files = bool(timeline) and bool(viewer)
    if base_ok and has_sync_files and coverage_ratio >= gold_threshold:
        return "gold"
    if base_ok and has_sync_files and coverage_ratio >= silver_threshold:
        return "silver_review"
    return "reject"


def build_material_row(
    rec: dict[str, Any],
    *,
    session_dir: Path,
    gold_threshold: float,
    silver_threshold: float,
) -> MaterialRow | None:
    merged_video = rec.get("merged_video")
    if not merged_video:
        return None
    video_path = Path(merged_video)
    if not video_path.exists():
        return None

    duration_sec = round(ffprobe_duration(video_path), 3)
    stem = video_path.stem.replace("__full", "")
    timeline_path = video_path.parent / f"{stem}__timeline.csv"
    viewer_path = video_path.parent / f"{stem}__sync_viewer.html"
    analysis_path = video_path.parent / f"{stem}__analysis_side_by_side.mp4"
    timeline_rows, timeline_last_elapsed = read_timeline_stats(timeline_path)
    coverage_ratio = 0.0
    if timeline_last_elapsed is not None and duration_sec > 0:
        coverage_ratio = round(timeline_last_elapsed / duration_sec, 6)

    quality_tier = classify_tier(
        status=str(rec.get("status") or ""),
        binding=str(rec.get("data_binding_status") or ""),
        matched_rows=int(rec.get("matched_rows") or 0),
        timeline=str(timeline_path) if timeline_path.exists() else "",
        viewer=str(viewer_path) if viewer_path.exists() else "",
        coverage_ratio=coverage_ratio,
        gold_threshold=gold_threshold,
        silver_threshold=silver_threshold,
    )

    return MaterialRow(
        session=session_dir.name,
        session_dir=str(session_dir),
        teams=str(rec.get("teams") or rec.get("match_id") or "unknown"),
        match_id=str(rec.get("match_id") or ""),
        status=str(rec.get("status") or ""),
        binding=str(rec.get("data_binding_status") or ""),
        matched_rows=int(rec.get("matched_rows") or 0),
        duration_sec=duration_sec,
        video=str(video_path),
        timeline=str(timeline_path) if timeline_path.exists() else "",
        viewer=str(viewer_path) if viewer_path.exists() else "",
        analysis=str(analysis_path) if analysis_path.exists() else "",
        timeline_rows=timeline_rows,
        timeline_last_elapsed=timeline_last_elapsed,
        coverage_ratio=coverage_ratio,
        recording_note=str(rec.get("recording_note") or ""),
        quality_tier=quality_tier,
    )


def scan_materials(
    recordings_root: Path, *, gold_threshold: float, silver_threshold: float
) -> list[MaterialRow]:
    rows: list[MaterialRow] = []
    for session_result in recordings_root.glob("2026-03-*/session_*/session_result.json"):
        try:
            payload = json.loads(session_result.read_text())
        except Exception:
            continue
        for rec in payload.get("streams", []):
            if not isinstance(rec, dict):
                continue
            row = build_material_row(
                rec,
                session_dir=session_result.parent,
                gold_threshold=gold_threshold,
                silver_threshold=silver_threshold,
            )
            if row:
                rows.append(row)
    return rows


def choose_best_by_match(rows: list[MaterialRow]) -> list[MaterialRow]:
    by_teams: dict[str, MaterialRow] = {}
    for row in rows:
        prev = by_teams.get(row.teams)
        score = (
            row.quality_tier == "gold",
            row.quality_tier == "silver_review",
            row.matched_rows,
            row.coverage_ratio,
            row.duration_sec,
        )
        if prev is None:
            by_teams[row.teams] = row
            continue
        prev_score = (
            prev.quality_tier == "gold",
            prev.quality_tier == "silver_review",
            prev.matched_rows,
            prev.coverage_ratio,
            prev.duration_sec,
        )
        if score > prev_score:
            by_teams[row.teams] = row
    return sorted(
        by_teams.values(),
        key=lambda item: (
            {"gold": 2, "silver_review": 1, "reject": 0}[item.quality_tier],
            item.matched_rows,
            item.coverage_ratio,
            item.duration_sec,
        ),
        reverse=True,
    )


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug[:120] or "unknown_match"


def ensure_dataset_dirs(dataset_root: Path) -> None:
    for rel in DATASET_DIRS:
        (dataset_root / rel).mkdir(parents=True, exist_ok=True)


def reset_item_dirs(base_dir: Path) -> None:
    for child in base_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def write_root_readme(dataset_root: Path) -> None:
    text = """# Match Plan Dataset Library

这个目录用于长期沉淀“已绑定数据的可训练素材”，不是普通录制临时目录。

目录约定：
- `00_docs`：数据标准、筛选规则、使用说明
- `01_gold_matches`：严格合格素材的长期清单与后续精修入口
- `02_silver_review_queue`：基本可用但时间覆盖不足，需要人工复核
- `03_rejected_materials`：明确不进入样本/评测/训练链的素材
- `04_golden_samples`：从 gold 比赛切出来的 clip/label/meta
- `05_eval_sets`：固定评测集
- `06_training_pool`：训练候选池
- `07_benchmarks`：模型 benchmark 结果
- `08_model_outputs`：模型推理输出归档
- `09_reviews`：人工复核结果
- `10_manifests`：机器生成的素材清单与索引
"""
    (dataset_root / "README.md").write_text(text)


def render_doc(
    *,
    rows: list[MaterialRow],
    selected: list[MaterialRow],
    recordings_root: Path,
    dataset_root: Path,
    gold_threshold: float,
    silver_threshold: float,
) -> str:
    gold = [row for row in selected if row.quality_tier == "gold"]
    silver = [row for row in selected if row.quality_tier == "silver_review"]
    reject_count = len([row for row in rows if row.quality_tier == "reject"])
    updated = datetime.now().strftime("%Y-%m-%d")
    lines: list[str] = []
    lines.append("# 素材过滤与长期存储标准\n\n")
    lines.append(f"> 更新时间：{updated}  \n")
    lines.append(f"> 录制素材根目录：`{recordings_root}`  \n")
    lines.append(f"> 长期素材库根目录：`{dataset_root}`\n\n")
    lines.append("## 1. 目标\n\n")
    lines.append("这份文档用于定义：\n\n")
    lines.append("- 什么素材是**真正合格**的，可反复打磨和长期留存\n")
    lines.append("- 什么素材只能作为待复核\n")
    lines.append("- 什么素材必须淘汰，不能进入样本、评测和训练链\n\n")
    lines.append("当前结论：**不是所有 `bound` 素材都合格**，还必须通过时间覆盖率校验。\n\n")
    lines.append("## 2. 严格过滤规则\n\n")
    lines.append("一条素材只有同时满足下面条件，才算合格：\n\n")
    lines.append("1. `status = completed`\n")
    lines.append("2. `data_binding_status = bound`\n")
    lines.append("3. `matched_rows > 0`\n")
    lines.append("4. `full.mp4` 存在\n")
    lines.append("5. `__timeline.csv` 存在\n")
    lines.append("6. `__sync_viewer.html` 存在\n")
    lines.append(f"7. **时间覆盖率 >= {gold_threshold:.2f}**\n\n")
    lines.append("时间覆盖率定义：\n\n")
    lines.append("`timeline_last_elapsed / video_duration_sec`\n\n")
    lines.append("含义：\n\n")
    lines.append("- 视频结束前，数据时间线必须基本跟到视频末尾\n")
    lines.append("- 如果后面视频还很长，但 timeline 只覆盖前面一小段，这条素材不能进入黄金样本\n\n")
    lines.append("## 3. 三档分类\n\n")
    lines.append("### Gold（可直接长期留存）\n\n")
    lines.append(f"满足全部严格规则，尤其是时间覆盖率 `>= {gold_threshold:.2f}`。\n\n")
    lines.append("用途：\n\n")
    lines.append("- 黄金样本\n- 固定评测集\n- 训练候选池\n- 反复打磨使用\n\n")
    lines.append("### Silver Review（待人工复核）\n\n")
    lines.append("满足：\n\n")
    lines.append("- `completed + bound + matched_rows > 0`\n")
    lines.append("- 有 timeline 和 sync viewer\n")
    lines.append(f"- 但时间覆盖率在 `{silver_threshold:.2f} ~ {gold_threshold:.2f}` 之间\n\n")
    lines.append("用途：\n\n")
    lines.append("- 作为复核候选\n- 人工确认是否只截取前段可用窗口\n- 不能直接进入黄金样本\n\n")
    lines.append("### Reject（淘汰）\n\n")
    lines.append("包含：\n\n")
    lines.append("- unbound/test-only\n- 没有 timeline\n- 没有 sync viewer\n")
    lines.append(f"- 时间覆盖率 < {silver_threshold:.2f}\n")
    lines.append("- 视频后半段没有数据推进\n\n")
    lines.append("这些素材：\n\n")
    lines.append("- 不进入样本库\n- 不进入评测集\n- 不进入训练池\n\n")
    lines.append("## 4. 当前盘点结果\n\n")
    lines.append(f"- 扫描录制流总数：`{len(rows)}`\n")
    lines.append(f"- Gold：`{len(gold)}`\n")
    lines.append(f"- Silver Review：`{len(silver)}`\n")
    lines.append(f"- Reject：`{reject_count}`\n\n")
    lines.append("### 当前 Gold 素材\n\n")
    if gold:
        for row in gold:
            video_name = Path(row.video).name
            lines.append(
                f"- **{row.teams}**：覆盖率 `{row.coverage_ratio:.3f}`，"
                f"匹配数据 `{row.matched_rows}`，视频 "
                f"[{video_name}]({row.video})\n"
            )
    else:
        lines.append("- 暂无\n")
    lines.append("\n### 当前 Silver Review 候选（最佳代表）\n\n")
    if silver:
        for row in silver[:10]:
            lines.append(
                f"- **{row.teams}**：覆盖率 `{row.coverage_ratio:.3f}`，"
                f"匹配数据 `{row.matched_rows}`，需人工判断是否截取局部窗口使用\n"
            )
    else:
        lines.append("- 暂无\n")
    lines.append("\n## 5. 长期素材库目录\n\n")
    lines.append(f"长期素材库根目录：`{dataset_root}`\n\n")
    for rel in DATASET_DIRS:
        lines.append(f"- `{rel}`\n")
    lines.append("\n目录用途：\n\n")
    lines.append("- `01_gold_matches`：记录严格合格比赛，不直接塞杂项\n")
    lines.append("- `02_silver_review_queue`：待人工复核的素材\n")
    lines.append("- `03_rejected_materials`：明确淘汰的素材索引\n")
    lines.append("- `04_golden_samples`：后续真正切出来的 clip/label/meta\n")
    lines.append("- `05_eval_sets`：固定评测集\n")
    lines.append("- `06_training_pool`：训练候选池\n")
    lines.append("- `10_manifests`：自动生成的全量清单和最佳素材索引\n\n")
    lines.append("## 6. 自动化脚本\n\n")
    lines.append(
        "后续不再建议手工整理。请直接使用脚本："
        "`/Users/niannianshunjing/match_plan/recordings/material_filter_pipeline.py`\n\n"
    )
    lines.append("示例：\n\n")
    lines.append("```bash\n")
    lines.append(
        "python3 /Users/niannianshunjing/match_plan/recordings/material_filter_pipeline.py\n"
    )
    lines.append("```\n\n")
    lines.append("脚本会自动：\n\n")
    lines.append("- 扫描录制目录\n- 刷新 Gold/Silver/Reject\n- 更新 manifests\n")
    lines.append("- 刷新长期素材库入口目录\n- 重写这份过滤标准文档\n\n")
    lines.append("## 7. 立即执行建议\n\n")
    lines.append("- 现在不要直接训练。\n")
    lines.append("- 先只从 **Gold** 素材里切第一批 clip。\n")
    lines.append("- `Silver Review` 先人工复核，再决定是否局部截取使用。\n")
    lines.append("- `Reject` 统一淘汰，不再混入后续流程。\n")
    return "".join(lines)


def write_tier_dirs(dataset_root: Path, selected: list[MaterialRow]) -> None:
    gold_dir = dataset_root / "01_gold_matches"
    silver_dir = dataset_root / "02_silver_review_queue"
    reject_dir = dataset_root / "03_rejected_materials"

    reset_item_dirs(gold_dir)
    reset_item_dirs(silver_dir)
    reset_item_dirs(reject_dir)

    for base_dir, tier in [
        (gold_dir, "gold"),
        (silver_dir, "silver_review"),
        (reject_dir, "reject"),
    ]:
        for row in [item for item in selected if item.quality_tier == tier]:
            target = base_dir / slugify(row.teams)
            target.mkdir(parents=True, exist_ok=True)
            (target / "source_session_dir.txt").write_text(f"{row.session_dir}\n")
            (target / "source_video.txt").write_text(f"{row.video}\n")
            if row.timeline:
                (target / "source_timeline.txt").write_text(f"{row.timeline}\n")
            if row.viewer:
                (target / "source_sync_viewer.txt").write_text(f"{row.viewer}\n")
            write_json(target / "material_manifest.json", row.as_dict())
            lines = [
                f"# {row.teams}",
                "",
                f"- 质量层级：`{row.quality_tier}`",
                f"- 覆盖率：`{row.coverage_ratio}`",
                f"- 匹配数据行数：`{row.matched_rows}`",
                f"- 原始 session：`{row.session_dir}`",
                f"- 视频：`{row.video}`",
            ]
            if row.timeline:
                lines.append(f"- 时间线：`{row.timeline}`")
            if row.viewer:
                lines.append(f"- 同步页：`{row.viewer}`")
            (target / "README.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    args = parse_args()
    recordings_root = Path(args.recordings_root)
    dataset_root = Path(args.dataset_root)
    doc_output = Path(args.doc_output)

    ensure_dataset_dirs(dataset_root)
    rows = scan_materials(
        recordings_root,
        gold_threshold=args.gold_threshold,
        silver_threshold=args.silver_threshold,
    )
    selected = choose_best_by_match(rows)

    write_root_readme(dataset_root)
    write_json(
        dataset_root / "10_manifests/current_material_scan.json",
        {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "materials": [row.as_dict() for row in rows],
        },
    )
    write_json(
        dataset_root / "10_manifests/current_best_by_match.json",
        {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "materials": [row.as_dict() for row in selected],
        },
    )

    gold = [row.as_dict() for row in selected if row.quality_tier == "gold"]
    silver = [row.as_dict() for row in selected if row.quality_tier == "silver_review"]
    reject = [row.as_dict() for row in selected if row.quality_tier == "reject"]
    write_json(dataset_root / "01_gold_matches/current_gold_matches.json", gold)
    write_json(dataset_root / "02_silver_review_queue/current_silver_review_queue.json", silver)
    write_json(dataset_root / "03_rejected_materials/current_rejected_matches.json", reject)

    (dataset_root / "01_gold_matches/README.md").write_text(
        "当前仅存放严格合格素材的索引与后续精修入口。\n"
    )
    (dataset_root / "02_silver_review_queue/README.md").write_text(
        "这里用于放置需要人工复核的边界素材。\n"
    )
    (dataset_root / "03_rejected_materials/README.md").write_text(
        "这里记录明确淘汰的无效素材，不进入样本/评测/训练链。\n"
    )
    write_tier_dirs(dataset_root, selected)

    doc_text = render_doc(
        rows=rows,
        selected=selected,
        recordings_root=recordings_root,
        dataset_root=dataset_root,
        gold_threshold=args.gold_threshold,
        silver_threshold=args.silver_threshold,
    )
    doc_output.write_text(doc_text)
    shutil.copy2(doc_output, dataset_root / "00_docs" / doc_output.name)

    print(doc_output)
    print(dataset_root)
    print(f"rows={len(rows)} selected={len(selected)}")
    print(
        "gold="
        f"{len(gold)} silver={len(silver)} reject={len(reject)}"
    )


if __name__ == "__main__":
    main()
