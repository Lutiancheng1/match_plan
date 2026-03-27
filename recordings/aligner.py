#!/usr/bin/env python3
"""
aligner.py - 视频与数据事件时间轴对齐工具
============================================
核心思路：
  1. 加载录制清单(manifest.json) + 数据事件(events.jsonl)
  2. 筛选"锚点事件"（进球、红牌、角球等突发事件）
  3. 对每个锚点：自动预览视频帧，用户确认或手动输入实际视频时间
  4. 计算分段线性校正量（处理漂移）
  5. 将校正量应用到所有事件，输出 aligned_events.jsonl

使用方法：
  python aligner.py events.jsonl manifest.json --video match.mp4 --delay 3.0

依赖：
  pip install ffmpeg-python  (仅用于帧提取，也可直接用系统ffmpeg)
"""

import json
import os
import subprocess
import argparse
import sys
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────
# 可用作锚点的事件类型（中英文均支持）
# 按"可靠性"排序：最显眼的事件放前面
# ─────────────────────────────────────────────
ANCHOR_EVENT_TYPES = [
    "goal", "goal_scored", "进球", "得分",
    "red_card", "红牌", "red card",
    "penalty", "点球", "penalty_kick",
    "kickoff", "kick_off", "开球", "开赛",
    "corner_kick", "corner", "角球",
    "yellow_card", "黄牌",
    "substitution", "sub", "换人",
    "free_kick", "任意球",
    "var", "VAR",
]


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def log(msg):
    print(msg, flush=True)


def seconds_to_hms(seconds):
    """秒数 → HH:MM:SS"""
    if seconds is None:
        return "N/A"
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def hms_to_seconds(hms_str):
    """HH:MM:SS 或 MM:SS 或 纯秒数 → 浮点秒数"""
    hms_str = hms_str.strip()
    parts = hms_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    else:
        return float(parts[0])


def parse_timestamp(ts_str):
    """解析多种格式的时间戳 → datetime"""
    ts_str = str(ts_str).strip()
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ]:
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间戳格式: {ts_str}")


def get_event_field(event, *keys):
    """从事件字典中尝试多个字段名，返回第一个非空值"""
    for key in keys:
        val = event.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


# ─────────────────────────────────────────────
# 数据加载
# ─────────────────────────────────────────────

