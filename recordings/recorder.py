#!/usr/bin/env python3
"""
recorder.py - 足球比赛屏幕录制器（含卡顿检测 + 断流处理）
============================================================
功能：
  1. 用 ffmpeg avfoundation 捕获指定屏幕
  2. 每隔 N 分钟自动分段保存（避免单个文件过大）
  3. 每2秒检查录制是否在进行（文件大小是否增长）
  4. 卡顿超过阈值 → 插入黑帧填补空白 → 重启录制
  5. 断流 → 记录空白段 → 等待恢复 → 继续录制
  6. 全程写入 manifest.json（视频文件 + 时间轴 + 空白段）

使用方法：
  # 基本录制（主屏幕，自动分段30分钟）
  python3 recorder.py --match-id chelsea_vs_milan_20260321 --screen 0

  # 指定输出目录
  python3 recorder.py --match-id test_match --screen 0 --output-dir ~/Desktop/recordings

  # 自定义分段时长和分辨率
  python3 recorder.py --match-id test_match --screen 0 --segment-minutes 20 --width 1920 --height 1080

依赖：
  brew install ffmpeg（已安装）
  pip3 install psutil --break-system-packages

首次运行前，需要给 Terminal 授权屏幕录制权限：
  系统设置 → 隐私与安全性 → 屏幕录制 → 勾选 Terminal
"""

import json
import os
import shutil
import subprocess
import sys
import time
import threading
import signal
import argparse
import re
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────
# 配置常量
# ─────────────────────────────────────────────

DEFAULT_SCREEN = 0               # avfoundation 屏幕编号
DEFAULT_FPS = 30                 # 录制帧率
DEFAULT_WIDTH = 1920             # 录制宽度（0 = 全屏原始分辨率）
DEFAULT_HEIGHT = 1080            # 录制高度（0 = 全屏原始分辨率）
DEFAULT_SEGMENT_MINUTES = 30     # 每个分段的时长（分钟）
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Desktop/recordings")

FREEZE_CHECK_INTERVAL = 2.0      # 每隔几秒检查一次文件大小
FREEZE_THRESHOLD_SECONDS = 8.0   # 文件大小超过几秒不增长 → 判定为卡顿
CONCURRENT_FREEZE_THRESHOLD = 20.0  # 并发模式下容忍度更高（多路编码更慢）
RECONNECT_WAIT_SECONDS = 5.0     # 检测到断流后等待几秒再重试
MAX_RECONNECT_ATTEMPTS = 10      # 最多重试次数

# ffmpeg 编码参数（单路和多路共用）
FFMPEG_ENCODE_ARGS = [
    "-vcodec", "libx264",
    "-preset", "medium",
    "-crf", "25",
    "-pix_fmt", "yuv420p",
    "-an",
    "-movflags", "+faststart",
]

_SCREEN_DEVICE_CACHE = {}
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WINDOW_CAPTURE_SOURCE = os.path.join(SCRIPT_DIR, "window_capture.swift")
WINDOW_CAPTURE_BUILD_DIR = os.path.join(SCRIPT_DIR, ".build")
WINDOW_CAPTURE_BIN = os.path.join(WINDOW_CAPTURE_BUILD_DIR, "window_capture_helper")


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] [{level}] {msg}", flush=True)


def now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def now_ts():
    return datetime.now().timestamp()


def seconds_to_hms(sec):
    sec = max(0, int(sec))
    return f"{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}"


