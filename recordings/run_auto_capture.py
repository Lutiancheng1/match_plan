#!/usr/bin/env python3
"""
全自动视频录制 + 秒级下注数据采集
==================================
一键流水线：
  API发现直播 → 用户选择 → 自动打开视频
  → ffmpeg录制 + 秒级赔率采集 → 分段保存
  → 黑屏/卡顿检测 → 比赛结束通知 → 合并输出

用法:
  python3 run_auto_capture.py
  python3 run_auto_capture.py --gtypes FT --max-streams 4
  python3 run_auto_capture.py --all --segment-minutes 5
"""

import argparse
import glob
import json
import math
import os
import urllib.error
import urllib.request
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── 路径设置 ─────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from recorder import ConcurrentRecorder, now_iso, now_ts
from post_match import (
    load_manifest,
    merge_segments,
    cleanup_redundant_single_segment,
    generate_analysis_copy,
    seconds_to_hms,
)
from aligner import interpolate_correction, find_video_position
from poll_get_game_list import (
    build_game_list_body, fetch_xml, parse_game_list_response,
    parse_form_body, DEFAULT_URL,
)
from auto_login import auto_login

# ─── 配置 ─────────────────────────────────────────────
SCREEN_IDX = 1
FPS = 30
OUTPUT_WIDTH = 1296
OUTPUT_HEIGHT = 576
DEFAULT_RECORDINGS_VOLUME = "/Volumes/990 PRO PCIe 4T"
DEFAULT_RECORDINGS_ROOT = os.path.join(DEFAULT_RECORDINGS_VOLUME, "match_plan_recordings")
BASE_OUTPUT_DIR = os.environ.get("MATCH_RECORDINGS_ROOT", DEFAULT_RECORDINGS_ROOT)
DATA_POLL_INTERVAL = 5.0  # Match data site native polling interval (was 1.0, caused account bans)
MAX_STREAMS = 8
SEGMENT_MINUTES = 10
MAX_DURATION_MINUTES = 180
ANALYSIS_COPY_DEFAULT = str(os.environ.get("MATCH_GENERATE_ANALYSIS_5M", "")).strip().lower() in {"1", "true", "yes", "on"}
ANALYSIS_COPY_Mbps = float(os.environ.get("MATCH_ANALYSIS_COPY_MBPS", "5"))
SCHEDULE_TIMEZONE_OFFSET_HOURS = float(os.environ.get("SF_SCHEDULE_TZ_OFFSET_HOURS", "-3"))
SCHEDULE_TIMEZONE = timezone(timedelta(hours=SCHEDULE_TIMEZONE_OFFSET_HOURS))
LIVE_DB = os.environ.get(
    "LIVE_DB",
    os.path.join(SCRIPT_DIR, "live_service_data", "history.db"),
)
LIVE_ENV = os.environ.get(
    "LIVE_ENV",
    os.path.join(SCRIPT_DIR, "live_dashboard.env"),
)
LIVE_ENV_FALLBACK = os.path.join(
    os.path.dirname(SCRIPT_DIR),
    "live_dashboard",
    "live_dashboard.env",
)
DASHBOARD_MAX_AGE_SECONDS = float(os.environ.get("DASHBOARD_MAX_AGE_SECONDS", "15"))
PREFER_DASHBOARD = str(os.environ.get("MATCH_PREFER_DASHBOARD", "")).strip().lower() in {"1", "true", "yes", "on"}
CHROME_CDP_URL = os.environ.get("CHROME_CDP_URL", "").strip()
BROWSER_DEFAULT = "safari"
BROWSER_APPS = {
    "chrome": "Google Chrome",
    "safari": "Safari",
}
BROWSER_OWNER_TOKENS = {
    "chrome": ("Chrome",),
    "safari": ("Safari",),
}
BROWSER_APP_PATHS = {
    "chrome": "/Applications/Google Chrome.app",
    "safari": "/Applications/Safari.app",
}
SCHEDULES_LIVE_URL = "https://sftraders.live/schedules/live"
SCHEDULES_URL_HINTS = (
    "sftraders.live/schedules/live",
    "sftraders.live/schedules",
)

GTYPE_LABELS = {
    "FT": "足球", "BK": "篮球", "ES": "电竞", "TN": "网球",
    "VB": "排球", "BM": "羽毛球", "TT": "乒乓球", "BS": "棒球",
    "SK": "斯诺克", "OP": "其他",
}
ALL_GTYPES = list(GTYPE_LABELS.keys())
TITLE_BAR_PHYS = 56
SINGLE_WINDOW_MAX_FILL = 0.72
TWO_WINDOW_MAX_FILL = 0.84

# 保留浏览器默认窗口尺寸，不再由脚本强制放大或重排尺寸
PRESERVE_BROWSER_DEFAULT_WINDOW_SIZE = True
SAFARI_WATCH_WINDOW_WIDTH = 544
SAFARI_WATCH_WINDOW_HEIGHT = 392
SAFARI_WATCH_WINDOW_LEFT = 80
SAFARI_WATCH_WINDOW_TOP = 80
SAFARI_WATCH_WINDOW_PADDING = 24
CDP_BASE_URLS = [u for u in [CHROME_CDP_URL] if u] or [
    "http://127.0.0.1:9222",
    "http://127.0.0.1:9223",
    "http://127.0.0.1:9333",
]
LIVE_FEED_HINTS = (
    "transform.php",
    "get_game_list",
    "get_game_more",
)

# 黑屏检测参数
BLACK_THRESHOLD = 10      # 帧平均亮度 < 10/255 判定黑屏
BLACK_CHECK_INTERVAL = 5  # 每 5 秒检测一次
BLACK_CONSECUTIVE = 3     # 连续 3 次黑屏 → 刷新页面
BLACK_MAX_RETRIES = 3     # 最多刷新 3 次

# 卡顿检测参数
FREEZE_REFRESH_THRESHOLD = 3  # 5 分钟内 3 次卡顿 → 刷新
FREEZE_ABANDON_THRESHOLD = 5  # 10 分钟内 5 次卡顿 → 放弃该路
RECOVERY_MAX_ATTEMPTS = 3
RECOVERY_REFRESH_TIMEOUT_SECONDS = 12
RECOVERY_REOPEN_TIMEOUT_SECONDS = 20
RECOVERY_COOLDOWN_SECONDS = 15
SEGMENT_ROTATE_GUARD_SECONDS = 1.0
TEAM_ALIAS_STORE = os.path.join(SCRIPT_DIR, "team_aliases.json")
LEAGUE_ALIAS_STORE = os.path.join(SCRIPT_DIR, "league_aliases.json")
TEAM_ALIAS_LEARNED_STORE = os.path.join(SCRIPT_DIR, "team_alias_learned.json")
LEAGUE_ALIAS_LEARNED_STORE = os.path.join(SCRIPT_DIR, "league_alias_learned.json")
ALIAS_AUTO_PROMOTE_HITS = 2
_TEAM_ALIAS_CACHE = None
_LEAGUE_ALIAS_CACHE = None
_TRANSLATION_PROVIDER_CACHE = None
_NLLB_RUNNER_CACHE = None
_NLLB_RUNNER_FAILED = False
NLLB_MODEL_DIR = os.environ.get(
    "MATCH_PLAN_NLLB_MODEL_DIR",
    str(Path.home() / ".cache" / "huggingface" / "nllb-200-distilled-1.3B"),
)
NLLB_ENABLED = str(os.environ.get("MATCH_PLAN_ENABLE_NLLB_FALLBACK", "1")).strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
KNOWN_LEAGUE_ALIAS_PAIRS = [
    ("FIFA Series", "国际友谊赛"),
    ("Friendlies", "国际友谊赛"),
    ("International Friendly", "国际友谊赛"),
    ("International Friendlies", "国际友谊赛"),
    ("UEFA Champions League Women", "欧洲女子冠军联赛"),
    ("UEFA Champions League Women", "欧女冠"),
    ("欧洲女子冠军联赛", "UEFA WCL"),
    ("Women's Championship", "英女冠"),
    ("Primera A", "哥伦比亚甲组联赛"),
    ("Primera Division Apertura", "乌拉圭甲组联赛"),
    ("Primera Division - Apertura", "乌拉圭甲组联赛"),
    ("League One", "英格兰甲组联赛"),
    ("League One", "英甲"),
    ("J1 League", "日职联"),
    ("Toppserien", "挪威女超"),
    ("Liga Revelação U23", "葡萄牙U23联赛"),
    ("World Cup - Qualification Intercontinental Play-offs", "世预赛洲际附加赛"),
]
AI_ALIAS_PLACEHOLDERS = {"", "unknown", "n/a", "null", "none", "...", "-", "_"}
GENERIC_CLUB_ALIASES_ZH = {"国际", "竞技"}
CLUB_NAME_HINTS = (
    "fc", "cf", "sc", "ac", "club", "city", "united", "town", "sporting",
    "athletic", "atletico", "olympique", "internacional", "deportivo",
)
COUNTRY_TEAM_HINTS_ZH = ("国家队", "男足")


# ═══════════════════════════════════════════════════════
#  日志
# ═══════════════════════════════════════════════════════

class SessionLogger:
    def __init__(self, log_path):
        self.log_path = log_path
        self._file = open(log_path, "a", encoding="utf-8")

    def log(self, msg, tag=""):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}]"
        if tag:
            line += f" [{tag}]"
        line += f" {msg}"
        print(line, flush=True)
        self._file.write(line + "\n")
        self._file.flush()

    def close(self):
        self._file.close()


