#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
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

from notify_recording_summary import send_text_with_optional_media
from pion_gst_direct_chain.shared_livekit_runtime import (
    extract_best_livekit_bootstrap_for_watch_url,
    resolve_selected_matches,
)
from pion_gst_direct_chain.simple_logger import SessionLogger


DEFAULT_RUNTIME_DIR = RECORDINGS_DIR / "watch_runtime" / "pion_gst_dispatcher"
DEFAULT_DISCOVER_INTERVAL_SECONDS = 60
DEFAULT_LOOP_INTERVAL_SECONDS = 1
COMPLETED_COOLDOWN_SECONDS = 300
WORKER_RESTART_COOLDOWN_SECONDS = 15
WORKER_SPAWN_STAGGER_SECONDS = 1.0
MAX_CONCURRENT_SPAWN = 4
PENDING_WORKER_START_STATES = {"", "initializing", "starting"}
BOOTSTRAP_RETRY_LIMIT = 5
DEFAULT_PATH_PREFIX = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
]


def match_label(item: dict) -> str:
    label = " vs ".join(
        [part for part in [str(item.get("team_h", "")), str(item.get("team_c", ""))] if part]
    ).strip()
    return label or str(item.get("watch_url", "")).rstrip("/")


def now_iso() -> str:
    return datetime.now().isoformat()


def now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def sanitize_name(text: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in (text or "").strip())
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_") or "match"


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        stat = (
            subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "stat="],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            .strip()
            .upper()
        )
    except Exception:
        return True
    if "Z" in stat:
        return False
    return True


def parse_iso_ts(value: str) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        return 0.0


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def normalized_path_env(current: str | None) -> str:
    entries = list(DEFAULT_PATH_PREFIX)
    for item in str(current or "").split(":"):
        if item and item not in entries:
            entries.append(item)
    return ":".join(entries)


def load_internal_recorder_status(session_dir: str) -> dict:
    if not session_dir:
        return {}
    base = Path(session_dir)
    if not base.exists():
        return {}
    for path in sorted(base.glob("*/pion_gst_status.json")):
        payload = read_json(path)
        if payload:
            payload["_status_path"] = str(path)
            return payload
    return {}