def load_events(events_file):
    events = []
    with open(events_file, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                log(f"[警告] 第{line_num}行JSON解析失败: {e}")
    log(f"[+] 加载事件: {len(events)} 条")
    return events


def load_manifest(manifest_file):
    with open(manifest_file, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    log(f"[+] 加载录制清单: 录制开始={manifest.get('recording_start')} | "
        f"分段数={len(manifest.get('segments', []))}")
    return manifest


# ─────────────────────────────────────────────
# 锚点筛选
# ─────────────────────────────────────────────

def is_anchor_event(event):
    """判断事件是否可以作为锚点"""
    event_type = get_event_field(
        event, "event_type", "type", "event", "event_name", "kind"
    ).lower()
    for anchor_type in ANCHOR_EVENT_TYPES:
        if anchor_type.lower() in event_type:
            return True
    return False


def get_anchor_events(events):
    return [ev for ev in events if is_anchor_event(ev)]


# ─────────────────────────────────────────────
# 视频帧预览
# ─────────────────────────────────────────────

def extract_frame(video_file, time_sec, output_path):
    """用ffmpeg从视频提取单帧"""
    if not video_file or not os.path.exists(video_file):
        return False
    time_sec = max(0, time_sec)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{time_sec:.3f}",
        "-i", video_file,
        "-vframes", "1",
        "-q:v", "2",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True)
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


def open_preview(image_path):
    """在Mac上用预览打开图片"""
    try:
        subprocess.Popen(["open", "-a", "Preview", image_path])
    except Exception:
        pass


# ─────────────────────────────────────────────
# 校正计算
# ─────────────────────────────────────────────

def interpolate_correction(data_wall_time, anchor_corrections):
    """
    在锚点之间做分段线性插值，计算当前时刻的校正量。
    anchor_corrections: [(data_wall_time, correction_sec), ...]
    """
    if not anchor_corrections:
        return 0.0

    sorted_anchors = sorted(anchor_corrections, key=lambda x: x[0])

    # 早于所有锚点：用第一个锚点的校正量
    if data_wall_time <= sorted_anchors[0][0]:
        return sorted_anchors[0][1]

    # 晚于所有锚点：用最后一个锚点的校正量
    if data_wall_time >= sorted_anchors[-1][0]:
        return sorted_anchors[-1][1]

    # 在两个锚点之间：线性插值
    for i in range(len(sorted_anchors) - 1):
        t0, c0 = sorted_anchors[i]
        t1, c1 = sorted_anchors[i + 1]
        if t0 <= data_wall_time <= t1:
            ratio = (data_wall_time - t0) / (t1 - t0)
            return c0 + ratio * (c1 - c0)

    return sorted_anchors[-1][1]


# ─────────────────────────────────────────────
# 视频分段查找
# ─────────────────────────────────────────────

def find_video_position(manifest, corrected_wall_time):
    """
    根据校正后的wall_time，在manifest中找到对应的视频文件和偏移位置。
    返回: (segment_info, offset_in_segment_sec)
    """
    for seg in manifest.get("segments", []):
        wall_start = seg.get("wall_start", 0)
        wall_end = seg.get("wall_end", float("inf"))
        if wall_start <= corrected_wall_time < wall_end:
            offset = corrected_wall_time - wall_start
            return seg, offset
    return None, None


# ─────────────────────────────────────────────
# 交互式锚点核对
# ─────────────────────────────────────────────

def interactive_anchor_check(anchor_events, manifest, video_file, initial_delay_sec):
    """
    逐个锚点让用户核对，返回校正列表。
    anchor_corrections: [(data_wall_time, correction_sec), ...]
    """
    recording_start = parse_timestamp(manifest["recording_start"])
    anchor_corrections = []
    frame_dir = "/tmp/aligner_frames"
    os.makedirs(frame_dir, exist_ok=True)

    log(f"\n{'─'*60}")
    log(f"  开始锚点核对（共 {len(anchor_events)} 个锚点）")
    log(f"  初始估计延迟: {initial_delay_sec:.1f} 秒")
    log(f"  操作说明:")
    log(f"    直接回车   → 确认估计时间正确")
    log(f"    MM:SS      → 输入实际视频时间（如 45:23 或 1:23:45）")
    log(f"    s          → 跳过此锚点（不用于校正）")
    log(f"    q          → 结束锚点核对，使用已确认的锚点")
    log(f"{'─'*60}\n")

    for idx, ev in enumerate(anchor_events):
        ev_type = get_event_field(ev, "event_type", "type", "event", "kind")
        ts_str = get_event_field(ev, "timestamp", "time", "event_time", "ts")
        detail = get_event_field(ev, "detail", "description", "team", "player", "minute")

        if not ts_str:
            log(f"[跳过] 锚点 {idx+1}: 缺少时间戳字段")
            continue

        try:
            event_time = parse_timestamp(ts_str)
        except ValueError as e:
            log(f"[跳过] 锚点 {idx+1}: {e}")
            continue

        # 计算相对于录制开始的秒数
        data_wall_time = (event_time - recording_start).total_seconds()

        # 用当前已知校正量估计视频时间
        current_correction = interpolate_correction(data_wall_time, anchor_corrections) if anchor_corrections else -initial_delay_sec
        estimated_video_time = data_wall_time + current_correction

        log(f"┌─ 锚点 {idx+1}/{len(anchor_events)}: [{ev_type}] {detail}")
        log(f"│  数据时间戳   : {ts_str}")
        log(f"│  数据Wall时间 : {data_wall_time:.1f}秒")
        log(f"│  估计视频时间 : {seconds_to_hms(estimated_video_time)}  ({estimated_video_time:.1f}秒)")

        # 提取并预览帧
        if video_file and os.path.exists(video_file):
            frame_path = os.path.join(frame_dir, f"anchor_{idx+1:03d}.jpg")
            if extract_frame(video_file, estimated_video_time, frame_path):
                log(f"│  [帧已提取，正在打开预览...]")
                open_preview(frame_path)
            else:
                log(f"│  [帧提取失败，请手动定位]")
        else:
            log(f"│  [未提供视频文件，请手动在播放器中定位]")

        log(f"└─ 请输入实际视频时间 > ", )

        try:
            user_input = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            log("\n[中断] 结束锚点核对")
            break

        if user_input.lower() == "q":
            log("[结束] 用户退出锚点核对")
            break
        elif user_input.lower() == "s":
            log(f"  → 跳过\n")
            continue
        elif user_input == "":
            actual_video_time = estimated_video_time
            log(f"  → 确认: {seconds_to_hms(actual_video_time)}")
        else:
            try:
                actual_video_time = hms_to_seconds(user_input)
                log(f"  → 手动输入: {seconds_to_hms(actual_video_time)}")
            except ValueError:
                log(f"  → 格式错误，跳过")
                continue

        correction = actual_video_time - data_wall_time
        anchor_corrections.append((data_wall_time, correction))
        log(f"  → 校正量: {correction:+.2f} 秒\n")

    return anchor_corrections


# ─────────────────────────────────────────────
# 输出对齐结果
# ─────────────────────────────────────────────

def align_and_save(events, manifest, anchor_corrections, output_file):
    """将所有事件应用校正，输出到JSONL"""
    recording_start = parse_timestamp(manifest["recording_start"])
    aligned = []
    skipped = 0
    gap_count = 0

    for ev in events:
        ts_str = get_event_field(ev, "timestamp", "time", "event_time", "ts")
        if not ts_str:
            skipped += 1
            continue

        try:
            event_time = parse_timestamp(ts_str)
        except ValueError:
            skipped += 1
            continue

        data_wall_time = (event_time - recording_start).total_seconds()
        correction = interpolate_correction(data_wall_time, anchor_corrections)
        corrected_wall_time = data_wall_time + correction

        segment, video_offset = find_video_position(manifest, corrected_wall_time)

        aligned_info = {
            "data_wall_time_sec": round(data_wall_time, 3),
            "correction_sec": round(correction, 3),
            "corrected_wall_time_sec": round(corrected_wall_time, 3),
        }

        if segment:
            seg_type = segment.get("type", "live")
            is_gap = seg_type != "live"
            aligned_info.update({
                "video_file": segment.get("file", ""),
                "video_time_sec": round(video_offset, 3),
                "video_time_hms": seconds_to_hms(video_offset),
                "segment_type": seg_type,
                "is_gap": is_gap,
            })
            if is_gap:
                gap_count += 1
        else:
            aligned_info.update({
                "video_file": None,
                "video_time_sec": None,
                "video_time_hms": None,
                "segment_type": "out_of_range",
                "is_gap": True,
            })
            gap_count += 1

        output_event = dict(ev)
        output_event["_aligned"] = aligned_info
        aligned.append(output_event)

    with open(output_file, "w", encoding="utf-8") as f:
        for ev in aligned:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    return len(aligned), skipped, gap_count


# ─────────────────────────────────────────────
# 生成对齐报告
# ─────────────────────────────────────────────

def generate_report(manifest, anchor_corrections, total, skipped, gap_count, output_file):
    report_file = output_file.replace(".jsonl", "_report.txt")
    lines = [
        "=" * 60,
        "  对齐报告",
        "=" * 60,
        f"录制开始时间 : {manifest.get('recording_start')}",
        f"视频分段数   : {len(manifest.get('segments', []))}",
        "",
        f"使用锚点数   : {len(anchor_corrections)}",
    ]
    for i, (t, c) in enumerate(sorted(anchor_corrections)):
        lines.append(f"  锚点{i+1}: Wall={seconds_to_hms(t)} 校正={c:+.2f}秒")
    lines += [
        "",
        f"总事件数     : {total + skipped}",
        f"成功对齐     : {total}",
        f"跳过(无时间戳): {skipped}",
        f"落在空白段   : {gap_count}",
        f"输出文件     : {output_file}",
        "=" * 60,
    ]
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"[+] 报告保存至: {report_file}")
    return report_file