def _append_jsonl(path, rows):
    """Append rows to a JSONL file. Returns number of rows written."""
    if not rows:
        return 0
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def _write_jsonl_atomic(path, rows):
    """Atomically overwrite a JSONL file with rows."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return len(rows)


# ═══════════════════════════════════════════════════════
#  凭据
# ═══════════════════════════════════════════════════════

def load_env_file(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                key = k.strip()
                value = v.strip()
                if not os.environ.get(key):
                    os.environ[key] = value


def get_browser_app(browser):
    return BROWSER_APPS.get(browser, BROWSER_APPS[BROWSER_DEFAULT])


def browser_owner_matches(owner_name, browser):
    owner = owner_name or ""
    return any(token in owner for token in BROWSER_OWNER_TOKENS.get(browser, ()))


def fetch_fresh_dashboard_payload(logger=None, max_age_seconds=DASHBOARD_MAX_AGE_SECONDS):
    payload = fetch_dashboard("/api/latest.json")
    if not payload or not payload.get("feeds"):
        return None

    snapshot_time = payload.get("snapshot_time", "")
    if not snapshot_time:
        if logger:
            logger.log("本地看板缺少 snapshot_time，视为不可用", "WARN")
        return None

    try:
        snapshot_dt = datetime.fromisoformat(snapshot_time.replace("Z", "+00:00"))
        if snapshot_dt.tzinfo is None:
            snapshot_dt = snapshot_dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - snapshot_dt.astimezone(timezone.utc)).total_seconds()
    except Exception:
        if logger:
            logger.log("本地看板 snapshot_time 无法解析，视为不可用", "WARN")
        return None

    if age > max_age_seconds:
        if logger:
            logger.log(f"本地看板快照已过期 {age:.1f}s，回退直连数据源", "WARN")
        return None

    return payload


def iter_dashboard_running_fields(payload, gtypes=None):
    feeds = (payload or {}).get("feeds", {}) or {}
    for gtype in (gtypes or ALL_GTYPES):
        feed = feeds.get(gtype, {}) or {}
        parsed = feed.get("parsed", {}) or {}
        for game in parsed.get("games", []) or []:
            fields = game.get("fields", {}) or {}
            if fields.get("RUNNING") == "Y":
                yield gtype, fields


def dashboard_available(logger=None) -> bool:
    return bool(fetch_fresh_dashboard_payload(logger))


def login_with_env_credentials(logger):
    username = os.environ.get("LOGIN_USERNAME", "")
    password = os.environ.get("LOGIN_PASSWORD", "")
    if not username or not password:
        return None

    logger.log(f"正在登录 (user={username})...")
    try:
        creds = auto_login(username, password)
        logger.log(f"登录成功: uid={creds.get('uid', '')}")
        return (
            creds["cookie"],
            parse_form_body(creds["body_template"]),
            DEFAULT_URL,
            "env_login",
        )
    except Exception as e:
        logger.log(f"登录失败: {e}", "WARN")
        return None


def _fetch_proxy_credentials(logger, refresh=False):
    """Try to get credentials from the data_site_proxy /credentials endpoint.
    If refresh=True, asks proxy to re-login first.
    Returns (cookie, template_dict, feed_url, source_name) or None."""
    import urllib.request
    endpoint = "/credentials/refresh" if refresh else "/credentials"
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:18780{endpoint}",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8 if refresh else 2) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
        cookie = data.get("cookie", "")
        body_template = data.get("body_template", "")
        if not cookie:
            return None
        template = parse_form_body(body_template) if body_template else {}
        action = "刷新" if refresh else "复用"
        logger.log(f"{action}数据站代理 session (proxy {endpoint})")
        return cookie, template, DEFAULT_URL, "proxy_shared"
    except Exception:
        return None


def _load_shared_credentials_file(logger):
    """Try to load credentials from MATCH_PLAN_SHARED_DATA_CREDENTIALS_FILE.
    Returns (cookie, template_dict, feed_url, source_name) or None."""
    shared_path = os.environ.get("MATCH_PLAN_SHARED_DATA_CREDENTIALS_FILE", "").strip()
    if not shared_path:
        return None
    try:
        import pathlib
        p = pathlib.Path(shared_path)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        cookie = data.get("cookie", "")
        if not cookie:
            return None
        template = data.get("template") or {}
        feed_url = data.get("feed_url", "") or DEFAULT_URL
        source = data.get("data_source", "") or "shared_file"
        logger.log(f"复用共享凭证文件: {shared_path}")
        return cookie, template, feed_url, source
    except Exception:
        return None


def bootstrap_credentials(logger, browser):
    """返回 (cookie, body_template_dict, use_dashboard_api, feed_url, source_name)"""
    load_env_file(LIVE_ENV)
    load_env_file(LIVE_ENV_FALLBACK)

    if browser == "chrome":
        browser_source = BrowserSessionDataSource.discover(logger)
        if browser_source:
            logger.log(
                f"检测到浏览器会话数据源: {browser_source.feed_url} "
                f"(target={browser_source.target_url})"
            )
            return (
                browser_source.cookie,
                browser_source.template,
                False,
                browser_source.feed_url,
                "browser_session",
            )
    else:
        logger.log("App 内嵌模式: 优先复用数据站代理 session")

    # Priority 1: reuse cookie from the App's data_site_proxy (no new login)
    proxy_creds = _fetch_proxy_credentials(logger)
    if proxy_creds:
        cookie, template, feed_url, source_name = proxy_creds
        return cookie, template, False, feed_url, source_name

    # Priority 2: reuse cookie from shared credentials file (written by
    # the dispatcher after a previous successful login)
    shared_creds = _load_shared_credentials_file(logger)
    if shared_creds:
        cookie, template, feed_url, source_name = shared_creds
        return cookie, template, False, feed_url, source_name

    # Priority 3: dashboard API
    if PREFER_DASHBOARD and dashboard_available(logger):
        fallback = login_with_env_credentials(logger)
        if fallback:
            cookie, template, feed_url, _ = fallback
            logger.log("检测到本地看板 API 可用，优先使用 dashboard 数据源，并已准备直连备用凭据")
            return cookie, template, True, feed_url, "dashboard+env_login"
        logger.log("检测到本地看板 API 可用，优先使用 dashboard 数据源")
        return None, None, True, None, "dashboard"

    if not os.environ.get("LOGIN_USERNAME", "") or not os.environ.get("LOGIN_PASSWORD", ""):
        logger.log("未配置凭据，使用本地看板 API", "WARN")
        return None, None, True, None, "dashboard"

    # Priority 4: fresh login (last resort — creates a new session)
    logger.log("无可复用 session，执行 auto_login (会使旧 session 失效)", "WARN")
    fallback = login_with_env_credentials(logger)
    if fallback:
        cookie, template, feed_url, source_name = fallback
        return cookie, template, False, feed_url, source_name

    if dashboard_available(logger):
        logger.log("直连登录失败，回退本地看板 API", "WARN")
        return None, None, True, None, "dashboard"

    logger.log("直连登录失败，使用本地看板 API", "WARN")
    return None, None, True, None, "dashboard"


def fetch_json_url(url, timeout=3):
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def find_cdp_base_url():
    for base_url in CDP_BASE_URLS:
        payload = fetch_json_url(f"{base_url}/json/version")
        if payload and payload.get("Browser"):
            return base_url
    return None


def list_cdp_targets(base_url):
    payload = fetch_json_url(f"{base_url}/json/list")
    return payload if isinstance(payload, list) else []


def choose_cdp_target(base_url):
    targets = list_cdp_targets(base_url)
    schedules = [
        t for t in targets
        if t.get("type") == "page" and "sftraders.live/schedules" in t.get("url", "")
    ]
    if schedules:
        return schedules[0]
    watches = [
        t for t in targets
        if t.get("type") == "page" and "/watch" in t.get("url", "")
    ]
    if watches:
        return watches[0]
    return None


def build_cookie_header(cookies):
    return "; ".join(
        f"{cookie.get('name', '')}={cookie.get('value', '')}"
        for cookie in cookies
        if cookie.get("name")
    )


def is_live_feed_request(url, post_data):
    text = f"{url or ''}\n{post_data or ''}".lower()
    if not any(hint in text for hint in LIVE_FEED_HINTS):
        return False
    return any(flag in text for flag in ("showtype=live", "rtype=rb", "gtype="))


class ChromeCDPClient:
    def __init__(self, websocket_url, timeout=5):
        import websocket

        self._websocket = websocket.create_connection(
            websocket_url,
            timeout=timeout,
            enable_multithread=True,
        )
        self._websocket.settimeout(1.0)
        self._next_id = 0

    def close(self):
        try:
            self._websocket.close()
        except Exception:
            pass

    def send(self, method, params=None):
        self._next_id += 1
        msg_id = self._next_id
        self._websocket.send(json.dumps({
            "id": msg_id,
            "method": method,
            "params": params or {},
        }))
        while True:
            raw = self._websocket.recv()
            payload = json.loads(raw)
            if payload.get("id") != msg_id:
                continue
            if "error" in payload:
                raise RuntimeError(payload["error"])
            return payload.get("result", {})

    def recv_event(self, timeout=1.0):
        previous_timeout = self._websocket.gettimeout()
        self._websocket.settimeout(timeout)
        try:
            while True:
                try:
                    raw = self._websocket.recv()
                except Exception as exc:
                    if "timed out" in str(exc).lower():
                        return None
                    raise
                payload = json.loads(raw)
                if payload.get("method"):
                    return payload
        finally:
            self._websocket.settimeout(previous_timeout)


class BrowserSessionDataSource:
    def __init__(self, cookie, template, feed_url, base_url, target_url):
        self.cookie = cookie
        self.template = template
        self.feed_url = feed_url
        self.base_url = base_url
        self.target_url = target_url

    @classmethod
    def discover(cls, logger, timeout=12):
        base_url = find_cdp_base_url()
        if not base_url:
            logger.log(
                "浏览器会话数据源不可用: 未检测到 Chrome CDP 端口。将回退到 dashboard/env。",
                "WARN",
            )
            return None

        target = choose_cdp_target(base_url)
        if not target or not target.get("webSocketDebuggerUrl"):
            logger.log(
                "浏览器会话数据源不可用: 未找到 sftraders.live schedules/watch 页的 CDP target。",
                "WARN",
            )
            return None

        client = ChromeCDPClient(target["webSocketDebuggerUrl"])
        try:
            client.send("Network.enable", {"maxPostDataSize": 65536})
            client.send("Page.enable")
            client.send("Network.setCacheDisabled", {"cacheDisabled": True})

            candidate = None
            for should_reload in (False, True):
                if should_reload and "schedules" in target.get("url", ""):
                    logger.log("浏览器会话数据源: 正在通过 CDP 重新加载 schedules 页以捕获数据请求")
                    client.send("Page.reload", {"ignoreCache": True})

                deadline = time.time() + timeout / 2
                while time.time() < deadline:
                    try:
                        event = client.recv_event(timeout=1.0)
                    except Exception:
                        break
                    if not event:
                        continue

                    if event.get("method") != "Network.requestWillBeSent":
                        continue
                    params = event.get("params", {})
                    request = params.get("request", {})
                    request_id = params.get("requestId")
                    post_data = request.get("postData", "")
                    if not post_data and request_id:
                        try:
                            post_data = client.send(
                                "Network.getRequestPostData",
                                {"requestId": request_id},
                            ).get("postData", "")
                        except Exception:
                            post_data = ""
                    if not is_live_feed_request(request.get("url", ""), post_data):
                        continue

                    headers = request.get("headers", {}) or {}
                    cookie = headers.get("Cookie") or headers.get("cookie") or ""
                    if not cookie:
                        try:
                            cookies = client.send(
                                "Network.getCookies",
                                {"urls": [request.get("url", "")]},
                            ).get("cookies", [])
                            cookie = build_cookie_header(cookies)
                        except Exception:
                            cookie = ""
                    if not cookie or not post_data:
                        candidate = candidate or (cookie, post_data, request.get("url", ""))
                        continue
                    template = parse_form_body(post_data)
                    if template:
                        return cls(
                            cookie=cookie,
                            template=template,
                            feed_url=request.get("url", ""),
                            base_url=base_url,
                            target_url=target.get("url", ""),
                        )
                if candidate and candidate[0] and candidate[1]:
                    template = parse_form_body(candidate[1])
                    if template:
                        return cls(
                            cookie=candidate[0],
                            template=template,
                            feed_url=candidate[2],
                            base_url=base_url,
                            target_url=target.get("url", ""),
                        )
            logger.log("浏览器会话数据源: 未能从 CDP 捕获到可复用的赔率请求", "WARN")
            return None
        except Exception as e:
            logger.log(f"浏览器会话数据源初始化失败: {e}", "WARN")
            return None
        finally:
            client.close()


# ═══════════════════════════════════════════════════════
#  发现直播比赛
# ═══════════════════════════════════════════════════════

def fetch_dashboard(path):
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:8765{path}", timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def discover_live_matches(cookie, template, gtypes=None, logger=None, use_dashboard=False, feed_url=DEFAULT_URL):
    if gtypes is None:
        gtypes = ALL_GTYPES
    if logger is None:
        class _:
            def log(self, *a, **kw): pass
        logger = _()

    if use_dashboard:
        payload = fetch_fresh_dashboard_payload(logger)
        if not payload and (not cookie or not template):
            return {}
        if not payload:
            logger.log("看板数据不可用，发现阶段回退直连数据源", "WARN")
        else:
            all_matches = {}
            for gtype, fields in iter_dashboard_running_fields(payload, gtypes):
                all_matches.setdefault(gtype, []).append({
                    "gid": fields.get("GID", ""),
                    "ecid": fields.get("ECID", ""),
                    "gtype": gtype,
                    "league": fields.get("LEAGUE", "") or fields.get("LID", ""),
                    "team_h": fields.get("TEAM_H", ""),
                    "team_c": fields.get("TEAM_C", ""),
                    "score_h": fields.get("SCORE_H", ""),
                    "score_c": fields.get("SCORE_C", ""),
                })

            total = sum(len(v) for v in all_matches.values())
            if total > 0:
                logger.log(f"看板 API: 发现 {total} 场直播: "
                           f"{', '.join(f'{k}({len(v)})' for k,v in sorted(all_matches.items()))}")
                return all_matches
            if not cookie or not template:
                logger.log("看板快照存在，但本轮没有可用直播且无直连凭据", "WARN")
                return {}
            logger.log("看板快照本轮无直播，发现阶段回退直连数据源", "WARN")

    all_matches = {}
    for gtype in gtypes:
        try:
            body = build_game_list_body(template, gtype=gtype, showtype="live", rtype="rb")
            raw = fetch_xml(feed_url, body, cookie, timeout=10)
            parsed = parse_game_list_response(raw)
            games = parsed.get("games", [])
            live = [g for g in games if g.get("fields", {}).get("RUNNING") == "Y"]
            if live:
                all_matches[gtype] = [{
                    "gid": g["fields"].get("GID", ""),
                    "ecid": g["fields"].get("ECID", ""),
                    "gtype": gtype,
                    "league": g["fields"].get("LEAGUE", "") or g["fields"].get("LID", ""),
                    "team_h": g["fields"].get("TEAM_H", ""),
                    "team_c": g["fields"].get("TEAM_C", ""),
                    "score_h": g["fields"].get("SCORE_H", ""),
                    "score_c": g["fields"].get("SCORE_C", ""),
                } for g in live]
        except Exception as e:
            logger.log(f"[{gtype}] 获取失败: {e}")

    total = sum(len(v) for v in all_matches.values())
    logger.log(f"API: 发现 {total} 场直播: "
               f"{', '.join(f'{k}({len(v)})' for k,v in sorted(all_matches.items()))}")
    return all_matches


# ═══════════════════════════════════════════════════════
#  交互式选择
# ═══════════════════════════════════════════════════════

def let_user_select(all_matches, max_streams):
    if not all_matches:
        print("没有直播比赛。")
        return []

    print(f"\n发现 {sum(len(v) for v in all_matches.values())} 场直播比赛：\n")
    idx = 1
    flat = []
    for gtype in sorted(all_matches.keys()):
        matches = all_matches[gtype]
        print(f"  [{gtype}] {GTYPE_LABELS.get(gtype, gtype)} ({len(matches)}场)")
        for m in matches:
            score = f"{m['score_h']}-{m['score_c']}" if m['score_h'] else ""
            print(f"    {idx:3d}. {m['team_h']} vs {m['team_c']}"
                  f"  ({m['league']})  {score}")
            m["_idx"] = idx
            flat.append(m)
            idx += 1
        print()

    print(f"选择要录制的比赛 (最多 {max_streams} 路):")
    print("  运动代码: FT, BK, TT ...   序号: 1,3,5   all: 全部   q: 退出\n")

    while True:
        choice = input("你的选择: ").strip().upper()
        if choice == "Q":
            return []
        if choice == "ALL":
            return flat[:max_streams]

        selected, errors = [], []
        for p in [x.strip() for x in choice.split(",")]:
            if p in all_matches:
                selected.extend(all_matches[p])
            elif p.isdigit():
                for m in flat:
                    if m.get("_idx") == int(p):
                        selected.append(m)
                        break
                else:
                    errors.append(p)
            else:
                errors.append(p)

        if errors:
            print(f"  无法识别: {', '.join(errors)}")
        if not selected:
            print("  未选择，请重试。")
            continue

        seen, unique = set(), []
        for m in selected:
            k = m["gid"]
            if k and k not in seen:
                seen.add(k); unique.append(m)
            elif not k:
                unique.append(m)
        unique = unique[:max_streams]
        print(f"\n已选择 {len(unique)} 场:")
        for m in unique:
            print(f"  - [{m['gtype']}] {m['team_h']} vs {m['team_c']}")
        return unique


# ═══════════════════════════════════════════════════════
#  打开视频窗口
# ═══════════════════════════════════════════════════════

def get_all_browser_window_ids(browser):
    try:
        import Quartz
    except ImportError:
        return set()
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    return {w.get("kCGWindowNumber") for w in wins
            if browser_owner_matches(w.get("kCGWindowOwnerName", ""), browser)}


def get_front_browser_tab_url(browser):
    app = get_browser_app(browser)
    if browser == "safari":
        script = f'tell application "{app}" to get URL of current tab of front window'
    else:
        script = f'tell application "{app}" to get URL of active tab of front window'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_browser_tab_info(browser, window_index, tab_index=None):
    app = get_browser_app(browser)
    try:
        if browser == "safari":
            target = (
                f"tab {tab_index} of window {window_index}"
                if tab_index else f"current tab of window {window_index}"
            )
        else:
            target = (
                f"tab {tab_index} of window {window_index}"
                if tab_index else f"active tab of window {window_index}"
            )
        script = f'''
            tell application "{app}"
                try
                    set currentUrl to URL of {target}
                on error
                    set currentUrl to ""
                end try
                try
                    set currentTitle to name of {target}
                on error
                    set currentTitle to ""
                end try
                return currentUrl & linefeed & currentTitle
            end tell
        '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"url": "", "title": ""}
        lines = result.stdout.splitlines()
        url = lines[0].strip() if lines else ""
        title = lines[1].strip() if len(lines) > 1 else ""
        return {"url": url, "title": title}
    except Exception:
        return {"url": "", "title": ""}


def list_sftraders_tabs(browser):
    app = get_browser_app(browser)
    try:
        script = '\n'.join([
            f'tell application "{app}"',
            '    set out to ""',
            '    repeat with wIndex from 1 to (count windows)',
            '        repeat with tIndex from 1 to (count tabs of window wIndex)',
            '            set u to URL of tab tIndex of window wIndex',
            '            if u contains "sftraders.live" then',
            '                set out to out & (wIndex as text) & "|" & (tIndex as text) & "|" & u & linefeed',
            '            end if',
            '        end repeat',
            '    end repeat',
            '    return out',
            'end tell'
        ])
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        tabs = []
        for line in result.stdout.splitlines():
            parts = line.split("|", 2)
            if len(parts) != 3:
                continue
            try:
                window_index = int(parts[0])
                tab_index = int(parts[1])
            except ValueError:
                continue
            tabs.append({
                "window_index": window_index,
                "tab_index": tab_index,
                "url": parts[2].strip(),
            })
        return tabs
    except Exception:
        return []


def activate_browser_tab(window_index, tab_index, browser, bring_to_front=False):
    app = get_browser_app(browser)
    try:
        if browser == "safari":
            lines = [
                f'tell application "{app}"',
                f'    set current tab of window {window_index} to tab {tab_index} of window {window_index}',
            ]
        else:
            lines = [
                f'tell application "{app}"',
                f'    set active tab index of window {window_index} to {tab_index}',
            ]
        if bring_to_front:
            lines.extend([
                f'    set index of window {window_index} to 1',
                '    activate',
            ])
        lines.append('end tell')
        subprocess.run(
            ["osascript", "-e", '\n'.join(lines)],
            capture_output=True, timeout=10,
        )
        return True
    except Exception:
        return False


def find_schedules_tab(browser):
    tabs = list_sftraders_tabs(browser)
    schedules_tabs = [
        tab for tab in tabs
        if any(marker in tab["url"] for marker in SCHEDULES_URL_HINTS)
    ]
    return schedules_tabs[0] if schedules_tabs else None


def open_schedules_live_page(logger, browser):
    app = get_browser_app(browser)
    existing = find_schedules_tab(browser)
    if existing:
        activate_browser_tab(
            existing["window_index"],
            existing["tab_index"],
            browser,
            bring_to_front=True,
        )
        logger.log(f"已切到现有 {app} 的 schedules/live 页面")
        return True

    escaped = applescript_quote(SCHEDULES_LIVE_URL)
    try:
        if browser == "safari":
            script = f'''
                tell application "{app}"
                    activate
                    make new document with properties {{URL:"{escaped}"}}
                end tell
            '''
        else:
            script = f'''
                tell application "{app}"
                    activate
                    set newWindow to make new window
                    set URL of active tab of newWindow to "{escaped}"
                end tell
            '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            logger.log(f"打开 schedules/live 失败: {detail[:160]}", "ERROR")
            return False
        logger.log(f"已在 {app} 打开 {SCHEDULES_LIVE_URL}")
        return True
    except Exception as e:
        logger.log(f"打开 schedules/live 失败: {e}", "ERROR")
        return False


def list_watch_tabs(browser):
    app = get_browser_app(browser)
    title_expr = "name of t" if browser == "safari" else "title of t"
    try:
        script = '\n'.join([
            f'tell application "{app}"',
            '    set out to ""',
            '    repeat with wIndex from 1 to (count windows)',
            '        set wId to id of window wIndex',
            '        repeat with tIndex from 1 to (count tabs of window wIndex)',
            '            set t to tab tIndex of window wIndex',
            '            set u to URL of t',
            '            if u contains "/watch" then',
            f'                set out to out & (wIndex as text) & "|" & (wId as text) & "|" & (tIndex as text) & "|" & u & "|" & ({title_expr}) & linefeed',
            '            end if',
            '        end repeat',
            '    end repeat',
            '    return out',
            'end tell'
        ])
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        tabs = []
        for line in result.stdout.splitlines():
            parts = line.split("|", 4)
            if len(parts) != 5:
                continue
            try:
                window_index = int(parts[0])
                window_id = int(parts[1])
                tab_index = int(parts[2])
            except ValueError:
                continue
            tabs.append({
                "window_index": window_index,
                "window_id": window_id,
                "tab_index": tab_index,
                "url": parts[3].strip(),
                "title": parts[4].strip(),
            })
        return tabs
    except Exception:
        return []


def ready_tabs_to_window_ids(ready_tabs):
    return {
        int(tab.get("window_id"))
        for tab in (ready_tabs or [])
        if str(tab.get("window_id", "")).isdigit()
    }


def get_watch_playback_state(window_index, tab_index, browser):
    app = get_browser_app(browser)
    js = applescript_quote(
        'JSON.stringify({'
        'title: document.title, '
        'ready: document.readyState, '
        'outerWidth: window.outerWidth, '
        'outerHeight: window.outerHeight, '
        'innerWidth: window.innerWidth, '
        'innerHeight: window.innerHeight, '
        'videos: Array.from(document.querySelectorAll("video")).map(v => ({'
        'paused: v.paused, '
        'readyState: v.readyState, '
        'currentTime: v.currentTime, '
        'ended: v.ended, '
        'width: v.videoWidth, '
        'height: v.videoHeight, '
        'rect: (() => { const r = v.getBoundingClientRect(); return {x: r.x, y: r.y, width: r.width, height: r.height}; })(), '
        'display: (() => { '
        '  const r = v.getBoundingClientRect(); '
        '  const aspect = (v.videoWidth && v.videoHeight) ? (v.videoWidth / v.videoHeight) : (16 / 9); '
        '  let dw = r.width; '
        '  let dh = r.height; '
        '  let dx = r.x; '
        '  let dy = r.y; '
        '  if ((r.width / r.height) > aspect) { '
        '    dh = r.height; '
        '    dw = dh * aspect; '
        '    dx = r.x + (r.width - dw) / 2; '
        '  } else { '
        '    dw = r.width; '
        '    dh = dw / aspect; '
        '    dy = r.y + (r.height - dh) / 2; '
        '  } '
        '  return {x: dx, y: dy, width: dw, height: dh, aspect: aspect}; '
        '})()'
        '}))'
        '})'
    )
    try:
        if browser == "safari":
            script = f'''
                tell application "{app}"
                    set js to "{js}"
                    return do JavaScript js in tab {tab_index} of window {window_index}
                end tell
            '''
        else:
            script = f'''
                tell application "{app}"
                    set js to "{js}"
                    return execute tab {tab_index} of window {window_index} javascript js
                end tell
            '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return json.loads(result.stdout.strip())
    except Exception:
        return None


def state_has_active_playback(state):
    videos = state.get("videos", []) if isinstance(state, dict) else []
    return any(
        (not v.get("paused", True))
        and not v.get("ended", False)
        and float(v.get("currentTime", 0) or 0) > 0.5
        and int(v.get("readyState", 0) or 0) >= 3
        for v in videos
    )


def compute_page_content_rect(state):
    outer_w = max(0.0, float(state.get("outerWidth", 0) or 0))
    outer_h = max(0.0, float(state.get("outerHeight", 0) or 0))
    inner_w = max(0.0, float(state.get("innerWidth", 0) or 0))
    inner_h = max(0.0, float(state.get("innerHeight", 0) or 0))
    if inner_w < 50 or inner_h < 50:
        return None

    chrome_extra_w = max(0.0, outer_w - inner_w)
    chrome_extra_h = max(0.0, outer_h - inner_h)
    border_x = chrome_extra_w / 2.0
    border_bottom = min(border_x, chrome_extra_h)
    chrome_top = max(0.0, chrome_extra_h - border_bottom)
    page_rect = {
        "left": round(border_x, 3),
        "top": round(chrome_top, 3),
        "width": round(inner_w, 3),
        "height": round(inner_h, 3),
    }
    return page_rect


def build_ready_watch_tab(tab, state):
    videos = state.get("videos", []) if isinstance(state, dict) else []
    first_video = videos[0] if videos else {}
    display = first_video.get("display", {}) or {}
    rect = first_video.get("rect", {}) or {}
    outer_w = float(state.get("outerWidth", 0) or 0)
    outer_h = float(state.get("outerHeight", 0) or 0)
    inner_w = float(state.get("innerWidth", 0) or 0)
    inner_h = float(state.get("innerHeight", 0) or 0)
    rect_w = float(rect.get("width", 0) or 0)
    rect_h = float(rect.get("height", 0) or 0)
    page_rect = compute_page_content_rect(state)
    return {
        **tab,
        "title": state.get("title", tab.get("title", "")),
        "layout": {
            "video_aspect": float(display.get("aspect", 16 / 9) or (16 / 9)),
            "extra_w": max(0.0, outer_w - rect_w),
            "extra_h": max(120.0, outer_h - rect_h),
            "page_content_rect": page_rect,
            "page_inner_w": inner_w,
            "page_inner_h": inner_h,
        },
    }


def wait_for_watch_playback(urls, logger, browser, timeout=45):
    target_urls = {url.rstrip("/") for url in urls}
    ready = {}
    last_seen_state = {}
    deadline = time.time() + timeout

    while time.time() < deadline:
        for tab in list_watch_tabs(browser):
            url = tab["url"].rstrip("/")
            if url not in target_urls or url in ready:
                continue
            state = get_watch_playback_state(tab["window_index"], tab["tab_index"], browser)
            if not state:
                continue
            last_seen_state[url] = {
                "title": state.get("title", tab.get("url", url)),
                "ready": state.get("ready", ""),
                "videos": len(state.get("videos", []) if isinstance(state, dict) else []),
            }
            if state_has_active_playback(state):
                ready[url] = build_ready_watch_tab(tab, state)
                logger.log(f"播放已开始: {state.get('title', tab.get('url', url))}")
        if len(ready) == len(target_urls):
            return [ready[url.rstrip("/")] for url in urls if url.rstrip("/") in ready]
        time.sleep(2)

    missing = [url for url in urls if url.rstrip("/") not in ready]
    if missing:
        details = []
        for url in missing[:3]:
            info = last_seen_state.get(url.rstrip("/"), {})
            details.append(
                f"{info.get('title', url)}(ready={info.get('ready','?')}, videos={info.get('videos','?')})"
            )
        detail_text = "；".join(details) if details else ", ".join(missing[:3])
        logger.log(
            f"等待视频开始播放超时: {detail_text}，继续用已就绪的 {len(ready)} 个窗口",
            "WARN",
        )
    if ready:
        return [ready[url.rstrip("/")] for url in urls if url.rstrip("/") in ready]
    return []


def arrange_watch_windows(tabs, logger, browser, padding=24):
    if not tabs:
        return
    if PRESERVE_BROWSER_DEFAULT_WINDOW_SIZE:
        logger.log("保留浏览器默认窗口尺寸，不再调整视频窗口大小")
        return
    try:
        import Quartz
    except ImportError:
        logger.log("无法排布窗口: PyObjC 不可用", "WARN")
        return

    screens = Quartz.NSScreen.screens()
    if len(screens) <= SCREEN_IDX:
        logger.log("无法排布窗口: 未检测到副屏", "WARN")
        return

    frame = screens[SCREEN_IDX].visibleFrame()
    total = len(tabs)
    sample_layout = next((tab.get("layout") for tab in tabs if tab.get("layout")), {}) or {}
    target_aspect = float(sample_layout.get("video_aspect", 16 / 9) or (16 / 9))
    extra_w = max(0.0, float(sample_layout.get("extra_w", 0) or 0))
    extra_h = max(120.0, float(sample_layout.get("extra_h", 161) or 161))
    if total <= 1:
        max_fill = SINGLE_WINDOW_MAX_FILL
    elif total == 2:
        max_fill = TWO_WINDOW_MAX_FILL
    else:
        max_fill = 1.0
    max_window_w = int(frame.size.width * max_fill)
    max_window_h = int(frame.size.height * max_fill)
    best = None

    for cols in range(1, total + 1):
        rows = math.ceil(total / cols)
        usable_w = max(200, int(frame.size.width) - padding * (cols + 1))
        usable_h = max(200, int(frame.size.height) - padding * (rows + 1))
        cell_box_w = usable_w / cols
        cell_box_h = usable_h / rows

        display_w = min(max(320.0, cell_box_w - extra_w), max(320.0, (cell_box_h - extra_h) * target_aspect))
        display_h = display_w / target_aspect
        width = display_w + extra_w
        height = display_h + extra_h
        width = min(width, max_window_w)
        height = min(height, max_window_h)
        if width > extra_w and height > extra_h:
            display_w = width - extra_w
            display_h = height - extra_h
            fitted_w = min(display_w, display_h * target_aspect)
            fitted_h = fitted_w / target_aspect
            width = fitted_w + extra_w
            height = fitted_h + extra_h
        if width > cell_box_w or height > cell_box_h:
            continue

        score = display_w * display_h
        candidate = {
            "cols": cols,
            "rows": rows,
            "width": max(320, int(width)),
            "height": max(220, int(height)),
            "score": score,
        }
        if best is None or candidate["score"] > best["score"]:
            best = candidate

    if best is None:
        logger.log("无法计算合适的横屏布局，保留当前窗口尺寸", "WARN")
        return

    cols = best["cols"]
    rows = best["rows"]
    cell_w = best["width"]
    cell_h = best["height"]
    usable_w = int(frame.size.width) - padding * (cols + 1)
    usable_h = int(frame.size.height) - padding * (rows + 1)
    cell_box_w = usable_w // cols
    cell_box_h = usable_h // rows

    for idx, tab in enumerate(tabs):
        row = idx // cols
        col = idx % cols
        slot_left = int(frame.origin.x + padding + col * (cell_box_w + padding))
        slot_top = int(frame.origin.y + padding + row * (cell_box_h + padding))
        left = slot_left + max(0, (cell_box_w - cell_w) // 2)
        top = slot_top + max(0, (cell_box_h - cell_h) // 2)
        right = left + cell_w
        bottom = top + cell_h
        try:
            subprocess.run(
                ["osascript", "-e", '\n'.join([
                    f'tell application "{get_browser_app(browser)}"',
                    f'    set bounds of window {tab["window_index"]} to {{{left}, {top}, {right}, {bottom}}}',
                    'end tell'
                ])],
                capture_output=True, timeout=10,
            )
        except Exception as e:
            logger.log(f"排布窗口失败: {tab.get('url', '')} ({e})", "WARN")
    logger.log(f"已在副屏排布 {total} 个录制窗口 ({cols}x{rows}, {cell_w}x{cell_h})")


def get_screen_capture_index(screens, preferred_idx=SCREEN_IDX):
    if not screens:
        return None
    if len(screens) > preferred_idx:
        return preferred_idx
    return 0


def ensure_schedules_live_ready(logger, browser):
    """确保浏览器停在 schedules/live 页面；不额外做登录态确认。"""
    tabs = list_sftraders_tabs(browser)
    browser_name = get_browser_app(browser)
    if not tabs:
        logger.log(
            f"未找到 {browser_name} 的 sftraders.live 标签页，直接打开 {SCHEDULES_LIVE_URL}"
        )
        if not open_schedules_live_page(logger, browser):
            return False
        time.sleep(3)
        tabs = list_sftraders_tabs(browser)
        if not tabs:
            logger.log(f"仍未识别到 {browser_name} 的 sftraders.live 标签页", "ERROR")
            return False

    schedules_tabs = [
        tab for tab in tabs
        if any(marker in tab["url"] for marker in SCHEDULES_URL_HINTS)
    ]
    if not schedules_tabs:
        logger.log(f"当前未停在 schedules 页面，直接打开 {SCHEDULES_LIVE_URL}")
        if not open_schedules_live_page(logger, browser):
            return False
        time.sleep(3)
        tabs = list_sftraders_tabs(browser)
        schedules_tabs = [
            tab for tab in tabs
            if any(marker in tab["url"] for marker in SCHEDULES_URL_HINTS)
        ]
    if not schedules_tabs:
        logger.log(f"未能进入 schedules/live，请检查 {browser_name} 当前页面是否可访问", "ERROR")
        return False
    first_tab = schedules_tabs[0]
    activate_browser_tab(
        first_tab["window_index"],
        first_tab["tab_index"],
        browser,
        bring_to_front=True,
    )
    return True


def build_watch_link_js():
    return (
        'JSON.stringify((() => {'
        '  const liveTab = document.getElementById("schedules-content-live-tab");'
        '  if (liveTab) { liveTab.click(); }'
        '  const livePane = document.getElementById("schedules-content-live");'
        '  if (!livePane) { return {error: "NO_LIVE_PANE"}; }'
        '  const rows = Array.from(livePane.querySelectorAll("tr, .has-data, .event-row, .match-row, li"))'
        '    .filter(el => el.querySelector(\'a[href*="/watch"]\'));'
        '  const seen = new Set();'
        '  const items = [];'
        '  for (const row of rows) {'
        '    const link = row.querySelector(\'a[href*="/watch"]\');'
        '    if (!link || !link.href || link.href.includes("withChart=1") || seen.has(link.href)) { continue; }'
        '    seen.add(link.href);'
        '    const parts = (row.innerText || "")'
        '      .split(/\\n+/)'
        '      .map(x => x.trim())'
        '      .filter(Boolean);'
        '    const xIndex = parts.findIndex(x => /^x$/i.test(x));'
        '    let home = "";'
        '    let away = "";'
        '    let league = "";'
        '    let kickoff = "";'
        '    if (xIndex >= 0) {'
        '      home = parts[xIndex - 1] || "";'
        '      away = parts[xIndex + 1] || "";'
        '      league = parts[xIndex + 2] || "";'
        '      kickoff = parts[xIndex + 3] || "";'
        '    } else {'
        '      home = parts[0] || "";'
        '      league = parts[1] || "";'
        '      kickoff = parts[2] || "";'
        '    }'
        '    items.push({'
        '      href: link.href,'
        '      home,'
        '      away,'
        '      league,'
        '      kickoff,'
        '      raw_text: (row.innerText || "").trim(),'
        '    });'
        '  }'
        '  return {items};'
        '})())'
    )


def infer_gtype_from_league(league):
    text = normalize_match_text(league)
    if any(k in text for k in ("basketball", "nba", "euroleague", "turkishcupwomen")):
        return "BK"
    if "tennis" in text or "atp" in text or "wta" in text or text.startswith("m25") or text.startswith("w35"):
        return "TN"
    if "volleyball" in text or "vleague" in text:
        return "VB"
    if "baseball" in text:
        return "BS"
    if "snooker" in text:
        return "SK"
    if "badminton" in text:
        return "BM"
    if "esport" in text or "esports" in text:
        return "ES"
    return "FT"


def normalize_match_text(text):
    text = unicodedata.normalize("NFKD", (text or "").strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def normalize_league_text(text):
    text = (text or "").replace("\t", " ").strip()
    text = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return normalize_match_text(text)


def contains_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def contains_latin(text):
    return any(("a" <= ch.lower() <= "z") for ch in str(text or ""))


def extract_age_markers(text):
    return {
        marker.upper()
        for marker in re.findall(r"(?<![A-Za-z0-9])U(?:17|19|20|21|23)(?![A-Za-z0-9])", str(text or ""), flags=re.I)
    }


def has_women_marker(text):
    raw = str(text or "").strip()
    lowered = raw.lower()
    normalized = normalize_match_text(raw)
    return (
        bool(re.search(r"\bW\b", raw))
        or "women" in lowered
        or "women's" in lowered
        or "女子" in raw
        or "女足" in raw
        or normalized.endswith("w")
    )


def has_known_team_aliases(text):
    normalized = normalize_match_text(text)
    if not normalized:
        return False
    return bool(load_team_aliases().get(normalized, set()))


def has_known_league_aliases(text):
    normalized = normalize_league_text(text)
    if not normalized:
        return False
    return bool(get_league_aliases(text) - {normalized})


def looks_like_club_name(text):
    raw = str(text or "").strip()
    lowered = raw.lower()
    tokens = [token for token in re.split(r"[^a-z]+", lowered) if token]
    if any(token in CLUB_NAME_HINTS for token in tokens):
        return True
    normalized = normalize_match_text(raw)
    if not normalized:
        return False
    if any(hint in normalized for hint in ("athletic", "atletico", "sporting", "olimpia", "olympique")):
        return True
    return bool(load_team_aliases().get(normalized, set()))


def looks_like_country_team_alias(text):
    raw = str(text or "").strip()
    normalized = normalize_match_text(raw)
    if not normalized:
        return False
    if any(hint in raw for hint in COUNTRY_TEAM_HINTS_ZH):
        return True
    if "女足" in raw:
        country_roots = (
            "中国", "日本", "韩国", "朝鲜", "泰国", "越南", "孟加拉", "伊拉克", "玻利维亚",
            "巴西", "德国", "法国", "英格兰", "西班牙", "意大利", "墨西哥", "加拿大", "美国",
            "阿根廷", "哥伦比亚", "乌拉圭", "巴拉圭", "葡萄牙", "荷兰", "比利时", "挪威",
        )
        if any(raw.startswith(root) for root in country_roots):
            return True
    return False


def validate_ai_alias_candidate(term, alias, *, alias_type):
    term_raw = str(term or "").strip()
    alias_raw = str(alias or "").strip()
    if not alias_raw:
        return "empty"
    if alias_raw.lower() in AI_ALIAS_PLACEHOLDERS:
        return "placeholder"

    normalizer = normalize_league_text if alias_type == "league" else normalize_match_text
    term_norm = normalizer(term_raw)
    alias_norm = normalizer(alias_raw)
    if not term_norm or not alias_norm:
        return "blank-normalized"
    if term_norm == alias_norm:
        return "same-normalized-text"

    term_has_cjk = contains_cjk(term_raw)
    term_has_latin = contains_latin(term_raw)
    alias_has_cjk = contains_cjk(alias_raw)
    alias_has_latin = contains_latin(alias_raw)
    if term_has_latin and not term_has_cjk and alias_has_latin and not alias_has_cjk:
        return "latin-input-kept-latin-output"
    if term_has_cjk and not term_has_latin and alias_has_cjk and not alias_has_latin:
        return "cjk-input-kept-cjk-output"
    if (
        alias_type == "team"
        and term_has_latin
        and not term_has_cjk
        and alias_has_cjk
        and not alias_has_latin
        and len(alias_raw) <= 1
    ):
        return "too-short-cjk-team-alias"

    term_ages = extract_age_markers(term_raw)
    alias_ages = extract_age_markers(alias_raw)
    if term_ages != alias_ages:
        return f"age-marker-mismatch term={sorted(term_ages)} alias={sorted(alias_ages)}"

    if has_women_marker(term_raw) != has_women_marker(alias_raw):
        return "women-marker-mismatch"

    if alias_type == "team" and looks_like_club_name(term_raw) and looks_like_country_team_alias(alias_raw):
        return "club-misread-as-national-team"
    if alias_type == "team" and looks_like_club_name(term_raw) and alias_raw in GENERIC_CLUB_ALIASES_ZH:
        return "generic-club-alias"

    return ""


def filter_ai_alias_items(items, *, alias_type, logger):
    filtered_items = []
    normalizer = normalize_league_text if alias_type == "league" else normalize_match_text
    for item in items or []:
        term = str((item or {}).get("term", "")).strip()
        if not term:
            continue
        cleaned_aliases = []
        seen = set()
        for alias in (item.get("aliases") or []):
            alias_raw = str(alias or "").strip()
            reason = validate_ai_alias_candidate(term, alias_raw, alias_type=alias_type)
            if reason:
                logger.log(
                    f"AI别名过滤({alias_type}): {term} -> {alias_raw or '<empty>'} ({reason})",
                    "WARN",
                )
                continue
            alias_norm = normalizer(alias_raw)
            if alias_norm in seen:
                continue
            seen.add(alias_norm)
            cleaned_aliases.append(alias_raw)
            if len(cleaned_aliases) >= 2:
                break
        if cleaned_aliases:
            filtered_items.append({"term": term, "aliases": cleaned_aliases})
    return filtered_items


def split_match_teams(text):
    text = (text or "").strip()
    if not text:
        return "", ""
    parts = re.split(r"\s+(?:vs|v)\s+|\s+x\s+|\s*[-–—]\s*", text, maxsplit=1, flags=re.I)
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return text, ""


def infer_translation_direction(term):
    raw = str(term or "").strip()
    has_cjk = contains_cjk(raw)
    has_latin = contains_latin(raw)
    if has_cjk and not has_latin:
        return "zho_Hans", "eng_Latn"
    if has_latin:
        return "eng_Latn", "zho_Hans"
    return "", ""


def heuristic_nllb_team_input(term):
    text = str(term or "").strip()
    if not text:
        return text
    if contains_cjk(text):
        if "女足" in text and "女子" not in text:
            return text.replace("女足", "女子足球队")
        return text
    if re.search(r"\bW\b", text):
        return re.sub(r"\bW\b", "Women FC", text).strip()
    if extract_age_markers(text):
        return text
    if " " in text and not re.search(r"\bFC\b", text, flags=re.I):
        return f"{text} FC"
    return text


def get_local_nllb_runner(logger):
    global _NLLB_RUNNER_CACHE, _NLLB_RUNNER_FAILED
    if _NLLB_RUNNER_CACHE is not None or _NLLB_RUNNER_FAILED:
        return _NLLB_RUNNER_CACHE
    if not NLLB_ENABLED:
        _NLLB_RUNNER_FAILED = True
        return None
    model_dir = Path(os.path.expanduser(NLLB_MODEL_DIR))
    if not model_dir.exists():
        _NLLB_RUNNER_FAILED = True
        logger.log(f"NLLB本地模型不存在，跳过队名fallback: {model_dir}", "WARN")
        return None
    try:
        import torch
        from transformers import AutoModelForSeq2SeqLM, NllbTokenizer

        class _NllbRunner:
            def __init__(self, model_path):
                self.device = "mps" if torch.backends.mps.is_available() else "cpu"
                self.tokenizer = NllbTokenizer.from_pretrained(model_path, local_files_only=True)
                self.model = AutoModelForSeq2SeqLM.from_pretrained(
                    model_path,
                    local_files_only=True,
                    dtype=torch.float16 if self.device == "mps" else torch.float32,
                    low_cpu_mem_usage=True,
                ).to(self.device)
                self.model.eval()

            def translate(self, text, src_lang, tgt_lang):
                self.tokenizer.src_lang = src_lang
                self.tokenizer.tgt_lang = tgt_lang
                inputs = self.tokenizer(text, return_tensors="pt")
                inputs = {key: value.to(self.device) for key, value in inputs.items()}
                with torch.inference_mode():
                    output = self.model.generate(
                        **inputs,
                        forced_bos_token_id=self.tokenizer.convert_tokens_to_ids(tgt_lang),
                        max_new_tokens=32,
                        num_beams=4,
                    )
                return self.tokenizer.batch_decode(output, skip_special_tokens=True)[0].strip()

        _NLLB_RUNNER_CACHE = _NllbRunner(model_dir)
        logger.log(f"NLLB fallback 已启用: dir={model_dir} device={_NLLB_RUNNER_CACHE.device}")
        return _NLLB_RUNNER_CACHE
    except Exception as exc:
        _NLLB_RUNNER_FAILED = True
        logger.log(f"加载NLLB fallback失败，后续跳过: {exc}", "WARN")
        return None


def build_nllb_team_seed_aliases(terms, logger):
    runner = get_local_nllb_runner(logger)
    if runner is None:
        return {}
    seed_aliases = {}
    for term in terms or []:
        raw = str(term or "").strip()
        if not raw:
            continue
        src_lang, tgt_lang = infer_translation_direction(raw)
        if not src_lang or not tgt_lang:
            continue
        nllb_input = heuristic_nllb_team_input(raw)
        try:
            alias = runner.translate(nllb_input, src_lang, tgt_lang).strip()
        except Exception as exc:
            logger.log(f"NLLB队名fallback失败: {raw} ({exc})", "WARN")
            continue
        if not alias:
            continue
        reason = validate_ai_alias_candidate(raw, alias, alias_type="team")
        if reason:
            logger.log(
                f"NLLB队名fallback过滤: {raw} -> {alias} ({reason})",
                "WARN",
            )
            continue
        seed_aliases[raw] = [alias]
        extra = "" if nllb_input == raw else f" input={nllb_input}"
        logger.log(f"NLLB队名fallback: {raw} -> {alias}{extra}")
    return seed_aliases


def _add_team_alias(alias_map, left, right):
    left_n = normalize_match_text(left)
    right_n = normalize_match_text(right)
    if not left_n or not right_n or left_n == right_n:
        return
    alias_map.setdefault(left_n, set()).add(right_n)
    alias_map.setdefault(right_n, set()).add(left_n)


def _add_league_alias(alias_map, left, right):
    left_n = normalize_league_text(left)
    right_n = normalize_league_text(right)
    if not left_n or not right_n or left_n == right_n:
        return
    alias_map.setdefault(left_n, set()).add(right_n)
    alias_map.setdefault(right_n, set()).add(left_n)


def _iter_known_alias_pairs():
    store_path = Path(TEAM_ALIAS_STORE)
    if store_path.exists():
        try:
            payload = json.loads(store_path.read_text(encoding="utf-8"))
            for left, values in payload.items():
                for right in values or []:
                    yield left, right
        except Exception:
            pass


def _iter_known_league_alias_pairs():
    for left, right in KNOWN_LEAGUE_ALIAS_PAIRS:
        yield left, right
    store_path = Path(LEAGUE_ALIAS_STORE)
    if store_path.exists():
        try:
            payload = json.loads(store_path.read_text(encoding="utf-8"))
            for left, values in payload.items():
                for right in values or []:
                    yield left, right
        except Exception:
            pass


def _write_serialized_alias_map(path, alias_map):
    serializable = {key: sorted(values) for key, values in alias_map.items() if values}
    if serializable:
        try:
            Path(path).write_text(
                json.dumps(serializable, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass


def load_team_aliases():
    global _TEAM_ALIAS_CACHE
    if _TEAM_ALIAS_CACHE is not None:
        return _TEAM_ALIAS_CACHE

    alias_map = {}
    for left, right in _iter_known_alias_pairs():
        _add_team_alias(alias_map, left, right)

    _write_serialized_alias_map(TEAM_ALIAS_STORE, alias_map)

    _TEAM_ALIAS_CACHE = alias_map
    return _TEAM_ALIAS_CACHE


def load_league_aliases():
    global _LEAGUE_ALIAS_CACHE
    if _LEAGUE_ALIAS_CACHE is not None:
        return _LEAGUE_ALIAS_CACHE

    alias_map = {}
    for left, right in _iter_known_league_alias_pairs():
        _add_league_alias(alias_map, left, right)

    _write_serialized_alias_map(LEAGUE_ALIAS_STORE, alias_map)
    _LEAGUE_ALIAS_CACHE = alias_map
    return _LEAGUE_ALIAS_CACHE


def persist_team_alias_pair(left, right):
    global _TEAM_ALIAS_CACHE
    alias_map = load_team_aliases()
    before = {key: set(values) for key, values in alias_map.items()}
    _add_team_alias(alias_map, left, right)
    if before != alias_map:
        _write_serialized_alias_map(TEAM_ALIAS_STORE, alias_map)
        _TEAM_ALIAS_CACHE = alias_map
        return True
    return False


def persist_league_alias_pair(left, right):
    global _LEAGUE_ALIAS_CACHE
    alias_map = load_league_aliases()
    before = {key: set(values) for key, values in alias_map.items()}
    _add_league_alias(alias_map, left, right)
    if before != alias_map:
        _write_serialized_alias_map(LEAGUE_ALIAS_STORE, alias_map)
        _LEAGUE_ALIAS_CACHE = alias_map
        return True
    return False


def _load_learned_alias_store(path):
    store_path = Path(path)
    if not store_path.exists():
        return {}
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _iter_learned_alias_pairs(path):
    payload = _load_learned_alias_store(path)
    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        left_raw = str(entry.get("left_example", "")).strip()
        right_raw = str(entry.get("right_example", "")).strip()
        left_n = str(entry.get("left_normalized", "")).strip()
        right_n = str(entry.get("right_normalized", "")).strip()
        if left_raw and right_raw:
            yield left_raw, right_raw
        elif left_n and right_n:
            yield left_n, right_n


def _merge_learned_aliases(aliases, text, *, path, normalizer):
    normalized = normalizer(text)
    if not normalized:
        return aliases
    for left_raw, right_raw in _iter_learned_alias_pairs(path):
        left_n = normalizer(left_raw)
        right_n = normalizer(right_raw)
        if normalized == left_n:
            aliases.add(right_n)
            aliases.add(normalizer(right_raw))
        if normalized == right_n:
            aliases.add(left_n)
            aliases.add(normalizer(left_raw))
    return aliases


def _write_learned_alias_store(path, payload):
    try:
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def record_learned_alias_pair(left, right, *, path, normalizer, persist_func, logger, alias_type):
    left_raw = str(left or "").strip()
    right_raw = str(right or "").strip()
    left_n = normalizer(left_raw)
    right_n = normalizer(right_raw)
    if not left_n or not right_n or left_n == right_n:
        return

    pair_key = " <-> ".join(sorted([left_n, right_n]))
    payload = _load_learned_alias_store(path)
    entry = payload.get(pair_key, {})
    count = int(entry.get("count", 0)) + 1
    promoted = bool(entry.get("promoted", False))

    payload[pair_key] = {
        "left_normalized": left_n,
        "right_normalized": right_n,
        "left_example": left_raw,
        "right_example": right_raw,
        "count": count,
        "promoted": promoted,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.log(
        f"{alias_type}别名 learned: {left_raw} <-> {right_raw} (count={count})"
    )

    if not promoted and count >= ALIAS_AUTO_PROMOTE_HITS:
        persist_func(left_raw, right_raw)
        payload[pair_key]["promoted"] = True
        logger.log(
            f"{alias_type}别名 auto-promote: {left_raw} <-> {right_raw} (count={count})"
        )

    _write_learned_alias_store(path, payload)


def record_successful_binding_alias_learning(selected_match, bound_match, logger):
    if not selected_match or not bound_match:
        return

    record_learned_alias_pair(
        selected_match.get("team_h", ""),
        bound_match.get("team_h", ""),
        path=TEAM_ALIAS_LEARNED_STORE,
        normalizer=normalize_match_text,
        persist_func=persist_team_alias_pair,
        logger=logger,
        alias_type="队名",
    )
    record_learned_alias_pair(
        selected_match.get("team_c", ""),
        bound_match.get("team_c", ""),
        path=TEAM_ALIAS_LEARNED_STORE,
        normalizer=normalize_match_text,
        persist_func=persist_team_alias_pair,
        logger=logger,
        alias_type="队名",
    )
    record_learned_alias_pair(
        selected_match.get("league", ""),
        bound_match.get("league", ""),
        path=LEAGUE_ALIAS_LEARNED_STORE,
        normalizer=normalize_league_text,
        persist_func=persist_league_alias_pair,
        logger=logger,
        alias_type="联赛",
    )


def get_team_aliases(text):
    normalized = normalize_match_text(text)
    if not normalized:
        return set()
    aliases = {normalized}
    aliases.update(load_team_aliases().get(normalized, set()))
    _merge_learned_aliases(
        aliases,
        text,
        path=TEAM_ALIAS_LEARNED_STORE,
        normalizer=normalize_match_text,
    )
    return aliases


def get_league_aliases(text):
    normalized = normalize_league_text(text)
    if not normalized:
        return set()

    aliases = {normalized}
    for left, right in KNOWN_LEAGUE_ALIAS_PAIRS:
        left_n = normalize_league_text(left)
        right_n = normalize_league_text(right)
        if not left_n or not right_n:
            continue
        if normalized == left_n:
            aliases.add(right_n)
        if normalized == right_n:
            aliases.add(left_n)
    aliases.update(load_league_aliases().get(normalized, set()))
    _merge_learned_aliases(
        aliases,
        text,
        path=LEAGUE_ALIAS_LEARNED_STORE,
        normalizer=normalize_league_text,
    )
    return aliases


def same_league_text(left, right):
    left_aliases = get_league_aliases(left)
    right_aliases = get_league_aliases(right)
    if not left_aliases or not right_aliases:
        return left_aliases == right_aliases
    if left_aliases & right_aliases:
        return True
    for left_n in left_aliases:
        for right_n in right_aliases:
            if left_n in right_n or right_n in left_n:
                return True
    return False


def load_openclaw_env_var(name):
    env_path = Path(os.path.expanduser("~/.openclaw/.env"))
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def get_openclaw_translation_provider():
    global _TRANSLATION_PROVIDER_CACHE
    if _TRANSLATION_PROVIDER_CACHE is not None:
        return _TRANSLATION_PROVIDER_CACHE

    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if not config_path.exists():
        _TRANSLATION_PROVIDER_CACHE = {}
        return _TRANSLATION_PROVIDER_CACHE

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        providers = ((config.get("models") or {}).get("providers") or {})
    except Exception:
        _TRANSLATION_PROVIDER_CACHE = {}
        return _TRANSLATION_PROVIDER_CACHE

    configured_preference = str(
        os.environ.get("MATCH_PLAN_TRANSLATION_PROVIDER", "")
        or load_openclaw_env_var("MATCH_PLAN_TRANSLATION_PROVIDER")
    ).strip().lower()
    provider_keys = []
    if configured_preference:
        provider_keys.append(configured_preference)
    for fallback_key in ("omlx", "custom"):
        if fallback_key not in provider_keys:
            provider_keys.append(fallback_key)

    provider_key = ""
    provider = {}
    for candidate_key in provider_keys:
        candidate = (providers.get(candidate_key) or {}).copy()
        base_url = str(candidate.get("baseUrl", "")).strip().rstrip("/")
        api_key = str(candidate.get("apiKey", "")).strip()
        if base_url and api_key:
            provider_key = candidate_key
            provider = candidate
            break

    base_url = str(provider.get("baseUrl", "")).strip().rstrip("/")
    api_key = str(provider.get("apiKey", "")).strip()
    if not provider_key or not base_url or not api_key:
        _TRANSLATION_PROVIDER_CACHE = {}
        return _TRANSLATION_PROVIDER_CACHE

    probed_model_ids = []
    try:
        request = urllib.request.Request(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        probed_model_ids = [
            str(item.get("id", "")).strip()
            for item in (payload.get("data") or [])
            if str(item.get("id", "")).strip()
        ]
    except Exception:
        probed_model_ids = [
            str(item.get("id", "")).strip()
            for item in provider.get("models", [])
            if str(item.get("id", "")).strip()
        ]

    preferred_models = []
    if provider_key == "omlx":
        preferred_models = [
            "Qwen3-4B-Instruct-2507-8bit",
            "Qwen3-8B-4bit",
            "Qwen3.5-VL-35B-A3B-4bit-MLX-CRACK",
            "Qwen3.5-VL-122B-A10B-4bit-MLX-CRACK",
            "Qwen3.5-VL-9B-8bit-MLX-CRACK",
            "Qwen3.5-27B-4bit",
            "Qwen2.5-VL-7B-Instruct-4bit",
        ]
    else:
        preferred_models = [
            "glm-5",
            "claude-sonnet-5",
            "claude-sonnet-4.7",
            "gpt-5.4",
            "qwen3-max",
            "qwen3-max-2026-01-23",
            "qwen3.5-plus",
            "qwen3-coder-plus",
            "qwen3-coder-next",
        ]
    available = set(probed_model_ids)
    selected_models = [model for model in preferred_models if model in available]
    if not selected_models:
        selected_models = [model for model in probed_model_ids if "minimax" not in model.lower()]
    if not selected_models:
        selected_models = probed_model_ids[:]

    _TRANSLATION_PROVIDER_CACHE = {
        "provider_key": provider_key,
        "base_url": base_url,
        "api_key": api_key,
        "probed_models": probed_model_ids,
        "selected_models": selected_models,
    }
    return _TRANSLATION_PROVIDER_CACHE


def extract_json_object_from_text(text):
    content = str(text or "").strip()
    if not content:
        return {}
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.S)
    if fence_match:
        content = fence_match.group(1).strip()
    decoder = json.JSONDecoder()
    start_positions = [idx for idx, ch in enumerate(content) if ch == "{"] or [0]
    for start in start_positions:
        candidate = content[start:].strip()
        try:
            parsed, _end = decoder.raw_decode(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start : end + 1]
    return json.loads(content)


def chunk_terms_for_alias_translation(terms, chunk_size=12):
    clean_terms = []
    seen = set()
    for term in terms:
        text = str(term or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        clean_terms.append(text)
    for idx in range(0, len(clean_terms), max(1, int(chunk_size))):
        yield clean_terms[idx : idx + max(1, int(chunk_size))]


def request_alias_translation_chunk(chunk, models, base_url, api_key, logger, *, alias_type, seed_aliases=None):
    last_error = None
    seed_aliases = seed_aliases or {}
    term_payload = []
    for term in chunk:
        item = {"term": term}
        draft_aliases = list(seed_aliases.get(term) or [])
        if draft_aliases:
            item["draft_aliases"] = draft_aliases[:2]
        term_payload.append(item)

    for model_name in models:
        seed_rule = ""
        if alias_type == "team":
            seed_rule = (
                "- Some team terms include draft_aliases generated by a translation model. "
                "Use them as hints, but normalize them into the best bookmaker-style alias if needed.\n"
            )
        prompt = (
            f"You normalize football {alias_type} names for cross-language betting-feed matching.\n"
            "Return strict JSON only with schema: {\"items\":[{\"term\":\"...\",\"aliases\":[\"...\"]}]}\n"
            "Rules:\n"
            "- Return the single best opposite-language alias for each term. Add a second alias only if both are very common in bookmaker feeds.\n"
            "- Never explain. Never add markdown. Never echo instructions.\n"
            "- Preserve exact entity identity. Do not change country, club, league, age group, or gender.\n"
            "- Preserve markers exactly: U17 U19 U20 U21 U23 W Women.\n"
            "- If input is English or other Latin script, output a Chinese alias in Chinese characters.\n"
            "- If input is Chinese, output an English alias in Latin letters.\n"
            "- The alias should usually be in the opposite script from the input. Avoid returning the same script unless there is no real translation.\n"
            "- Prefer short bookmaker-style names, not literal explanations.\n"
            f"{seed_rule}"
            "- For 国际友谊赛 / Friendlies / FIFA Series, prefer International Friendlies in English and 国际友谊赛 in Chinese.\n"
            "- For 欧洲女子冠军联赛 / UEFA Champions League Women, prefer 欧女冠 in Chinese and UEFA WCL in English.\n"
            "- For 哥伦比亚甲组联赛, prefer Primera A in English.\n"
            "- For 乌拉圭甲组联赛, prefer Uruguayan Primera Division in English.\n"
            "- For 英格兰甲组联赛 / League One, prefer League One in English and 英甲 in Chinese.\n"
            "- If unsure, use an empty aliases array.\n"
            "Examples:\n"
            "- Mexico -> [\"墨西哥\"]\n"
            "- 比利时 -> [\"Belgium\"]\n"
            "- Japan W -> [\"日本女足\"]\n"
            "- 日本女足 -> [\"Japan W\"]\n"
            "- 意大利U21 -> [\"Italy U21\"]\n"
            "- 国际友谊赛 -> [\"International Friendlies\"]\n"
            "- Friendlies -> [\"国际友谊赛\"]\n"
            "- FIFA Series -> [\"国际友谊赛\"]\n"
            "- 欧洲女子冠军联赛 -> [\"UEFA WCL\"]\n"
            "- UEFA Champions League Women -> [\"欧女冠\"]\n"
            "- 哥伦比亚甲组联赛 -> [\"Primera A\"]\n"
            "- 乌拉圭甲组联赛 -> [\"Uruguayan Primera Division\"]\n"
            "- League One -> [\"英甲\"]\n"
            f"Terms: {json.dumps(term_payload, ensure_ascii=False)}"
        )
        body = {
            "model": model_name,
            "temperature": 0,
            "max_tokens": 768,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a translation helper. "
                        "Output strict JSON only. "
                        "Never output reasoning, explanations, or <think>."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
            finish_reason = payload["choices"][0].get("finish_reason", "")
            content = payload["choices"][0]["message"]["content"]
            if finish_reason == "length" and "{" not in str(content or ""):
                raise ValueError("model exhausted tokens before returning JSON")
            parsed = extract_json_object_from_text(content)
            items = parsed.get("items", [])
            if isinstance(items, list):
                logger.log(f"AI别名翻译使用 {model_name} 成功: {alias_type} {len(chunk)} 项")
                return items
            raise ValueError("missing items list")
        except urllib.error.HTTPError as exc:
            last_error = exc
            logger.log(f"AI别名翻译 HTTP错误({model_name}): {exc.code}", "WARN")
        except Exception as exc:
            last_error = exc
            logger.log(f"AI别名翻译失败({model_name}): {exc}", "WARN")
    if last_error:
        raise last_error
    raise RuntimeError("translation request failed without model response")


def translate_terms_with_retry(
    terms,
    models,
    base_url,
    api_key,
    logger,
    *,
    alias_type,
    seed_aliases=None,
    chunk_size=12,
    min_chunk_size=2,
):
    clean_terms = []
    seen = set()
    for term in terms:
        text = str(term or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        clean_terms.append(text)
    if not clean_terms:
        return []

    logger.log(
        f"AI别名翻译开始: type={alias_type} total={len(clean_terms)} "
        f"chunk_size={chunk_size} min_chunk_size={min_chunk_size}"
    )
    learned_items = []
    for chunk in chunk_terms_for_alias_translation(clean_terms, chunk_size=chunk_size):
        try:
            learned_items.extend(
                request_alias_translation_chunk(
                    chunk,
                    models,
                    base_url,
                    api_key,
                    logger,
                    alias_type=alias_type,
                    seed_aliases=seed_aliases,
                )
            )
            continue
        except Exception as exc:
            logger.log(
                f"AI别名大批量翻译失败，改为拆小批重试: chunk={len(chunk)} err={exc}",
                "WARN",
            )
        if len(chunk) <= min_chunk_size:
            continue
        fallback_size = max(min_chunk_size, len(chunk) // 2)
        for sub_chunk in chunk_terms_for_alias_translation(chunk, chunk_size=fallback_size):
            try:
                learned_items.extend(
                    request_alias_translation_chunk(
                        sub_chunk,
                        models,
                        base_url,
                        api_key,
                        logger,
                        alias_type=alias_type,
                        seed_aliases=seed_aliases,
                    )
                )
            except Exception as exc:
                logger.log(
                    f"AI别名小批量翻译仍失败，跳过该批: chunk={len(sub_chunk)} err={exc}",
                    "WARN",
                )
    logger.log(
        f"AI别名翻译完成: type={alias_type} total={len(clean_terms)} learned={len(learned_items)}"
    )
    return learned_items


def custom_batch_translate_aliases(terms, logger, *, alias_type):
    provider = get_openclaw_translation_provider()
    provider_key = str(provider.get("provider_key", "")).strip() or "unknown"
    base_url = str(provider.get("base_url", "")).strip()
    api_key = str(provider.get("api_key", "")).strip()
    models = list(provider.get("selected_models") or [])
    nllb_seed_aliases = build_nllb_team_seed_aliases(terms, logger) if alias_type == "team" else {}
    if not base_url or not api_key or not models:
        return [{"term": term, "aliases": aliases} for term, aliases in nllb_seed_aliases.items()]
    logger.log(
        f"AI别名翻译 provider={provider_key} base={base_url} model={models[0]} type={alias_type}"
    )
    ai_items = translate_terms_with_retry(
        terms,
        models=models,
        base_url=base_url,
        api_key=api_key,
        logger=logger,
        alias_type=alias_type,
        seed_aliases=nllb_seed_aliases,
        chunk_size=12,
        min_chunk_size=2,
    )
    if alias_type != "team" or not nllb_seed_aliases:
        return ai_items

    merged = {}
    for item in ai_items or []:
        term = str((item or {}).get("term", "")).strip()
        aliases = []
        for alias in (item.get("aliases") or []):
            alias_raw = str(alias or "").strip()
            if not alias_raw:
                continue
            if validate_ai_alias_candidate(term, alias_raw, alias_type="team"):
                continue
            aliases.append(alias_raw)
            if len(aliases) >= 2:
                break
        if term and aliases:
            merged[term] = aliases[:2]
            continue
        if term and term in nllb_seed_aliases:
            merged[term] = list(nllb_seed_aliases[term][:2])
    fallback_count = 0
    for term, aliases in nllb_seed_aliases.items():
        if term in merged:
            continue
        merged[term] = list(aliases[:2])
        fallback_count += 1
    if fallback_count:
        logger.log(f"NLLB队名fallback补位: {fallback_count} 项")
    return [{"term": term, "aliases": aliases} for term, aliases in merged.items()]


def apply_ai_alias_batch(selected_matches, logger):
    team_terms = []
    league_terms = []
    for match in selected_matches or []:
        if match.get("team_h") and not has_known_team_aliases(match["team_h"]):
            team_terms.append(match["team_h"])
        if match.get("team_c") and not has_known_team_aliases(match["team_c"]):
            team_terms.append(match["team_c"])
        if match.get("league") and not has_known_league_aliases(match["league"]):
            league_terms.append(match["league"])

    if team_terms or league_terms:
        logger.log(
            f"AI别名候选: team={len(team_terms)} league={len(league_terms)} "
            f"(已跳过本地已有别名的高频项)"
        )

    translated_team_items = filter_ai_alias_items(
        custom_batch_translate_aliases(team_terms, logger, alias_type="team"),
        alias_type="team",
        logger=logger,
    )
    translated_league_items = filter_ai_alias_items(
        custom_batch_translate_aliases(league_terms, logger, alias_type="league"),
        alias_type="league",
        logger=logger,
    )
    learned = {"team": 0, "league": 0}

    for item in translated_team_items:
        term = str(item.get("term", "")).strip()
        for alias in item.get("aliases", []) or []:
            if persist_team_alias_pair(term, alias):
                learned["team"] += 1

    for item in translated_league_items:
        term = str(item.get("term", "")).strip()
        for alias in item.get("aliases", []) or []:
            if persist_league_alias_pair(term, alias):
                learned["league"] += 1

    if learned["team"] or learned["league"]:
        logger.log(
            f"AI别名学习完成: team={learned['team']} league={learned['league']}"
        )
    return learned


def sanitize_filename_component(text):
    text = (text or "").strip()
    text = re.sub(r"[\\/:\*\?\"<>\|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._ ")
    return text or "match"


def format_session_stamp_for_path(session_id):
    try:
        dt = datetime.strptime(session_id, "%Y%m%d_%H%M%S")
        return dt.strftime("%Y-%m-%d_%H-%M-%S")
    except Exception:
        return session_id


def build_session_output_dir(session_id):
    root = os.path.expanduser(BASE_OUTPUT_DIR)
    volume_root = os.path.expanduser(DEFAULT_RECORDINGS_VOLUME)
    if root.startswith(volume_root) and not os.path.isdir(volume_root):
        raise RuntimeError(f"外接硬盘未挂载: {volume_root}")
    date_folder = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(root, date_folder, f"session_{session_id}")


def load_selected_matches_file(path, logger):
    if not path:
        return []
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        logger.log(f"读取外部选定比赛列表失败: {path} ({exc})", "ERROR")
        return []
    if isinstance(raw, dict):
        raw = raw.get("selected_matches") or raw.get("matches") or []
    if not isinstance(raw, list):
        logger.log(f"外部选定比赛列表格式错误: {path}", "ERROR")
        return []
    selected = [item for item in raw if isinstance(item, dict)]
    logger.log(f"载入外部选定比赛列表: {len(selected)} 场 ← {path}")
    return selected


def annotate_selected_matches_for_recording(selected_matches):
    for match in selected_matches:
        if match.get("gid") or match.get("ecid"):
            match["data_binding_status"] = "bound"
            if not str(match.get("recording_note", "")).startswith("测试流"):
                match["recording_note"] = ""
        else:
            match["data_binding_status"] = "unbound"
            match["recording_note"] = (
                match.get("recording_note")
                or "未匹配到对应比赛数据，仅用于测试录制稳定性"
            )
    return selected_matches


def send_anomaly_notification(
    logger,
    channel,
    target,
    account,
    title,
    session_dir,
    lines,
):
    if not channel or not target:
        return False
    try:
        from notify_recording_summary import send_text_with_optional_media
    except Exception as exc:
        logger.log(f"异常通知模块加载失败: {exc}", "WARN")
        return False

    message = "\n".join(
        [title or "录制异常提醒", f"目录：{session_dir}"] + [line for line in lines if line]
    )
    try:
        rc = send_text_with_optional_media(
            channel,
            target,
            message,
            account=account or None,
            dry_run=False,
        )
    except Exception as exc:
        logger.log(f"异常通知发送异常: {exc}", "WARN")
        return False
    if rc == 0:
        logger.log(f"已发送异常通知: channel={channel} target={target}")
        return True
    logger.log(f"异常通知发送失败: channel={channel} target={target} rc={rc}", "WARN")
    return False


def build_stream_naming(selected_match, fallback_teams, session_id, index):
    gtype = (selected_match or {}).get("gtype", "") or "NA"
    home = (selected_match or {}).get("team_h", "")
    away = (selected_match or {}).get("team_c", "")
    if home and away:
        label = f"{gtype}_{home}_vs_{away}"
    elif fallback_teams:
        label = fallback_teams.replace(" x ", "_vs_").replace(" vs ", "_vs_")
    else:
        label = f"match_{index}"
    base = sanitize_filename_component(label)
    session_stamp = format_session_stamp_for_path(session_id)
    folder_name = f"{base}__{session_stamp}"
    file_prefix = f"{base}__{session_stamp}"
    return base, folder_name, file_prefix


def extract_match_pair_from_label(text):
    text = (text or "").strip()
    if not text:
        return "", ""

    parts = [part.strip() for part in re.split(r"\s+-\s+", text) if part.strip()]
    candidates = list(reversed(parts)) + [text]
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        home, away = split_match_teams(candidate)
        if home and away:
            return home, away
    return "", ""


def pop_selected_match_for_window(window_teams, selected_pool):
    home, away = extract_match_pair_from_label(window_teams)
    if home and away:
        for idx, match in enumerate(selected_pool):
            if (
                same_match_text(home, match.get("team_h", ""))
                and same_match_text(away, match.get("team_c", ""))
            ):
                return selected_pool.pop(idx)
    return selected_pool.pop(0) if selected_pool else None


def pop_ready_tab_for_window(window_teams, ready_pool, selected_match=None):
    target_url = ((selected_match or {}).get("watch_url") or "").rstrip("/")
    if target_url:
        for idx, tab in enumerate(ready_pool):
            if (tab.get("url", "") or "").rstrip("/") == target_url:
                return ready_pool.pop(idx)

    home, away = extract_match_pair_from_label(window_teams)
    if home and away:
        for idx, tab in enumerate(ready_pool):
            tab_home, tab_away = extract_match_pair_from_label(tab.get("title", ""))
            if not tab_home or not tab_away:
                continue
            if same_match_text(home, tab_home) and same_match_text(away, tab_away):
                return ready_pool.pop(idx)

    return ready_pool.pop(0) if ready_pool else {}


def applescript_quote(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def find_pinchtab_cli():
    cli = shutil.which("pinchtab")
    if cli:
        return cli

    candidates = [
        "/opt/homebrew/bin/pinchtab",
        "/usr/local/bin/pinchtab",
        os.path.expanduser("~/.local/bin/pinchtab"),
    ]
    candidates.extend(sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/pinchtab"))))

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def parse_watch_candidates_payload(output, logger, source_label, stderr_text=""):
    if not output:
        if stderr_text:
            logger.log(f"{source_label}: 未读取到直播候选 ({stderr_text[:120]})", "WARN")
        else:
            logger.log(f"{source_label}: 未读取到直播候选", "WARN")
        return []

    try:
        payload = json.loads(output)
    except Exception:
        logger.log(f"{source_label}: 返回了无法解析的内容: {output[:160]}", "WARN")
        return []

    if isinstance(payload, dict):
        if payload.get("error"):
            logger.log(f"{source_label}: {payload['error']}", "WARN")
            return []
        items = payload.get("items", [])
        if not items:
            logger.log(f"{source_label}: 无直播链接", "WARN")
            return []
        return items

    if isinstance(payload, list):
        if not payload:
            logger.log(f"{source_label}: 无直播链接", "WARN")
        return payload

    logger.log(f"{source_label}: 返回了未知结构的候选结果", "WARN")
    return []


def parse_watch_candidates_output(output, logger, source_label, stderr_text=""):
    return parse_watch_candidates_payload(
        output,
        logger,
        source_label=source_label,
        stderr_text=stderr_text,
    )


def open_urls_in_new_browser_windows(urls, logger, browser, close_existing_watch_windows=False):
    app = get_browser_app(browser)
    opened = 0
    existing_watch_count = 0
    if browser == "safari" and close_existing_watch_windows:
        try:
            subprocess.run(
                ["osascript", "-e", f'''
                    tell application "{app}"
                        set watchWindows to {{}}
                        repeat with w in windows
                            try
                                set u to URL of current tab of w
                                if u contains "/watch" then
                                    set end of watchWindows to w
                                end if
                            end try
                        end repeat
                        repeat with w in watchWindows
                            try
                                close w
                            end try
                        end repeat
                    end tell
                '''],
                capture_output=True, timeout=10,
            )
            time.sleep(0.5)
        except Exception as e:
            logger.log(f"清理旧视频窗口失败: {e}", "WARN")
    elif browser == "safari":
        try:
            result = subprocess.run(
                ["osascript", "-e", f'''
                    tell application "{app}"
                        set watchCount to 0
                        repeat with w in windows
                            try
                                set u to URL of current tab of w
                                if u contains "/watch" then
                                    set watchCount to watchCount + 1
                                end if
                            end try
                        end repeat
                        return watchCount as text
                    end tell
                '''],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                existing_watch_count = int((result.stdout or "0").strip() or "0")
        except Exception:
            existing_watch_count = 0
    for idx, url in enumerate(urls):
        escaped = applescript_quote(url)
        try:
            if browser == "safari":
                slot_index = existing_watch_count + idx
                cols = 2
                row = slot_index // cols
                col = slot_index % cols
                left = SAFARI_WATCH_WINDOW_LEFT + col * (
                    SAFARI_WATCH_WINDOW_WIDTH + SAFARI_WATCH_WINDOW_PADDING
                )
                top = SAFARI_WATCH_WINDOW_TOP + row * (
                    SAFARI_WATCH_WINDOW_HEIGHT + SAFARI_WATCH_WINDOW_PADDING
                )
                right = left + SAFARI_WATCH_WINDOW_WIDTH
                bottom = top + SAFARI_WATCH_WINDOW_HEIGHT
                script = f'''
                    tell application "{app}"
                        make new document with properties {{URL:"{escaped}"}}
                        delay 0.5
                        set bounds of front window to {{{left}, {top}, {right}, {bottom}}}
                    end tell
                '''
            else:
                script = f'''
                    tell application "{app}"
                        set newWindow to make new window
                        set URL of active tab of newWindow to "{escaped}"
                    end tell
                '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=20,
            )
            if result.returncode == 0:
                opened += 1
                time.sleep(2)
            else:
                logger.log(f"打开窗口失败: {url} ({result.stderr.strip()[:120]})", "WARN")
        except Exception as e:
            logger.log(f"打开窗口失败: {url} ({e})", "WARN")
    return opened


def fetch_live_watch_candidates(logger, browser):
    if not ensure_schedules_live_ready(logger, browser):
        return []
    schedules_tab = find_schedules_tab(browser)
    if not schedules_tab:
        return []
    js_escape = applescript_quote(build_watch_link_js())
    try:
        app = get_browser_app(browser)
        if browser == "safari":
            script = f'''
                tell application "{app}"
                    set js to "{js_escape}"
                    return do JavaScript js in tab {schedules_tab["tab_index"]} of window {schedules_tab["window_index"]}
                end tell
            '''
        else:
            script = f'''
                tell application "{app}"
                    set js to "{js_escape}"
                    return execute tab {schedules_tab["tab_index"]} of window {schedules_tab["window_index"]} javascript js
                end tell
            '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
        return parse_watch_candidates_output(
            result.stdout.strip(),
            logger,
            source_label="Ao vivo 页签",
            stderr_text=result.stderr.strip(),
        )
    except Exception as e:
        logger.log(f"抓取 Ao vivo 页签失败: {e}", "WARN")
        return []


def discover_live_matches_from_schedule(logger, browser):
    candidates = fetch_live_watch_candidates(logger, browser)
    if not candidates:
        return {}
    grouped = {}
    for item in candidates:
        league = item.get("league", "").strip()
        home = item.get("home", "").strip()
        away = item.get("away", "").strip()
        gtype = infer_gtype_from_league(league)
        grouped.setdefault(gtype, []).append({
            "gid": "",
            "ecid": "",
            "gtype": gtype,
            "league": league,
            "team_h": home,
            "team_c": away,
            "score_h": "",
            "score_c": "",
            "watch_url": item.get("href", ""),
        })
    total = sum(len(v) for v in grouped.values())
    logger.log(f"Ao vivo 页签: 发现 {total} 场可观看直播: "
               f"{', '.join(f'{k}({len(v)})' for k,v in sorted(grouped.items()))}")
    return grouped


def same_match_text(left, right):
    left_aliases = get_team_aliases(left)
    right_aliases = get_team_aliases(right)
    if not left_aliases or not right_aliases:
        return left_aliases == right_aliases
    if left_aliases & right_aliases:
        return True
    for left_n in left_aliases:
        for right_n in right_aliases:
            if left_n in right_n or right_n in left_n:
                return True
    return False


def filter_matches_by_query(all_matches, query):
    query = (query or "").strip()
    if not query:
        return []

    query_norm = normalize_match_text(query)
    raw_parts = re.split(r"\s+(?:vs|v)\s+|\s+x\s+|,|/|\\|[-–—]", query, flags=re.I)
    parts = [normalize_match_text(part) for part in raw_parts if normalize_match_text(part)]

    selected = []
    for matches in all_matches.values():
        for match in matches:
            haystack = " ".join(
                [
                    match.get("team_h", ""),
                    match.get("team_c", ""),
                    match.get("league", ""),
                ]
            )
            haystack_norm = normalize_match_text(haystack)
            if query_norm and query_norm in haystack_norm:
                selected.append(match)
                continue
            if parts and all(part in haystack_norm for part in parts):
                selected.append(match)
                continue
    return selected


def choose_watch_urls(candidates, selected, max_streams):
    direct_urls = [m.get("watch_url", "") for m in selected[:max_streams] if m.get("watch_url")]
    if len(direct_urls) == min(len(selected[:max_streams]), max_streams):
        return direct_urls, []

    items = candidates.get("items", []) if isinstance(candidates, dict) else candidates
    if not items:
        return [], selected[:max_streams]

    picked = []
    used = set()
    unmatched = []
    for match in selected[:max_streams]:
        league = match.get("league", "")
        home = match.get("team_h", "")
        away = match.get("team_c", "")
        chosen_idx = None
        for idx, candidate in enumerate(items):
            if idx in used:
                continue
            if (
                same_match_text(candidate.get("league", ""), league)
                and same_match_text(candidate.get("home", ""), home)
                and same_match_text(candidate.get("away", ""), away)
            ):
                chosen_idx = idx
                break
        if chosen_idx is None:
            unmatched.append(match)
            continue
        used.add(chosen_idx)
        picked.append(items[chosen_idx]["href"])
    return picked, unmatched


def open_via_applescript(selected, max_streams, logger, browser):
    """用 AppleScript 从 schedules 页面提取直播链接并打开"""
    if not ensure_schedules_live_ready(logger, browser):
        return None
    schedules_tab = find_schedules_tab(browser)
    if not schedules_tab:
        logger.log("AppleScript: 未找到 schedules 页面", "WARN")
        return None
    js_escape = applescript_quote(build_watch_link_js())
    try:
        app = get_browser_app(browser)
        if browser == "safari":
            script = f'''
                tell application "{app}"
                    try
                        set js to "{js_escape}"
                        set links to do JavaScript js in tab {schedules_tab["tab_index"]} of window {schedules_tab["window_index"]}
                        return links
                    on error errMsg
                        return "JS_ERROR:" & errMsg
                    end try
                end tell
            '''
        else:
            script = f'''
                tell application "{app}"
                    try
                        set js to "{js_escape}"
                        set links to execute tab {schedules_tab["tab_index"]} of window {schedules_tab["window_index"]} javascript js
                        return links
                    on error errMsg
                        return "JS_ERROR:" & errMsg
                    end try
                end tell
            '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()

        if output.startswith("JS_ERROR:"):
            err_msg = output[9:]
            logger.log(f"AppleScript JS 执行被拒绝: {err_msg[:120]}", "WARN")
            if "JavaScript" in err_msg or "Apple Event" in err_msg or "AppleScript" in err_msg:
                if browser == "safari":
                    logger.log("请在 Safari 菜单: 开发 > 允许来自 Apple Events 的 JavaScript", "WARN")
                else:
                    logger.log("请在 Chrome 菜单: View → Developer → Allow JavaScript from Apple Events 开启权限", "WARN")
            return None

        candidates = parse_watch_candidates_payload(
            output,
            logger,
            source_label="AppleScript",
            stderr_text=result.stderr.strip(),
        )
        if not candidates:
            return None
        urls, unmatched = choose_watch_urls(candidates, selected, max_streams)
        if unmatched:
            detail = ", ".join(
                f"{m.get('league','')} | {m.get('team_h','')} vs {m.get('team_c','')}"
                for m in unmatched[:3]
            )
            logger.log(f"AppleScript: 未在 Ao vivo 页签匹配到选中比赛: {detail}", "ERROR")
            return None

        before = get_all_browser_window_ids(browser)
        opened = open_urls_in_new_browser_windows(
            urls[:max_streams], logger, browser, close_existing_watch_windows=True
        )
        if opened == 0:
            logger.log("AppleScript: 未能打开任何目标页面", "WARN")
            return None
        logger.log(f"AppleScript: 打开了 {opened} 个目标页面")
        ready_tabs = wait_for_watch_playback(urls[:max_streams], logger, browser)
        if not ready_tabs:
            return None
        arrange_watch_windows(ready_tabs, logger, browser)
        after = get_all_browser_window_ids(browser)
        new_ids = after - before
        logger.log(f"检测到 {len(new_ids)} 个新窗口")
        return {
            "new_window_ids": new_ids if new_ids else None,
            "ready_tabs": ready_tabs,
        } if new_ids else None
    except Exception as e:
        logger.log(f"AppleScript 失败: {e}", "WARN")
        return None


def open_via_pinchtab(selected, max_streams, logger, browser):
    if browser != "chrome":
        return None
    cli = find_pinchtab_cli()
    if not cli:
        logger.log("pinchtab 不存在: 未在 PATH、Homebrew、NVM 常见路径中找到", "WARN")
        return None
    logger.log(f"pinchtab: 使用 {cli}")
    try:
        r = subprocess.run([cli, "tab"], capture_output=True, text=True, timeout=15)
        tab_id = None
        for line in r.stdout.strip().split("\n"):
            if "sftraders.live/schedules" in line:
                tab_id = line.split()[0]
                break
        if not tab_id:
            logger.log("pinchtab: 未找到 schedules tab", "WARN")
            return None

        js = build_watch_link_js()
        r = subprocess.run([cli, "eval", js, "--tab", tab_id],
                           capture_output=True, text=True, timeout=15)
        candidates = parse_watch_candidates_payload(
            r.stdout.strip(),
            logger,
            source_label="pinchtab",
            stderr_text=r.stderr.strip(),
        )
        if not candidates:
            return None
        urls, unmatched = choose_watch_urls(candidates, selected, max_streams)
        if unmatched:
            detail = ", ".join(
                f"{m.get('league','')} | {m.get('team_h','')} vs {m.get('team_c','')}"
                for m in unmatched[:3]
            )
            logger.log(f"pinchtab: 未在 Ao vivo 页签匹配到选中比赛: {detail}", "ERROR")
            return None
        before = get_all_browser_window_ids(browser)
        opened = open_urls_in_new_browser_windows(
            urls[:max_streams], logger, browser, close_existing_watch_windows=True
        )
        if opened == 0:
            logger.log("pinchtab: 未能打开任何目标页面", "WARN")
            return None
        logger.log(f"pinchtab: 打开了 {opened} 个目标页面")
        ready_tabs = wait_for_watch_playback(urls[:max_streams], logger, browser)
        if not ready_tabs:
            return None
        arrange_watch_windows(ready_tabs, logger, browser)
        after = get_all_browser_window_ids(browser)
        new_ids = after - before
        logger.log(f"检测到 {len(new_ids)} 个新窗口")
        return {
            "new_window_ids": new_ids if new_ids else None,
            "ready_tabs": ready_tabs,
        } if new_ids else None
    except Exception as e:
        logger.log(f"pinchtab 失败: {e}", "WARN")
        return None


def open_via_ui_scripting(num_streams, logger):
    """用 UI Scripting: 导航到 schedules/live → Tab导航 + Enter 打开游戏窗口"""
    # 这是保留的 Chrome 兜底方案，当前主流程默认不会走到这里。
    browser = "chrome"
    app = get_browser_app(browser)

    def tab_n_and_enter(n):
        """发送 n 次 Tab 然后回车，激活 Chrome 后执行"""
        script = '\n'.join([
            f'tell application "{app}"',
            '    activate',
            'end tell',
            'delay 0.2',
            'tell application "System Events"',
            f'    tell process "{app}"',
            '        set frontmost to true',
            '        repeat ' + str(n) + ' times',
            '            key code 48',
            '            delay 0.5',
            '        end repeat',
            '        delay 0.3',
            '        key code 36',
            '    end tell',
            'end tell'
        ])
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=30)

    # 切换到 schedules/live
    if not ensure_schedules_live_ready(logger, browser):
        return None

    # 逐个打开游戏
    all_new_ids = []
    for i in range(num_streams):
        before = get_all_browser_window_ids(browser)

        # 如果不是第一路，先关闭上一轮的game窗口
        if i > 0:
            r = subprocess.run(
                ["osascript", "-e",
                 f'tell application "{app}" to close window 1'],
                capture_output=True, timeout=5
            )
            time.sleep(1)
            # 切换回 schedules/live tab
            if not ensure_schedules_live_ready(logger, browser):
                return None

        # Tab×3 + Enter 打开游戏 (Tab×3: Ao vivo tab → 第1个游戏行 → ASSISTIR)
        tab_count = 3 + i * 2
        logger.log(f"UI Scripting: 第 {i+1} 路 Tab×{tab_count}")
        tab_n_and_enter(tab_count)
        time.sleep(5)

        after = get_all_browser_window_ids(browser)
        new_ids = after - before
        if new_ids:
            logger.log(f"UI Scripting: 第 {i+1} 路打开成功")
            all_new_ids.extend(new_ids)
        else:
            logger.log(f"UI Scripting: 第 {i+1} 路未检测到新窗口 (Tab×{tab_count})", "WARN")

        time.sleep(2)

    if all_new_ids:
        logger.log(f"UI Scripting: 共打开 {len(all_new_ids)} 个窗口")
        return set(all_new_ids)
    return None


def open_match_videos(selected, max_streams, mode, logger, browser):
    if not ensure_schedules_live_ready(logger, browser):
        return None
    result = None
    if mode in ("auto", "applescript"):
        result = open_via_applescript(selected, max_streams, logger, browser)
    if not result and mode in ("auto", "pinchtab"):
        result = open_via_pinchtab(selected, max_streams, logger, browser)
    return result


# ═══════════════════════════════════════════════════════
#  检测视频窗口
# ═══════════════════════════════════════════════════════

def window_bounds_to_crop(bounds, screen):
    try:
        import Quartz
    except ImportError:
        return None
    gx = float(bounds.get("X", 0))
    gy = float(bounds.get("Y", 0))
    gw = float(bounds.get("Width", 0))
    gh = float(bounds.get("Height", 0))
    screen_frame = screen.frame()
    scale = screen.backingScaleFactor()
    screen_w = int(screen_frame.size.width * scale)
    screen_h = int(screen_frame.size.height * scale)
    phys_x = int((gx - screen_frame.origin.x) * scale)
    phys_y = int((gy - screen_frame.origin.y) * scale)
    phys_w, phys_h = int(gw * scale), int(gh * scale)
    phys_y += TITLE_BAR_PHYS
    phys_h -= TITLE_BAR_PHYS
    phys_x = max(0, phys_x); phys_y = max(0, phys_y)
    phys_w = min(phys_w, screen_w - phys_x)
    phys_h = min(phys_h, screen_h - phys_y)
    if phys_w < 100 or phys_h < 100:
        return None
    return (phys_x, phys_y, phys_w, phys_h)


def _intersection_area(bounds, screen_frame):
    gx = float(bounds.get("X", 0))
    gy = float(bounds.get("Y", 0))
    gw = float(bounds.get("Width", 0))
    gh = float(bounds.get("Height", 0))
    left = max(gx, float(screen_frame.origin.x))
    top = max(gy, float(screen_frame.origin.y))
    right = min(gx + gw, float(screen_frame.origin.x + screen_frame.size.width))
    bottom = min(gy + gh, float(screen_frame.origin.y + screen_frame.size.height))
    if right <= left or bottom <= top:
        return 0.0
    return (right - left) * (bottom - top)


def find_screen_for_window(bounds, screens):
    best_idx = None
    best_area = 0.0
    for idx, screen in enumerate(screens):
        area = _intersection_area(bounds, screen.frame())
        if area > best_area:
            best_area = area
            best_idx = idx
    if best_idx is None and screens:
        return 0, screens[0]
    return (best_idx, screens[best_idx]) if best_idx is not None else (None, None)


def detect_video_windows(new_window_ids, logger, browser):
    try:
        import Quartz
    except ImportError:
        logger.log("PyObjC 不可用", "ERROR")
        return []
    screens = Quartz.NSScreen.screens()
    if not screens:
        logger.log("未检测到任何屏幕", "ERROR")
        return []
    if len(screens) < 2:
        logger.log("未检测到副屏，改为在当前屏幕继续录制", "WARN")
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)

    video_wins = []
    for w in wins:
        wid = w.get("kCGWindowNumber")
        owner = w.get("kCGWindowOwnerName", "")
        name = w.get("kCGWindowName", "")
        if not browser_owner_matches(owner, browser):
            continue
        if new_window_ids and wid not in new_window_ids:
            continue
        if not new_window_ids and "SF Traders" not in name:
            continue
        screen_idx, screen = find_screen_for_window(w.get("kCGWindowBounds", {}), screens)
        if screen is None or screen_idx is None:
            continue
        crop = window_bounds_to_crop(w.get("kCGWindowBounds", {}), screen)
        if not crop:
            continue
        teams = ""
        for part in name.split(" - "):
            if " x " in part.lower() or " vs " in part.lower():
                teams = part.strip(); break
        video_wins.append({
            "window_id": wid,
            "name": name[:80],
            "teams": teams,
            "crop": crop,
            "screen_idx": screen_idx,
        })

    video_wins.sort(key=lambda w: (w.get("screen_idx", 0), w["crop"][1]))
    return video_wins


def stream_display_name(stream):
    selected = stream.get("selected_match") or {}
    home = selected.get("team_h", "")
    away = selected.get("team_c", "")
    if home and away:
        return f"{home} vs {away}"
    if stream.get("teams"):
        return stream["teams"]
    return stream.get("match_id", "unknown_stream")


def get_stream_watch_url(stream):
    return (
        stream.get("watch_url")
        or (stream.get("selected_match") or {}).get("watch_url")
        or ""
    )


def get_current_stream_video_window(stream, logger, browser):
    current_window_id = stream.get("window_id")
    if not current_window_id:
        return None
    matches = detect_video_windows({current_window_id}, logger, browser)
    return matches[0] if matches else None


def update_stream_window_binding(stream, recorder, stream_idx, ready_tab, video_win):
    if video_win:
        stream["window_id"] = video_win.get("window_id", stream.get("window_id"))
        stream["crop"] = video_win.get("crop", stream.get("crop"))
        stream["screen_idx"] = video_win.get("screen_idx", stream.get("screen_idx", 0))
        if video_win.get("teams"):
            stream["teams"] = video_win["teams"]
    layout = (ready_tab or {}).get("layout") or {}
    stream["content_crop"] = layout.get("page_content_rect") or stream.get("content_crop")
    stream["browser_window_index"] = (ready_tab or {}).get(
        "window_index", stream.get("browser_window_index")
    )
    stream["browser_tab_index"] = (ready_tab or {}).get(
        "tab_index", stream.get("browser_tab_index")
    )
    stream["watch_url"] = (ready_tab or {}).get("url") or get_stream_watch_url(stream)

    if recorder and 0 <= stream_idx < len(recorder.streams):
        recorder_stream = recorder.streams[stream_idx]
        recorder_stream["window_id"] = stream.get("window_id")
        recorder_stream["crop"] = stream.get("crop")
        recorder_stream["content_crop"] = stream.get("content_crop")


def wait_for_specific_watch_playback(window_index, tab_index, url, logger, browser, timeout):
    deadline = time.time() + timeout
    fallback_title = url
    while time.time() < deadline:
        state = get_watch_playback_state(window_index, tab_index, browser)
        if state and state_has_active_playback(state):
            ready_tab = build_ready_watch_tab(
                {
                    "window_index": window_index,
                    "tab_index": tab_index,
                    "url": url,
                    "title": state.get("title", fallback_title),
                },
                state,
            )
            logger.log(f"播放已恢复: {ready_tab.get('title', fallback_title)}")
            return ready_tab
        time.sleep(2)
    logger.log(f"等待恢复播放超时: {fallback_title}", "WARN")
    return None


def refresh_stream_watch_window(stream, logger, browser):
    browser_window_index = stream.get("browser_window_index")
    browser_tab_index = stream.get("browser_tab_index")
    watch_url = get_stream_watch_url(stream)
    if not browser_window_index or not browser_tab_index or not watch_url:
        logger.log(f"[{stream_display_name(stream)}] 缺少刷新所需的窗口绑定信息", "WARN")
        return None
    tab_info = get_browser_tab_info(browser, browser_window_index, browser_tab_index)
    current_url = (tab_info or {}).get("url") or ""
    if current_url and current_url != watch_url:
        logger.log(
            f"[{stream_display_name(stream)}] 当前绑定标签页已漂移到其它比赛，跳过刷新并改为重开 "
            f"(expected={watch_url}, current={current_url})",
            "WARN",
        )
        return None
    if not refresh_browser_tab(browser_window_index, logger, browser, tab_index=browser_tab_index):
        return None
    return wait_for_specific_watch_playback(
        browser_window_index,
        browser_tab_index,
        watch_url,
        logger,
        browser,
        timeout=RECOVERY_REFRESH_TIMEOUT_SECONDS,
    )


def reopen_stream_watch_window(stream, logger, browser):
    watch_url = get_stream_watch_url(stream)
    if not watch_url:
        logger.log(f"[{stream_display_name(stream)}] 缺少 watch_url，无法重开视频页", "WARN")
        return None, None
    # Recovery should not stack duplicate tabs for the same match. Clear stale copies first.
    close_browser_video_windows(browser, logger, [watch_url])
    before = get_all_browser_window_ids(browser)
    opened = open_urls_in_new_browser_windows([watch_url], logger, browser)
    if opened <= 0:
        logger.log(f"[{stream_display_name(stream)}] 重开视频窗口失败", "WARN")
        return None, None
    ready_tabs = wait_for_watch_playback(
        [watch_url], logger, browser, timeout=RECOVERY_REOPEN_TIMEOUT_SECONDS
    )
    if not ready_tabs:
        return None, None
    time.sleep(1)
    after = get_all_browser_window_ids(browser)
    new_ids = after - before
    if not new_ids:
        logger.log(f"[{stream_display_name(stream)}] 已重开页面，但未识别到新的系统窗口", "WARN")
        return None, None
    video_wins = detect_video_windows(new_ids, logger, browser)
    if not video_wins:
        logger.log(f"[{stream_display_name(stream)}] 新视频页已播放，但未定位到窗口裁剪区域", "WARN")
        return None, None
    return ready_tabs[0], video_wins[0]


def attempt_stream_recovery(
    stream_idx,
    streams,
    recorder,
    logger,
    browser,
    recovery_state,
    recovery_locks,
    recovery_events,
    recovery_events_lock,
    trigger,
    detail=None,
    request_restart_on_reopen=True,
    stream_status=None,
    anomaly_notifier=None,
    on_stream_abandoned=None,
):
    if stream_idx < 0 or stream_idx >= len(streams):
        return False
    stream = streams[stream_idx]
    state = recovery_state[stream_idx]

    if state.get("abandoned"):
        logger.log(f"[{stream_display_name(stream)}] 已被标记为放弃恢复，忽略 {trigger}", "WARN")
        return False

    lock = recovery_locks[stream_idx]
    if not lock.acquire(blocking=False):
        logger.log(f"[{stream_display_name(stream)}] 已有恢复任务在进行，忽略重复触发 {trigger}", "WARN")
        return False

    event = {
        "timestamp": datetime.now().isoformat(),
        "stream_index": stream_idx,
        "match_id": stream.get("match_id", ""),
        "teams": stream_display_name(stream),
        "trigger": trigger,
        "detail": detail or {},
        "success": False,
        "action": "none",
        "requested_segment_restart": False,
        "recovery_abandoned": False,
    }

    try:
        now_ts_local = time.time()
        cooldown_left = RECOVERY_COOLDOWN_SECONDS - (now_ts_local - state.get("last_attempt_ts", 0.0))
        if cooldown_left > 0:
            logger.log(
                f"[{stream_display_name(stream)}] 距离上次恢复过近，{cooldown_left:.0f}s 后再试",
                "WARN",
            )
            event["action"] = "cooldown_skip"
            return False

        state["last_attempt_ts"] = now_ts_local
        attempt_no = int(state.get("attempts", 0)) + 1
        event["attempt"] = attempt_no
        logger.log(f"[{stream_display_name(stream)}] 触发恢复: {trigger} (第 {attempt_no} 次)")

        ready_tab = refresh_stream_watch_window(stream, logger, browser)
        if ready_tab:
            video_win = get_current_stream_video_window(stream, logger, browser)
            update_stream_window_binding(stream, recorder, stream_idx, ready_tab, video_win)
            state["attempts"] = 0
            state["successes"] = int(state.get("successes", 0)) + 1
            event["success"] = True
            event["action"] = "refresh"
            return True

        ready_tab, video_win = reopen_stream_watch_window(stream, logger, browser)
        if ready_tab:
            update_stream_window_binding(stream, recorder, stream_idx, ready_tab, video_win)
            state["attempts"] = 0
            state["successes"] = int(state.get("successes", 0)) + 1
            event["success"] = True
            event["action"] = "reopen"
            if request_restart_on_reopen and recorder:
                recorder.request_segment_restart(
                    f"{trigger}_stream_{stream_idx + 1}_reopen"
                )
                event["requested_segment_restart"] = True
            return True

        state["attempts"] = attempt_no
        state["failures"] = int(state.get("failures", 0)) + 1
        event["action"] = "failed"
        if attempt_no >= RECOVERY_MAX_ATTEMPTS:
            state["abandoned"] = True
            event["recovery_abandoned"] = True
            if stream_status is not None and stream_idx < len(stream_status):
                stream_status[stream_idx] = "failed"
            logger.log(
                f"[{stream_display_name(stream)}] 连续恢复失败 {attempt_no} 次，标记该路为放弃恢复",
                "ERROR",
            )
            if anomaly_notifier:
                anomaly_notifier(
                    "录制异常提醒",
                    [
                        f"比赛流已放弃恢复：{stream_display_name(stream)}",
                        f"触发原因：{trigger}",
                        f"连续恢复失败：{attempt_no} 次",
                    ],
                )
            if on_stream_abandoned:
                on_stream_abandoned(stream_idx, stream, trigger, attempt_no)
        else:
            logger.log(
                f"[{stream_display_name(stream)}] 本次恢复失败，后续仍会继续监听",
                "WARN",
            )
        return False
    finally:
        with recovery_events_lock:
            recovery_events.append(event)
        lock.release()


# ═══════════════════════════════════════════════════════
#  秒级数据采集
# ═══════════════════════════════════════════════════════

class BettingDataPoller:
    """每秒调 API 采集赔率数据"""

    def __init__(self, cookie, template, gtypes=None,
                 poll_interval=DATA_POLL_INTERVAL, use_dashboard=False, feed_url=DEFAULT_URL):
        self.cookie = cookie
        self.template = template
        self.gtypes = gtypes or ALL_GTYPES
        self.poll_interval = poll_interval
        self.use_dashboard = use_dashboard
        self.feed_url = feed_url or DEFAULT_URL
        self.data = []          # 全量数据
        self._stop = threading.Event()
        self._poll_count = 0
        self._error_count = 0

    def start(self):
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(timeout=self.poll_interval)

    def stop(self):
        self._stop.set()

    @property
    def poll_count(self):
        return self._poll_count

    @property
    def error_count(self):
        return self._error_count

    def _poll_once(self):
        ts = datetime.now(timezone.utc).isoformat()

        if self.use_dashboard:
            payload = fetch_fresh_dashboard_payload()
            if payload:
                dashboard_rows = 0
                for gtype, fields in iter_dashboard_running_fields(payload, self.gtypes):
                    self.data.append({
                        "timestamp": ts, "gtype": gtype,
                        "gid": fields.get("GID", ""),
                        "ecid": fields.get("ECID", ""),
                        "team_h": fields.get("TEAM_H", ""),
                        "team_c": fields.get("TEAM_C", ""),
                        "score_h": fields.get("SCORE_H", ""),
                        "score_c": fields.get("SCORE_C", ""),
                        "fields": fields,
                    })
                    dashboard_rows += 1
                if dashboard_rows > 0:
                    self._poll_count += 1
                    return

            if not self.cookie or not self.template:
                self._error_count += 1
                return

        # 直连 API
        if not self.cookie or not self.template:
            self._error_count += 1
            return
        for gtype in self.gtypes:
            try:
                body = build_game_list_body(
                    self.template, gtype=gtype, showtype="live", rtype="rb")
                raw = fetch_xml(self.feed_url, body, self.cookie, timeout=5)
                parsed = parse_game_list_response(raw)
                for g in parsed.get("games", []):
                    fields = g.get("fields", {})
                    if fields.get("RUNNING") == "Y":
                        self.data.append({
                            "timestamp": ts, "gtype": gtype,
                            "gid": fields.get("GID", ""),
                            "ecid": fields.get("ECID", ""),
                            "team_h": fields.get("TEAM_H", ""),
                            "team_c": fields.get("TEAM_C", ""),
                            "score_h": fields.get("SCORE_H", ""),
                            "score_c": fields.get("SCORE_C", ""),
                            "fields": fields,
                        })
                self._poll_count += 1
            except Exception:
                self._error_count += 1

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            for r in self.data:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def save_segment(self, path, start_idx):
        """保存 start_idx 之后的新数据"""
        with open(path, "w", encoding="utf-8") as f:
            for r in self.data[start_idx:]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return len(self.data) - start_idx

    def get_live_gids(self):
        """最新快照仍在滚球的 GID 集合"""
        if not self.data:
            return set()
        latest_ts = max(r["timestamp"] for r in self.data)
        return {r["gid"] for r in self.data
                if r["timestamp"] == latest_ts and r["gid"]}

    def get_stopped_gids(self, prev_live_gids):
        """从 prev_live_gids 变为非滚球的 GID"""
        curr = self.get_live_gids()
        return prev_live_gids - curr


# ═══════════════════════════════════════════════════════
#  黑屏检测
# ═══════════════════════════════════════════════════════

class BlackScreenDetector:
    """用 ffprobe 检测视频帧亮度，判定黑屏"""

    def __init__(self, video_paths, threshold=BLACK_THRESHOLD,
                 check_interval=BLACK_CHECK_INTERVAL,
                 consecutive_limit=BLACK_CONSECUTIVE):
        self.video_paths = video_paths  # list of current file paths (per stream)
        self.threshold = threshold
        self.check_interval = check_interval
        self.consecutive_limit = consecutive_limit
        self._stop = threading.Event()
        self._black_counts = [0] * len(video_paths)
        self._last_result = [False] * len(video_paths)

    def update_paths(self, paths):
        """录制切换分段时更新文件路径"""
        self.video_paths = paths

    def start(self, on_black_screen):
        """
        on_black_screen(stream_idx) - 回调，返回 True=恢复成功
        """
        while not self._stop.is_set():
            self._stop.wait(timeout=self.check_interval)
            if self._stop.is_set():
                break
            for i, path in enumerate(self.video_paths):
                if not path or not os.path.exists(path):
                    continue
                brightness = self._probe_brightness(path)
                if brightness is not None and brightness < self.threshold:
                    self._black_counts[i] += 1
                else:
                    self._black_counts[i] = 0
                self._last_result[i] = self._black_counts[i] >= self.consecutive_limit

                if self._last_result[i]:
                    ok = on_black_screen(i)
                    if ok:
                        self._black_counts[i] = 0
                        self._last_result[i] = False
                    else:
                        # 刷新失败，计数器继续累计
                        pass

    def stop(self):
        self._stop.set()

    def _probe_brightness(self, path):
        """用 ffmpeg 提取一帧 PNG，计算平均亮度 (0-255)"""
        try:
            # 提取一帧到临时 PNG
            png = path + ".brightness_tmp.png"
            r1 = subprocess.run(
                ["ffmpeg", "-y", "-ss", "0",
                 "-i", path, "-frames:v", "1", "-f", "image2", png],
                capture_output=True, text=True, timeout=10,
            )
            if r1.returncode != 0 or not os.path.exists(png):
                return None
            # 用 Python 读取 PNG 像素计算亮度
            try:
                from PIL import Image
            except ImportError:
                # 不用 PIL，用 ffmpeg 的 histogram 滤波器
                r2 = subprocess.run(
                    ["ffmpeg", "-i", png,
                     "-vf", "signalstats",
                     "-f", "null", "-"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in r2.stdout.split("\n") + r2.stderr.split("\n"):
                    if "YAVG" in line:
                        val = float(line.split("YAVG")[1].split()[0])
                        os.remove(png)
                        return val
                os.remove(png)
                return None
            img = Image.open(png)
            import numpy as np
            arr = np.array(img.convert("RGB"))
            brightness = arr.mean()
            os.remove(png)
            return brightness
        except Exception:
            try:
                os.remove(path + ".brightness_tmp.png")
            except Exception:
                pass
            return None


# ═══════════════════════════════════════════════════════
#  刷新 Chrome 页面
# ═══════════════════════════════════════════════════════

def refresh_browser_tab(window_index, logger, browser, tab_index=None):
    app = get_browser_app(browser)
    try:
        if browser == "safari":
            target = (
                f"tab {tab_index} of window {window_index}"
                if tab_index else f"current tab of window {window_index}"
            )
            scripts = [
                f'''
                    tell application "{app}"
                        try
                            do JavaScript "document.location.reload(true)" in {target}
                            return "OK"
                        on error errMsg number errNum
                            return "ERROR:" & errNum & ":" & errMsg
                        end try
                    end tell
                ''',
                f'''
                    tell application "{app}"
                        try
                            set currentUrl to URL of {target}
                            set URL of {target} to currentUrl
                            return "OK"
                        on error errMsg number errNum
                            return "ERROR:" & errNum & ":" & errMsg
                        end try
                    end tell
                ''',
            ]
        else:
            if tab_index:
                scripts = [f'''
                    tell application "{app}"
                        try
                            reload tab {tab_index} of window {window_index}
                            return "OK"
                        on error errMsg number errNum
                            return "ERROR:" & errNum & ":" & errMsg
                        end try
                    end tell
                ''']
            else:
                scripts = [f'''
                    tell application "{app}"
                        try
                            reload active tab of window {window_index}
                            return "OK"
                        on error errMsg number errNum
                            return "ERROR:" & errNum & ":" & errMsg
                        end try
                    end tell
                ''']

        last_detail = ""
        for script in scripts:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
            output = (result.stdout or "").strip()
            detail = (result.stderr or "").strip()
            if result.returncode == 0 and (not output or output == "OK"):
                suffix = f"/tab {tab_index}" if tab_index else ""
                logger.log(f"已刷新窗口 {window_index}{suffix}")
                return True
            if output.startswith("ERROR:"):
                last_detail = output
            elif detail:
                last_detail = detail

        if last_detail:
            logger.log(f"刷新失败: {last_detail[:160]}", "WARN")
        else:
            logger.log("刷新失败: 未获得成功确认", "WARN")
        return False
    except Exception as e:
        logger.log(f"刷新失败: {e}")
        return False


def close_browser_video_windows(browser, logger, target_urls=None):
    """关闭 /watch 视频窗口，保留并恢复 schedules/live 主页。

    默认关闭全部 /watch 页；如果提供 target_urls，则只关闭命中的目标链接。
    """
    try:
        app = get_browser_app(browser)
        target_urls = {
            (url or "").rstrip("/")
            for url in (target_urls or [])
            if (url or "").strip()
        }
        if target_urls:
            clauses = [f'u is "{applescript_quote(url)}"' for url in sorted(target_urls)]
            safari_match = "(" + " or ".join(clauses) + ")"
            other_match = "(" + " or ".join(clauses) + ")"
        else:
            safari_match = 'u contains "/watch"'
            other_match = 'u contains "/watch"'
        if browser == "safari":
            script = f'''
                tell application "{app}"
                    set closedCount to 0
                    repeat 32 times
                        set didClose to false
                        repeat with w in every window
                            repeat with t in every tab of w
                                try
                                    set u to URL of t as text
                                    if {safari_match} then
                                        if (count of tabs of w) > 1 then
                                            close t
                                        else
                                            close w
                                        end if
                                        set closedCount to closedCount + 1
                                        set didClose to true
                                        exit repeat
                                    end if
                                end try
                            end repeat
                            if didClose then exit repeat
                        end repeat
                        if didClose is false then exit repeat
                    end repeat
                    return closedCount
                end tell
            '''
        else:
            script = f'''
                tell application "{app}"
                    set closedCount to 0
                    set watchWindows to {{}}
                    repeat with w in windows
                        try
                            set u to URL of active tab of w
                            if {other_match} then
                                set end of watchWindows to w
                            end if
                        end try
                    end repeat
                    repeat with w in watchWindows
                        try
                            close w
                            set closedCount to closedCount + 1
                        end try
                    end repeat
                    return closedCount
                end tell
            '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 and result.stderr:
            logger.log(f"关闭视频窗口脚本返回错误: {result.stderr.strip()[:200]}", "WARN")
        closed_count = (result.stdout or "").strip() or "0"
        if not find_schedules_tab(browser):
            open_schedules_live_page(logger, browser)
            time.sleep(1)
        logger.log(f"已关闭视频窗口 {closed_count} 个，并保留 schedules/live 主页")
    except Exception as e:
        logger.log(f"关闭视频窗口失败: {e}")


def collect_match_watch_urls(matches):
    return [m.get("watch_url", "") for m in (matches or []) if m.get("watch_url")]


def close_specific_watch_window(window_index, tab_index, browser, logger):
    """关闭指定的 /watch 页，尽量不影响其他正常录制流。"""
    try:
        app = get_browser_app(browser)
        if browser == "safari":
            script = f'''
                tell application "{app}"
                    try
                        set targetWindow to window {window_index}
                        set targetTab to tab {tab_index} of targetWindow
                        set u to URL of targetTab as text
                        if u contains "/watch" then
                            if (count of tabs of targetWindow) > 1 then
                                close targetTab
                            else
                                close targetWindow
                            end if
                            return "CLOSED"
                        end if
                        return "SKIP"
                    on error errMsg
                        return "ERROR:" & errMsg
                    end try
                end tell
            '''
        else:
            script = f'''
                tell application "{app}"
                    try
                        set targetWindow to window {window_index}
                        set u to URL of active tab of targetWindow as text
                        if u contains "/watch" then
                            close targetWindow
                            return "CLOSED"
                        end if
                        return "SKIP"
                    on error errMsg
                        return "ERROR:" & errMsg
                    end try
                end tell
            '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        output = (result.stdout or "").strip()
        if output == "CLOSED":
            logger.log(f"已关闭异常比赛窗口: window={window_index} tab={tab_index}")
            if not find_schedules_tab(browser):
                open_schedules_live_page(logger, browser)
            return True
        if output.startswith("ERROR:"):
            logger.log(f"关闭异常比赛窗口失败: {output[:160]}", "WARN")
            return False
        return False
    except Exception as e:
        logger.log(f"关闭异常比赛窗口失败: {e}", "WARN")
        return False


def run_preflight(logger, browser):
    logger.log("开始运行环境检查")

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        logger.log(f"依赖检查: ffmpeg 已找到 ({ffmpeg_bin})")
    else:
        logger.log("依赖检查: 未找到 ffmpeg，录制阶段会失败", "WARN")

    browser_path = BROWSER_APP_PATHS.get(browser, BROWSER_APP_PATHS[BROWSER_DEFAULT])
    browser_name = get_browser_app(browser)
    if os.path.exists(browser_path):
        logger.log(f"依赖检查: {browser_name} 已安装")
    else:
        logger.log(f"依赖检查: 未检测到 {browser_name}.app", "ERROR")

    if browser == "chrome":
        pinchtab_cli = find_pinchtab_cli()
        if pinchtab_cli:
            logger.log(f"依赖检查: pinchtab 可用 ({pinchtab_cli})")
        else:
            logger.log("依赖检查: pinchtab 不可用，备用打开链路将仅保留 AppleScript", "WARN")

        cdp_base_url = find_cdp_base_url()
        if cdp_base_url:
            logger.log(f"依赖检查: Chrome CDP 可用 ({cdp_base_url})")
        else:
            logger.log(
                "依赖检查: Chrome CDP 不可用。浏览器会话数据源将跳过，回退到 dashboard/env。",
                "WARN",
            )
            logger.log(
                "如需直接复用浏览器登录态采集数据，请先为 Chrome 开启远程调试并设置 CHROME_CDP_URL。",
                "WARN",
            )
    else:
        pass


# ═══════════════════════════════════════════════════════
#  数据匹配
# ═══════════════════════════════════════════════════════

def match_data_to_stream(data, teams, gtype=None, selected_match=None):
    """按队名模糊匹配数据到视频窗口"""
    if selected_match:
        selected_gid = selected_match.get("gid", "")
        selected_ecid = selected_match.get("ecid", "")
        if selected_gid or selected_ecid:
            matched = []
            for r in data:
                if selected_gid and r.get("gid") == selected_gid:
                    matched.append(r)
                    continue
                if selected_ecid and r.get("ecid") == selected_ecid:
                    matched.append(r)
            if matched:
                return matched
        match_gtype = selected_match.get("gtype", "") or gtype
        home = selected_match.get("data_team_h", "") or selected_match.get("team_h", "")
        away = selected_match.get("data_team_c", "") or selected_match.get("team_c", "")
        matched = []
        for r in data:
            if match_gtype and r.get("gtype") != match_gtype:
                continue
            if same_match_text(r.get("team_h", ""), home) and same_match_text(r.get("team_c", ""), away):
                matched.append(r)
        if matched:
            return matched
    if not teams:
        return data
    parts = teams.lower().replace(" x ", " ").replace(" vs ", " ").split()
    matched = []
    for r in data:
        if gtype and r.get("gtype") != gtype:
            continue
        haystack = f"{r['team_h']} {r['team_c']}".lower()
        score = sum(1 for p in parts if p in haystack and len(p) > 1)
        if score >= 1:
            matched.append(r)
    return matched


def has_women_marker(*texts):
    for text in texts:
        normalized = normalize_match_text(text)
        if not normalized:
            continue
        if "women" in normalized or normalized.endswith("w") or "女" in (text or ""):
            return True
    return False


def parse_schedule_kickoff_minutes(selected_match):
    for text in (
        (selected_match or {}).get("kickoff", ""),
        (selected_match or {}).get("league", ""),
    ):
        match = re.search(r"(\d{1,2}):(\d{2})", text or "")
        if match:
            return int(match.group(1)) * 60 + int(match.group(2))
    return None


def parse_feed_datetime_minutes(value):
    text = (value or "").strip().lower()
    match = re.search(r"(\d{1,2}):(\d{2})\s*([ap])", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        marker = match.group(3)
        if marker == "p" and hour != 12:
            hour += 12
        if marker == "a" and hour == 12:
            hour = 0
        return hour * 60 + minute
    match = re.search(r"(\d{1,2}):(\d{2})", text)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))
    return None


def kickoff_distance_minutes(schedule_min, feed_min):
    if schedule_min is None or feed_min is None:
        return None
    raw = (schedule_min - feed_min) % (24 * 60)
    return min(abs(raw - target) for target in (0, 60, 24 * 60 - 60))


def minutes_until_schedule_kickoff(selected_match, now=None):
    kickoff_min = parse_schedule_kickoff_minutes(selected_match)
    if kickoff_min is None:
        return None
    now = now or datetime.now(SCHEDULE_TIMEZONE)
    current_min = now.hour * 60 + now.minute
    delta = (kickoff_min - current_min) % (24 * 60)
    if delta > 12 * 60:
        delta -= 24 * 60
    return delta


def filter_matches_ready_to_record(selected_matches, lead_minutes, logger):
    ready = []
    for match in selected_matches:
        delta = minutes_until_schedule_kickoff(match)
        if delta is None:
            ready.append(match)
            continue
        if delta > lead_minutes:
            if match.get("watch_url"):
                logger.log(
                    f"赛程时间仅作弱参考: {match.get('team_h','')} vs {match.get('team_c','')} "
                    f"(距离开赛约 {delta} 分钟)，但前端已有可点击直播链接，继续尝试打开并验证播放",
                    "WARN",
                )
                ready.append(match)
                continue
            logger.log(
                f"比赛未到录制窗口，先跳过: {match.get('team_h','')} vs {match.get('team_c','')} "
                f"(距离开赛约 {delta} 分钟，且当前没有可点击直播链接)",
                "WARN",
            )
            continue
        ready.append(match)
    return ready


def dedupe_live_snapshot_rows(rows):
    unique = []
    seen = set()
    for row in rows:
        key = (
            row.get("gtype", ""),
            row.get("gid", ""),
            row.get("ecid", ""),
            row.get("team_h", ""),
            row.get("team_c", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def fetch_live_data_snapshot(cookie, template, gtypes=None, use_dashboard=False, feed_url=DEFAULT_URL):
    rows = []
    target_gtypes = gtypes or ALL_GTYPES

    if use_dashboard:
        payload = fetch_fresh_dashboard_payload()
        if payload:
            for gtype, fields in iter_dashboard_running_fields(payload, target_gtypes):
                rows.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "gtype": gtype,
                    "gid": fields.get("GID", ""),
                    "ecid": fields.get("ECID", ""),
                    "team_h": fields.get("TEAM_H", ""),
                    "team_c": fields.get("TEAM_C", ""),
                    "league": fields.get("LEAGUE", ""),
                    "score_h": fields.get("SCORE_H", ""),
                    "score_c": fields.get("SCORE_C", ""),
                    "fields": fields,
                })
            if rows:
                return dedupe_live_snapshot_rows(rows)
        if not cookie or not template:
            return dedupe_live_snapshot_rows(rows)

    for gtype in target_gtypes:
        body = build_game_list_body(
            template, gtype=gtype, showtype="live", rtype="rb"
        )
        raw = fetch_xml(feed_url or DEFAULT_URL, body, cookie, timeout=5)
        parsed = parse_game_list_response(raw)
        for game in parsed.get("games", []):
            fields = game.get("fields", {})
            if fields.get("RUNNING") != "Y":
                continue
            rows.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "gtype": gtype,
                "gid": fields.get("GID", ""),
                "ecid": fields.get("ECID", ""),
                "team_h": fields.get("TEAM_H", ""),
                "team_c": fields.get("TEAM_C", ""),
                "league": fields.get("LEAGUE", ""),
                "score_h": fields.get("SCORE_H", ""),
                "score_c": fields.get("SCORE_C", ""),
                "fields": fields,
            })
    return dedupe_live_snapshot_rows(rows)


def score_snapshot_candidate_for_selected(selected_match, candidate):
    if candidate.get("gtype") != (selected_match.get("gtype") or candidate.get("gtype")):
        return -1

    selected_home = selected_match.get("team_h", "")
    selected_away = selected_match.get("team_c", "")
    candidate_home = candidate.get("team_h", "")
    candidate_away = candidate.get("team_c", "")
    selected_league = selected_match.get("league", "")
    candidate_league = candidate.get("league", "")
    fields = candidate.get("fields", {}) or {}
    selected_home_aliases = get_team_aliases(selected_home)
    selected_away_aliases = get_team_aliases(selected_away)
    selected_league_aliases = get_league_aliases(selected_league)
    candidate_home_n = normalize_match_text(candidate_home)
    candidate_away_n = normalize_match_text(candidate_away)
    candidate_league_n = normalize_league_text(candidate_league)

    score = 0
    team_name_hits = 0
    league_hit = False
    if selected_home and same_match_text(selected_home, candidate_home):
        score += 90
        team_name_hits += 1
    elif candidate_home_n and any(
        alias and (alias == candidate_home_n or alias in candidate_home_n or candidate_home_n in alias)
        for alias in selected_home_aliases
    ):
        score += 82
        team_name_hits += 1
    if selected_away and same_match_text(selected_away, candidate_away):
        score += 90
        team_name_hits += 1
    elif candidate_away_n and any(
        alias and (alias == candidate_away_n or alias in candidate_away_n or candidate_away_n in alias)
        for alias in selected_away_aliases
    ):
        score += 82
        team_name_hits += 1

    if selected_league and candidate_league and same_league_text(selected_league, candidate_league):
        score += 75
        league_hit = True
    elif candidate_league_n and any(
        alias and (alias == candidate_league_n or alias in candidate_league_n or candidate_league_n in alias)
        for alias in selected_league_aliases
    ):
        score += 68
        league_hit = True

    # 对于来自 schedules/live 的比赛，至少要有一侧队名能对上；
    # 不能只靠联赛、开赛时间和 TV 标记去“猜中”一场比赛。
    if (
        selected_match.get("watch_url")
        and selected_home and selected_away
        and team_name_hits == 0
    ):
        return -1

    selected_women = has_women_marker(selected_home, selected_away, selected_league)
    candidate_women = has_women_marker(candidate_home, candidate_away, candidate_league)
    if selected_women == candidate_women:
        score += 30
    elif selected_women != candidate_women:
        score -= 20

    kickoff_distance = kickoff_distance_minutes(
        parse_schedule_kickoff_minutes(selected_match),
        parse_feed_datetime_minutes(fields.get("DATETIME", "")),
    )
    if kickoff_distance is not None:
        if kickoff_distance <= 3:
            score += 80
        elif kickoff_distance <= 10:
            score += 50
        else:
            score -= min(40, kickoff_distance // 5)

    if selected_match.get("watch_url"):
        if fields.get("TV_WEB_SW") == "Y":
            score += 45
        elif fields.get("CENTER_TV"):
            score += 30
        else:
            score -= 10

    if selected_match.get("team_c") and not candidate_away:
        score -= 20

    return score


def rank_snapshot_candidates_for_match(selected_match, snapshot_rows, used_gids):
    ranked = []
    for candidate in snapshot_rows:
        if candidate.get("gid") and candidate["gid"] in used_gids:
            continue
        score = score_snapshot_candidate_for_selected(selected_match, candidate)
        if score < 60:
            continue
        ranked.append((score, candidate))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def bind_selected_matches_to_feed(selected_matches, snapshot_rows, logger):
    if not selected_matches or not snapshot_rows:
        return

    snapshot_rows = dedupe_live_snapshot_rows(snapshot_rows)
    ai_alias_attempted = False

    used_gids = set()
    for match in selected_matches:
        if match.get("gid") or match.get("ecid"):
            continue

        ranked = rank_snapshot_candidates_for_match(match, snapshot_rows, used_gids)
        if not ranked and not ai_alias_attempted:
            ai_alias_attempted = True
            learned = apply_ai_alias_batch(selected_matches, logger)
            if learned.get("team") or learned.get("league"):
                ranked = rank_snapshot_candidates_for_match(match, snapshot_rows, used_gids)
        if not ranked:
            logger.log(
                f"未给比赛绑定到数据源: {match.get('team_h','')} vs {match.get('team_c','')}",
                "WARN",
            )
            continue

        best_score, best = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else None
        if second_score is not None and best_score - second_score < 20:
            logger.log(
                f"比赛数据绑定不够确定，先跳过: {match.get('team_h','')} vs {match.get('team_c','')}",
                "WARN",
            )
            continue

        match["gid"] = best.get("gid", "")
        match["ecid"] = best.get("ecid", "")
        match["data_team_h"] = best.get("team_h", "")
        match["data_team_c"] = best.get("team_c", "")
        match["data_league"] = best.get("league", "")
        match["_feed_binding"] = {
            "score": best_score,
            "gid": best.get("gid", ""),
            "ecid": best.get("ecid", ""),
            "team_h": best.get("team_h", ""),
            "team_c": best.get("team_c", ""),
            "league": best.get("league", ""),
        }
        record_successful_binding_alias_learning(match, best, logger)
        if best.get("gid"):
            used_gids.add(best["gid"])
        logger.log(
            f"比赛已绑定数据源: {match.get('team_h','')} vs {match.get('team_c','')} "
            f"-> gid={best.get('gid','')} ({best.get('team_h','')} vs {best.get('team_c','')})"
        )


def prioritize_selected_matches(selected_matches, max_streams, logger):
    if max_streams <= 0 or len(selected_matches) <= max_streams:
        if any(match.get("gid") or match.get("ecid") for match in selected_matches):
            bound_count = sum(1 for m in selected_matches if m.get("gid") or m.get("ecid"))
            logger.log(
                f"自动模式优先保留已绑定数据源的比赛: {bound_count}/{len(selected_matches)} 场可对齐"
            )
        return selected_matches

    bound = [
        match for match in selected_matches
        if match.get("gid") or match.get("ecid")
    ]
    unbound = [
        match for match in selected_matches
        if not (match.get("gid") or match.get("ecid"))
    ]
    bound.sort(
        key=lambda match: (match.get("_feed_binding") or {}).get("score", 0),
        reverse=True,
    )
    prioritized = bound + unbound
    chosen = prioritized[:max_streams]
    if bound:
        logger.log(
            f"自动模式优先保留已绑定数据源的比赛: {len(bound)}/{len(selected_matches)} 场可对齐"
        )
    return chosen


def require_bound_data_matches(selected_matches, desired_count, logger):
    if not selected_matches:
        return []

    kept = []
    fillers = []
    skipped = []
    for match in selected_matches:
        if match.get("gid") or match.get("ecid"):
            match["data_binding_status"] = "bound"
            match["recording_note"] = ""
            kept.append(match)
        else:
            match["data_binding_status"] = "unbound"
            match["recording_note"] = "未匹配到对应比赛数据，仅用于测试录制稳定性"
            skipped.append(match)

    needed = max(0, int(desired_count or 0) - len(kept))
    if needed > 0 and skipped:
        fillers = skipped[:needed]
        skipped = skipped[needed:]
        for match in fillers:
            logger.log(
                f"补录测试场次（暂无比赛数据）: {match.get('team_h','')} vs {match.get('team_c','')}",
                "WARN",
            )

    if skipped:
        logger.log(
            f"未进入本轮录制（无数据且超出补位需求）: {len(skipped)} 场",
            "WARN",
        )

    if fillers:
        logger.log(
            f"强制比赛-数据对应优先: 绑定数据 {len(kept)} 场，不足时补入 {len(fillers)} 场纯录制测试场次"
        )
    elif skipped:
        logger.log(
            f"强制比赛-数据一一对应: 保留 {len(kept)} 场，剩余 {len(skipped)} 场未绑定数据源的比赛不录制"
        )

    return kept + fillers


# ═══════════════════════════════════════════════════════
#  对齐事件
# ═══════════════════════════════════════════════════════

def align_events_to_video(matched_data, manifest, rec_start_iso, stream_idx):
    """
    用 aligner 模块将比分变化事件对齐到视频时间轴。
    返回对齐后的事件列表。
    """
    if not matched_data or not manifest:
        return []

    # 检测比分变化作为锚点事件
    events = []
    prev_h, prev_c = "", ""
    for r in matched_data:
        cur_h, cur_c = r.get("score_h", ""), r.get("score_c", "")
        if cur_h != prev_h or cur_c != prev_c:
            if prev_h or prev_c:
                events.append({
                    "event_type": "score_change",
                    "timestamp": r["timestamp"],
                    "gtype": r.get("gtype", ""),
                    "team_h": r.get("team_h", ""),
                    "team_c": r.get("team_c", ""),
                    "prev_score": f"{prev_h}-{prev_c}",
                    "new_score": f"{cur_h}-{cur_c}",
                    "stream_idx": stream_idx,
                })
            prev_h, prev_c = cur_h, cur_c

    if not events:
        return []

    # 解析录制开始时间
    try:
        rec_start = datetime.fromisoformat(rec_start_iso.replace("Z", "+00:00"))
        if rec_start.tzinfo is None:
            rec_start = rec_start.replace(tzinfo=timezone.utc)
    except Exception:
        rec_start = datetime.now(timezone.utc)

    # 生成锚点（校正量初始为 0）
    anchor_corrections = []
    for ev in events:
        try:
            ev_time = datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00"))
            if ev_time.tzinfo is None:
                ev_time = ev_time.replace(tzinfo=timezone.utc)
            wall_time = (ev_time - rec_start).total_seconds()
            anchor_corrections.append((wall_time, 0.0))
        except Exception:
            continue

    if not anchor_corrections:
        return events  # 返回未对齐的事件

    # 对齐每个事件
    aligned = []
    for ev in events:
        try:
            ev_time = datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00"))
            if ev_time.tzinfo is None:
                ev_time = ev_time.replace(tzinfo=timezone.utc)
            wall_time = (ev_time - rec_start).total_seconds()
        except Exception:
            aligned.append(ev); continue

        correction = interpolate_correction(wall_time, anchor_corrections)
        corrected = wall_time + correction
        segment, offset = find_video_position(manifest, corrected)

        aligned_ev = dict(ev)
        aligned_ev["_aligned"] = {
            "data_wall_time_sec": round(wall_time, 3),
            "correction_sec": round(correction, 3),
            "corrected_wall_time_sec": round(corrected, 3),
        }
        if segment:
            aligned_ev["_aligned"].update({
                "video_file": segment.get("file", ""),
                "video_time_sec": round(offset, 3) if offset else 0,
                "video_time_hms": seconds_to_hms(offset) if offset else "00:00:00",
                "segment_type": segment.get("type", "live"),
                "is_gap": segment.get("type") != "live",
            })
        aligned.append(aligned_ev)

    return aligned


# ═══════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="全自动视频录制 + 秒级数据采集")
    parser.add_argument("--max-streams", type=int, default=MAX_STREAMS)
    parser.add_argument("--mode", choices=["auto", "applescript", "pinchtab"], default="auto")
    parser.add_argument("--browser", choices=["chrome", "safari"], default=BROWSER_DEFAULT)
    parser.add_argument("--gtypes", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--match-query", default="")
    parser.add_argument("--prestart-minutes", type=int, default=1)
    parser.add_argument("--segment-minutes", type=int, default=SEGMENT_MINUTES)
    parser.add_argument("--max-duration-minutes", type=int, default=MAX_DURATION_MINUTES)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--selected-matches-file", default="")
    parser.add_argument("--watch-job-id", default="")
    parser.add_argument("--trigger-reason", default="")
    parser.add_argument("--match-rule-source", default="")
    parser.add_argument("--trigger-mode", default="")
    parser.add_argument("--watch-lock-key", default="")
    parser.add_argument("--notify-channel", default="")
    parser.add_argument("--notify-account", default="")
    parser.add_argument("--notify-target", default="")
    parser.add_argument("--notify-title", default="录制任务已结束。")
    parser.add_argument("--disable-final-notify", action="store_true")
    parser.add_argument("--analysis-5m", action="store_true", default=ANALYSIS_COPY_DEFAULT)
    parser.add_argument("--analysis-mbps", type=float, default=ANALYSIS_COPY_Mbps)
    args = parser.parse_args()

    # ── Phase 0: session 目录 + 日志 ──
    session_id = (
        args.session_id.strip()
        or os.environ.get("MATCH_RECORDING_SESSION_ID", "").strip()
        or datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    session_dir = build_session_output_dir(session_id)
    os.makedirs(session_dir, exist_ok=True)
    logger = SessionLogger(os.path.join(session_dir, "recording.log"))

    logger.log("=" * 60)
    logger.log("全自动视频录制 + 秒级数据采集")
    logger.log(f"Session: {session_id}")
    logger.log(f"分段: {args.segment_minutes}分钟  最大路数: {args.max_streams}")
    logger.log(f"浏览器: {get_browser_app(args.browser)}")
    logger.log(f"赛程时区: UTC{SCHEDULE_TIMEZONE_OFFSET_HOURS:+g}")
    logger.log(f"analysis副本: {'开启' if args.analysis_5m else '关闭'}"
               + (f" ({args.analysis_mbps:g}Mbps)" if args.analysis_5m else ""))
    logger.log(f"输出: {session_dir}")
    if args.watch_job_id:
        logger.log(
            f"watch任务: job={args.watch_job_id} source={args.match_rule_source or 'unknown'} "
            f"mode={args.trigger_mode or 'unknown'}"
        )
    logger.log("=" * 60)
    run_preflight(logger, args.browser)

    if not ensure_schedules_live_ready(logger, args.browser):
        logger.close()
        return

    # ── Phase 1: 凭据 ──
    cookie, template, use_dashboard, feed_url, data_source = bootstrap_credentials(logger, args.browser)
    logger.log(f"数据源模式: {data_source}")

    selected = []
    explicit_selected = load_selected_matches_file(args.selected_matches_file, logger)
    if explicit_selected:
        selected = explicit_selected
    else:
        # ── Phase 2: 发现直播 ──
        all_matches = discover_live_matches_from_schedule(logger, args.browser)
        if not all_matches:
            all_matches = discover_live_matches(
                cookie, template, ALL_GTYPES, logger, use_dashboard, feed_url=feed_url or DEFAULT_URL)
        if not all_matches:
            logger.log("没有找到任何直播比赛，退出。")
            logger.close()
            return

        # ── Phase 3: 选择 ──
        if args.match_query:
            selected = filter_matches_by_query(all_matches, args.match_query)
            logger.log(f"按关键词选择 {args.match_query!r} → 候选 {len(selected)} 场")
        elif args.gtypes:
            wanted = [g.strip().upper() for g in args.gtypes.split(",")]
            selected = []
            for g in wanted:
                if g in all_matches:
                    selected.extend(all_matches[g])
            logger.log(f"命令行选择 {wanted} → 候选 {len(selected)} 场")
        elif args.all:
            selected = []
            for m in all_matches.values():
                selected.extend(m)
            logger.log(f"录制全部候选 {len(selected)} 场")
        else:
            selected = let_user_select(all_matches, args.max_streams)

        if not selected:
            logger.log("未选择任何比赛，退出。")
            logger.close()
            return

    try:
        snapshot_rows = fetch_live_data_snapshot(
            cookie,
            template,
            gtypes=list({m.get("gtype") for m in selected if m.get("gtype")}) or ALL_GTYPES,
            use_dashboard=use_dashboard,
            feed_url=feed_url or DEFAULT_URL,
        )
        logger.log(f"当前数据快照: {len(snapshot_rows)} 条候选比赛")
        bind_selected_matches_to_feed(selected, snapshot_rows, logger)
    except Exception as e:
        logger.log(f"比赛数据绑定失败，继续使用原始队名匹配: {e}", "WARN")

    if explicit_selected:
        selected = annotate_selected_matches_for_recording(selected)
    else:
        selected = require_bound_data_matches(selected, args.max_streams, logger)
        if not selected:
            logger.log("当前没有任何已绑定到实时数据源的比赛，停止录制。", "WARN")
            logger.close()
            return

        if args.match_query or args.gtypes or args.all:
            selected = prioritize_selected_matches(selected, args.max_streams, logger)
            logger.log(f"自动选择最终录制 {len(selected)} 场")

    selected = filter_matches_ready_to_record(selected, args.prestart_minutes, logger)
    if not selected:
        logger.log(
            f"当前没有进入录制窗口的比赛。规则: 开赛前 {args.prestart_minutes} 分钟内或已开赛才允许录制。",
            "WARN",
        )
        logger.close()
        return

    # ── Phase 4: 打开视频窗口 ──
    open_result = open_match_videos(selected, args.max_streams, args.mode, logger, args.browser)
    if not open_result:
        logger.log("未能按选中比赛打开新窗口，停止本次录制。", "ERROR")
        close_browser_video_windows(args.browser, logger, collect_match_watch_urls(selected))
        send_anomaly_notification(
            logger,
            args.notify_channel,
            args.notify_target,
            args.notify_account,
            "录制异常提醒",
            session_dir,
            [
                "未能成功打开并确认任何比赛视频窗口。",
                f"浏览器：{get_browser_app(args.browser)}",
                f"目标场次：{len(selected)} 场",
            ],
        )
        logger.close()
        return
    ready_tabs = open_result.get("ready_tabs") or []
    ready_window_ids = ready_tabs_to_window_ids(ready_tabs)
    new_win_ids = ready_window_ids or (open_result.get("new_window_ids") or set())
    expected_watch_count = min(len(selected), args.max_streams)
    if len(ready_tabs) < expected_watch_count:
        missing_count = expected_watch_count - len(ready_tabs)
        ready_urls = {str(item.get('url', '')).rstrip('/') for item in ready_tabs}
        missing_matches = [
            f"{m.get('team_h','')} vs {m.get('team_c','')}"
            for m in selected[:args.max_streams]
            if str(m.get('watch_url', '')).rstrip('/') not in ready_urls
        ]
        logger.log(
            f"有 {missing_count} 场比赛在起播等待阶段超时或未真正开始播放，将只继续录制已就绪窗口",
            "WARN",
        )
        send_anomaly_notification(
            logger,
            args.notify_channel,
            args.notify_target,
            args.notify_account,
            "录制异常提醒",
            session_dir,
            [
                f"起播等待阶段出现异常：{missing_count} 场未能在超时时间内开始播放。",
                f"继续录制：{len(ready_tabs)} 场",
                f"未就绪：{'; '.join(missing_matches[:5]) or 'unknown'}",
            ],
        )

    # ── Phase 5: 检测窗口 ──
    video_wins = detect_video_windows(new_win_ids, logger, args.browser)
    if not video_wins:
        logger.log("未检测到视频窗口，退出。")
        logger.close()
        return

    logger.log(f"检测到 {len(video_wins)} 个视频窗口:")
    for i, vw in enumerate(video_wins):
        cx, cy, cw, ch = vw["crop"]
        logger.log(f"  [{i+1}] {vw['teams']} screen={vw.get('screen_idx', 0)} crop=({cx},{cy},{cw},{ch})")

    # ── 构建 streams ──
    streams = []
    selected_pool = list(selected[:args.max_streams])
    ready_pool = list(ready_tabs[:args.max_streams])
    for i, vw in enumerate(video_wins[:args.max_streams]):
        selected_match = pop_selected_match_for_window(vw["teams"], selected_pool)
        ready_tab = pop_ready_tab_for_window(vw["teams"], ready_pool, selected_match)
        content_crop = ((ready_tab.get("layout") or {}).get("page_content_rect") or None)
        name, folder_name, file_prefix = build_stream_naming(
            selected_match, vw["teams"], session_id, i + 1
        )
        if selected_match:
            logger.log(
                f"窗口映射 [{i+1}]: {vw['teams']} -> {selected_match.get('team_h','')} vs {selected_match.get('team_c','')}"
            )
        else:
            logger.log(f"窗口映射 [{i+1}]: {vw['teams']} -> 未匹配到选中比赛，使用窗口标题命名", "WARN")
        streams.append({
            "match_id": name,
            "output_dir": session_dir,
            "crop": vw["crop"],
            "teams": vw["teams"],
            "window_id": vw["window_id"],
            "screen_idx": vw.get("screen_idx", 0),
            "selected_match": selected_match,
            "gtype": (selected_match.get("gtype", "") if selected_match else ""),
            "folder_name": folder_name,
            "file_prefix": file_prefix,
            "content_crop": content_crop,
            "browser_window_index": ready_tab.get("window_index"),
            "browser_tab_index": ready_tab.get("tab_index"),
            "watch_url": ready_tab.get("url") or ((selected_match or {}).get("watch_url", "")),
            "data_binding_status": (selected_match or {}).get("data_binding_status", "unknown"),
            "recording_note": (selected_match or {}).get("recording_note", ""),
        })

    active_screens = sorted({s.get("screen_idx", 0) for s in streams})
    if len(active_screens) > 1:
        logger.log(
            f"当前选中的录制窗口分散在多个屏幕: {active_screens}。"
            "当前录制器一次只能捕获同一块屏幕，请先把窗口放到同一块屏幕再试。",
            "ERROR",
        )
        logger.close()
        return
    record_screen_idx = active_screens[0] if active_screens else 0
    logger.log(f"录制屏幕: {record_screen_idx}")

    # ── Phase 6: 启动数据采集 ──
    sel_gtypes = list({m["gtype"] for m in selected if m.get("gtype")}) or ALL_GTYPES
    poller = BettingDataPoller(cookie, template, gtypes=sel_gtypes,
                                use_dashboard=use_dashboard, feed_url=feed_url or DEFAULT_URL)
    poller_thread = threading.Thread(target=poller.start, daemon=True)
    poller_thread.start()
    logger.log(f"数据采集线程启动 (间隔: {DATA_POLL_INTERVAL}s)")

    # ── Phase 7: 录制 + 监控循环 ──
    rec_start = datetime.now()
    rec_start_iso = rec_start.isoformat()
    max_duration_deadline_ts = None
    if args.max_duration_minutes > 0:
        max_duration_deadline_ts = time.time() + (args.max_duration_minutes * 60)
    segment_count = 0
    data_save_idx = 0
    segment_event = threading.Event()  # 分段定时器用

    # 初始化各路状态
    stream_status = ["recording"] * len(streams)  # recording | ended | failed
    freeze_times = [[] for _ in streams]  # 记录每次卡顿发生的时间
    prev_live_gids = poller.get_live_gids()  # 初始滚球 GID 集合
    recovery_events = []
    recovery_events_lock = threading.Lock()
    recovery_state = [
        {
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "abandoned": False,
            "last_attempt_ts": 0.0,
        }
        for _ in streams
    ]
    recovery_locks = [threading.Lock() for _ in streams]

    def anomaly_notifier(title, lines):
        send_anomaly_notification(
            logger,
            args.notify_channel,
            args.notify_target,
            args.notify_account,
            title,
            session_dir,
            lines,
        )

    def stop_if_all_streams_failed():
        if stream_status and all(status == "failed" for status in stream_status):
            logger.log("所有录制流都已放弃恢复，提前终止本次录制", "ERROR")
            anomaly_notifier(
                "录制异常提醒",
                [
                    "所有录制流都已进入放弃恢复状态，本次录制将提前终止。",
                    f"总路数：{len(stream_status)}",
                ],
            )
            close_browser_video_windows(args.browser, logger, collect_match_watch_urls(selected))
            recorder._stop_event.set()
            segment_event.set()

    def on_stream_abandoned(stream_idx, stream, trigger, attempt_no):
        if len(streams) <= 1:
            logger.log(
                f"[{stream_display_name(stream)}] 单场录制已放弃恢复，立即终止并清理直播窗口",
                "ERROR",
            )
            close_browser_video_windows(args.browser, logger)
            recorder._stop_event.set()
            segment_event.set()
            return

        logger.log(
            f"[{stream_display_name(stream)}] 多场录制中的异常路已放弃恢复，关闭该路窗口，其余路继续",
            "WARN",
        )
        close_specific_watch_window(
            stream.get("browser_window_index"),
            stream.get("browser_tab_index"),
            args.browser,
            logger,
        )

    recorder = ConcurrentRecorder(
        streams=[{"match_id": s["match_id"],
                  "output_dir": s["output_dir"],
                  "crop": s["crop"],
                  "window_id": s.get("window_id"),
                  "folder_name": s["folder_name"],
                  "file_prefix": s["file_prefix"],
                  "content_crop": s.get("content_crop")} for s in streams],
        screen_idx=record_screen_idx, fps=FPS,
        width=OUTPUT_WIDTH, height=OUTPUT_HEIGHT,
        segment_minutes=args.segment_minutes,
        issue_callback=None,
        auto_rotate_segments=False,
    )
    recorder.register_signals()

    # 黑屏检测器
    black_detector = BlackScreenDetector(
        video_paths=recorder._current_paths,
        threshold=BLACK_THRESHOLD,
        check_interval=BLACK_CHECK_INTERVAL,
        consecutive_limit=BLACK_CONSECUTIVE,
    )

    def on_black_screen(stream_idx):
        """黑屏回调：刷新 -> 重开 -> 必要时切换新分段。"""
        if stream_idx >= len(streams):
            return False
        ok = attempt_stream_recovery(
            stream_idx,
            streams,
            recorder,
            logger,
            args.browser,
            recovery_state,
            recovery_locks,
            recovery_events,
            recovery_events_lock,
            trigger="black_screen",
            detail={"video_path": recorder._current_paths[stream_idx]},
            request_restart_on_reopen=True,
            stream_status=stream_status,
            anomaly_notifier=anomaly_notifier,
            on_stream_abandoned=on_stream_abandoned,
        )
        stop_if_all_streams_failed()
        return ok

    def on_recorder_issue(issue_type, payload):
        payload = payload or {}
        if issue_type == "freeze":
            stream_idx = payload.get("stream_index")
            if stream_idx is None:
                return
            freeze_times[stream_idx].append(time.time())
            attempt_stream_recovery(
                stream_idx,
                streams,
                recorder,
                logger,
                args.browser,
                recovery_state,
                recovery_locks,
                recovery_events,
                recovery_events_lock,
                trigger="freeze",
                detail={"frozen_sec": payload.get("frozen_sec")},
                request_restart_on_reopen=False,
                stream_status=stream_status,
                anomaly_notifier=anomaly_notifier,
                on_stream_abandoned=on_stream_abandoned,
            )
            stop_if_all_streams_failed()
            return

        if issue_type == "backend_exit":
            for stream_idx in range(len(streams)):
                attempt_stream_recovery(
                    stream_idx,
                    streams,
                    recorder,
                    logger,
                    args.browser,
                    recovery_state,
                    recovery_locks,
                    recovery_events,
                    recovery_events_lock,
                    trigger="backend_exit",
                    detail=payload,
                    request_restart_on_reopen=False,
                    stream_status=stream_status,
                    anomaly_notifier=anomaly_notifier,
                    on_stream_abandoned=on_stream_abandoned,
                )
            stop_if_all_streams_failed()

    recorder.issue_callback = on_recorder_issue

    black_thread = threading.Thread(
        target=black_detector.start,
        args=(on_black_screen,),
        daemon=True,
    )
    black_thread.start()

    # 分段定时器（独立线程）
    def segment_timer():
        nonlocal segment_count, data_save_idx
        while not recorder._stop_event.is_set():
            timeout = args.segment_minutes * 60
            if max_duration_deadline_ts is not None:
                remaining = max_duration_deadline_ts - time.time()
                if remaining <= SEGMENT_ROTATE_GUARD_SECONDS:
                    break
                timeout = min(timeout, remaining)
            segment_event.wait(timeout=timeout)
            segment_event.clear()
            if recorder._stop_event.is_set():
                break
            if max_duration_deadline_ts is not None:
                remaining = max_duration_deadline_ts - time.time()
                if remaining <= SEGMENT_ROTATE_GUARD_SECONDS:
                    logger.log("接近最大录制时长，跳过新分段切换")
                    break
            segment_count += 1
            logger.log(f"分段 #{segment_count} 到期，开始新分段...")
            with recorder.segment_transition():
                # 停止当前段
                recorder._stop_segment()
                # 保存当前段数据
                for i, sdir in enumerate(recorder._output_dirs):
                    mid = streams[i]["match_id"]
                    seg_time_label = datetime.now().strftime("%Y%m%d_%H%M%S")
                    seg_path = os.path.join(
                        sdir,
                        f"{streams[i]['file_prefix']}__data_seg_{segment_count:03d}__{seg_time_label}.jsonl",
                    )
                    cnt = poller.save_segment(seg_path, data_save_idx)
                    logger.log(f"  [{mid}] {os.path.basename(seg_path)} ({cnt}条)")
                data_save_idx = len(poller.data)
                # 开始新段
                if max_duration_deadline_ts is not None:
                    remaining = max_duration_deadline_ts - time.time()
                    if remaining <= SEGMENT_ROTATE_GUARD_SECONDS:
                        logger.log("已到录制截止时间，停止在当前分段，不再启动新分段")
                        recorder._stop_event.set()
                        break
                if not recorder._start_segment():
                    logger.log(f"  分段 {segment_count} 启动失败", "ERROR")
                    recorder._stop_event.set()
                    break
                # 更新黑屏检测器路径
                black_detector.update_paths(recorder._current_paths[:])

    timer_thread = threading.Thread(target=segment_timer, daemon=True)
    timer_thread.start()

    # max-duration 定时器
    def max_duration_stop():
        if args.max_duration_minutes <= 0:
            return
        if max_duration_deadline_ts is None:
            return
        remaining = max(0.0, max_duration_deadline_ts - time.time())
        if recorder._stop_event.wait(timeout=remaining):
            return
        if recorder._stop_event.is_set():
            return
        logger.log(f"达到最大录制时长 {args.max_duration_minutes} 分钟")
        recorder._stop_event.set()
        segment_event.set()

    duration_thread = threading.Thread(target=max_duration_stop, daemon=True)
    duration_thread.start()

    logger.log(f"开始录制 {len(streams)} 路 (分段: {args.segment_minutes}分钟)")

    # 主录制循环（阻塞）
    # 注意：这里用 recorder.start() 是阻塞的，但在 finally 里会等
    try:
        recorder.start()
    except KeyboardInterrupt:
        logger.log("用户中断")
        recorder._stop_event.set()
    finally:
        # 更新黑屏检测器路径（录制结束时取最终状态）
        black_detector.update_paths(recorder._current_paths[:])

    # ── 停止所有监控线程 ──
    black_detector.stop()
    poller.stop()
    timer_thread.join(timeout=2)
    duration_thread.join(timeout=2)
    rec_end = datetime.now()
    actual_duration = (rec_end - rec_start).total_seconds()

    logger.log(f"录制结束: {actual_duration:.1f}s ({actual_duration/60:.1f}分钟)")

    # ── Phase 8: 检查比赛结束（检测最后几轮有没有 GID 停止） ──
    stopped_gids = poller.get_stopped_gids(prev_live_gids)
    if stopped_gids:
        logger.log(f"检测到 {len(stopped_gids)} 场比赛已结束: {stopped_gids}")

    # ── Phase 9: 保存数据 ──
    raw_data_path = os.path.join(session_dir, "raw_betting_data.jsonl")
    poller.save(raw_data_path)
    logger.log(f"原始数据: {len(poller.data)}条 → {raw_data_path}")

    all_aligned = []
    matched_rows_by_stream = [0] * len(streams)
    data_file_by_stream = [None] * len(streams)
    for i, sdir in enumerate(recorder._output_dirs):
        mid = streams[i]["match_id"]
        teams = streams[i].get("teams", "")
        gtype = streams[i].get("gtype", "")

        # 匹配数据
        matched = match_data_to_stream(
            poller.data,
            teams,
            gtype,
            selected_match=streams[i].get("selected_match"),
        )
        data_path = os.path.join(sdir, f"{streams[i]['file_prefix']}__betting_data.jsonl")
        with open(data_path, "w", encoding="utf-8") as f:
            for r in matched:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        matched_rows_by_stream[i] = len(matched)
        data_file_by_stream[i] = data_path
        logger.log(f"[{mid}] 匹配数据: {len(matched)}条")
        if len(matched) == 0 and streams[i].get("data_binding_status") != "bound":
            logger.log(
                f"[{mid}] 当前无对应比赛数据，这一路仅作为录制稳定性测试",
                "WARN",
            )

        # 对齐
        manifest = load_manifest(sdir)
        if manifest:
            aligned = align_events_to_video(matched, manifest, rec_start_iso, i)
            all_aligned.extend(aligned)

    # 保存对齐事件
    if all_aligned:
        align_path = os.path.join(session_dir, "aligned_events.jsonl")
        with open(align_path, "w", encoding="utf-8") as f:
            for ev in all_aligned:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        score_changes = [e for e in all_aligned if e.get("event_type") == "score_change"]
        logger.log(f"对齐事件: {len(score_changes)} 个比分变化 → {align_path}")
    else:
        score_changes = []

    if recovery_events:
        recovery_path = os.path.join(session_dir, "recovery_events.json")
        with open(recovery_path, "w", encoding="utf-8") as f:
            json.dump(recovery_events, f, ensure_ascii=False, indent=2)
        logger.log(f"恢复事件: {len(recovery_events)} 条 → {recovery_path}")

    # ── Phase 10: 合并视频 ──
    logger.log("合并视频分段...")
    merged_videos = []
    stream_results = []
    for i, sdir in enumerate(recorder._output_dirs):
        mid = streams[i]["match_id"]
        manifest = load_manifest(sdir)
        if not manifest:
            logger.log(f"  [{mid}] 无 manifest，跳过")
            stream_results.append({
                "index": i+1,
                "match_id": mid,
                "status": "no_manifest",
                "recovery_successes": recovery_state[i]["successes"],
                "recovery_failures": recovery_state[i]["failures"],
                "recovery_abandoned": recovery_state[i]["abandoned"],
            })
            continue

        freeze_count = manifest.get("freeze_count", 0)
        total_dur = manifest.get("total_duration_sec", 0)
        full_path = os.path.join(sdir, f"{streams[i]['file_prefix']}__full.mp4")
        ok = merge_segments(sdir, manifest, full_path)
        if ok:
            size_mb = os.path.getsize(full_path) / 1024 / 1024
            cleanup = cleanup_redundant_single_segment(sdir, manifest, full_path)
            if cleanup["deleted"]:
                saved_mb = cleanup["saved_bytes"] / 1024 / 1024
                deleted_names = ", ".join(os.path.basename(p) for p in cleanup["deleted"])
                logger.log(f"  [{mid}] 已清理单段重复文件: {deleted_names} (-{saved_mb:.1f}MB)")
            analysis_path = None
            if args.analysis_5m:
                analysis_path = generate_analysis_copy(
                    full_path,
                    output_path=os.path.join(
                        sdir,
                        f"{streams[i]['file_prefix']}__analysis_{int(round(args.analysis_mbps))}m.mp4",
                    ),
                    target_mbps=args.analysis_mbps,
                )
                if analysis_path and os.path.exists(analysis_path):
                    analysis_mb = os.path.getsize(analysis_path) / 1024 / 1024
                    logger.log(
                        f"  [{mid}] analysis副本完成: {os.path.basename(analysis_path)} ({analysis_mb:.1f}MB)"
                    )
                else:
                    logger.log(f"  [{mid}] analysis副本生成失败", "WARN")
            logger.log(f"  [{mid}] 合并完成: {size_mb:.1f}MB "
                      f"(分段{len(manifest.get('segments',[]))}, "
                      f"卡顿{freeze_count}次, {seconds_to_hms(total_dur)})")
            merged_videos.append(full_path)
            stream_results.append({
                "index": i+1, "match_id": mid,
                "teams": streams[i].get("teams", ""),
                "merged_video": full_path,
                "analysis_video": analysis_path,
                "data_file": data_file_by_stream[i],
                "matched_rows": matched_rows_by_stream[i],
                "data_binding_status": streams[i].get("data_binding_status", "unknown"),
                "recording_note": streams[i].get("recording_note", ""),
                "status": manifest.get("status", "completed"),
                "segments": len(manifest.get("segments", [])),
                "cleanup_deleted": [os.path.basename(p) for p in cleanup["deleted"]],
                "cleanup_saved_bytes": cleanup["saved_bytes"],
                "freeze_count": freeze_count,
                "total_duration_sec": round(total_dur, 1),
                "recovery_successes": recovery_state[i]["successes"],
                "recovery_failures": recovery_state[i]["failures"],
                "recovery_abandoned": recovery_state[i]["abandoned"],
            })
        else:
            logger.log(f"  [{mid}] 合并失败", "WARN")
            stream_results.append({
                "index": i+1,
                "match_id": mid,
                "status": "merge_failed",
                "recovery_successes": recovery_state[i]["successes"],
                "recovery_failures": recovery_state[i]["failures"],
                "recovery_abandoned": recovery_state[i]["abandoned"],
            })

    # ── Phase 11: 汇总报告 ──
    score_changes = [e for e in all_aligned if e.get("event_type") == "score_change"]
    result = {
        "session_id": session_id,
        "recording": {
            "start": rec_start_iso, "end": rec_end.isoformat(),
            "actual_duration_sec": round(actual_duration, 1),
            "streams": len(streams), "segments": segment_count,
        },
        "data": {
            "total_records": len(poller.data),
            "poll_count": poller.poll_count,
            "error_count": poller.error_count,
            "poll_interval_sec": DATA_POLL_INTERVAL,
            "stopped_games": len(stopped_gids),
        },
        "recovery": {
            "events": len(recovery_events),
            "successes": sum(1 for event in recovery_events if event.get("success")),
            "failures": sum(
                1
                for event in recovery_events
                if event.get("action") == "failed" or event.get("recovery_abandoned")
            ),
            "streams": recovery_state,
        },
        "streams": stream_results,
        "score_change_events": len(score_changes),
        "watch": {
            "watch_job_id": args.watch_job_id,
            "trigger_reason": args.trigger_reason,
            "target_match_rule_source": args.match_rule_source,
            "trigger_mode": args.trigger_mode,
            "session_lock_metadata": {
                "watch_lock_key": args.watch_lock_key,
            },
            "selected_matches_file": args.selected_matches_file,
        },
        "progress": {},
        "generated_at": datetime.now().isoformat(),
    }

    result_path = os.path.join(session_dir, "session_result.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    try:
        from generate_sync_viewer import generate_session_viewers

        viewer_files, index_path = generate_session_viewers(Path(session_dir))
        logger.log(f"同步查看页入口: {index_path}")
        for viewer_file in viewer_files:
            logger.log(f"  viewer: {viewer_file}")
    except Exception as e:
        logger.log(f"生成同步查看页失败: {e}", "WARN")

    # ── 最终报告 ──
    logger.log("=" * 60)
    logger.log("录制完成!")
    close_browser_video_windows(args.browser, logger, collect_match_watch_urls(selected))
    if args.notify_channel and args.notify_target and not args.disable_final_notify:
        try:
            from notify_recording_summary import send_session_summary

            rc = send_session_summary(
                Path(session_dir),
                channel=args.notify_channel,
                target=args.notify_target,
                account=(args.notify_account or None),
                timeout_seconds=0,
                title=args.notify_title,
                dry_run=False,
            )
            if rc == 0:
                logger.log(
                    f"已发送完成通知: channel={args.notify_channel} target={args.notify_target}"
                )
            else:
                logger.log(
                    f"完成通知发送失败: channel={args.notify_channel} target={args.notify_target} rc={rc}",
                    "WARN",
                )
        except Exception as e:
            logger.log(f"完成通知发送异常: {e}", "WARN")
    completed = sum(1 for s in stream_results if s.get("status") == "completed")
    failed = sum(1 for s in stream_results if "failed" in s.get("status", ""))
    logger.log(f"输出: {session_dir}")
    logger.log(f"路数: {len(streams)}, 完成: {completed}, 失败: {failed}")
    logger.log(f"数据: {len(poller.data)}条 ({poller.poll_count}轮, "
               f"{poller.error_count}轮失败)")
    logger.log(f"比分变化: {len(score_changes)} 个")
    for sv in stream_results:
        if sv.get("merged_video") and os.path.exists(sv["merged_video"]):
            sz = os.path.getsize(sv["merged_video"]) / 1024 / 1024
            logger.log(f"  [{sv['index']}] {sv.get('teams', sv['match_id'])}: "
                      f"{sz:.1f}MB")
    logger.log("=" * 60)
    print(f"\nSESSION_DIR={session_dir}", flush=True)
    logger.close()


if __name__ == "__main__":
    main()
