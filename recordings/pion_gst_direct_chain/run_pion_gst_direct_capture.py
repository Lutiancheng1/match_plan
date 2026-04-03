#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
RECORDINGS_DIR = SCRIPT_DIR.parent
if str(RECORDINGS_DIR) not in sys.path:
    sys.path.insert(0, str(RECORDINGS_DIR))

from post_match import get_video_duration, merge_segments
from recorder import Manifest
from run_auto_capture import (
    ALL_GTYPES,
    BettingDataPoller,
    SessionLogger,
    _append_jsonl,
    _write_jsonl_atomic,
    bootstrap_credentials,
    build_session_output_dir,
    build_stream_naming,
    match_data_to_stream,
)
from pion_gst_direct_chain.shared_livekit_runtime import (
    DEFAULT_SEGMENT_MINUTES,
    extract_best_livekit_bootstrap_for_watch_url,
    load_manifest,
    normalize_full_output_to_mp4,
    now_iso,
    resolve_selected_matches,
)
from pion_gst_direct_chain.live_text_599 import AlignmentEngine, LiveTextPoller599, Shared599Reader, parse_retimeset

GO_BIN = "/opt/homebrew/bin/go"
RECORDER_BIN = SCRIPT_DIR / ".build" / "pion_livekit_gst_recorder"
GO_MAIN = SCRIPT_DIR / "main.go"
RAW_DATA_APPEND_INTERVAL = 1.0
STREAM_DATA_FLUSH_INTERVAL = 60.0
BOUND_STREAM_DATA_FLUSH_INTERVAL = 1.0
DATA_FLUSH_TICK_INTERVAL = 0.5
MAX_START_ATTEMPTS = 3
OUTPUT_WATCHDOG_SECONDS = 45
OUTPUT_WATCHDOG_MIN_PACKETS = 400
BLACK_SCREEN_TIMEOUT_SECONDS = 5 * 60
BLACK_DETECT_MIN_DURATION_SECONDS = 0.8
BLACK_DETECT_PIC_TH = 0.98
BLACK_DETECT_PIX_TH = 0.10
BLACK_DETECT_READY_FILE_AGE_SECONDS = 2.0
BLACK_DETECT_GAP_TOLERANCE_SECONDS = 0.35
BLACK_DETECT_LOG_RE = re.compile(
    r"black_start:(?P<start>[0-9.]+)\s+black_end:(?P<end>[0-9.]+)\s+black_duration:(?P<duration>[0-9.]+)"
)


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_match_payload(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        raise RuntimeError("match payload 格式错误")
    return payload


class SharedBettingDataReader:
    """Reads betting data from a shared JSONL file written by the dispatcher.

    Provides the same interface as BettingDataPoller (.data, .poll_interval,
    .start(), .stop()) so workers can use it as a drop-in replacement.
    """

    def __init__(self, shared_path: str, read_interval: float = 2.0):
        self.shared_path = Path(shared_path)
        self.data: list[dict] = []
        self.poll_interval = 5.0
        self._stop = threading.Event()
        self._read_interval = read_interval
        self._last_offset = 0  # byte offset into the file

    def start(self):
        while not self._stop.is_set():
            self._read_new_rows()
            self._stop.wait(timeout=self._read_interval)

    def stop(self):
        self._stop.set()

    @property
    def poll_count(self):
        return len(self.data)

    @property
    def error_count(self):
        return 0

    def _read_new_rows(self):
        if not self.shared_path.exists():
            return
        try:
            with open(self.shared_path, "r", encoding="utf-8") as f:
                f.seek(self._last_offset)
                new_lines = f.readlines()
                if new_lines:
                    for line in new_lines:
                        line = line.strip()
                        if line:
                            try:
                                self.data.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                    self._last_offset = f.tell()
        except Exception:
            pass


def load_shared_data_credentials(path: str) -> tuple[str | None, dict | None, bool, str | None, str]:
    if not path:
        return None, None, False, None, ""
    candidate = Path(path)
    if not candidate.exists():
        return None, None, False, None, ""
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except Exception:
        return None, None, False, None, ""
    if not isinstance(payload, dict):
        return None, None, False, None, ""
    cookie = payload.get("cookie") or None
    template = payload.get("template") if isinstance(payload.get("template"), dict) else None
    use_dashboard = bool(payload.get("use_dashboard", False))
    feed_url = payload.get("feed_url") or None
    data_source = str(payload.get("data_source") or "").strip()
    return cookie, template, use_dashboard, feed_url, data_source


def ensure_go_recorder(logger: SessionLogger) -> Path:
    if shutil.which(GO_BIN) is None and not Path(GO_BIN).exists():
        raise RuntimeError("未找到 Go，无法编译 Pion/GStreamer 录制器")
    RECORDER_BIN.parent.mkdir(parents=True, exist_ok=True)
    need_build = not RECORDER_BIN.exists()
    if RECORDER_BIN.exists():
        need_build = GO_MAIN.stat().st_mtime > RECORDER_BIN.stat().st_mtime
    if need_build:
        logger.log("编译 Pion/GStreamer 录制器...")
        cmd = [GO_BIN, "build", "-o", str(RECORDER_BIN), str(GO_MAIN)]
        result = subprocess.run(cmd, cwd=str(SCRIPT_DIR), capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "go build failed")
    return RECORDER_BIN


def probe_segment_files(match_dir: Path, file_prefix: str) -> list[Path]:
    patterns = [
        f"{file_prefix}__seg_*.mkv",
        f"{file_prefix}__seg_*.webm",
        f"{file_prefix}__seg_*.ivf",
        f"{file_prefix}__seg_*.h264",
    ]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(sorted(match_dir.glob(pattern)))
    return sorted(candidates)


def wait_for_probeable_segment(path: Path, timeout_seconds: float = 15.0) -> float:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        duration = get_video_duration(str(path))
        if duration > 0:
            return duration
        time.sleep(0.5)
    return 0.0


def detect_black_intervals(video_path: str, timeout_seconds: float = 30.0) -> list[tuple[float, float]]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        video_path,
        "-vf",
        (
            "blackdetect="
            f"d={BLACK_DETECT_MIN_DURATION_SECONDS}:"
            f"pic_th={BLACK_DETECT_PIC_TH}:"
            f"pix_th={BLACK_DETECT_PIX_TH}"
        ),
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception:
        return []

    intervals: list[tuple[float, float]] = []
    for line in (result.stderr or "").splitlines():
        match = BLACK_DETECT_LOG_RE.search(line)
        if not match:
            continue
        start = float(match.group("start") or 0.0)
        end = float(match.group("end") or start)
        if end - start >= BLACK_DETECT_MIN_DURATION_SECONDS:
            intervals.append((start, end))
    return intervals


class RollingBlackDetector:
    def __init__(self, logger: SessionLogger, timeout_seconds: float):
        self.logger = logger
        self.timeout_seconds = float(timeout_seconds)
        self.processed_files: set[str] = set()
        self.timeline_cursor = 0.0
        self.current_black_start: float | None = None
        self.current_black_end: float | None = None

    def scan(self, candidates: list[Path]) -> dict | None:
        now_ts = time.time()
        pending: list[Path] = []
        for path in sorted(candidates):
            if path.name in self.processed_files:
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if now_ts - stat.st_mtime < BLACK_DETECT_READY_FILE_AGE_SECONDS:
                continue
            pending.append(path)

        for path in pending:
            result = self._consume_file(path)
            if result:
                return result
        return None

    def _consume_file(self, path: Path) -> dict | None:
        duration = get_video_duration(str(path))
        self.processed_files.add(path.name)
        if duration <= 0.05:
            return None

        seg_start = self.timeline_cursor
        seg_end = seg_start + duration
        intervals = detect_black_intervals(str(path), timeout_seconds=max(15.0, duration * 2.0))
        abs_intervals: list[tuple[float, float]] = []
        for start, end in intervals:
            start = max(0.0, min(duration, start))
            end = max(start, min(duration, end))
            if end - start >= BLACK_DETECT_MIN_DURATION_SECONDS:
                abs_intervals.append((seg_start + start, seg_start + end))

        cursor = seg_start
        triggered_trim_start: float | None = None
        for abs_start, abs_end in abs_intervals:
            if abs_start > cursor + BLACK_DETECT_GAP_TOLERANCE_SECONDS:
                self.current_black_start = None
                self.current_black_end = None
            if self.current_black_start is None:
                self.current_black_start = abs_start
                self.current_black_end = abs_end
            elif abs_start <= (self.current_black_end or abs_start) + BLACK_DETECT_GAP_TOLERANCE_SECONDS:
                self.current_black_end = max(self.current_black_end or abs_end, abs_end)
            else:
                self.current_black_start = abs_start
                self.current_black_end = abs_end
            if (
                self.current_black_start is not None
                and self.current_black_end is not None
                and self.current_black_end - self.current_black_start >= self.timeout_seconds
                and triggered_trim_start is None
            ):
                triggered_trim_start = self.current_black_start
            cursor = max(cursor, abs_end)

        if seg_end > cursor + BLACK_DETECT_GAP_TOLERANCE_SECONDS:
            self.current_black_start = None
            self.current_black_end = None

        self.timeline_cursor = seg_end

        if triggered_trim_start is not None:
            streak_seconds = max(0.0, (self.current_black_end or seg_end) - triggered_trim_start)
            self.logger.log(
                "检测到持续黑屏，准备提前结束录制: "
                f"start={triggered_trim_start:.1f}s streak={streak_seconds:.1f}s file={path.name}",
                "WARN",
            )
            return {
                "trim_start_sec": round(triggered_trim_start, 3),
                "streak_seconds": round(streak_seconds, 3),
                "source_file": path.name,
            }
        return None


def apply_tail_trim_to_manifest(
    manifest_path: Path,
    manifest_payload: dict | None,
    trim_end_sec: float | None,
    reason: str,
    logger: SessionLogger,
) -> dict | None:
    if not manifest_payload or trim_end_sec is None:
        return manifest_payload
    current_total = float(manifest_payload.get("total_duration_sec", 0) or 0)
    trim_end = max(0.0, min(current_total, float(trim_end_sec)))
    if trim_end >= current_total - 0.05:
        return manifest_payload
    manifest_payload["total_duration_sec"] = round(trim_end, 3)
    manifest_payload["trimmed_tail_reason"] = reason
    manifest_payload["trimmed_tail_start_sec"] = round(trim_end, 3)
    manifest_payload["status"] = "completed"
    write_json_atomic(manifest_path, manifest_payload)
    logger.log(
        f"manifest 尾段裁剪: total {current_total:.1f}s -> {trim_end:.1f}s ({reason})",
        "WARN",
    )
    return manifest_payload


def rebuild_manifest_from_segments(match_dir: Path, file_prefix: str, manifest: Manifest, logger: SessionLogger) -> dict:
    segments = probe_segment_files(match_dir, file_prefix)
    wall_cursor = 0.0
    for path in segments:
        duration = wait_for_probeable_segment(path)
        if duration <= 0:
            logger.log(f"跳过无法探测时长的分段: {path.name}", "WARN")
            continue
        manifest.add_segment(
            "live",
            wall_cursor,
            wall_cursor + duration,
            path.name,
            reason="segment_end",
        )
        wall_cursor += duration
    manifest.set_status("completed" if wall_cursor > 0 else "interrupted")
    return load_manifest(match_dir) or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Pion + GStreamer splitmuxsink 单流直连原型")
    parser.add_argument("--watch-url", default="")
    parser.add_argument("--browser", choices=["safari", "chrome", "app"], default="app")
    parser.add_argument("--match-query", default="")
    parser.add_argument("--match-file", default="")
    parser.add_argument("--server-host", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--server-ms", default="")
    parser.add_argument("--status-path", default="")
    parser.add_argument("--data-credentials-file", default="")
    parser.add_argument("--shared-betting-data", default="")
    parser.add_argument("--shared-599-data", default="")
    parser.add_argument("--segment-minutes", type=int, default=2)
    parser.add_argument("--max-duration-minutes", type=int, default=2)
    parser.add_argument("--disable-hls-preview", action="store_true", default=False)
    parser.add_argument("--hls-segment-seconds", type=int, default=6)
    parser.add_argument("--hls-playlist-length", type=int, default=6)
    parser.add_argument("--black-screen-timeout-seconds", type=int, default=int(BLACK_SCREEN_TIMEOUT_SECONDS))
    parser.add_argument("--archive-width", type=int, default=960)
    parser.add_argument("--archive-height", type=int, default=540)
    parser.add_argument("--archive-bitrate-kbps", type=int, default=5000)
    parser.add_argument("--hls-width", type=int, default=960)
    parser.add_argument("--hls-height", type=int, default=540)
    parser.add_argument("--hls-bitrate-kbps", type=int, default=3500)
    parser.add_argument("--enable-live-text-599", action="store_true", default=True)
    parser.add_argument("--live-text-599-poll-seconds", type=float, default=5.0)
    parser.add_argument("--enable-ocr-calibration", action="store_true", default=True)
    parser.add_argument("--ocr-calibration-interval-seconds", type=int, default=120)
    parser.add_argument("--require-data-binding", action="store_true", default=True)
    parser.add_argument("--allow-unbound", action="store_true", default=False)
    parser.add_argument("--session-id", default="")
    args = parser.parse_args()

    session_id = args.session_id.strip() or f"pgst_probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    session_dir = Path(build_session_output_dir(session_id))
    session_dir.mkdir(parents=True, exist_ok=True)
    logger = SessionLogger(str(session_dir / "recording.log"))
    logger.log("=" * 60)
    logger.log("Pion + GStreamer splitmuxsink 单流原型")
    logger.log(f"Session: {session_id}")
    logger.log(f"浏览器: {args.browser}")
    logger.log(f"分段: {args.segment_minutes}分钟")
    logger.log(f"最大时长: {args.max_duration_minutes}分钟")
    logger.log(f"黑屏停录阈值: {int(args.black_screen_timeout_seconds)}秒")
    logger.log(f"输出: {session_dir}")
    logger.log("=" * 60)

    worker_status_path = Path(args.status_path).resolve() if args.status_path else session_dir / "worker_status.json"
    worker_state = {
        "startedAt": now_iso(),
        "updatedAt": now_iso(),
        "state": "initializing",
        "pid": os.getpid(),
        "sessionId": session_id,
        "sessionDir": str(session_dir),
        "watchUrl": "",
        "serverHost": "",
        "serverMs": "",
        "matchId": "",
        "teams": "",
        "league": "",
        "team_h": "",
        "team_c": "",
        "gid": "",
        "ecid": "",
        "hgid": "",
        "dataBindingStatus": "",
        "recordingNote": "",
        "stopReason": "",
        "mergedVideo": "",
        "matchedRows": 0,
        "dataFile": "",
        "pollIntervalSec": 5.0,
        "blackScreenTrimStartSec": 0.0,
        "blackScreenStreakSec": 0.0,
        "liveText599": {},
        "error": "",
    }

    def update_state(**patch):
        worker_state.update(patch)
        worker_state["updatedAt"] = now_iso()
        write_json_atomic(worker_status_path, worker_state)

    update_state()

    recorder_bin = ensure_go_recorder(logger)

    # Try shared dispatcher credentials first — avoid re-login which invalidates other sessions
    shared_creds = load_shared_data_credentials(args.data_credentials_file)
    has_shared_creds = shared_creds[0] or shared_creds[1] or shared_creds[2] or shared_creds[3]

    if args.match_file:
        selected_match = load_match_payload(args.match_file)
        if args.watch_url:
            selected_match["watch_url"] = args.watch_url
        if not has_shared_creds:
            creds = bootstrap_credentials(logger, args.browser)
        else:
            creds = (None, None, False, None, "none")
    elif args.watch_url:
        selected_match = {
            "watch_url": args.watch_url,
            "team_h": "",
            "team_c": "",
            "gtype": "FT",
            "data_binding_status": "manual",
            "recording_note": "manual_watch_url",
        }
        if not has_shared_creds:
            creds = bootstrap_credentials(logger, args.browser)
        else:
            creds = (None, None, False, None, "none")
    else:
        ns = argparse.Namespace(
            max_streams=1,
            browser=args.browser,
            gtypes="FT",
            all=True,
            match_query=args.match_query,
            prestart_minutes=1,
            selected_matches_file="",
            skip_data_binding=not args.require_data_binding,
            allow_unbound=args.allow_unbound,
        )
        selected, creds = resolve_selected_matches(ns, logger)
        if not selected:
            logger.log("当前没有找到可录制的 FT 比赛。", "ERROR")
            update_state(state="failed", error="当前没有找到可录制的 FT 比赛")
            logger.close()
            return 1
        selected_match = selected[0]

    if has_shared_creds:
        cookie, template, use_dashboard, feed_url, data_source = shared_creds
        logger.log(f"复用 dispatcher 数据源凭证: {data_source or 'shared_cache'}")
    else:
        cookie, template, use_dashboard, feed_url, data_source = creds
    logger.log(f"数据源模式: {data_source}")

    watch_url = str(selected_match.get("watch_url", "")).rstrip("/")
    bootstrap = {
        "serverMs": args.server_ms,
        "serverHost": args.server_host,
        "token": args.token,
        "title": str(selected_match.get("title") or watch_url),
    }
    if not bootstrap.get("serverHost") or not bootstrap.get("token"):
        bootstrap = extract_best_livekit_bootstrap_for_watch_url(args.browser, watch_url, ready_tab=None, logger=logger)
    if str(bootstrap.get("serverMs", "")).strip().lower() != "lk":
        logger.log(f"当前比赛不是 LiveKit: {watch_url}", "ERROR")
        update_state(
            state="failed",
            watchUrl=watch_url,
            serverHost=str(bootstrap.get("serverHost", "")),
            serverMs=str(bootstrap.get("serverMs", "")),
            error="当前比赛不是 LiveKit",
        )
        logger.close()
        return 1
    if not bootstrap.get("serverHost") or not bootstrap.get("token"):
        logger.log(f"缺少 serverHost/token: {watch_url}", "ERROR")
        update_state(
            state="failed",
            watchUrl=watch_url,
            serverHost=str(bootstrap.get("serverHost", "")),
            serverMs=str(bootstrap.get("serverMs", "")),
            error="缺少 serverHost/token",
        )
        logger.close()
        return 1

    match_id, folder_name, file_prefix = build_stream_naming(
        selected_match,
        str(bootstrap.get("title") or watch_url),
        session_id,
        1,
    )
    output_dir = session_dir / folder_name
    output_dir.mkdir(parents=True, exist_ok=True)
    hls_dir = output_dir / "hls"
    hls_playlist = hls_dir / "playlist.m3u8"
    manifest = Manifest(str(output_dir), match_id, now_iso())
    update_state(
        watchUrl=watch_url,
        serverHost=str(bootstrap.get("serverHost", "")),
        serverMs=str(bootstrap.get("serverMs", "")),
        matchId=match_id,
        teams=" vs ".join([part for part in [str(selected_match.get("team_h", "")), str(selected_match.get("team_c", ""))] if part]),
        league=str(selected_match.get("league", "")),
        team_h=str(selected_match.get("team_h", "")),
        team_c=str(selected_match.get("team_c", "")),
        gid=str(selected_match.get("gid", "")),
        ecid=str(selected_match.get("ecid", "")),
        hgid=str(selected_match.get("hgid", "")),
        dataBindingStatus=str(selected_match.get("data_binding_status", "")),
        recordingNote=str(selected_match.get("recording_note", "")),
        state="polling",
    )

    if args.shared_betting_data:
        poller = SharedBettingDataReader(args.shared_betting_data)
        poller_thread = threading.Thread(target=poller.start, daemon=True)
        poller_thread.start()
        logger.log(f"数据采集: 读取 dispatcher 共享文件 (不独立请求数据站)")
    else:
        poller = BettingDataPoller(
            cookie,
            template,
            gtypes=list({selected_match.get("gtype")} - {None, ""}) or ALL_GTYPES,
            use_dashboard=use_dashboard,
            feed_url=feed_url,
        )
        poller_thread = threading.Thread(target=poller.start, daemon=True)
        poller_thread.start()
        logger.log("数据采集线程启动 (独立轮询模式)")
    update_state(pollIntervalSec=poller.poll_interval)

    # --- 599 文字直播对齐 ---
    alignment_engine = AlignmentEngine()
    live_text_path = output_dir / f"{file_prefix}__live_events.jsonl"
    live_text_rows_written = 0
    live_text_poller = None
    live_text_thread = None
    _live_text_enabled = (
        args.enable_live_text_599
        and str(selected_match.get("gtype") or "FT") == "FT"
        and str(selected_match.get("team_h", "")).strip()
        and str(selected_match.get("team_c", "")).strip()
    )
    if _live_text_enabled:
        live_text_path.touch(exist_ok=True)
        if args.shared_599_data:
            live_text_poller = Shared599Reader(
                args.shared_599_data,
                str(selected_match.get("team_h", "")),
                str(selected_match.get("team_c", "")),
                alignment=alignment_engine,
                logger=logger,
            )
            live_text_thread = threading.Thread(target=live_text_poller.start, daemon=True)
            live_text_thread.start()
            logger.log(f"599 文字直播: 读取 dispatcher 共享文件 (不独立请求599)")
        else:
            live_text_poller = LiveTextPoller599(
                str(selected_match.get("team_h", "")),
                str(selected_match.get("team_c", "")),
                selected_match=selected_match,
                league=str(selected_match.get("league", "")),
                alignment=alignment_engine,
                poll_interval=float(args.live_text_599_poll_seconds),
                logger=logger,
            )
            live_text_thread = threading.Thread(target=live_text_poller.start, daemon=True)
            live_text_thread.start()
            logger.log(f"599 文字直播线程启动: poll={args.live_text_599_poll_seconds}s (独立轮询)")
        update_state(liveText599={**live_text_poller.snapshot(), **alignment_engine.snapshot()})
    elif args.enable_live_text_599:
        logger.log("599 文字直播未启用: 需要 FT 且 team_h/team_c 完整", "WARN")

    raw_data_path = session_dir / "raw_betting_data.jsonl"
    stream_data_path = output_dir / f"{file_prefix}__betting_data.jsonl"
    raw_data_path.touch(exist_ok=True)
    stream_data_path.touch(exist_ok=True)
    raw_rows_flushed = 0
    stream_rows_flushed = 0
    stream_rows_written = 0
    stop_event = threading.Event()
    stream_flush_interval = (
        BOUND_STREAM_DATA_FLUSH_INTERVAL
        if args.require_data_binding and not args.allow_unbound
        else STREAM_DATA_FLUSH_INTERVAL
    )
    update_state(dataFile=str(stream_data_path))

    def append_live_raw_data(reason="periodic_raw"):
        nonlocal raw_rows_flushed
        rows = list(poller.data[raw_rows_flushed:])
        if not rows:
            return 0
        written = _append_jsonl(str(raw_data_path), rows)
        raw_rows_flushed += written
        logger.log(f"实时原始数据追加({reason}): +{written}条, total={raw_rows_flushed}")
        return written

    def append_stream_data(reason="periodic_stream"):
        nonlocal stream_rows_flushed, stream_rows_written
        rows = list(poller.data[stream_rows_flushed:])
        if not rows:
            update_state(dataFile=str(stream_data_path), matchedRows=stream_rows_written)
            return 0
        matched = match_data_to_stream(
            rows,
            " vs ".join(
                [part for part in [str(selected_match.get("team_h", "")), str(selected_match.get("team_c", ""))] if part]
            ),
            str(selected_match.get("gtype", "")),
            selected_match=selected_match,
        )
        # 标注统一时间轴: RETIMESET → match_time_ms → video_pos_sec
        for row in matched:
            retimeset = (row.get("fields") or {}).get("RETIMESET", "")
            parsed = parse_retimeset(retimeset)
            row["_match_time_ms"] = parsed["match_time_ms"]
            row["_match_time_sec"] = parsed["match_time_sec"]
            row["_match_half"] = parsed["half"]
            row["_match_clock"] = parsed["match_clock"]
            if parsed["match_time_ms"] is not None:
                vpos = alignment_engine.match_time_to_video(parsed["match_time_ms"])
                row["_video_pos_sec"] = round(vpos, 3) if vpos is not None else None
            else:
                row["_video_pos_sec"] = None
        written = _append_jsonl(str(stream_data_path), matched)
        if live_text_poller and matched:
            try:
                alignment_engine.observe_betting_score(matched)
            except Exception:
                pass
        stream_rows_flushed += len(rows)
        stream_rows_written += written
        update_state(dataFile=str(stream_data_path), matchedRows=stream_rows_written)
        if written:
            logger.log(f"实时比赛数据追加({reason}): +{written}条")
        return written

    def flush_live_text_599(reason="periodic"):
        nonlocal live_text_rows_written
        if not live_text_poller:
            return 0
        rows = live_text_poller.drain_pending()
        if not rows:
            update_state(liveText599={**live_text_poller.snapshot(), **alignment_engine.snapshot()})
            return 0
        annotated = [alignment_engine.annotate_event(e) for e in sorted(rows, key=lambda x: int(x.get("time", 0) or 0))]
        written = _append_jsonl(str(live_text_path), annotated)
        live_text_rows_written += written
        update_state(liveText599={**live_text_poller.snapshot(), **alignment_engine.snapshot()})
        if written:
            logger.log(f"599 文字直播落盘({reason}): +{written}条, total={live_text_rows_written}")
        return written

    def data_flush_loop():
        raw_round = 0
        stream_round = 0
        next_raw = time.monotonic() + 1.0
        next_stream = time.monotonic() + min(1.0, stream_flush_interval)
        while not stop_event.wait(timeout=DATA_FLUSH_TICK_INTERVAL):
            now_mono = time.monotonic()
            try:
                update_state(pollIntervalSec=poller.poll_interval)
            except Exception:
                pass
            try:
                if now_mono >= next_raw:
                    raw_round += 1
                    append_live_raw_data(reason=f"periodic_raw_{raw_round:03d}")
                    while next_raw <= now_mono:
                        next_raw += RAW_DATA_APPEND_INTERVAL
                if now_mono >= next_stream:
                    stream_round += 1
                    append_stream_data(reason=f"periodic_stream_{stream_round:03d}")
                    while next_stream <= now_mono:
                        next_stream += stream_flush_interval
                if live_text_poller:
                    flush_live_text_599(reason="periodic")
            except Exception as exc:
                logger.log(f"Pion/GStreamer 实时数据落盘失败: {exc}", "WARN")

    flush_thread = threading.Thread(target=data_flush_loop, daemon=True)
    flush_thread.start()

    # --- OCR 校准线程: 每 N 秒从最新 HLS 段抽一帧 → 9B 模型 OCR 比赛时钟 → 校准对齐 ---
    ocr_calibration_thread = None
    if args.enable_ocr_calibration and not args.disable_hls_preview:
        _ocr_frame_path = output_dir / "_ocr_calibration_frame.jpg"

        def _extract_frame_from_ts(ts_path: Path, out_path: Path) -> bool:
            """从 .ts 段中提取第一帧 JPEG。"""
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(ts_path),
                     "-frames:v", "1", "-q:v", "2", "-f", "image2", str(out_path)],
                    capture_output=True, timeout=15,
                )
                return out_path.exists() and out_path.stat().st_size > 1000
            except Exception:
                return False

        def ocr_calibration_loop():
            """定期从 HLS 段截帧 → OCR 比赛时钟 → 喂给 AlignmentEngine。"""
            try:
                project_root = str(Path(__file__).resolve().parent.parent.parent)
                if project_root not in sys.path:
                    sys.path.insert(0, project_root)
                from analysis_vlm.lib.live_observer import LiveObserver
                observer = LiveObserver(timeout=20)
            except Exception as exc:
                logger.log(f"OCR校准: 无法加载 LiveObserver: {exc}，跳过校准", "WARN")
                return

            # 等录制真正开始
            for _ in range(60):
                if stop_event.wait(timeout=5):
                    return
                if alignment_engine.video_start_utc is not None:
                    break
            else:
                logger.log("OCR校准: 60次等待后视频仍未开始，退出校准线程", "WARN")
                return

            # 首次校准在录制开始 30 秒后
            if stop_event.wait(timeout=30):
                return

            interval = max(60, int(args.ocr_calibration_interval_seconds))
            logger.log(f"OCR校准线程启动: interval={interval}s")

            while not stop_event.is_set():
                try:
                    if not hls_dir.exists():
                        if stop_event.wait(timeout=interval):
                            break
                        continue
                    candidates = sorted(hls_dir.glob("segment_*.ts"))
                    if not candidates:
                        if stop_event.wait(timeout=interval):
                            break
                        continue

                    latest_ts = candidates[-1]
                    if not _extract_frame_from_ts(latest_ts, _ocr_frame_path):
                        logger.log("OCR校准: ffmpeg 截帧失败", "WARN")
                        if stop_event.wait(timeout=interval):
                            break
                        continue

                    # 视频位置 ≈ 当前时刻 - 录制开始时刻
                    video_pos_sec = (datetime.now(timezone.utc) - alignment_engine.video_start_utc).total_seconds()

                    result = observer.observe_frame(str(_ocr_frame_path))
                    _ocr_frame_path.unlink(missing_ok=True)

                    if not result.get("success"):
                        logger.log(f"OCR校准: 模型调用失败 — {result.get('error', '?')}", "WARN")
                        if stop_event.wait(timeout=interval):
                            break
                        continue

                    obs = result["observation"] or {}
                    clock = str(obs.get("match_clock_detected") or "").strip()
                    vis = str(obs.get("scoreboard_visibility") or "").strip()

                    if clock and vis in ("clear", "partial"):
                        cal = alignment_engine.ingest_ocr_calibration(video_pos_sec, clock)
                        cal_off = cal.get("offset")
                        cal_off_str = f"{cal_off:.1f}s" if cal_off is not None else "N/A"
                        logger.log(
                            f"OCR校准成功: clock={clock} vis={vis} video={video_pos_sec:.0f}s "
                            f"half={cal.get('half')} offset={cal_off_str} "
                            f"latency={result.get('latency_ms', 0):.0f}ms"
                        )
                    else:
                        logger.log(
                            f"OCR校准: 时钟不可读 clock='{clock}' vis='{vis}' "
                            f"scene={obs.get('scene_type', '?')} latency={result.get('latency_ms', 0):.0f}ms"
                        )
                except Exception as exc:
                    logger.log(f"OCR校准异常: {exc}", "WARN")

                if stop_event.wait(timeout=interval):
                    break

            _ocr_frame_path.unlink(missing_ok=True)
            logger.log(f"OCR校准线程结束: 共 {len(alignment_engine._ocr_points)} 个校准点")

        ocr_calibration_thread = threading.Thread(target=ocr_calibration_loop, daemon=True, name="ocr_calibration")
        ocr_calibration_thread.start()
        logger.log(f"OCR校准线程已创建: interval={args.ocr_calibration_interval_seconds}s (等待录制开始)")

    recorder_status_path = output_dir / "pion_gst_status.json"
    output_pattern = output_dir / f"{file_prefix}__seg_%05d.mkv"
    env = os.environ.copy()
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "MATCH_RECORDING_PROXY_URL",
        "MATCH_RECORDING_PROXY_POLICY",
    ):
        env.pop(key, None)

    process = None
    stop_requested = False
    final_status = {}
    requested_stop_reason = ""
    black_trim_start_sec: float | None = None
    black_streak_seconds = 0.0
    black_detector = RollingBlackDetector(logger, timeout_seconds=max(30.0, float(args.black_screen_timeout_seconds)))

    def request_process_stop(reason: str, *, mark_manual: bool = False):
        nonlocal stop_requested, process, requested_stop_reason
        if mark_manual:
            stop_requested = True
        requested_stop_reason = reason
        update_state(state="stopping", stopReason=reason)
        if process is None:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGINT)
        except Exception:
            pass

    def handle_signal(signum, _frame):
        logger.log(f"收到信号 {signum}，准备停止 Pion/GStreamer 原型", "WARN")
        request_process_stop(f"signal_{signum}", mark_manual=True)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    for attempt in range(1, MAX_START_ATTEMPTS + 1):
        forced_retry_reason = ""
        no_output_since = None
        black_detector = RollingBlackDetector(logger, timeout_seconds=max(30.0, float(args.black_screen_timeout_seconds)))
        if attempt > 1:
            logger.log(f"Pion/GStreamer 启动重试前刷新 bootstrap: attempt={attempt}", "WARN")
            bootstrap = extract_best_livekit_bootstrap_for_watch_url(args.browser, watch_url, ready_tab=None, logger=logger)
            if not bootstrap.get("serverHost") or not bootstrap.get("token"):
                logger.log("刷新 bootstrap 失败，停止重试。", "ERROR")
                break
        cmd = [
            str(recorder_bin),
            "--server-host",
            str(bootstrap.get("serverHost", "")),
            "--token",
            str(bootstrap.get("token", "")),
            "--output-pattern",
            str(output_pattern),
            "--status-path",
            str(recorder_status_path),
            "--archive-width",
            str(int(args.archive_width)),
            "--archive-height",
            str(int(args.archive_height)),
            "--archive-bitrate-kbps",
            str(int(args.archive_bitrate_kbps)),
            "--segment-seconds",
            str(max(30, int(args.segment_minutes) * 60)),
            "--connect-timeout",
            "45s",
            "--track-wait",
            "12s",
        ]
        if not args.disable_hls_preview:
            cmd.extend(
                [
                    "--enable-hls",
                    "--hls-dir",
                    str(hls_dir),
                    "--hls-segment-seconds",
                    str(max(2, int(args.hls_segment_seconds))),
                    "--hls-playlist-length",
                    str(max(3, int(args.hls_playlist_length))),
                    "--hls-width",
                    str(int(args.hls_width)),
                    "--hls-height",
                    str(int(args.hls_height)),
                    "--hls-bitrate-kbps",
                    str(int(args.hls_bitrate_kbps)),
                ]
            )
        logger.log(f"Pion/GStreamer 录制启动: host={bootstrap.get('serverHost', '')} | attempt={attempt}")
        logger.log("CMD: " + " ".join(cmd))
        update_state(
            state="starting",
            serverHost=str(bootstrap.get("serverHost", "")),
            serverMs=str(bootstrap.get("serverMs", "")),
            error="",
            stopReason="",
        )
        process = subprocess.Popen(
            cmd,
            cwd=str(SCRIPT_DIR),
            env=env,
            stdout=open(session_dir / "pion_gst_stdout.log", "a", encoding="utf-8"),
            stderr=open(session_dir / "pion_gst_stderr.log", "a", encoding="utf-8"),
            start_new_session=True,
        )

        deadline = time.time() + max(0, args.max_duration_minutes) * 60 if args.max_duration_minutes > 0 else None
        last_status_state = ""
        while True:
            if process.poll() is not None:
                break
            if deadline and time.time() >= deadline:
                logger.log("达到演示最大时长，准备优雅停止当前单流原型")
                handle_signal(signal.SIGTERM, None)
                deadline = None
            if recorder_status_path.exists():
                try:
                    payload = json.loads(recorder_status_path.read_text(encoding="utf-8"))
                    state = str(payload.get("state", ""))
                    if state != last_status_state:
                        logger.log(
                            f"状态更新: {state} | video={payload.get('videoCodec', '')} | audio={payload.get('audioCodec', '')} | segments={payload.get('segmentCount', 0)}"
                        )
                        if state == "recording" and last_status_state != "recording":
                            alignment_engine.set_video_start_utc(datetime.now(timezone.utc))
                        last_status_state = state
                    update_state(
                        state="recording" if state == "recording" else state,
                        stopReason=requested_stop_reason or str(payload.get("stopReason", "")),
                        error=str(payload.get("lastError", "")),
                        activeSegments=int(payload.get("segmentCount", 0) or 0),
                        videoCodec=str(payload.get("videoCodec", "")),
                        audioCodec=str(payload.get("audioCodec", "")),
                        hlsPlaylist=str(payload.get("hlsPlaylistPath", "")),
                        hlsSegmentCount=int(payload.get("hlsSegmentCount", 0) or 0),
                    )
                    output_count = int(payload.get("segmentCount", 0) or 0) + int(payload.get("hlsSegmentCount", 0) or 0)
                    packet_count = int(payload.get("videoPackets", 0) or 0) + int(payload.get("audioPackets", 0) or 0)
                    if state in {"connected", "recording"} and output_count <= 0 and packet_count >= OUTPUT_WATCHDOG_MIN_PACKETS:
                        if no_output_since is None:
                            no_output_since = time.monotonic()
                        elif time.monotonic() - no_output_since >= OUTPUT_WATCHDOG_SECONDS:
                            forced_retry_reason = "no_output_timeout"
                            logger.log(
                                f"Pion/GStreamer 已收包但 {OUTPUT_WATCHDOG_SECONDS}s 内仍未生成任何分段，准备刷新 bootstrap 后重试。"
                                f" packets={packet_count}",
                                "WARN",
                            )
                            try:
                                os.killpg(os.getpgid(process.pid), signal.SIGINT)
                            except Exception:
                                pass
                    else:
                        no_output_since = None
                    if state == "recording" and requested_stop_reason != "black_screen_timeout":
                        if not args.disable_hls_preview and hls_dir.exists():
                            candidates = sorted(hls_dir.glob("segment_*.ts"))
                        else:
                            candidates = probe_segment_files(output_dir, file_prefix)
                        black_hit = black_detector.scan(candidates)
                        if black_hit:
                            black_trim_start_sec = float(black_hit.get("trim_start_sec", 0.0) or 0.0)
                            black_streak_seconds = float(black_hit.get("streak_seconds", 0.0) or 0.0)
                            logger.log(
                                "持续黑屏达到阈值，准备结束录制并裁掉黑屏尾段: "
                                f"trim_start={black_trim_start_sec:.1f}s streak={black_streak_seconds:.1f}s",
                                "WARN",
                            )
                            update_state(
                                blackScreenTrimStartSec=black_trim_start_sec,
                                blackScreenStreakSec=black_streak_seconds,
                            )
                            request_process_stop("black_screen_timeout", mark_manual=False)
                except Exception:
                    pass
            time.sleep(2)

        if recorder_status_path.exists():
            try:
                final_status = json.loads(recorder_status_path.read_text(encoding="utf-8"))
            except Exception:
                final_status = {}
        stop_reason = forced_retry_reason or str(final_status.get("stopReason", ""))
        if stop_requested or probe_segment_files(output_dir, file_prefix):
            break
        if stop_reason not in {"signal_connect_failed", "no_output_timeout"} or attempt >= MAX_START_ATTEMPTS:
            break
        logger.log(f"Pion/GStreamer 本轮为 {stop_reason}，准备自动重试。", "WARN")
        update_state(state="retrying", stopReason=stop_reason, error=str(final_status.get("lastError", "")))

    stop_event.set()
    poller.stop()
    if live_text_poller:
        live_text_poller.stop()
    poller_thread.join(timeout=2)
    if live_text_thread:
        live_text_thread.join(timeout=2)
    flush_thread.join(timeout=2)
    if ocr_calibration_thread:
        ocr_calibration_thread.join(timeout=5)

    final_rows = list(poller.data)
    _write_jsonl_atomic(str(raw_data_path), final_rows)
    matched = match_data_to_stream(
        final_rows,
        " vs ".join([part for part in [str(selected_match.get("team_h", "")), str(selected_match.get("team_c", ""))] if part]),
        str(selected_match.get("gtype", "")),
        selected_match=selected_match,
    )
    # 最终写入: 统一标注时间轴
    for row in matched:
        retimeset = (row.get("fields") or {}).get("RETIMESET", "")
        parsed = parse_retimeset(retimeset)
        row["_match_time_ms"] = parsed["match_time_ms"]
        row["_match_time_sec"] = parsed["match_time_sec"]
        row["_match_half"] = parsed["half"]
        row["_match_clock"] = parsed["match_clock"]
        if parsed["match_time_ms"] is not None:
            vpos = alignment_engine.match_time_to_video(parsed["match_time_ms"])
            row["_video_pos_sec"] = round(vpos, 3) if vpos is not None else None
        else:
            row["_video_pos_sec"] = None
    _write_jsonl_atomic(str(stream_data_path), matched)
    stream_rows_written = len(matched)

    # 599 文字直播最终写入
    if live_text_poller:
        alignment_engine.observe_betting_score(matched)
        remaining = live_text_poller.drain_pending()
        all_events = list(live_text_poller.data) + remaining
        final_annotated = [alignment_engine.annotate_event(e) for e in sorted(all_events, key=lambda x: int(x.get("time", 0) or 0))]
        # 去重（by msgId）
        seen = set()
        deduped = []
        for e in final_annotated:
            mid = str(e.get("msgId", ""))
            if mid and mid in seen:
                continue
            if mid:
                seen.add(mid)
            deduped.append(e)
        _write_jsonl_atomic(str(live_text_path), deduped)
        live_text_rows_written = len(deduped)
        snap = live_text_poller.snapshot()
        asnap = alignment_engine.snapshot()
        logger.log(
            f"599 文字直播最终写入: {live_text_rows_written}条 | "
            f"thirdId={snap.get('thirdId','')} | "
            f"kickoff_offset={asnap.get('kickoffVideoOffsetSec','')}s | "
            f"ocr_points={asnap.get('ocrCalibrationPoints', 0)} "
            f"ocr_h1={asnap.get('ocrOffsetH1', 'N/A')} ocr_h2={asnap.get('ocrOffsetH2', 'N/A')} "
            f"source={asnap.get('alignmentSource', 'none')}"
        )
        update_state(liveText599={**snap, **asnap, "rows": live_text_rows_written, "file": str(live_text_path)})

    # OCR 校准点落盘（调试用）
    if alignment_engine._ocr_points:
        ocr_cal_path = output_dir / f"{file_prefix}__ocr_calibration.json"
        try:
            ocr_cal_path.write_text(json.dumps({
                "points": alignment_engine._ocr_points,
                "offset_h1": alignment_engine._ocr_offset_h1,
                "offset_h2": alignment_engine._ocr_offset_h2,
                "total_points": len(alignment_engine._ocr_points),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.log(f"OCR校准数据已保存: {ocr_cal_path} ({len(alignment_engine._ocr_points)}点)")
        except Exception as exc:
            logger.log(f"OCR校准数据保存失败: {exc}", "WARN")

    manifest_payload = rebuild_manifest_from_segments(output_dir, file_prefix, manifest, logger)
    manifest_payload = apply_tail_trim_to_manifest(
        output_dir / "manifest.json",
        manifest_payload,
        black_trim_start_sec,
        "black_screen_timeout",
        logger,
    )
    full_path = output_dir / f"{file_prefix}__full.mp4"
    merged_video = merge_segments(str(output_dir), manifest_payload, str(full_path)) if manifest_payload else None
    if merged_video:
        merged_video = str(normalize_full_output_to_mp4(Path(merged_video), logger) or merged_video)

    if recorder_status_path.exists():
        try:
            final_status = json.loads(recorder_status_path.read_text(encoding="utf-8"))
        except Exception:
            final_status = final_status or {}

    result = {
        "session_id": session_id,
        "chain": "pion_gst_direct_probe",
        "watch_url": watch_url,
        "server_host": bootstrap.get("serverHost", ""),
        "server_ms": bootstrap.get("serverMs", ""),
        "match_id": match_id,
        "teams": " vs ".join([part for part in [str(selected_match.get("team_h", "")), str(selected_match.get("team_c", ""))] if part]),
        "segments": len((manifest_payload or {}).get("segments", [])),
        "merged_video": merged_video,
        "hls_playlist": str(hls_playlist) if hls_playlist.exists() else "",
        "hls_dir": str(hls_dir),
        "matched_rows": len(matched),
        "status": final_status,
        "stop_requested": stop_requested,
        "requested_stop_reason": requested_stop_reason,
        "black_screen_trim_start_sec": black_trim_start_sec,
        "black_screen_streak_seconds": black_streak_seconds,
        "live_text_599": {
            "enabled": bool(live_text_poller),
            "file": str(live_text_path) if live_text_poller else "",
            "rows": live_text_rows_written,
            "thirdId": live_text_poller.snapshot().get("thirdId", "") if live_text_poller else "",
            "alignment": alignment_engine.snapshot() if live_text_poller else {},
        },
        "process_returncode": process.returncode,
    }
    (session_dir / "session_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.log(f"单流 Pion/GStreamer 原型结束: {session_dir}")
    if merged_video:
        logger.log(f"合并成片: {merged_video}")

    # Auto-generate timeline.csv from betting_data.jsonl
    try:
        from backfill_timeline_csv import read_betting_jsonl, build_timeline_rows, write_timeline_csv
        if stream_data_path.exists() and stream_data_path.stat().st_size > 0:
            bd_rows = read_betting_jsonl(stream_data_path)
            if len(bd_rows) >= 2:
                tl_stem = stream_data_path.stem.replace("__betting_data", "")
                tl_path = stream_data_path.parent / f"{tl_stem}__timeline.csv"
                tl_rows = build_timeline_rows(bd_rows)
                write_timeline_csv(tl_rows, tl_path)
                logger.log(f"自动生成 timeline: {tl_path.name} ({len(tl_rows)} rows)")
            else:
                logger.log(f"betting_data 行数不足 ({len(bd_rows)})，跳过 timeline 生成")
    except Exception as e:
        logger.log(f"timeline 自动生成失败: {e}", "WARN")

    final_state = "completed"
    stop_reason_final = requested_stop_reason or str(final_status.get("stopReason", ""))
    last_error_final = str(final_status.get("lastError", ""))
    if stop_requested or stop_reason_final == "manual_stop" or stop_reason_final.startswith("signal_"):
        final_state = "stopped"
        if stop_reason_final.startswith("signal_"):
            stop_reason_final = "manual_stop"
        last_error_final = ""
    elif stop_reason_final == "black_screen_timeout":
        final_state = "completed"
        last_error_final = ""
    elif stop_reason_final == "no_video_track":
        final_state = "skipped"
    elif process and process.returncode not in (0, None) and not merged_video and not stop_requested:
        final_state = "failed"
    if final_status.get("state") == "failed" and not merged_video and not stop_requested and final_state != "skipped":
        final_state = "failed"
    update_state(
        state=final_state,
        stopReason=stop_reason_final,
        mergedVideo=merged_video or "",
        matchedRows=stream_rows_written,
        dataFile=str(stream_data_path),
        error=last_error_final,
        blackScreenTrimStartSec=black_trim_start_sec or 0.0,
        blackScreenStreakSec=black_streak_seconds,
        activeSegments=int((manifest_payload or {}).get("segments", []) and len((manifest_payload or {}).get("segments", [])) or 0),
        hlsPlaylist=str(final_status.get("hlsPlaylistPath", "")) or (str(hls_playlist) if hls_playlist.exists() else ""),
        hlsSegmentCount=int(final_status.get("hlsSegmentCount", 0) or 0),
    )
    logger.close()
    if merged_video or stop_requested or stop_reason_final == "black_screen_timeout":
        return 0
    return 0 if process.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