def get_file_size(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def terminate_process(process, timeout=10):
    """安全终止子进程"""
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def wait_for_files(paths, max_wait=10.0, interval=0.5):
    """等待文件创建并有内容写入，返回 True 如果全部就绪"""
    if isinstance(paths, str):
        paths = [paths]
    iterations = int(max_wait / interval)
    for _ in range(iterations):
        if all(os.path.exists(p) and get_file_size(p) > 0 for p in paths):
            return True
        time.sleep(interval)
    return False


def build_window_capture_helper(force=False):
    if not os.path.exists(WINDOW_CAPTURE_SOURCE):
        return None
    if shutil.which("swiftc") is None:
        return None
    os.makedirs(WINDOW_CAPTURE_BUILD_DIR, exist_ok=True)
    if (
        not force
        and os.path.exists(WINDOW_CAPTURE_BIN)
        and os.path.getmtime(WINDOW_CAPTURE_BIN) >= os.path.getmtime(WINDOW_CAPTURE_SOURCE)
    ):
        return WINDOW_CAPTURE_BIN

    cmd = [
        "swiftc",
        "-parse-as-library",
        WINDOW_CAPTURE_SOURCE,
        "-O",
        "-o",
        WINDOW_CAPTURE_BIN,
        "-framework",
        "Foundation",
        "-framework",
        "ScreenCaptureKit",
        "-framework",
        "AVFoundation",
        "-framework",
        "CoreMedia",
        "-framework",
        "AppKit",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log(f"编译窗口录制辅助器失败: {(result.stderr or result.stdout).strip()[:400]}", "WARN")
        return None
    return WINDOW_CAPTURE_BIN


def resolve_screen_input_device(screen_idx):
    """Map logical screen index (0,1,...) to avfoundation device index."""
    if screen_idx in _SCREEN_DEVICE_CACHE:
        return _SCREEN_DEVICE_CACHE[screen_idx]

    try:
        result = subprocess.run(
            ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
    except Exception:
        _SCREEN_DEVICE_CACHE[screen_idx] = screen_idx
        return screen_idx

    pattern = re.compile(r"\[(\d+)\]\s+Capture screen\s+(\d+)", re.IGNORECASE)
    for match in pattern.finditer(output):
        device_idx = int(match.group(1))
        logical_idx = int(match.group(2))
        _SCREEN_DEVICE_CACHE[logical_idx] = device_idx

    return _SCREEN_DEVICE_CACHE.get(screen_idx, screen_idx)


def make_output_dir(base_dir, match_id):
    output_dir = os.path.join(base_dir, match_id)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def sanitize_path_component(text):
    text = (text or "").strip()
    text = re.sub(r"[\\/:\*\?\"<>\|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._ ")
    return text or "match"


def compact_time_label(dt=None):
    dt = dt or datetime.now()
    return dt.strftime("%Y%m%d_%H%M%S")


def wall_time_label(sec):
    sec = max(0, int(sec))
    return f"{sec//3600:02d}{(sec%3600)//60:02d}{sec%60:02d}"


# ─────────────────────────────────────────────
# 黑帧生成（填补卡顿/断流空白）
# ─────────────────────────────────────────────

def generate_black_frames(duration_sec, output_path, width=1920, height=1080, fps=30):
    """
    生成指定时长的黑帧视频，用于填补卡顿/断流空白。
    这保证了视频时间轴与真实时间轴 1:1 对应。
    """
    if duration_sec < 0.1:
        return True

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"color=c=black:s={width}x{height}:r={fps}",
        "-t", f"{duration_sec:.3f}",
        "-vcodec", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-an",  # 无音频
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0 and os.path.exists(output_path)
    except Exception as e:
        log(f"生成黑帧失败: {e}", "ERROR")
        return False


# ─────────────────────────────────────────────
# Manifest 管理
# ─────────────────────────────────────────────

class Manifest:
    """管理录制时间轴清单文件"""

    def __init__(self, output_dir, match_id, recording_start):
        self.path = os.path.join(output_dir, "manifest.json")
        match_dir_name = os.path.basename(output_dir)
        self.data = {
            "match_id": match_id,
            "match_dir_name": match_dir_name,
            "recording_start": recording_start,
            "recording_start_ts": datetime.strptime(
                recording_start, "%Y-%m-%dT%H:%M:%S.%f"
            ).timestamp(),
            "segments": [],
            "total_duration_sec": 0.0,
            "freeze_count": 0,
            "disconnect_count": 0,
            "status": "recording",
        }
        self._lock = threading.Lock()
        self._save()

    def add_segment(self, seg_type, wall_start, wall_end, filename, reason=""):
        """
        添加一个分段记录。
        seg_type: 'live' | 'freeze' | 'disconnect' | 'segment_end'
        wall_start/wall_end: 相对于录制开始的秒数
        """
        with self._lock:
            seg = {
                "seq": len(self.data["segments"]) + 1,
                "type": seg_type,
                "wall_start": round(wall_start, 3),
                "wall_end": round(wall_end, 3),
                "duration_sec": round(wall_end - wall_start, 3),
                "file": filename,
            }
            if reason:
                seg["reason"] = reason
            self.data["segments"].append(seg)

            # 更新统计
            self.data["total_duration_sec"] = round(wall_end, 3)
            if seg_type == "freeze":
                self.data["freeze_count"] += 1
            elif seg_type == "disconnect":
                self.data["disconnect_count"] += 1

            self._save()
        return seg

    def set_status(self, status):
        with self._lock:
            self.data["status"] = status
            self.data["recording_end"] = now_iso()
            self._save()

    def _save(self):
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)  # 原子写入


# ─────────────────────────────────────────────
# 冻帧检测器
# ─────────────────────────────────────────────

class FreezeDetector:
    """监控录制文件大小，检测卡顿"""

    def __init__(self, check_interval=FREEZE_CHECK_INTERVAL, threshold=FREEZE_THRESHOLD_SECONDS):
        self.check_interval = check_interval
        self.threshold = threshold
        self._last_size = 0
        self._last_growth_time = now_ts()
        self._current_file = None
        self._lock = threading.Lock()

    def update_file(self, filepath):
        with self._lock:
            self._current_file = filepath
            self._last_size = get_file_size(filepath)
            self._last_growth_time = now_ts()

    def check(self):
        """返回 (is_frozen, frozen_seconds)"""
        with self._lock:
            if not self._current_file:
                return False, 0

            current_size = get_file_size(self._current_file)
            now = now_ts()

            if current_size > self._last_size:
                self._last_size = current_size
                self._last_growth_time = now
                return False, 0

            frozen_seconds = now - self._last_growth_time
            if frozen_seconds >= self.threshold:
                return True, frozen_seconds

            return False, 0

    def reset(self, filepath):
        with self._lock:
            self._current_file = filepath
            self._last_size = get_file_size(filepath)
            self._last_growth_time = now_ts()


# ─────────────────────────────────────────────
# ffmpeg 录制进程封装
# ─────────────────────────────────────────────

class FFmpegRecorder:
    """管理单个 ffmpeg 录制进程"""

    def __init__(self, screen_idx, output_path, fps=30, width=0, height=0,
                 crop_region=None):
        self.screen_idx = screen_idx
        self.output_path = output_path
        self.fps = fps
        self.width = width
        self.height = height
        self.crop_region = crop_region  # (x, y, w, h) 或 None
        self._process = None
        self._start_time = None

    def _build_command(self):
        """构建 ffmpeg 命令"""
        # 视频输入：avfoundation 屏幕捕获
        input_device = f"{resolve_screen_input_device(self.screen_idx)}"

        cmd = [
            "ffmpeg", "-y",
            "-loglevel", "error",
            "-f", "avfoundation",
            "-framerate", str(self.fps),
            "-capture_cursor", "0",
            "-i", input_device,
        ]

        # 视频滤镜链：裁剪 → 缩放
        vf_filters = []
        if self.crop_region:
            cx, cy, cw, ch = self.crop_region
            vf_filters.append(f"crop={cw}:{ch}:{cx}:{cy}")
        if self.width > 0 and self.height > 0:
            vf_filters.append(f"scale={self.width}:{self.height}")
        if vf_filters:
            cmd += ["-vf", ",".join(vf_filters)]

        cmd += FFMPEG_ENCODE_ARGS + [self.output_path]
        return cmd

    def start(self):
        cmd = self._build_command()
        log(f"启动 ffmpeg: {' '.join(cmd[:8])}...")
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._start_time = now_ts()
        wait_for_files(self.output_path, max_wait=10.0)
        return self._process

    def stop(self):
        terminate_process(self._process)
        self._process = None

    def is_running(self):
        return self._process is not None and self._process.poll() is None

    def get_elapsed(self):
        if self._start_time:
            return now_ts() - self._start_time
        return 0


class WindowCaptureProcess:
    """用 ScreenCaptureKit 录制单个指定窗口。"""

    def __init__(self, window_id, output_path, fps=30, width=0, height=0, content_crop=None,
                 stderr_handle=None):
        self.window_id = int(window_id)
        self.output_path = output_path
        self.fps = fps
        self.width = width
        self.height = height
        self.content_crop = content_crop or {}
        self.stderr_handle = stderr_handle
        self._process = None
        self._start_time = None

    def _build_command(self):
        helper = build_window_capture_helper()
        if not helper:
            return None
        cmd = [
            helper,
            "--window-id", str(self.window_id),
            "--output", self.output_path,
            "--fps", str(self.fps),
        ]
        if self.width > 0:
            cmd += ["--width", str(self.width)]
        if self.height > 0:
            cmd += ["--height", str(self.height)]
        crop = self.content_crop or {}
        if crop:
            cmd += [
                "--crop-left", str(crop.get("left", 0)),
                "--crop-top", str(crop.get("top", 0)),
                "--crop-width", str(crop.get("width", 0)),
                "--crop-height", str(crop.get("height", 0)),
            ]
        return cmd

    def start(self):
        cmd = self._build_command()
        if not cmd:
            return None
        log(f"启动窗口录制: window={self.window_id} -> {os.path.basename(self.output_path)}")
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=self.stderr_handle or subprocess.DEVNULL,
        )
        self._start_time = now_ts()
        wait_for_files(self.output_path, max_wait=10.0)
        return self._process

    def stop(self):
        terminate_process(self._process)
        self._process = None

    def is_running(self):
        return self._process is not None and self._process.poll() is None

    def get_elapsed(self):
        if self._start_time:
            return now_ts() - self._start_time
        return 0


# ─────────────────────────────────────────────
# 主录制控制器
# ─────────────────────────────────────────────

class RecordingController:
    """
    主控制器：管理分段录制 + 卡顿检测 + 黑帧插入 + Manifest 写入
    """

    def __init__(
        self,
        match_id,
        output_dir,
        screen_idx=0,
        fps=DEFAULT_FPS,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        segment_minutes=DEFAULT_SEGMENT_MINUTES,
        crop_region=None,
    ):
        self.match_id = match_id
        self.output_dir = make_output_dir(output_dir, match_id)
        self.screen_idx = screen_idx
        self.fps = fps
        self.width = width
        self.height = height
        self.crop_region = crop_region  # (x, y, w, h) 或 None
        self.segment_duration = segment_minutes * 60

        self.recording_start_iso = now_iso()
        self.recording_start_ts = now_ts()
        self.recording_label = compact_time_label(
            datetime.strptime(self.recording_start_iso, "%Y-%m-%dT%H:%M:%S.%f")
        )
        self.file_prefix = sanitize_path_component(match_id)

        self.manifest = Manifest(self.output_dir, match_id, self.recording_start_iso)
        self.freeze_detector = FreezeDetector()

        self._stop_event = threading.Event()
        self._segment_idx = 0
        self._gap_idx = 0
        self._current_recorder = None
        self._current_seg_start_wall = 0.0

        log(f"录制控制器初始化完成")
        log(f"  比赛ID     : {match_id}")
        log(f"  输出目录   : {self.output_dir}")
        log(f"  屏幕编号   : {screen_idx}")
        log(f"  分辨率     : {width}x{height} @ {fps}fps")
        if crop_region:
            cx, cy, cw, ch = crop_region
            log(f"  裁剪区域   : {cw}x{ch} @ ({cx},{cy})")
        log(f"  分段时长   : {segment_minutes} 分钟")
        log(f"  清单文件   : {self.manifest.path}")

    def _wall_time(self):
        """当前相对于录制开始的秒数"""
        return now_ts() - self.recording_start_ts

    def _next_segment_path(self, prefix="seg"):
        self._segment_idx += 1
        start_label = wall_time_label(self._wall_time())
        return os.path.join(
            self.output_dir,
            f"{self.file_prefix}__{prefix}_{self._segment_idx:03d}__t{start_label}.mp4",
        )

    def _next_gap_path(self):
        self._gap_idx += 1
        start_label = wall_time_label(self._wall_time())
        return os.path.join(
            self.output_dir,
            f"{self.file_prefix}__gap_{self._gap_idx:03d}__t{start_label}.mp4",
        )

    def _start_new_segment(self):
        """启动一个新的录制分段"""
        seg_path = self._next_segment_path()
        self._current_seg_start_wall = self._wall_time()

        recorder = FFmpegRecorder(
            self.screen_idx, seg_path,
            fps=self.fps, width=self.width, height=self.height,
            crop_region=self.crop_region
        )
        recorder.start()

        if not recorder.is_running():
            log("ffmpeg 启动失败！请检查屏幕录制权限。", "ERROR")
            log("前往：系统设置 → 隐私与安全性 → 屏幕录制 → 勾选 Terminal", "ERROR")
            return None, None

        self.freeze_detector.reset(seg_path)
        log(f"▶ 开始录制分段 {self._segment_idx}: {os.path.basename(seg_path)}")
        log(f"  Wall时间: {seconds_to_hms(self._current_seg_start_wall)}")
        return recorder, seg_path

    def _handle_freeze(self, frozen_seconds):
        """处理卡顿：停止当前录制 → 生成黑帧 → 重启录制"""
        wall_freeze_start = self._wall_time() - frozen_seconds
        wall_freeze_end = self._wall_time()

        log(f"⚠ 检测到卡顿！已冻结 {frozen_seconds:.1f} 秒", "WARN")
        log(f"  卡顿时间: {seconds_to_hms(wall_freeze_start)} → {seconds_to_hms(wall_freeze_end)}")

        # 停止当前录制，记录有效段
        if self._current_recorder and self._current_recorder.is_running():
            self._current_recorder.stop()

        seg_file = os.path.basename(
            os.path.join(self.output_dir, f"seg_{self._segment_idx:04d}.mp4")
        )
        self.manifest.add_segment(
            "live",
            self._current_seg_start_wall,
            wall_freeze_start,
            seg_file,
        )

        # 生成黑帧填补空白
        gap_duration = wall_freeze_end - wall_freeze_start
        gap_path = self._next_gap_path()
        log(f"  生成黑帧: {gap_duration:.1f}秒 → {os.path.basename(gap_path)}")

        success = generate_black_frames(
            gap_duration, gap_path,
            width=self.width, height=self.height, fps=self.fps
        )
        if success:
            self.manifest.add_segment(
                "freeze", wall_freeze_start, wall_freeze_end,
                os.path.basename(gap_path), reason="video_freeze"
            )
        else:
            log("黑帧生成失败，跳过", "WARN")
            self.manifest.add_segment(
                "freeze", wall_freeze_start, wall_freeze_end,
                "", reason="video_freeze_gap_failed"
            )

        # 重启录制
        log("  重新启动录制...")
        return self._start_new_segment()

    def _handle_disconnect(self, attempt):
        """处理完全断流"""
        wall_disconnect = self._wall_time()
        log(f"✗ 录制进程意外退出（尝试重连 {attempt}/{MAX_RECONNECT_ATTEMPTS}）", "WARN")

        # 记录断流段
        seg_file = os.path.basename(
            os.path.join(self.output_dir, f"seg_{self._segment_idx:04d}.mp4")
        )
        self.manifest.add_segment(
            "live",
            self._current_seg_start_wall,
            wall_disconnect,
            seg_file,
        )

        # 等待重连
        log(f"  等待 {RECONNECT_WAIT_SECONDS} 秒后重试...")
        time.sleep(RECONNECT_WAIT_SECONDS)

        # 记录空白段（等待期间）
        wall_reconnect = self._wall_time()
        gap_duration = wall_reconnect - wall_disconnect
        gap_path = self._next_gap_path()

        log(f"  生成断流黑帧: {gap_duration:.1f}秒")
        generate_black_frames(
            gap_duration, gap_path,
            width=self.width, height=self.height, fps=self.fps
        )
        self.manifest.add_segment(
            "disconnect", wall_disconnect, wall_reconnect,
            os.path.basename(gap_path), reason="process_exited"
        )

        return self._start_new_segment()

    def stop(self):
        """公开的停止接口"""
        self._stop_event.set()

    def register_signals(self):
        """注册停止信号（必须在主线程调用）"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def start(self):
        """开始录制主循环"""
        log(f"\n{'='*60}")
        log(f"  开始录制比赛: {self.match_id}")
        log(f"  录制开始时间: {self.recording_start_iso}")
        log(f"  按 Ctrl+C 停止录制")
        log(f"{'='*60}\n")

        # 启动第一个分段
        self._current_recorder, current_seg_path = self._start_new_segment()
        if not self._current_recorder:
            return False

        reconnect_attempts = 0

        try:
            while not self._stop_event.is_set():
                time.sleep(FREEZE_CHECK_INTERVAL)

                if self._stop_event.is_set():
                    break

                # ── 检查录制进程是否还活着 ──
                if not self._current_recorder.is_running():
                    reconnect_attempts += 1
                    if reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
                        log(f"✗ 超过最大重连次数 ({MAX_RECONNECT_ATTEMPTS})，停止录制", "ERROR")
                        break
                    self._current_recorder, current_seg_path = self._handle_disconnect(reconnect_attempts)
                    if not self._current_recorder:
                        break
                    continue

                reconnect_attempts = 0  # 重置重连计数

                # ── 检查卡顿 ──
                is_frozen, frozen_secs = self.freeze_detector.check()
                if is_frozen:
                    self._current_recorder, current_seg_path = self._handle_freeze(frozen_secs)
                    if not self._current_recorder:
                        break
                    continue

                # ── 检查是否需要分段 ──
                elapsed_in_segment = self._current_recorder.get_elapsed()
                if elapsed_in_segment >= self.segment_duration:
                    wall_seg_end = self._wall_time()
                    log(f"⏱ 到达分段时长，开始新分段...")

                    # 保存当前分段
                    self._current_recorder.stop()
                    seg_file = os.path.basename(current_seg_path)
                    self.manifest.add_segment(
                        "live",
                        self._current_seg_start_wall,
                        wall_seg_end,
                        seg_file,
                        reason="segment_end"
                    )

                    # 启动新分段
                    self._current_recorder, current_seg_path = self._start_new_segment()
                    if not self._current_recorder:
                        break
                    continue

                # ── 状态日志（每30秒输出一次）──
                wall_now = self._wall_time()
                if int(wall_now) % 30 == 0 and int(wall_now) > 0:
                    size_mb = get_file_size(current_seg_path) / 1024 / 1024
                    log(f"● 录制中 | 总时长: {seconds_to_hms(wall_now)} | "
                        f"当前分段: {size_mb:.1f}MB | "
                        f"分段: {self._segment_idx} | "
                        f"卡顿: {self.manifest.data['freeze_count']}次")

        except Exception as e:
            log(f"录制异常: {e}", "ERROR")
            import traceback
            traceback.print_exc()

        finally:
            self._stop_recording(current_seg_path)

        return True

    def _stop_recording(self, current_seg_path=None):
        """停止录制并保存最终状态"""
        log(f"\n{'─'*60}")
        log(f"停止录制...")

        wall_end = self._wall_time()

        if self._current_recorder and self._current_recorder.is_running():
            self._current_recorder.stop()

        # 记录最后一个分段
        if current_seg_path and os.path.exists(current_seg_path):
            seg_file = os.path.basename(current_seg_path)
            self.manifest.add_segment(
                "live",
                self._current_seg_start_wall,
                wall_end,
                seg_file,
                reason="recording_stopped"
            )

        self.manifest.set_status("completed")

        log(f"\n{'='*60}")
        log(f"  ✅ 录制完成: {self.match_id}")
        log(f"  总时长   : {seconds_to_hms(wall_end)}")
        log(f"  分段数   : {self._segment_idx}")
        log(f"  卡顿次数 : {self.manifest.data['freeze_count']}")
        log(f"  断流次数 : {self.manifest.data['disconnect_count']}")
        log(f"  输出目录 : {self.output_dir}")
        log(f"  清单文件 : {self.manifest.path}")
        log(f"{'='*60}\n")

    def _signal_handler(self, signum, frame):
        log("\n收到停止信号，正在安全停止录制...")
        self._stop_event.set()


# ─────────────────────────────────────────────
# 并发录制器（单 ffmpeg 多路输出）
# ─────────────────────────────────────────────

class ConcurrentRecorder:
    """
    用单个 ffmpeg 进程同时录制多路裁剪视频。
    macOS avfoundation 不支持多进程同时捕获同一屏幕，
    所以用 filter_complex split + crop 实现多路输出。

    每路输出有独立的：
      - 输出目录 + 文件名
      - Manifest（时间轴清单）
      - FreezeDetector（卡顿检测）

    用法：
        recorder = ConcurrentRecorder(
            streams=[
                {"match_id": "match_1", "output_dir": "~/recordings", "crop": (0, 0, 1920, 1080)},
                {"match_id": "match_2", "output_dir": "~/recordings", "crop": (1920, 0, 1920, 1080)},
            ],
            screen_idx=0, fps=30, width=960, height=540,
        )
        recorder.register_signals()
        recorder.start()
    """

    def __init__(self, streams, screen_idx=0, fps=DEFAULT_FPS,
                 width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
                 segment_minutes=DEFAULT_SEGMENT_MINUTES,
                 issue_callback=None,
                 auto_rotate_segments=True):
        """
        streams: [{"match_id": str, "output_dir": str, "crop": (x, y, w, h)}, ...]
        width/height: 每路输出的分辨率（缩放后）
        """
        self.streams = streams
        self.screen_idx = screen_idx
        self.fps = fps
        self.width = width
        self.height = height
        self.segment_duration = segment_minutes * 60
        self.issue_callback = issue_callback
        self.auto_rotate_segments = auto_rotate_segments
        self.n = len(streams)
        self._window_backend_requested = all(s.get("window_id") for s in streams)
        self._window_backend_available = bool(build_window_capture_helper()) if self._window_backend_requested else False
        self._backend = "window" if self._window_backend_requested and self._window_backend_available else "screen"

        self.recording_start_iso = now_iso()
        self.recording_start_ts = now_ts()
        self.recording_label = compact_time_label(
            datetime.strptime(self.recording_start_iso, "%Y-%m-%dT%H:%M:%S.%f")
        )

        # 每路独立的 manifest、freeze detector、segment index
        self._output_dirs = []
        self._manifests = []
        self._freeze_detectors = []
        self._segment_idxs = []
        self._current_paths = []
        self._stream_prefixes = []

        for s in streams:
            folder_name = sanitize_path_component(
                s.get("folder_name") or f"{s['match_id']}__{self.recording_label}"
            )
            out_dir = make_output_dir(s["output_dir"], folder_name)
            self._output_dirs.append(out_dir)
            self._manifests.append(Manifest(out_dir, s["match_id"], self.recording_start_iso))
            # 并发模式下 ffmpeg 编码更慢，给更大容忍度
            self._freeze_detectors.append(FreezeDetector(threshold=CONCURRENT_FREEZE_THRESHOLD))
            self._segment_idxs.append(0)
            self._current_paths.append(None)
            self._stream_prefixes.append(
                sanitize_path_component(s.get("file_prefix") or s["match_id"])
            )

        self._stop_event = threading.Event()
        self._process = None
        self._window_recorders = []
        self._seg_start_wall = 0.0
        self._last_log_wall = 0.0
        self._stderr_handle = None
        self._recovery_lock = threading.Lock()
        self._segment_lock = threading.RLock()
        self._pending_restart_reason = None

        log(f"并发录制器初始化完成: {self.n} 路输出")
        if self._backend == "window":
            log("  录制后端   : ScreenCaptureKit 指定窗口")
        else:
            log(f"  录制后端   : ffmpeg avfoundation 屏幕裁剪")
            log(f"  avfoundation设备: screen {screen_idx} -> input {resolve_screen_input_device(screen_idx)}")
            if self._window_backend_requested and not self._window_backend_available:
                log("  窗口录制辅助器不可用，已回退到屏幕裁剪", "WARN")
        for i, s in enumerate(streams):
            if self._backend == "window":
                crop = s.get("content_crop") or {}
                log(
                    f"  [{i+1}] {s['match_id']}: window={s.get('window_id')} "
                    f"content=({crop.get('left', 0)},{crop.get('top', 0)},"
                    f"{crop.get('width', 0)},{crop.get('height', 0)})"
                )
            else:
                cx, cy, cw, ch = s["crop"]
                log(f"  [{i+1}] {s['match_id']}: crop {cw}x{ch}@({cx},{cy}) → {width}x{height}")

    def _backend_alive(self):
        if self._backend == "window":
            return bool(self._window_recorders) and all(r.is_running() for r in self._window_recorders)
        return self._process is not None and self._process.poll() is None

    def _stop_backend(self):
        if self._backend == "window":
            for recorder in self._window_recorders:
                recorder.stop()
            self._window_recorders = []
            return
        terminate_process(self._process)

    def stop(self):
        """公开的停止接口"""
        self._stop_event.set()

    def request_segment_restart(self, reason="external_recovery"):
        with self._recovery_lock:
            self._pending_restart_reason = reason

    def _consume_pending_restart(self):
        with self._recovery_lock:
            reason = self._pending_restart_reason
            self._pending_restart_reason = None
            return reason

    def segment_transition(self):
        return self._segment_lock

    def _next_segment_paths(self):
        """为所有流生成下一组分段文件路径"""
        paths = []
        for i in range(self.n):
            self._segment_idxs[i] += 1
            start_label = wall_time_label(self._wall_time())
            path = os.path.join(
                self._output_dirs[i],
                f"{self._stream_prefixes[i]}__seg_{self._segment_idxs[i]:03d}__t{start_label}.mp4"
            )
            paths.append(path)
            self._current_paths[i] = path
        return paths

    def _build_command(self, output_paths):
        """构建带 filter_complex 的多路输出 ffmpeg 命令"""
        cmd = [
            "ffmpeg", "-y",
            "-loglevel", "error",
            "-f", "avfoundation",
            "-framerate", str(self.fps),
            "-capture_cursor", "0",
            "-i", f"{resolve_screen_input_device(self.screen_idx)}",
        ]

        # filter_complex: split → crop → scale
        splits = [f"[s{i}]" for i in range(self.n)]
        filter_parts = [f"[0:v]split={self.n}{''.join(splits)}"]

        for i, s in enumerate(self.streams):
            cx, cy, cw, ch = s["crop"]
            vf = f"[s{i}]crop={cw}:{ch}:{cx}:{cy}"
            if self.width > 0 and self.height > 0:
                vf += f",scale={self.width}:{self.height}"
            vf += f"[o{i}]"
            filter_parts.append(vf)

        cmd += ["-filter_complex", ";".join(filter_parts)]

        for i, path in enumerate(output_paths):
            cmd += ["-map", f"[o{i}]"] + FFMPEG_ENCODE_ARGS + [path]

        return cmd

    def _start_segment(self):
        """启动一组新的分段录制"""
        paths = self._next_segment_paths()
        self._seg_start_wall = self._wall_time()

        if self._stderr_handle:
            try:
                self._stderr_handle.close()
            except Exception:
                pass
        stderr_path = os.path.join(self._output_dirs[0], "ffmpeg_stderr.log")
        self._stderr_handle = open(stderr_path, "a", encoding="utf-8")
        self._stderr_handle.write("\n=== start segment ===\n")

        if self._backend == "window":
            log(f"启动窗口多路录制 ({self.n} 路)...")
            self._window_recorders = []
            for i, path in enumerate(paths):
                stream = self.streams[i]
                recorder = WindowCaptureProcess(
                    window_id=stream["window_id"],
                    output_path=path,
                    fps=self.fps,
                    width=0,
                    height=0,
                    content_crop=stream.get("content_crop"),
                    stderr_handle=self._stderr_handle,
                )
                self._window_recorders.append(recorder)
                cmd = recorder._build_command() or []
                self._stderr_handle.write("CMD: " + " ".join(cmd) + "\n")
                self._stderr_handle.flush()
                recorder.start()
            self._process = None
        else:
            cmd = self._build_command(paths)
            log(f"启动 ffmpeg 多路录制 ({self.n} 路)...")
            self._stderr_handle.write("CMD: " + " ".join(cmd) + "\n")
            self._stderr_handle.flush()
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_handle,
            )

        ready = wait_for_files(paths, max_wait=20.0)
        if not ready:
            sizes = [get_file_size(p) for p in paths]
            log(f"{self._backend} 后端启动后 20 秒内未看到分段写入: {sizes}", "WARN")

        if not self._backend_alive():
            if self._backend == "window":
                log("窗口录制启动失败！检查屏幕录制权限和 ScreenCaptureKit 日志。", "ERROR")
            else:
                log("ffmpeg 启动失败！检查屏幕录制权限。", "ERROR")
            return False

        for i, path in enumerate(paths):
            self._freeze_detectors[i].reset(path)
            log(f"  ▶ [{i+1}] 分段 {self._segment_idxs[i]}: {os.path.basename(path)}")

        return True

    def _record_segments(self, reason="segment_end"):
        """将当前各路分段记录到 manifest"""
        wall_end = self._wall_time()
        for i in range(self.n):
            path = self._current_paths[i]
            if path and os.path.exists(path) and get_file_size(path) > 0:
                self._manifests[i].add_segment(
                    "live", self._seg_start_wall, wall_end,
                    os.path.basename(path), reason=reason
                )

    def _stop_segment(self):
        """停止当前分段"""
        self._stop_backend()
        self._record_segments("segment_end")

    def _wall_time(self):
        return now_ts() - self.recording_start_ts

    def register_signals(self):
        """注册停止信号（必须在主线程调用）"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        log("\n收到停止信号，正在安全停止并发录制...")
        self._stop_event.set()

    def start(self):
        """开始并发录制主循环"""
        log(f"\n{'='*60}")
        log(f"  开始并发录制: {self.n} 场比赛")
        log(f"  录制开始时间: {self.recording_start_iso}")
        log(f"  按 Ctrl+C 停止录制")
        log(f"{'='*60}\n")

        if not self._start_segment():
            log("ffmpeg 启动失败！", "ERROR")
            return False

        try:
            while not self._stop_event.is_set():
                time.sleep(FREEZE_CHECK_INTERVAL)

                if self._stop_event.is_set():
                    break

                pending_restart_reason = self._consume_pending_restart()
                if pending_restart_reason:
                    log(f"收到外部恢复请求，准备切换新分段: {pending_restart_reason}", "WARN")
                    with self._segment_lock:
                        self._stop_segment()
                        if not self._start_segment():
                            break
                    continue

                # 检查 ffmpeg 进程是否还活着
                if not self._backend_alive():
                    if self.issue_callback:
                        try:
                            self.issue_callback("backend_exit", {"stream_index": None})
                        except Exception as exc:
                            log(f"issue_callback(backend_exit) 失败: {exc}", "WARN")
                    if self._backend == "window":
                        log("窗口录制进程意外退出，尝试重启...", "WARN")
                    else:
                        log("ffmpeg 进程意外退出，尝试重启...", "WARN")
                    with self._segment_lock:
                        self._stop_segment()
                        if not self._start_segment():
                            break
                    continue

                # 检查各路是否卡顿（任一路卡顿 → 全部重启）
                any_frozen = False
                actual_frozen_sec = 0.0
                for i in range(self.n):
                    is_frozen, frozen_sec = self._freeze_detectors[i].check()
                    if is_frozen:
                        log(f"⚠ 检测到第 {i+1} 路卡顿（{frozen_sec:.0f}秒）", "WARN")
                        any_frozen = True
                        actual_frozen_sec = frozen_sec
                        break

                if any_frozen:
                    if self.issue_callback:
                        try:
                            self.issue_callback(
                                "freeze",
                                {"stream_index": i, "frozen_sec": actual_frozen_sec},
                            )
                        except Exception as exc:
                            log(f"issue_callback(freeze) 失败: {exc}", "WARN")
                    with self._segment_lock:
                        self._stop_segment()
                        wall_now = self._wall_time()
                        gap_dur = actual_frozen_sec  # 用实际冻结时长，不用硬编码阈值
                        for i in range(self.n):
                            gap_path = os.path.join(
                                self._output_dirs[i],
                                f"{self._stream_prefixes[i]}__gap_{self._segment_idxs[i]:03d}__t{wall_time_label(wall_now - gap_dur)}.mp4",
                            )
                            generate_black_frames(gap_dur, gap_path, self.width, self.height, self.fps)
                            self._manifests[i].add_segment(
                                "freeze", wall_now - gap_dur, wall_now,
                                os.path.basename(gap_path)
                            )
                        if not self._start_segment():
                            break
                    continue

                # 分段时间检查
                if self.auto_rotate_segments and self._wall_time() - self._seg_start_wall >= self.segment_duration:
                    log("分段时间到，切换新分段...")
                    with self._segment_lock:
                        self._stop_segment()
                        if not self._start_segment():
                            break

                # 状态日志（每 30 秒一次）
                wall_now = self._wall_time()
                if wall_now - self._last_log_wall >= 30.0:
                    self._last_log_wall = wall_now
                    sizes = [get_file_size(p) / 1024 / 1024 for p in self._current_paths if p]
                    size_str = " | ".join(f"{s:.1f}MB" for s in sizes)
                    log(f"● 并发录制中 | 总时长: {seconds_to_hms(wall_now)} | 各路大小: {size_str}")

        except Exception as e:
            log(f"并发录制异常: {e}", "ERROR")
            import traceback
            traceback.print_exc()

        finally:
            log(f"\n{'─'*60}")
            log("停止并发录制...")
            with self._segment_lock:
                self._stop_backend()
                self._record_segments("recording_stopped")
            if self._stderr_handle:
                try:
                    self._stderr_handle.close()
                except Exception:
                    pass
                self._stderr_handle = None
            for i in range(self.n):
                self._manifests[i].set_status("completed")

            wall_end = self._wall_time()
            log(f"\n{'='*60}")
            log(f"  ✅ 并发录制完成: {self.n} 路")
            log(f"  总时长: {seconds_to_hms(wall_end)}")
            for i in range(self.n):
                log(f"  [{i+1}] {self.streams[i]['match_id']}: "
                    f"分段 {self._segment_idxs[i]}, "
                    f"卡顿 {self._manifests[i].data['freeze_count']}次")
                log(f"      目录: {self._output_dirs[i]}")
            log(f"{'='*60}\n")

        return True


# ─────────────────────────────────────────────
# 权限检查
# ─────────────────────────────────────────────

def check_screen_recording_permission(screen_idx=DEFAULT_SCREEN):
    """
    快速测试 avfoundation 是否有权限录制屏幕（不实际录制）
    """
    test_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "avfoundation",
        "-framerate", "1",
        "-t", "0.1",
        "-i", str(resolve_screen_input_device(screen_idx)),
        "-f", "null", "-"
    ]
    result = subprocess.run(test_cmd, capture_output=True, timeout=10)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")
        if "permission" in err.lower() or "SCStreamError" in err or "not permitted" in err.lower():
            return False
    return True


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="足球比赛录制器 - 屏幕录制 + 卡顿检测 + 时间轴清单",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 录制主屏幕
  python3 recorder.py --match-id chelsea_vs_milan_20260321

  # 录制第二块屏幕，自定义输出目录
  python3 recorder.py --match-id test --screen 1 --output-dir ~/Desktop/recordings

  # 测试能否录制（录5秒）
  python3 recorder.py --match-id test --test-seconds 5
        """
    )
    parser.add_argument("--match-id",         required=True,       help="比赛唯一标识，用作目录名")
    parser.add_argument("--screen",           type=int, default=0, help="屏幕编号，0=主屏，1=第二屏（默认0）")
    parser.add_argument("--output-dir",       default=DEFAULT_OUTPUT_DIR, help="输出根目录")
    parser.add_argument("--fps",              type=int, default=DEFAULT_FPS, help="帧率（默认30）")
    parser.add_argument("--width",            type=int, default=DEFAULT_WIDTH,  help="录制宽度（默认1920）")
    parser.add_argument("--height",           type=int, default=DEFAULT_HEIGHT, help="录制高度（默认1080）")
    parser.add_argument("--segment-minutes",  type=int, default=DEFAULT_SEGMENT_MINUTES, help="分段时长分钟（默认30）")
    parser.add_argument("--test-seconds",     type=int, default=0, help="测试模式：录制N秒后自动停止")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  足球比赛录制器 v1.0")
    print(f"{'='*60}")

    # 权限检查
    print(f"\n[+] 检查屏幕录制权限...")
    if not check_screen_recording_permission():
        print(f"\n❌ 没有屏幕录制权限！")
        print(f"   请前往：系统设置 → 隐私与安全性 → 屏幕录制")
        print(f"   勾选 Terminal，然后重新运行本脚本。\n")
        sys.exit(1)
    print(f"   ✅ 权限正常\n")

    # 测试模式
    if args.test_seconds > 0:
        print(f"[测试模式] 将录制 {args.test_seconds} 秒后自动停止")
        controller = RecordingController(
            match_id=args.match_id,
            output_dir=args.output_dir,
            screen_idx=args.screen,
            fps=args.fps,
            width=args.width,
            height=args.height,
            segment_minutes=999,  # 测试时不分段
        )
        # 信号处理必须在主线程注册
        controller.register_signals()
        # 在后台线程启动录制
        t = threading.Thread(target=controller.start)
        t.start()
        time.sleep(args.test_seconds)
        controller.stop()
        t.join()
        return

    # 正常录制
    controller = RecordingController(
        match_id=args.match_id,
        output_dir=args.output_dir,
        screen_idx=args.screen,
        fps=args.fps,
        width=args.width,
        height=args.height,
        segment_minutes=args.segment_minutes,
    )
    # 信号处理在主线程注册
    controller.register_signals()
    controller.start()


if __name__ == "__main__":
    main()
