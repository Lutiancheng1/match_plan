#!/usr/bin/env python3
"""
post_match.py - 比赛结束后视频整理工具
========================================
功能：
  1. 读取 manifest.json，展示比赛录制概况
  2. 合并所有分段（live + gap 黑帧）成一个完整视频
  3. 验证最终视频时长与 manifest 是否一致
  4. 生成帧索引（每N秒提取一帧，供后续 AI 分析用）
  5. 输出整理报告

使用方法：
  python3 post_match.py ~/Desktop/recordings/chelsea_milan_20260321/
  python3 post_match.py ~/Desktop/recordings/chelsea_milan_20260321/ --extract-frames
  python3 post_match.py ~/Desktop/recordings/chelsea_milan_20260321/ --merge-only
"""

import json
import os
import sys
import subprocess
import argparse
import shutil
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def seconds_to_hms(sec):
    sec = max(0, int(sec))
    return f"{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}"


def get_video_duration(video_path):
    """用 ffprobe 获取视频时长（秒）"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def get_video_shape(video_path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        payload = json.loads(result.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        return int(stream.get("width") or 0), int(stream.get("height") or 0)
    except Exception:
        return 0, 0


def run_ffmpeg(cmd, desc=""):
    """执行 ffmpeg 命令"""
    if desc:
        log(f"  {desc}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"  [警告] ffmpeg 返回 {result.returncode}: {result.stderr[-200:]}")
    return result.returncode == 0


def generate_analysis_copy(video_path, output_path=None, target_mbps=5.0):
    """
    生成供浏览/次级分析使用的 HEVC 压缩副本。
    默认不替代原始 full.mp4，只作为额外产物保留。
    """
    if not video_path or not os.path.exists(video_path):
        log(f"[错误] 原视频不存在，无法生成 analysis 副本: {video_path}")
        return None

    if output_path is None:
        root, ext = os.path.splitext(video_path)
        output_path = f"{root}__analysis_{int(round(target_mbps))}m{ext or '.mp4'}"

    bitrate_m = max(0.5, float(target_mbps))
    maxrate_m = max(bitrate_m + 1.0, bitrate_m * 1.2)
    bufsize_m = max(maxrate_m * 2.0, bitrate_m * 2.0)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", video_path,
        "-an",
        "-c:v", "hevc_videotoolbox",
        "-tag:v", "hvc1",
        "-b:v", f"{bitrate_m:.0f}M",
        "-maxrate", f"{maxrate_m:.0f}M",
        "-bufsize", f"{bufsize_m:.0f}M",
        output_path,
    ]
    success = run_ffmpeg(
        cmd,
        f"生成 analysis 副本 {os.path.basename(output_path)} ({bitrate_m:.0f}Mbps HEVC)",
    )
    if not success or not os.path.exists(output_path):
        return None
    return output_path


def collect_valid_segments(match_dir, manifest):
    """返回可用于合并的有效分段及缺失文件列表。"""
    segments = manifest.get("segments", [])
    missing = []
    valid_segments = []
    cursor = 0.0
    expected_total = float(manifest.get("total_duration_sec", 0) or 0)
    hard_end = expected_total if expected_total > 0 else None
    for seg in sorted(segments, key=lambda s: s["wall_start"]):
        fname = seg.get("file", "")
        if not fname:
            continue
        fpath = os.path.join(match_dir, fname)
        if not os.path.exists(fpath):
            missing.append(fname)
            continue
        if os.path.getsize(fpath) == 0:
            continue
        seg_start = float(seg.get("wall_start", 0) or 0)
        seg_end = float(seg.get("wall_end", seg_start) or seg_start)
        play_start = max(seg_start, cursor)
        play_end = seg_end if hard_end is None else min(seg_end, hard_end)
        play_duration = play_end - play_start
        if play_duration <= 0.05:
            continue
        trim_start = max(0.0, play_start - seg_start)
        valid_segments.append({
            "seg": seg,
            "path": fpath,
            "trim_start": trim_start,
            "trim_duration": play_duration,
        })
        cursor = max(cursor, play_end)
    return valid_segments, missing


def cleanup_redundant_single_segment(match_dir, manifest, merged_video):
    """
    如果只有一个完整有效分段且 full.mp4 已成功生成，
    删除与 full 内容重复的单个 seg 文件，返回清理结果字典。
    """
    if not merged_video or not os.path.exists(merged_video):
        return {"deleted": [], "saved_bytes": 0}

    valid_segments, _ = collect_valid_segments(match_dir, manifest)
    if len(valid_segments) != 1:
        return {"deleted": [], "saved_bytes": 0}

    item = valid_segments[0]
    if item["trim_start"] > 0.01:
        return {"deleted": [], "saved_bytes": 0}

    seg_path = item["path"]
    if not os.path.exists(seg_path):
        return {"deleted": [], "saved_bytes": 0}
    if os.path.abspath(seg_path) == os.path.abspath(merged_video):
        return {"deleted": [], "saved_bytes": 0}

    merged_dur = get_video_duration(merged_video)
    seg_dur = get_video_duration(seg_path)
    if merged_dur <= 0 or seg_dur <= 0:
        return {"deleted": [], "saved_bytes": 0}
    if abs(merged_dur - seg_dur) > 1.0:
        return {"deleted": [], "saved_bytes": 0}

    saved_bytes = os.path.getsize(seg_path)
    os.remove(seg_path)
    return {"deleted": [seg_path], "saved_bytes": saved_bytes}


# ─────────────────────────────────────────────
# 读取 Manifest
# ─────────────────────────────────────────────

def load_manifest(match_dir):
    manifest_path = os.path.join(match_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        log(f"[错误] 找不到 manifest.json: {manifest_path}")
        return None
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def print_manifest_summary(manifest):
    """打印录制概况"""
    log(f"\n{'─'*60}")
    log(f"  📋 录制概况")
    log(f"{'─'*60}")
    log(f"  比赛ID     : {manifest.get('match_id', 'unknown')}")
    log(f"  录制开始   : {manifest.get('recording_start', 'unknown')}")
    log(f"  录制状态   : {manifest.get('status', 'unknown')}")
    log(f"  总时长     : {seconds_to_hms(manifest.get('total_duration_sec', 0))}")
    log(f"  卡顿次数   : {manifest.get('freeze_count', 0)}")
    log(f"  断流次数   : {manifest.get('disconnect_count', 0)}")

    segments = manifest.get("segments", [])
    live_segs = [s for s in segments if s["type"] == "live"]
    gap_segs  = [s for s in segments if s["type"] != "live"]

    live_duration = sum(s.get("duration_sec", 0) for s in live_segs)
    gap_duration  = sum(s.get("duration_sec", 0) for s in gap_segs)

    log(f"\n  分段明细:")
    log(f"    有效录制 : {len(live_segs)} 段 | {seconds_to_hms(live_duration)}")
    log(f"    空白填充 : {len(gap_segs)} 段 | {seconds_to_hms(gap_duration)}")
    log(f"    总分段数 : {len(segments)}")

    if gap_segs:
        log(f"\n  空白段详情:")
        for seg in gap_segs:
            log(f"    [{seg['type']}] "
                f"{seconds_to_hms(seg['wall_start'])} → {seconds_to_hms(seg['wall_end'])} "
                f"| {seg.get('duration_sec', 0):.1f}秒"
                f"{' | ' + seg.get('reason','') if seg.get('reason') else ''}")
    log(f"{'─'*60}\n")


# ─────────────────────────────────────────────
# 合并视频
# ─────────────────────────────────────────────

def merge_segments(match_dir, manifest, output_path=None):
    """按 manifest 顺序合并所有分段为一个完整视频"""
    segments = manifest.get("segments", [])
    if not segments:
        log("[错误] manifest 中没有分段记录")
        return None

    if output_path is None:
        base_name = manifest.get("match_dir_name") or manifest.get("match_id", "match")
        output_path = os.path.join(match_dir, f"{base_name}__full.mp4")

    expected_total = float(manifest.get("total_duration_sec", 0) or 0)

    # 检查所有分段文件是否存在，并裁掉 manifest 中互相重叠的片段
    valid_segments, missing = collect_valid_segments(match_dir, manifest)
    present_files = {os.path.basename(item["path"]) for item in valid_segments}
    for seg in sorted(segments, key=lambda s: s["wall_start"]):
        fname = seg.get("file", "")
        if not fname:
            log(f"  [跳过] 空白段无文件名: seq={seg.get('seq')}")
            continue
        if fname in missing:
            log(f"  [警告] 文件不存在: {fname}")
            continue
        fpath = os.path.join(match_dir, fname)
        if os.path.exists(fpath) and os.path.getsize(fpath) == 0:
            log(f"  [警告] 文件为空: {fname}")
        elif os.path.exists(fpath) and fname not in present_files:
            log(f"  [跳过] 重叠/无效片段: {fname}")

    if missing:
        log(f"[警告] {len(missing)} 个文件缺失，这些时段将无视频")

    if not valid_segments:
        log("[错误] 没有可用的视频分段")
        return None

    direct_copy_only = False
    if len(valid_segments) == 1 and valid_segments[0]["trim_start"] <= 0.01:
        seg_duration = get_video_duration(valid_segments[0]["path"])
        if abs(seg_duration - valid_segments[0]["trim_duration"]) <= 0.5:
            # 只有一个完整分段且不需要裁剪，直接复制
            log(f"  只有1个分段，直接复制...")
            shutil.copy2(valid_segments[0]["path"], output_path)
            direct_copy_only = True

    if not direct_copy_only:
        target_w, target_h = get_video_shape(valid_segments[0]["path"])
        if target_w <= 0 or target_h <= 0:
            target_w, target_h = 1088, 680

        inputs = []
        filter_parts = []
        concat_labels = []
        for idx, item in enumerate(valid_segments):
            inputs += ["-i", item["path"]]
            trim_start = max(0.0, item["trim_start"])
            trim_duration = max(0.05, item["trim_duration"])
            label = f"v{idx}"
            filter_parts.append(
                f"[{idx}:v]"
                f"trim=start={trim_start:.3f}:duration={trim_duration:.3f},"
                f"fps=30,scale={target_w}:{target_h},setsar=1,setpts=PTS-STARTPTS"
                f"[{label}]"
            )
            concat_labels.append(f"[{label}]")

        filter_parts.append(f"{''.join(concat_labels)}concat=n={len(valid_segments)}:v=1:a=0[v]")

        log(f"  合并 {len(valid_segments)} 个片段切片...")
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            *inputs,
            "-filter_complex", ";".join(filter_parts),
            "-map", "[v]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-an",
            "-movflags", "+faststart",
            output_path
        ]
        success = run_ffmpeg(cmd, f"合并到 {os.path.basename(output_path)}")

        if not success:
            log("[错误] 合并失败")
            return None

    # 验证输出
    if os.path.exists(output_path):
        duration = get_video_duration(output_path)
        expected = expected_total
        diff = abs(duration - expected)
        size_mb = os.path.getsize(output_path) / 1024 / 1024

        log(f"\n  ✅ 合并完成: {os.path.basename(output_path)}")
        log(f"     文件大小 : {size_mb:.1f} MB")
        log(f"     实际时长 : {seconds_to_hms(duration)} ({duration:.1f}秒)")
        log(f"     预期时长 : {seconds_to_hms(expected)} ({expected:.1f}秒)")
        if diff > 2:
            log(f"     ⚠ 时长偏差 {diff:.1f}秒（超过2秒，请检查）")
        else:
            log(f"     时长偏差 : {diff:.1f}秒 ✓")
        return output_path
    else:
        log("[错误] 输出文件未生成")
        return None


# ─────────────────────────────────────────────
# 帧提取（供 AI 分析）
# ─────────────────────────────────────────────

def extract_frames(video_path, output_dir, interval_sec=5, max_frames=None):
    """
    每隔 N 秒提取一帧，用于后续 AI 战术分析。
    输出：frames/ 目录 + frame_index.jsonl（帧索引文件）
    """
    frames_dir = os.path.join(output_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    duration = get_video_duration(video_path)
    if duration <= 0:
        log("[错误] 无法获取视频时长")
        return None

    total_frames = int(duration / interval_sec)
    if max_frames and total_frames > max_frames:
        total_frames = max_frames
        log(f"  限制最多提取 {max_frames} 帧")

    log(f"  视频时长: {seconds_to_hms(duration)}")
    log(f"  提取间隔: {interval_sec}秒")
    log(f"  预计帧数: {total_frames}")

    frame_index = []
    extracted = 0

    for i in range(total_frames):
        time_sec = i * interval_sec
        frame_filename = f"frame_{i:06d}_{int(time_sec):06d}s.jpg"
        frame_path = os.path.join(frames_dir, frame_filename)

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{time_sec:.3f}",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "3",
            frame_path
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=15)

        if result.returncode == 0 and os.path.exists(frame_path):
            frame_index.append({
                "frame_index": i,
                "video_time_sec": time_sec,
                "video_time_hms": seconds_to_hms(time_sec),
                "file": frame_filename,
                "analysis": None,  # 待 AI 填充
            })
            extracted += 1
            if extracted % 50 == 0:
                log(f"  已提取 {extracted}/{total_frames} 帧...")
        else:
            log(f"  [跳过] 第{i}帧提取失败 (time={time_sec:.1f}s)")

    # 保存帧索引
    index_path = os.path.join(output_dir, "frame_index.jsonl")
    with open(index_path, "w", encoding="utf-8") as f:
        for entry in frame_index:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    log(f"\n  ✅ 帧提取完成")
    log(f"     提取帧数 : {extracted}")
    log(f"     帧目录   : {frames_dir}")
    log(f"     帧索引   : {index_path}")
    return index_path


# ─────────────────────────────────────────────
# 生成整理报告
# ─────────────────────────────────────────────

def generate_report(match_dir, manifest, merged_video=None, frame_index=None):
    report_path = os.path.join(match_dir, "post_match_report.txt")
    lines = [
        "=" * 60,
        "  比赛整理报告",
        "=" * 60,
        f"比赛ID   : {manifest.get('match_id')}",
        f"录制开始 : {manifest.get('recording_start')}",
        f"录制状态 : {manifest.get('status')}",
        f"总时长   : {seconds_to_hms(manifest.get('total_duration_sec', 0))}",
        f"卡顿次数 : {manifest.get('freeze_count', 0)}",
        f"断流次数 : {manifest.get('disconnect_count', 0)}",
        "",
    ]

    if merged_video:
        dur = get_video_duration(merged_video)
        size = os.path.getsize(merged_video) / 1024 / 1024
        lines += [
            f"合并视频 : {os.path.basename(merged_video)}",
            f"视频时长 : {seconds_to_hms(dur)}",
            f"文件大小 : {size:.1f} MB",
            "",
        ]

    if frame_index:
        count = sum(1 for _ in open(frame_index))
        lines += [
            f"帧索引   : {os.path.basename(frame_index)}",
            f"总帧数   : {count}",
            "",
        ]

    lines += [
        "分段明细:",
        "-" * 40,
    ]
    for seg in manifest.get("segments", []):
        marker = "▶" if seg["type"] == "live" else "░"
        lines.append(
            f"  {marker} [{seg['type']:12s}] "
            f"{seconds_to_hms(seg['wall_start'])} → {seconds_to_hms(seg['wall_end'])} "
            f"| {seg.get('duration_sec', 0):.1f}s"
            f"  {seg.get('file', '')}"
        )

    lines.append("=" * 60)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log(f"  报告已保存: {report_path}")
    return report_path


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="比赛结束后视频整理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 完整整理（合并视频 + 提取帧）
  python3 post_match.py ~/Desktop/recordings/chelsea_milan/

  # 只合并视频
  python3 post_match.py ~/Desktop/recordings/chelsea_milan/ --merge-only

  # 合并视频并提取帧（每5秒一帧）
  python3 post_match.py ~/Desktop/recordings/chelsea_milan/ --extract-frames --frame-interval 5

  # 只显示概况，不做任何操作
  python3 post_match.py ~/Desktop/recordings/chelsea_milan/ --summary-only
        """
    )
    parser.add_argument("match_dir",        help="比赛录制目录（包含 manifest.json）")
    parser.add_argument("--merge-only",     action="store_true", help="只合并视频，不提取帧")
    parser.add_argument("--extract-frames", action="store_true", help="合并视频后提取帧")
    parser.add_argument("--frame-interval", type=int, default=5,  help="帧提取间隔秒数（默认5）")
    parser.add_argument("--max-frames",     type=int, default=0,  help="最大帧数限制（0=不限）")
    parser.add_argument("--summary-only",   action="store_true", help="只显示概况")
    parser.add_argument("--output-video",   help="合并视频输出路径（可选）")
    args = parser.parse_args()

    match_dir = os.path.expanduser(args.match_dir)

    print(f"\n{'='*60}")
    print(f"  比赛整理工具 v1.0")
    print(f"{'='*60}")

    # 加载清单
    manifest = load_manifest(match_dir)
    if not manifest:
        sys.exit(1)

    # 显示概况
    print_manifest_summary(manifest)

    if args.summary_only:
        return

    merged_video = None
    frame_index_path = None

    # 合并视频
    log("[1/3] 合并视频分段...")
    merged_video = merge_segments(match_dir, manifest, args.output_video)

    # 提取帧（可选）
    if args.extract_frames and merged_video:
        log(f"\n[2/3] 提取帧（每{args.frame_interval}秒一帧）...")
        max_frames = args.max_frames if args.max_frames > 0 else None
        frame_index_path = extract_frames(
            merged_video, match_dir,
            interval_sec=args.frame_interval,
            max_frames=max_frames
        )
    else:
        log("\n[2/3] 跳过帧提取（使用 --extract-frames 启用）")

    # 生成报告
    log("\n[3/3] 生成整理报告...")
    generate_report(match_dir, manifest, merged_video, frame_index_path)

    print(f"\n{'='*60}")
    print(f"  ✅ 整理完成")
    if merged_video:
        print(f"  合并视频 : {merged_video}")
    if frame_index_path:
        print(f"  帧索引   : {frame_index_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