class PionGstDispatcher:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.runtime_dir = Path(args.runtime_dir).resolve()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.status_dir = self.runtime_dir / "worker_status"
        self.status_dir.mkdir(parents=True, exist_ok=True)
        self.match_dir = self.runtime_dir / "match_payloads"
        self.match_dir.mkdir(parents=True, exist_ok=True)
        self.credentials_path = self.runtime_dir / "data_source_credentials.json"
        self.shared_betting_data_path = self.runtime_dir / "shared_betting_data.jsonl"
        self.state_path = self.runtime_dir / "dispatcher_state.json"
        self.log_path = self.runtime_dir / "dispatcher.log"
        self.logger = SessionLogger(str(self.log_path))
        self._stop = False
        self._next_discovery_at = 0.0
        self._next_worker_spawn_at = 0.0
        self._shared_poller = None
        self._shared_poller_thread = None
        self._shared_poller_rows_flushed = 0

    def ensure_shared_poller(self) -> None:
        """Start or restart the shared BettingDataPoller if credentials are available."""
        # Ensure file exists so workers can start in shared mode immediately
        if not self.shared_betting_data_path.exists():
            self.shared_betting_data_path.touch(exist_ok=True)
        if self._shared_poller is not None:
            return
        if not self.credentials_path.exists():
            return
        try:
            creds_data = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        except Exception:
            return
        cookie = creds_data.get("cookie", "")
        template = creds_data.get("template") or {}
        feed_url = creds_data.get("feed_url", "")
        if not cookie or not template:
            return
        from run_auto_capture import BettingDataPoller, ALL_GTYPES, DEFAULT_URL
        gtypes_str = str(getattr(self.args, "gtypes", "FT") or "FT")
        gtypes = [g.strip().upper() for g in gtypes_str.split(",") if g.strip()] or ALL_GTYPES
        self._shared_poller = BettingDataPoller(
            cookie, template,
            gtypes=gtypes,
            feed_url=feed_url or DEFAULT_URL,
        )
        self._shared_poller_thread = threading.Thread(
            target=self._shared_poller.start, daemon=True
        )
        self._shared_poller_thread.start()
        self.logger.log(f"共享数据采集器启动 (gtypes={gtypes}, interval=5s)")

    def flush_shared_betting_data(self) -> None:
        """Write new rows from shared poller to the shared JSONL file."""
        if self._shared_poller is None:
            return
        rows = self._shared_poller.data[self._shared_poller_rows_flushed:]
        if not rows:
            return
        try:
            with open(self.shared_betting_data_path, "a", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._shared_poller_rows_flushed += len(rows)
        except Exception as exc:
            self.logger.log(f"共享数据落盘失败: {exc}", "WARN")

    def stop_shared_poller(self) -> None:
        if self._shared_poller is not None:
            self._shared_poller.stop()
            self._shared_poller = None

    def reap_finished_children(self) -> None:
        while True:
            try:
                pid, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            except OSError:
                break
            if pid <= 0:
                break
            self.logger.log(f"回收已结束 worker pid={pid}")

    def save_credentials(self, creds: tuple) -> None:
        cookie, template, use_dashboard, feed_url, data_source = creds
        payload = {
            "cookie": cookie or "",
            "template": template or {},
            "use_dashboard": bool(use_dashboard),
            "feed_url": feed_url or "",
            "data_source": data_source or "",
            "updated_at": now_iso(),
        }
        tmp = self.credentials_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.credentials_path)

    def notifications_enabled(self) -> bool:
        return bool(str(getattr(self.args, "notify_channel", "") or "").strip() and str(getattr(self.args, "notify_target", "") or "").strip())

    def send_notification(self, message: str) -> None:
        if not self.notifications_enabled():
            return
        rc = send_text_with_optional_media(
            channel=str(self.args.notify_channel),
            target=str(self.args.notify_target),
            account=(str(getattr(self.args, "notify_account", "") or "").strip() or None),
            message=message,
        )
        if rc == 0:
            self.logger.log(f"通知已发送: {message.splitlines()[0]}")
        else:
            self.logger.log(f"通知发送失败 rc={rc}: {message.splitlines()[0]}", "WARN")

    def load_state(self) -> dict:
        if not self.state_path.exists():
            return {"updated_at": "", "workers": [], "recent_finished": [], "pending_queue": []}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("workers", [])
                payload.setdefault("recent_finished", [])
                payload.setdefault("pending_queue", [])
                return payload
            return {"updated_at": "", "workers": [], "recent_finished": [], "pending_queue": []}
        except Exception:
            return {"updated_at": "", "workers": [], "recent_finished": [], "pending_queue": []}

    def save_state(self, state: dict) -> None:
        state["updated_at"] = now_iso()
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def reconcile_state(self, state: dict) -> dict:
        active = []
        recent_finished = []
        now_ts = time.time()
        for item in state.get("workers", []):
            prev_worker_state = str(item.get("worker_state", ""))
            pid = int(item.get("pid") or 0)
            alive = process_alive(pid)
            status_path = Path(str(item.get("status_path", "")))
            worker_status = read_json(status_path)
            internal_status = load_internal_recorder_status(str(item.get("worker_session_dir", "")))
            worker_updated_ts = parse_iso_ts(str(worker_status.get("updatedAt", "")))
            internal_updated_ts = parse_iso_ts(str(internal_status.get("updatedAt", "")))
            use_internal = bool(internal_status) and (
                not worker_status
                or internal_updated_ts > worker_updated_ts
                or str(worker_status.get("state", "")).strip().lower() in PENDING_WORKER_START_STATES
            )
            if worker_status:
                item["worker_state"] = str(worker_status.get("state", item.get("worker_state", "")))
                item["worker_updated_at"] = str(worker_status.get("updatedAt", item.get("worker_updated_at", "")))
                item["worker_stop_reason"] = str(worker_status.get("stopReason", item.get("worker_stop_reason", "")))
                item["worker_session_dir"] = str(worker_status.get("sessionDir", item.get("worker_session_dir", "")))
                item["worker_merged_video"] = str(worker_status.get("mergedVideo", item.get("worker_merged_video", "")))
                item["worker_error"] = str(worker_status.get("error", item.get("worker_error", "")))
                item["matched_rows"] = int(worker_status.get("matchedRows") or item.get("matched_rows") or 0)
            if use_internal:
                item["worker_state"] = str(internal_status.get("state", item.get("worker_state", "")))
                item["worker_updated_at"] = str(internal_status.get("updatedAt", item.get("worker_updated_at", "")))
                item["worker_stop_reason"] = str(internal_status.get("stopReason", item.get("worker_stop_reason", "")))
                item["worker_error"] = str(internal_status.get("lastError", item.get("worker_error", "")))
            current_state = str(item.get("worker_state", ""))
            if (
                current_state == "recording"
                and prev_worker_state != "recording"
                and getattr(self.args, "notify_on_recording_started", False)
            ):
                self.send_notification(
                    "\n".join(
                        [
                            "开始录制",
                            f"比赛：{match_label(item)}",
                            f"联赛：{str(item.get('league', '')).strip() or '-'}",
                            f"数据：{str(item.get('data_binding_status', '')).strip() or '-'}",
                        ]
                    )
                )
            item["worker_alive"] = alive
            if alive:
                active.append(item)
                continue
            final_label = match_label(item)
            final_state = str(item.get("worker_state", "dead"))
            final_stop = str(item.get("worker_stop_reason", ""))
            final_error = str(item.get("worker_error", ""))
            recent_finished.append(
                {
                    "watch_url": item.get("watch_url", ""),
                    "finished_at": now_iso(),
                    "state": final_state,
                    "stop_reason": final_stop,
                }
            )
            self.logger.log(
                f"worker 结束 pid={pid} | state={final_state} | stop={final_stop} | {item.get('watch_url', '')}"
            )
            if final_state == "completed" and getattr(self.args, "notify_on_recording_completed", False):
                self.send_notification(
                    "\n".join(
                        [
                            "录制完成",
                            f"比赛：{final_label}",
                            f"停止原因：{final_stop or 'completed'}",
                            f"成片：{str(item.get('worker_merged_video', '')).strip() or '待检查'}",
                        ]
                    )
                )
            elif final_state in {"failed", "skipped"} and getattr(self.args, "notify_on_recording_failed", False):
                self.send_notification(
                    "\n".join(
                        [
                            "录制异常",
                            f"比赛：{final_label}",
                            f"状态：{final_state}",
                            f"停止原因：{final_stop or '-'}",
                            f"错误：{final_error or '-'}",
                        ]
                    )
                )
        trimmed_finished = []
        for item in state.get("recent_finished", []) + recent_finished:
            finished_at = item.get("finished_at", "")
            try:
                age = now_ts - datetime.fromisoformat(finished_at).timestamp()
            except Exception:
                age = COMPLETED_COOLDOWN_SECONDS + 1
            if age <= COMPLETED_COOLDOWN_SECONDS:
                trimmed_finished.append(item)
        state["workers"] = active
        state["recent_finished"] = trimmed_finished
        state.setdefault("pending_queue", [])
        return state

    def recently_finished(self, state: dict, watch_url: str) -> bool:
        target = watch_url.rstrip("/")
        for item in state.get("recent_finished", []):
            if str(item.get("watch_url", "")).rstrip("/") == target:
                return True
        return False

    def discover_matches(self) -> list[dict]:
        os.environ["MATCH_PLAN_SHARED_DATA_CREDENTIALS_FILE"] = str(self.credentials_path)
        resolve_args = argparse.Namespace(
            browser=self.args.browser,
            skip_data_binding=bool(getattr(self.args, "skip_data_binding", False)),
            allow_unbound=bool(getattr(self.args, "allow_unbound", False)),
            selected_matches_file="",
            match_query="",
            gtypes=self.args.gtypes,
            all=True,
            prestart_minutes=self.args.prestart_minutes,
            max_streams=self.args.max_streams,
        )
        selected, creds = resolve_selected_matches(resolve_args, self.logger)
        self.save_credentials(creds)
        return selected

    def spawn_worker(self, match: dict, bootstrap: dict, watch_url: str) -> dict:
        label = " vs ".join(
            [part for part in [str(match.get("team_h", "")), str(match.get("team_c", ""))] if part]
        ) or watch_url
        slug = sanitize_name(f"{match.get('gtype', 'FT')}_{label}")[:120]
        tag = now_tag()
        match_file = self.match_dir / f"{slug}__{tag}.json"
        match_file.write_text(json.dumps(match, ensure_ascii=False, indent=2), encoding="utf-8")
        status_path = self.status_dir / f"{slug}__worker.json"
        session_id = f"{sanitize_name(self.args.chain_tag)[:24]}_{tag}_{sanitize_name(label)[:24]}"
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "run_pion_gst_direct_capture.py"),
            "--browser",
            str(self.args.browser),
            "--match-file",
            str(match_file),
            "--watch-url",
            watch_url,
            "--server-host",
            str(bootstrap.get("serverHost", "")),
            "--token",
            str(bootstrap.get("token", "")),
            "--server-ms",
            str(bootstrap.get("serverMs", "lk")),
            "--status-path",
            str(status_path),
            "--data-credentials-file",
            str(self.credentials_path),
            "--segment-minutes",
            str(int(self.args.segment_minutes)),
            "--max-duration-minutes",
            str(int(self.args.max_duration_minutes)),
            "--black-screen-timeout-seconds",
            str(int(self.args.black_screen_timeout_seconds)),
            "--archive-width",
            str(int(self.args.archive_width)),
            "--archive-height",
            str(int(self.args.archive_height)),
            "--archive-bitrate-kbps",
            str(int(self.args.archive_bitrate_kbps)),
            "--hls-width",
            str(int(self.args.hls_width)),
            "--hls-height",
            str(int(self.args.hls_height)),
            "--hls-bitrate-kbps",
            str(int(self.args.hls_bitrate_kbps)),
            "--session-id",
            session_id,
            "--shared-betting-data",
            str(self.shared_betting_data_path),
        ]
        if self.args.allow_unbound:
            cmd.append("--allow-unbound")
        env = os.environ.copy()
        env["PATH"] = normalized_path_env(env.get("PATH"))
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
        worker_stderr_path = self.runtime_dir / f"worker_stderr_{slug}__{tag}.log"
        worker_stderr_fh = open(worker_stderr_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            cwd=str(RECORDINGS_DIR.parent),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=worker_stderr_fh,
            start_new_session=True,
        )
        self.logger.log(
            f"分发 Pion worker pid={proc.pid} | {label} | {bootstrap.get('serverHost', '')}"
        )
        return {
            "pid": proc.pid,
            "watch_url": watch_url,
            "server_host": str(bootstrap.get("serverHost", "")),
            "server_ms": str(bootstrap.get("serverMs", "")),
            "team_h": str(match.get("team_h", "")),
            "team_c": str(match.get("team_c", "")),
            "league": str(match.get("league", "")),
            "gtype": str(match.get("gtype", "")),
            "data_binding_status": str(match.get("data_binding_status", "")),
            "recording_note": str(match.get("recording_note", "")),
            "status_path": str(status_path),
            "match_file": str(match_file),
            "started_at": now_iso(),
            "worker_state": "starting",
            "worker_alive": True,
            "worker_stop_reason": "",
            "worker_updated_at": "",
            "worker_session_dir": "",
            "worker_merged_video": "",
            "worker_error": "",
            "matched_rows": 0,
        }

    def worker_restart_allowed(self, state: dict, watch_url: str) -> bool:
        target = watch_url.rstrip("/")
        for item in state.get("recent_finished", []):
            if str(item.get("watch_url", "")).rstrip("/") != target:
                continue
            finished_at = str(item.get("finished_at", "")).strip()
            try:
                age = time.time() - datetime.fromisoformat(finished_at).timestamp()
            except Exception:
                return True
            return age >= WORKER_RESTART_COOLDOWN_SECONDS
        return True

    def pending_worker_start_exists(self, state: dict) -> bool:
        for item in state.get("workers", []):
            if not item.get("worker_alive"):
                continue
            worker_state = str(item.get("worker_state", "")).strip().lower()
            if worker_state in PENDING_WORKER_START_STATES:
                return True
        return False

    def discover_and_queue(self, state: dict) -> dict:
        active_urls = {str(item.get("watch_url", "")).rstrip("/") for item in state.get("workers", [])}
        queued_urls = {
            str(item.get("watch_url", "")).rstrip("/")
            for item in state.get("pending_queue", [])
            if isinstance(item, dict)
        }
        selected = self.discover_matches()
        self.logger.log(f"Pion 本轮发现足球 {len(selected)} 场 | 活跃 worker {len(active_urls)} 条")
        for match in selected:
            watch_url = str(match.get("watch_url", "")).rstrip("/")
            if not watch_url:
                continue
            if watch_url in active_urls or watch_url in queued_urls:
                continue
            if self.recently_finished(state, watch_url) and not self.worker_restart_allowed(state, watch_url):
                continue
            state.setdefault("pending_queue", []).append(match)
            queued_urls.add(watch_url)
            if getattr(self.args, "notify_on_new_live", False):
                self.send_notification(
                    "\n".join(
                        [
                            "发现新直播",
                            f"比赛：{match_label(match)}",
                            f"联赛：{str(match.get('league', '')).strip() or '-'}",
                            f"数据：{str(match.get('data_binding_status', '')).strip() or ('unbound' if self.args.allow_unbound else '-')}",
                        ]
                    )
                )
        self.save_state(state)
        return state

    def dispatch_next_worker(self, state: dict) -> dict:
        if time.time() < self._next_worker_spawn_at:
            return state
        queue = state.get("pending_queue", [])
        if not queue:
            return state
        active_urls = {str(item.get("watch_url", "")).rstrip("/") for item in state.get("workers", [])}
        spawned = 0
        deferred = []
        while queue and spawned < MAX_CONCURRENT_SPAWN:
            match = queue.pop(0)
            watch_url = str(match.get("watch_url", "")).rstrip("/")
            if not watch_url or watch_url in active_urls:
                continue
            if self.recently_finished(state, watch_url) and not self.worker_restart_allowed(state, watch_url):
                continue
            try:
                bootstrap = extract_best_livekit_bootstrap_for_watch_url(
                    self.args.browser,
                    watch_url,
                    ready_tab=None,
                    logger=self.logger,
                )
            except Exception as exc:
                self.logger.log(f"Pion 提取订阅地址失败: {watch_url} | {exc}", "WARN")
                retry_count = int(match.get("_bootstrap_retry_count") or 0) + 1
                if retry_count <= BOOTSTRAP_RETRY_LIMIT:
                    match["_bootstrap_retry_count"] = retry_count
                    deferred.append(match)
                continue
            if str(bootstrap.get("serverMs", "")).strip().lower() != "lk":
                self.logger.log(f"Pion 跳过非 LiveKit 订阅地址: {watch_url}", "WARN")
                retry_count = int(match.get("_bootstrap_retry_count") or 0) + 1
                if retry_count <= BOOTSTRAP_RETRY_LIMIT:
                    match["_bootstrap_retry_count"] = retry_count
                    deferred.append(match)
                continue
            if not bootstrap.get("serverHost") or not bootstrap.get("token"):
                self.logger.log(f"Pion 订阅地址缺少凭据: {watch_url}", "WARN")
                retry_count = int(match.get("_bootstrap_retry_count") or 0) + 1
                if retry_count <= BOOTSTRAP_RETRY_LIMIT:
                    match["_bootstrap_retry_count"] = retry_count
                    deferred.append(match)
                continue
            state.setdefault("workers", []).append(self.spawn_worker(match, bootstrap, watch_url))
            active_urls.add(watch_url)
            spawned += 1
        if deferred:
            state.setdefault("pending_queue", []).extend(deferred)
        if spawned > 0:
            self._next_worker_spawn_at = time.time() + WORKER_SPAWN_STAGGER_SECONDS
        self.save_state(state)
        return state

    def stop(self, signum: int) -> None:
        self.logger.log(f"收到信号 {signum}，Pion dispatcher 退出", "WARN")
        self._stop = True

    def loop(self) -> int:
        signal.signal(signal.SIGINT, lambda s, f: self.stop(s))
        signal.signal(signal.SIGTERM, lambda s, f: self.stop(s))
        state = self.load_state()
        while not self._stop:
            self.reap_finished_children()
            state = self.reconcile_state(state)
            now_ts = time.time()
            if now_ts >= self._next_discovery_at:
                try:
                    state = self.discover_and_queue(state)
                except Exception as exc:
                    self.logger.log(f"Pion dispatcher 本轮异常: {exc}", "ERROR")
                self._next_discovery_at = now_ts + max(15, int(self.args.discover_interval_seconds))
            self.ensure_shared_poller()
            self.flush_shared_betting_data()
            state = self.dispatch_next_worker(state)
            self.save_state(state)
            if self.args.check_once:
                break
            time.sleep(max(1, int(self.args.loop_interval_seconds)))
        self.stop_shared_poller()
        self.logger.close()
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Pion + GStreamer 发现 + 分发 dispatcher")
    parser.add_argument("--browser", choices=["safari", "chrome", "app"], default="app")
    parser.add_argument("--gtypes", default="FT")
    parser.add_argument("--max-streams", type=int, default=0)
    parser.add_argument("--prestart-minutes", type=int, default=1)
    parser.add_argument("--discover-interval-seconds", type=int, default=DEFAULT_DISCOVER_INTERVAL_SECONDS)
    parser.add_argument("--loop-interval-seconds", type=int, default=DEFAULT_LOOP_INTERVAL_SECONDS)
    parser.add_argument("--segment-minutes", type=int, default=5)
    parser.add_argument("--max-duration-minutes", type=int, default=0)
    parser.add_argument("--archive-width", type=int, default=960)
    parser.add_argument("--archive-height", type=int, default=540)
    parser.add_argument("--archive-bitrate-kbps", type=int, default=5000)
    parser.add_argument("--hls-width", type=int, default=960)
    parser.add_argument("--hls-height", type=int, default=540)
    parser.add_argument("--hls-bitrate-kbps", type=int, default=3500)
    parser.add_argument("--black-screen-timeout-seconds", type=int, default=300)
    parser.add_argument("--skip-data-binding", action="store_true")
    parser.add_argument("--allow-unbound", action="store_true")
    parser.add_argument("--chain-tag", default="pgst")
    parser.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR))
    parser.add_argument("--notify-channel", default="")
    parser.add_argument("--notify-account", default="")
    parser.add_argument("--notify-target", default="")
    parser.add_argument("--notify-on-new-live", action="store_true")
    parser.add_argument("--notify-on-recording-started", action="store_true")
    parser.add_argument("--notify-on-recording-completed", action="store_true")
    parser.add_argument("--notify-on-recording-failed", action="store_true")
    parser.add_argument("--check-once", action="store_true")
    args = parser.parse_args()
    dispatcher = PionGstDispatcher(args)
    return dispatcher.loop()


if __name__ == "__main__":
    raise SystemExit(main())
