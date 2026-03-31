#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
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
    parser.add_argument("--segment-minutes", type=int, default=2)
    parser.add_argument("--max-duration-minutes", type=int, default=2)
    parser.add_argument("--disable-hls-preview", action="store_true", default=False)
    parser.add_argument("--hls-segment-seconds", type=int, default=6)
    parser.add_argument("--hls-playlist-length", type=int, default=6)
    parser.add_argument("--archive-width", type=int, default=960)
    parser.add_argument("--archive-height", type=int, default=540)
    parser.add_argument("--archive-bitrate-kbps", type=int, default=5000)
    parser.add_argument("--hls-width", type=int, default=960)
    parser.add_argument("--hls-height", type=int, default=540)
    parser.add_argument("--hls-bitrate-kbps", type=int, default=3500)
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
        "pollIntervalSec": 1.0,
        "error": "",
    }

    def update_state(**patch):
        worker_state.update(patch)
        worker_state["updatedAt"] = now_iso()
        write_json_atomic(worker_status_path, worker_state)

    update_state()

    recorder_bin = ensure_go_recorder(logger)

    if args.match_file:
        selected_match = load_match_payload(args.match_file)
        if args.watch_url:
            selected_match["watch_url"] = args.watch_url
        creds = bootstrap_credentials(logger, args.browser)
    elif args.watch_url:
        selected_match = {
            "watch_url": args.watch_url,
            "team_h": "",
            "team_c": "",
            "gtype": "FT",
            "data_binding_status": "manual",
            "recording_note": "manual_watch_url",
        }
        creds = bootstrap_credentials(logger, args.browser)
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

    shared_creds = load_shared_data_credentials(args.data_credentials_file)
    if shared_creds[0] or shared_creds[1] or shared_creds[2] or shared_creds[3]:
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

    poller = BettingDataPoller(
        cookie,
        template,
        gtypes=list({selected_match.get("gtype")} - {None, ""}) or ALL_GTYPES,
        use_dashboard=use_dashboard,
        feed_url=feed_url,
        logger=logger,
        app_bridge_url="http://127.0.0.1:18765",
    )
    poller_thread = threading.Thread(target=poller.start, daemon=True)
    poller_thread.start()
    logger.log("数据采集线程启动 (Pion/GStreamer 单流)")
    update_state(pollIntervalSec=poller.current_poll_interval)

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
        rows = poller.snapshot_rows_since(raw_rows_flushed)
        if not rows:
            return 0
        written = _append_jsonl(str(raw_data_path), rows)
        raw_rows_flushed += written
        logger.log(f"实时原始数据追加({reason}): +{written}条, total={raw_rows_flushed}")
        return written

    def append_stream_data(reason="periodic_stream"):
        nonlocal stream_rows_flushed, stream_rows_written
        rows = poller.snapshot_rows_since(stream_rows_flushed)
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
        written = _append_jsonl(str(stream_data_path), matched)
        stream_rows_flushed += len(rows)
        stream_rows_written += written
        update_state(dataFile=str(stream_data_path), matchedRows=stream_rows_written)
        if written:
            logger.log(f"实时比赛数据追加({reason}): +{written}条")
        return written

    def data_flush_loop():
        raw_round = 0
        stream_round = 0
        next_raw = time.monotonic() + 1.0
        next_stream = time.monotonic() + min(1.0, stream_flush_interval)
        while not stop_event.wait(timeout=DATA_FLUSH_TICK_INTERVAL):
            now_mono = time.monotonic()
            try:
                update_state(pollIntervalSec=poller.current_poll_interval)
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
            except Exception as exc:
                logger.log(f"Pion/GStreamer 实时数据落盘失败: {exc}", "WARN")

    flush_thread = threading.Thread(target=data_flush_loop, daemon=True)
    flush_thread.start()

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

    def handle_signal(signum, _frame):
        nonlocal stop_requested, process
        stop_requested = True
        logger.log(f"收到信号 {signum}，准备停止 Pion/GStreamer 原型", "WARN")
        update_state(state="stopping", stopReason=f"signal_{signum}")
        if process is None:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGINT)
        except Exception:
            pass

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    for attempt in range(1, MAX_START_ATTEMPTS + 1):
        forced_retry_reason = ""
        no_output_since = None
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
                        last_status_state = state
                    update_state(
                        state="recording" if state == "recording" else state,
                        stopReason=str(payload.get("stopReason", "")),
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
    poller_thread.join(timeout=2)
    flush_thread.join(timeout=2)

    final_rows = poller.snapshot_rows()
    _write_jsonl_atomic(str(raw_data_path), final_rows)
    matched = match_data_to_stream(
        final_rows,
        " vs ".join([part for part in [str(selected_match.get("team_h", "")), str(selected_match.get("team_c", ""))] if part]),
        str(selected_match.get("gtype", "")),
        selected_match=selected_match,
    )
    _write_jsonl_atomic(str(stream_data_path), matched)
    stream_rows_written = len(matched)

    manifest_payload = rebuild_manifest_from_segments(output_dir, file_prefix, manifest, logger)
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
    stop_reason_final = str(final_status.get("stopReason", ""))
    last_error_final = str(final_status.get("lastError", ""))
    if stop_requested or stop_reason_final == "manual_stop" or stop_reason_final.startswith("signal_"):
        final_state = "stopped"
        if stop_reason_final.startswith("signal_"):
            stop_reason_final = "manual_stop"
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
        activeSegments=int((manifest_payload or {}).get("segments", []) and len((manifest_payload or {}).get("segments", [])) or 0),
        hlsPlaylist=str(final_status.get("hlsPlaylistPath", "")) or (str(hls_playlist) if hls_playlist.exists() else ""),
        hlsSegmentCount=int(final_status.get("hlsSegmentCount", 0) or 0),
    )
    logger.close()
    if merged_video or stop_requested:
        return 0
    return 0 if process.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