# ─────────────────────────────────────────────
# 测试数据生成（方便没有真实数据时测试）
# ─────────────────────────────────────────────

def generate_test_data(output_dir):
    """生成测试用的假数据，用于验证脚本逻辑"""
    os.makedirs(output_dir, exist_ok=True)

    # 假manifest
    manifest = {
        "recording_start": "2026-03-21T10:00:00.000",
        "match_id": "test_match",
        "segments": [
            {"seq": 1, "type": "live",       "wall_start": 0,      "wall_end": 1800.0, "file": "seg_001.mp4"},
            {"seq": 2, "type": "freeze",     "wall_start": 1800.0, "wall_end": 1808.5, "file": "gap_001.mp4"},
            {"seq": 3, "type": "live",       "wall_start": 1808.5, "wall_end": 3600.0, "file": "seg_002.mp4"},
        ]
    }
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 假事件（加3秒延迟模拟数据源延迟）
    import random
    base_time = datetime(2026, 3, 21, 10, 0, 0)
    events = [
        {"event_type": "kickoff",      "timestamp": "2026-03-21T10:00:03.0", "team": "home"},
        {"event_type": "corner_kick",  "timestamp": "2026-03-21T10:08:45.2", "team": "away"},
        {"event_type": "yellow_card",  "timestamp": "2026-03-21T10:23:12.8", "player": "Smith"},
        {"event_type": "goal",         "timestamp": "2026-03-21T10:35:07.4", "team": "home", "score": "1-0"},
        {"event_type": "corner_kick",  "timestamp": "2026-03-21T10:42:33.1", "team": "home"},
        {"event_type": "kickoff",      "timestamp": "2026-03-21T10:49:03.0", "team": "away", "note": "second_half"},
        {"event_type": "red_card",     "timestamp": "2026-03-21T11:02:18.5", "player": "Jones"},
        {"event_type": "goal",         "timestamp": "2026-03-21T11:15:44.9", "team": "away", "score": "1-1"},
        {"event_type": "substitution", "timestamp": "2026-03-21T11:28:02.3", "player_out": "Brown", "player_in": "Davis"},
        {"event_type": "full_time",    "timestamp": "2026-03-21T11:32:03.0"},
        # 几个无时间戳的事件（测试跳过逻辑）
        {"event_type": "pass",         "detail": "no timestamp"},
    ]
    events_path = os.path.join(output_dir, "events.jsonl")
    with open(events_path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    log(f"[+] 测试数据已生成:")
    log(f"    清单文件: {manifest_path}")
    log(f"    事件文件: {events_path}")
    log(f"    说明: 所有数据时间戳比真实事件晚3秒（模拟固定延迟）")
    return manifest_path, events_path


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="视频-数据对齐工具 — 用突发事件作锚点校正时间轴",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 正常使用
  python aligner.py events.jsonl manifest.json --video match.mp4 --delay 3.0

  # 只有数据文件，没有视频（手动输入时间）
  python aligner.py events.jsonl manifest.json --delay 5.0

  # 生成测试数据并运行
  python aligner.py --test
        """
    )
    parser.add_argument("events_file",    nargs="?", help="事件JSONL文件路径")
    parser.add_argument("manifest_file",  nargs="?", help="录制清单JSON文件路径")
    parser.add_argument("--video",        help="视频文件路径（用于自动预览锚点帧）")
    parser.add_argument("--delay",        type=float, default=3.0,
                        help="初始估计延迟秒数：数据比真实事件晚多少秒（默认3秒）")
    parser.add_argument("--output",       help="输出文件路径（默认在events_file同目录）")
    parser.add_argument("--auto",         action="store_true",
                        help="全自动模式：不交互，直接用初始延迟作固定校正")
    parser.add_argument("--test",         action="store_true",
                        help="生成测试数据并运行对齐（无需真实数据）")
    args = parser.parse_args()

    log("\n" + "=" * 60)
    log("  视频-数据对齐工具 v1.0")
    log("=" * 60)

    # 测试模式
    if args.test:
        test_dir = "/tmp/aligner_test"
        manifest_file, events_file = generate_test_data(test_dir)
        output_file = os.path.join(test_dir, "aligned_events.jsonl")
        video_file = None
        delay = 3.0
        auto_mode = True  # 测试模式不交互
    else:
        if not args.events_file or not args.manifest_file:
            parser.print_help()
            sys.exit(1)
        events_file = args.events_file
        manifest_file = args.manifest_file
        video_file = args.video
        delay = args.delay
        auto_mode = args.auto
        if args.output:
            output_file = args.output
        else:
            base = os.path.splitext(events_file)[0]
            output_file = base + "_aligned.jsonl"

    # 加载数据
    events = load_events(events_file)
    manifest = load_manifest(manifest_file)

    # 筛选锚点
    anchor_events = get_anchor_events(events)
    log(f"[+] 发现锚点事件: {len(anchor_events)} 个")
    for ev in anchor_events:
        ev_type = get_event_field(ev, "event_type", "type", "event")
        ts = get_event_field(ev, "timestamp", "time", "event_time")
        detail = get_event_field(ev, "detail", "description", "team", "player")
        log(f"    [{ev_type}] {ts} {detail}")

    # 校正
    if auto_mode or not anchor_events:
        log(f"\n[自动模式] 使用固定延迟 {delay:.1f} 秒")
        anchor_corrections = [(0.0, -delay)]
    else:
        anchor_corrections = interactive_anchor_check(
            anchor_events, manifest, video_file, delay
        )
        if not anchor_corrections:
            log(f"[提示] 没有确认任何锚点，使用初始延迟 {delay:.1f} 秒")
            anchor_corrections = [(0.0, -delay)]

    # 对齐并保存
    log(f"\n[+] 应用校正，输出至: {output_file}")
    total, skipped, gap_count = align_and_save(events, manifest, anchor_corrections, output_file)

    # 报告
    report_file = generate_report(
        manifest, anchor_corrections, total, skipped, gap_count, output_file
    )

    log(f"\n{'='*60}")
    log(f"  ✅ 对齐完成")
    log(f"  成功对齐 : {total} 个事件")
    log(f"  跳过     : {skipped} 个（无时间戳）")
    log(f"  落在空白段: {gap_count} 个（已标记 is_gap=true）")
    log(f"  锚点数   : {len(anchor_corrections)} 个")
    log(f"  输出文件 : {output_file}")
    log(f"{'='*60}\n")


if __name__ == "__main__":
    main()
