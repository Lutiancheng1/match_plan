#!/usr/bin/env python
"""
Video Pipeline - V6.2 单站点多比赛录制版

功能：
1. 兼容原有直接 URL 任务
2. 支持 site_rooms 任务：登录一次后，从同一站点发现多场比赛
3. 自动复用 storage_state，首次登录支持人工辅助完成 Cloudflare/验证码
4. 将站点中的比赛展开成多个子任务并发录制
5. 每个任务独立输出 data.json, report.txt, screenshot.png, video.webm, error.log, alignment
6. 任务失败自动重试 1 次
7. 最后生成 batch_summary.json
"""

import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests


LOG_LOCK = Lock()

DEFAULT_FIELD_SELECTORS = {
    "headings": "h1, h2",
    "paragraphs": "p",
    "links": "a",
    "images": "img",
}

DEFAULT_VIEWPORT = {"width": 1280, "height": 720}

DEFAULT_ROOMS_CONFIG = {
    "table_selector": "#rooms-tbody",
    "row_selector": "#rooms-tbody tr",
    "search_input_selector": "#search-rooms-filter",
    "viewer_link_selectors": [
        "a.broadcast-viewer-window",
        "a.broadcast-window",
    ],
    "limit": 6,
    "load_wait_ms": 4000,
    "include_terms": [],
    "exclude_terms": [],
    "search_text": "",
    "priority_groups": [],
    "require_priority_group_match": False,
}

DEFAULT_MONITOR_CONFIG = {
    "enabled": False,
    "poll_interval_seconds": 30,
    "max_cycles": 1,
    "empty_cycles_before_stop": 1,
    "summary_prefix": "batch_summary_cycle",
}

DEFAULT_PINCHTAB_CONFIG = {
    "enabled": False,
    "cli_path": "/opt/homebrew/bin/pinchtab",
    "config_path": "~/.pinchtab/config.json",
    "server_url": "http://127.0.0.1:9867",
    "attach_debug_chrome": False,
    "debug_origin": "http://127.0.0.1:9222",
    "debug_version_url": "http://127.0.0.1:9222/json/version",
    "attach_name": "shared-chrome",
    "reuse_existing_tab_only": True,
    "required_url_substrings": [],
    "open_rooms_window_when_missing": False,
}


class NoRoomCandidatesError(RuntimeError):
    """未发现可录制比赛。"""


class DiscoveryWindowGuardError(RuntimeError):
    """检测到已有可见站点窗口时，禁止脚本再新开浏览器。"""


def log_message(message):
    """统一输出日志，确保后台运行时及时刷新。"""
    with LOG_LOCK:
        print(message, flush=True)


def parse_bool(value, default=False):
    """将字符串/布尔值转换为布尔值。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def parse_int(value, default):
    """安全解析整数。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_whitespace(text):
    """折叠空白字符，便于比较。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def normalize_url(url):
    """规范化 URL。"""
    if not url:
        raise ValueError("任务缺少 url/login_url/rooms_url")

    cleaned = str(url).strip()
    if cleaned.startswith("www."):
        cleaned = "https://" + cleaned
    if not cleaned.startswith(("http://", "https://")):
        cleaned = "https://" + cleaned
    return cleaned


def slugify(text, fallback="item"):
    """生成适合作为 task_id 后缀的 slug。"""
    normalized = re.sub(r"[^0-9A-Za-z]+", "_", (text or "").strip())
    normalized = normalized.strip("_").lower()
    return normalized or fallback


def resolve_task_path(task, raw_path, default_name=None):
    """将相对路径解析为绝对路径。"""
    root_dir = task.get("__task_root_dir") or os.getcwd()

    if raw_path:
        if os.path.isabs(raw_path):
            return raw_path
        return os.path.abspath(os.path.join(root_dir, raw_path))

    if default_name:
        return os.path.abspath(os.path.join(root_dir, "auth", default_name))

    return os.path.abspath(root_dir)


def infer_task_root_dir(tasks_file):
    """推断任务配置的根目录，默认将 tasks/ 上一级视为项目根目录。"""
    tasks_file = os.path.abspath(tasks_file)
    task_dir = os.path.dirname(tasks_file)
    if os.path.basename(task_dir).lower() == "tasks":
        return os.path.dirname(task_dir)
    return task_dir


def apply_runtime_overrides(task):
    """允许通过环境变量切换发现后端，避免反复改 JSON 配置。"""
    cloned_task = dict(task)

    discovery_backend_override = normalize_whitespace(
        os.getenv("VIDEO_PIPELINE_DISCOVERY_BACKEND", "")
    ).lower()
    if discovery_backend_override in {"playwright", "pinchtab"}:
        cloned_task["discovery_backend"] = discovery_backend_override

    pinchtab_enabled_override = normalize_whitespace(
        os.getenv("VIDEO_PIPELINE_PINCHTAB_ENABLED", "")
    ).lower()
    if pinchtab_enabled_override:
        pinchtab = dict(cloned_task.get("pinchtab", {}) or {})
        pinchtab["enabled"] = parse_bool(pinchtab_enabled_override, pinchtab.get("enabled", False))
        cloned_task["pinchtab"] = pinchtab

    monitor_enabled_override = normalize_whitespace(
        os.getenv("VIDEO_PIPELINE_MONITOR_ENABLED", "")
    ).lower()
    if monitor_enabled_override:
        monitor = dict(cloned_task.get("monitor", {}) or {})
        monitor["enabled"] = parse_bool(monitor_enabled_override, monitor.get("enabled", False))
        cloned_task["monitor"] = monitor

    return cloned_task


def build_browser_launch_kwargs(task, headless):
    """构建浏览器启动参数。"""
    launch_kwargs = {"headless": headless}
    browser_channel = task.get("browser_channel")
    if browser_channel:
        launch_kwargs["channel"] = browser_channel
    proxy_server = (task.get("proxy_server") or "").strip()
    if proxy_server:
        proxy_config = {"server": proxy_server}
        proxy_username = (task.get("proxy_username") or "").strip()
        proxy_password = (task.get("proxy_password") or "").strip()
        if proxy_username:
            proxy_config["username"] = proxy_username
        if proxy_password:
            proxy_config["password"] = proxy_password
        launch_kwargs["proxy"] = proxy_config
    return launch_kwargs


def load_pinchtab_config(task):
    """加载 PinchTab 控制配置。"""
    pinchtab = dict(DEFAULT_PINCHTAB_CONFIG)
    pinchtab.update(task.get("pinchtab", {}) or {})
    pinchtab["enabled"] = parse_bool(pinchtab.get("enabled"), DEFAULT_PINCHTAB_CONFIG["enabled"])
    pinchtab["attach_debug_chrome"] = parse_bool(
        pinchtab.get("attach_debug_chrome"),
        DEFAULT_PINCHTAB_CONFIG["attach_debug_chrome"],
    )
    pinchtab["reuse_existing_tab_only"] = parse_bool(
        pinchtab.get("reuse_existing_tab_only"),
        DEFAULT_PINCHTAB_CONFIG["reuse_existing_tab_only"],
    )
    pinchtab["open_rooms_window_when_missing"] = parse_bool(
        pinchtab.get("open_rooms_window_when_missing"),
        DEFAULT_PINCHTAB_CONFIG["open_rooms_window_when_missing"],
    )

    cli_path = normalize_whitespace(pinchtab.get("cli_path", DEFAULT_PINCHTAB_CONFIG["cli_path"]))
    pinchtab["cli_path"] = cli_path or DEFAULT_PINCHTAB_CONFIG["cli_path"]

    config_path = normalize_whitespace(pinchtab.get("config_path", DEFAULT_PINCHTAB_CONFIG["config_path"]))
    pinchtab["config_path"] = os.path.expanduser(config_path or DEFAULT_PINCHTAB_CONFIG["config_path"])

    server_url = normalize_whitespace(pinchtab.get("server_url", DEFAULT_PINCHTAB_CONFIG["server_url"]))
    if server_url and not server_url.startswith(("http://", "https://")):
        server_url = "http://" + server_url
    pinchtab["server_url"] = server_url or DEFAULT_PINCHTAB_CONFIG["server_url"]

    debug_origin = normalize_whitespace(pinchtab.get("debug_origin", DEFAULT_PINCHTAB_CONFIG["debug_origin"]))
    if debug_origin and not debug_origin.startswith(("http://", "https://")):
        debug_origin = "http://" + debug_origin
    pinchtab["debug_origin"] = debug_origin or DEFAULT_PINCHTAB_CONFIG["debug_origin"]

    debug_version_url = normalize_whitespace(
        pinchtab.get("debug_version_url", DEFAULT_PINCHTAB_CONFIG["debug_version_url"])
    )
    if debug_version_url and not debug_version_url.startswith(("http://", "https://")):
        debug_version_url = "http://" + debug_version_url
    pinchtab["debug_version_url"] = (
        debug_version_url or f"{pinchtab['debug_origin'].rstrip('/')}/json/version"
    )

    attach_name = normalize_whitespace(pinchtab.get("attach_name", task.get("task_id")))
    pinchtab["attach_name"] = attach_name or task.get("task_id") or DEFAULT_PINCHTAB_CONFIG["attach_name"]

    required_url_substrings = pinchtab.get("required_url_substrings", []) or []
    if isinstance(required_url_substrings, str):
        required_url_substrings = [required_url_substrings]
    required_url_substrings = [
        normalize_whitespace(item) for item in required_url_substrings if normalize_whitespace(item)
    ]
    rooms_url = normalize_url(task.get("rooms_url") or task.get("url") or task.get("login_url"))
    login_url = normalize_url(task.get("login_url") or rooms_url)
    if not required_url_substrings:
        rooms_target = rooms_url.rstrip("/")
        login_target = login_url.rstrip("/")
        domain = re.sub(r"^https?://", "", rooms_target).split("/", 1)[0]
        required_url_substrings = [rooms_target, login_target, domain]
    pinchtab["required_url_substrings"] = required_url_substrings
    return pinchtab


def use_pinchtab_discovery(task):
    """判断赛程发现是否走 PinchTab 控制浏览器。"""
    if task.get("mode") != "site_rooms":
        return False
    discovery_backend = normalize_whitespace(task.get("discovery_backend", "")).lower()
    pinchtab_enabled = parse_bool((task.get("pinchtab", {}) or {}).get("enabled"), False)
    return discovery_backend == "pinchtab" or pinchtab_enabled


def run_pinchtab_command(pinchtab_config, args, expect_json=False):
    """执行 PinchTab CLI 命令。"""
    command = [pinchtab_config["cli_path"], *args]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"未找到 PinchTab CLI：{pinchtab_config['cli_path']}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = normalize_whitespace(exc.stderr or exc.stdout or str(exc))
        raise RuntimeError(f"PinchTab 命令失败：{' '.join(args)} -> {detail}") from exc

    output = (completed.stdout or "").strip()
    if not expect_json:
        return output

    if not output:
        return {}

    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"PinchTab 输出不是合法 JSON：{output[:200]}"
        ) from exc


def open_visible_chrome_window(url):
    """直接用系统 Chrome 新开一个可见窗口并打开目标 URL。"""
    normalized_url = normalize_url(url)
    if has_visible_sftraders_window():
        log_message("▶ 检测到已有可见的 SF Traders 窗口，取消自动新开。")
        return False
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "Google Chrome" to activate',
                "-e",
                'tell application "Google Chrome" to make new window',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["open", "-a", "Google Chrome", normalized_url],
            check=True,
            capture_output=True,
            text=True,
        )
        log_message(f"▶ 已新开 Chrome 窗口并打开 {normalized_url}")
        return True
    except Exception as exc:
        log_message(f"⚠ 无法自动新开 Chrome 窗口：{exc}")
        return False


def list_visible_chrome_window_names():
    """通过 macOS System Events 获取当前可见 Chrome 窗口标题。"""
    applescript = """
tell application "System Events"
  tell process "Google Chrome"
    set winCount to count of windows
    set out to ""
    repeat with w from 1 to winCount
      set out to out & (name of window w) & linefeed
    end repeat
    return out
  end tell
end tell
"""
    try:
        completed = subprocess.run(
            ["osascript", "-e", applescript],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []

    return [
        normalize_whitespace(line)
        for line in (completed.stdout or "").splitlines()
        if normalize_whitespace(line)
    ]


def has_visible_sftraders_window():
    """判断当前屏幕上是否已经有可见的 SF Traders Chrome 窗口。"""
    window_names = list_visible_chrome_window_names()
    for name in window_names:
        lowered = name.lower()
        if "sf traders" in lowered or "sftraders.live" in lowered:
            return True
    return False


def block_new_window_when_visible(task):
    """只要已有可见站点窗口，就阻止脚本再起新的发现浏览器。"""
    return parse_bool(task.get("block_new_window_when_visible"), True)


def skip_storage_validation_when_visible_window(task):
    """检测到已有可见站点窗口时，是否跳过登录态预校验。"""
    return parse_bool(task.get("skip_storage_validation_when_visible_window"), True)


def load_pinchtab_server_token(pinchtab_config):
    """读取本地 PinchTab server token。"""
    config_path = pinchtab_config["config_path"]
    if not os.path.exists(config_path):
        raise RuntimeError(f"未找到 PinchTab 配置文件：{config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    token = normalize_whitespace(config_data.get("server", {}).get("token"))
    if not token:
        raise RuntimeError(f"PinchTab 配置缺少 server.token：{config_path}")
    return token


def pinchtab_api_request(pinchtab_config, method, path, payload=None, timeout=10):
    """通过 PinchTab HTTP API 调用需要鉴权的接口。"""
    token = load_pinchtab_server_token(pinchtab_config)
    response = requests.request(
        method.upper(),
        f"{pinchtab_config['server_url'].rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=timeout,
    )

    if response.status_code >= 400:
        message = normalize_whitespace(response.text)
        raise RuntimeError(
            f"PinchTab API 调用失败 {method.upper()} {path} -> {response.status_code}: {message}"
        )

    if not response.content:
        return None
    return response.json()


def try_attach_pinchtab_debug_chrome(task, pinchtab_config):
    """尝试把已启动的调试 Chrome 挂到 PinchTab。"""
    if not pinchtab_config["enabled"] or not pinchtab_config["attach_debug_chrome"]:
        return None

    try:
        version_info = requests.get(pinchtab_config["debug_version_url"], timeout=5).json()
    except Exception:
        return None

    cdp_url = normalize_whitespace(version_info.get("webSocketDebuggerUrl"))
    if not cdp_url:
        return None

    try:
        pinchtab_api_request(
            pinchtab_config,
            "POST",
            "/instances/attach",
            payload={
                "name": pinchtab_config["attach_name"],
                "cdpUrl": cdp_url,
            },
        )
    except Exception as exc:
        if "already" not in str(exc).lower():
            raise
    return cdp_url


def refresh_storage_state_from_debug_chrome(task, rooms_config, storage_state_path):
    """从远程调试 Chrome 导出当前登录态，避免重新登录。"""
    if not use_pinchtab_discovery(task):
        return False

    pinchtab_config = load_pinchtab_config(task)
    if not pinchtab_config["attach_debug_chrome"]:
        return False

    try:
        try_attach_pinchtab_debug_chrome(task, pinchtab_config)
    except Exception as exc:
        log_message(f"⚠ PinchTab 附着调试 Chrome 失败：{exc}")
        return False

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(pinchtab_config["debug_origin"])
            contexts = browser.contexts
            if not contexts:
                browser.close()
                return False

            os.makedirs(os.path.dirname(storage_state_path), exist_ok=True)
            contexts[0].storage_state(path=storage_state_path)
            browser.close()
    except Exception:
        return False

    if validate_storage_state(task, rooms_config, storage_state_path):
        log_message(f"✓ 已从受控 Chrome 刷新登录态：{storage_state_path}")
        return True
    return False


def select_pinchtab_tab(task, pinchtab_config):
    """从 PinchTab 当前可见标签中选择 sftraders 对应标签。"""
    tabs_payload = run_pinchtab_command(pinchtab_config, ["tab"], expect_json=True)
    tabs = tabs_payload.get("tabs", []) or []
    rooms_url = normalize_url(task.get("rooms_url") or task.get("url") or task.get("login_url")).rstrip("/")
    login_url = normalize_url(task.get("login_url") or rooms_url).rstrip("/")
    required_fragments = pinchtab_config.get("required_url_substrings", [])
    best_match = None
    best_score = -1

    for tab in tabs:
        url = normalize_whitespace(tab.get("url", "")).rstrip("/")
        if not url:
            continue

        score = -1
        if url == rooms_url or rooms_url in url:
            score = 30
        elif url == login_url or login_url in url:
            score = 20
        elif any(fragment in url for fragment in required_fragments):
            score = 10

        if score > best_score:
            best_match = tab
            best_score = score

    return best_match


def build_pinchtab_candidate_expression(rooms_config):
    """生成在页面中提取比赛候选的 JS 表达式。"""
    viewer_selectors_json = json.dumps(
        rooms_config["viewer_link_selectors"],
        ensure_ascii=False,
    )
    row_selector_json = json.dumps(rooms_config["row_selector"], ensure_ascii=False)
    return (
        "JSON.stringify((() => {"
        f"const viewerSelectors = {viewer_selectors_json};"
        f"const rowSelector = {row_selector_json};"
        "const rows = Array.from(document.querySelectorAll(rowSelector));"
        "return rows.map((row, index) => {"
        "const textParts = (row.innerText || '').split(/\\n+/).map((part) => part.trim()).filter(Boolean);"
        "let roomUrl = null;"
        "let selectorUsed = null;"
        "let linkText = '';"
        "for (const selector of viewerSelectors) {"
        "const link = row.querySelector(selector);"
        "if (!link) continue;"
        "const href = link.getAttribute('href');"
        "if (!href) continue;"
        "roomUrl = href;"
        "selectorUsed = selector;"
        "linkText = (link.innerText || '').trim();"
        "break;"
        "}"
        "return {"
        "session_id: row.getAttribute('data-session-id') || String(index + 1),"
        "room_name: row.getAttribute('data-room-name') || textParts[0] || `room-${index + 1}`,"
        "row_text: textParts.join(' '),"
        "url: roomUrl,"
        "selector_used: selectorUsed,"
        "link_text: linkText,"
        "discovery_index: index"
        "};"
        "}).filter((item) => item.url);"
        "})())"
    )


def collect_pinchtab_room_candidates(discovery_session):
    """通过 PinchTab 在当前受控标签页中提取比赛链接。"""
    pinchtab_config = discovery_session["pinchtab_config"]
    task = discovery_session["task"]
    rooms_config = discovery_session["rooms_config"]
    rooms_url = discovery_session["rooms_url"]
    tab_id = discovery_session["tab_id"]

    search_text = rooms_config.get("search_text")
    search_input_selector = rooms_config.get("search_input_selector")
    if search_text and search_input_selector:
        search_expression = (
            "(() => {"
            f"const input = document.querySelector({json.dumps(search_input_selector, ensure_ascii=False)});"
            "if (!input) return false;"
            f"input.value = {json.dumps(search_text, ensure_ascii=False)};"
            "input.dispatchEvent(new Event('input', { bubbles: true }));"
            "input.dispatchEvent(new Event('change', { bubbles: true }));"
            "return true;"
            "})()"
        )
        run_pinchtab_command(
            pinchtab_config,
            ["eval", search_expression, "--tab", tab_id],
            expect_json=True,
        )
        time.sleep(1)

    ready_selector = rooms_config.get("table_selector") or rooms_config.get("row_selector")
    if ready_selector:
        try:
            run_pinchtab_command(
                pinchtab_config,
                [
                    "wait",
                    ready_selector,
                    "--tab",
                    tab_id,
                    "--timeout",
                    "30000",
                ],
            )
        except Exception:
            pass

    time.sleep(max(rooms_config["load_wait_ms"], 500) / 1000.0)
    current_url_payload = run_pinchtab_command(
        pinchtab_config,
        ["eval", "location.href", "--tab", tab_id],
        expect_json=True,
    )
    current_url = normalize_whitespace(current_url_payload.get("result"))
    if is_login_url(current_url):
        raise ValueError(
            "PinchTab 当前看到的是登录页，说明人工会话还未就绪。请先在受控 Chrome 中完成登录后再继续。"
        )

    result_payload = run_pinchtab_command(
        pinchtab_config,
        ["eval", build_pinchtab_candidate_expression(rooms_config), "--tab", tab_id],
        expect_json=True,
    )
    raw_result = result_payload.get("result") or "[]"
    candidates = json.loads(raw_result)

    normalized_candidates = []
    for candidate in candidates:
        row_text = normalize_whitespace(candidate.get("row_text", ""))
        room_name = normalize_whitespace(candidate.get("room_name", "")) or candidate.get("session_id", "room")
        normalized_candidates.append(
            {
                "session_id": normalize_whitespace(candidate.get("session_id")) or "unknown",
                "room_name": room_name,
                "row_text": row_text,
                "competition_name": extract_competition_name(row_text, room_name),
                "url": urljoin(rooms_url, candidate.get("url", "")),
                "selector_used": candidate.get("selector_used"),
                "link_text": normalize_whitespace(candidate.get("link_text", "")),
                "discovery_index": parse_int(candidate.get("discovery_index"), 0),
            }
        )

    return normalized_candidates


def build_login_notification_message(task, storage_state_path):
    """构造 Telegram 通知内容。"""
    telegram_config = task.get("telegram", {}) or {}
    custom_message = telegram_config.get("message")
    if custom_message:
        return custom_message

    return (
        f"[{task.get('task_id')}] 需要人工接管登录\n"
        f"登录页: {task.get('login_url') or task.get('rooms_url') or task.get('url')}\n"
        f"房间页: {task.get('rooms_url') or task.get('url')}\n"
        f"登录态文件: {storage_state_path}\n"
        "请在人工完成登录并确认会话就绪后，让任务继续。"
    )


def send_telegram_notification(task, storage_state_path):
    """可选地向 Telegram 发送人工接管提醒。"""
    telegram_config = task.get("telegram", {}) or {}
    if not parse_bool(telegram_config.get("enabled"), False):
        return False

    bot_token_env = telegram_config.get("bot_token_env", "TG_BOT_TOKEN")
    chat_id_env = telegram_config.get("chat_id_env", "TG_CHAT_ID")
    bot_token = os.getenv(bot_token_env, "")
    chat_id = os.getenv(chat_id_env, "")

    if not bot_token or not chat_id:
        log_message(
            f"⚠ Telegram 通知已启用，但缺少 {bot_token_env} 或 {chat_id_env} 环境变量。"
        )
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": build_login_notification_message(task, storage_state_path),
            },
            timeout=20,
        )
        response.raise_for_status()
        log_message("✓ 已发送 Telegram 接管通知。")
        return True
    except Exception as exc:
        log_message(f"⚠ Telegram 通知发送失败：{exc}")
        return False


def wait_for_manual_session(task, rooms_config, storage_state_path):
    """等待人工或外部流程准备好有效会话。"""
    manual_session_timeout_seconds = parse_int(task.get("manual_session_timeout_seconds"), 900)
    manual_session_poll_interval_seconds = parse_int(
        task.get("manual_session_poll_interval_seconds"),
        5,
    )
    ready_file_path = None
    if task.get("ready_file"):
        ready_file_path = resolve_task_path(task, task.get("ready_file"))

    log_message(
        f"▶ 等待人工准备登录态，最长 {manual_session_timeout_seconds} 秒。"
    )

    deadline = time.time() + manual_session_timeout_seconds
    while time.time() < deadline:
        if validate_storage_state(task, rooms_config, storage_state_path):
            log_message(f"✓ 检测到人工准备完成的登录态：{storage_state_path}")
            return True

        if ready_file_path and os.path.exists(ready_file_path):
            log_message(f"▶ 检测到就绪标记文件：{ready_file_path}")

        time.sleep(max(manual_session_poll_interval_seconds, 1))

    return False


def build_field_items(fields):
    """生成字段与选择器的映射列表。"""
    if fields is None:
        return []

    if isinstance(fields, list):
        return [
            (field_name, DEFAULT_FIELD_SELECTORS[field_name])
            for field_name in fields
            if field_name in DEFAULT_FIELD_SELECTORS
        ]

    if isinstance(fields, dict):
        return list(fields.items())

    raise ValueError(f"不支持的 fields 类型: {type(fields).__name__}")


def parse_element_from_html(html, selector, field_name):
    """从 HTML 中解析单个字段。"""
    soup = BeautifulSoup(html or "", "html.parser")

    if field_name == "links":
        return [a.get("href") for a in soup.select(selector) if a.get("href")]
    if field_name == "images":
        return [img.get("src") for img in soup.select(selector) if img.get("src")]
    if field_name == "headings":
        h1_texts = [h.get_text(strip=True) for h in soup.find_all("h1")]
        h2_texts = [h.get_text(strip=True) for h in soup.find_all("h2")]
        return h1_texts + h2_texts
    if field_name == "paragraphs":
        return [p.get_text(strip=True) for p in soup.find_all("p")[:3]]

    text = soup.get_text(strip=True)
    return text[:500] if len(text) > 500 else text


def extract_fields_from_html(html, fields):
    """提取页面字段。"""
    extracted_data = {}
    for field_name, selector in build_field_items(fields):
        extracted_data[field_name] = parse_element_from_html(html, selector, field_name)
    return extracted_data


def find_recorded_video(output_dir, existing_webm_files):
    """查找本次录制新生成的视频文件。"""
    webm_files = [
        filename for filename in os.listdir(output_dir) if filename.endswith(".webm")
    ]
    new_webm_files = sorted(set(webm_files) - existing_webm_files)
    candidate_files = new_webm_files or sorted(webm_files)

    for filename in reversed(candidate_files):
        video_path = os.path.join(output_dir, filename)
        if os.path.exists(video_path) and os.path.getsize(video_path) > 0:
            return video_path
    return None


def load_storage_state_payload(storage_state_path):
    """读取 storage_state 文件内容。"""
    if not storage_state_path or not os.path.exists(storage_state_path):
        return {}

    with open(storage_state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def capture_page(
    url,
    output_dir,
    timestamp,
    storage_state_path=None,
    headless=True,
    browser_channel=None,
    proxy_server=None,
    proxy_username=None,
    proxy_password=None,
    record_seconds=5,
    page_ready_selector=None,
    wait_until="load",
    wait_after_load_ms=2000,
):
    """使用 Playwright 打开页面、截图并录制。"""
    from playwright.sync_api import sync_playwright

    os.makedirs(output_dir, exist_ok=True)

    existing_webm_files = {
        filename for filename in os.listdir(output_dir) if filename.endswith(".webm")
    }
    screenshot_path = os.path.join(output_dir, f"{timestamp}.png")

    capture_data = {
        "screenshot_path": None,
        "video_path": None,
        "html": "",
        "page_title": "",
        "final_url": url,
        "http_status": None,
    }

    with sync_playwright() as p:
        launch_kwargs = {"headless": parse_bool(headless, True)}
        if browser_channel:
            launch_kwargs["channel"] = browser_channel
        if normalize_whitespace(proxy_server):
            proxy_config = {"server": normalize_whitespace(proxy_server)}
            if normalize_whitespace(proxy_username):
                proxy_config["username"] = normalize_whitespace(proxy_username)
            if normalize_whitespace(proxy_password):
                proxy_config["password"] = normalize_whitespace(proxy_password)
            launch_kwargs["proxy"] = proxy_config

        browser = p.chromium.launch(**launch_kwargs)
        context_kwargs = {
            "record_video_dir": output_dir,
            "record_video_size": DEFAULT_VIEWPORT,
            "viewport": DEFAULT_VIEWPORT,
        }
        if storage_state_path:
            context_kwargs["storage_state"] = storage_state_path

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        response = page.goto(url, wait_until=wait_until, timeout=60000)
        if page_ready_selector:
            page.wait_for_selector(page_ready_selector, timeout=60000)

        if wait_after_load_ms > 0:
            page.wait_for_timeout(wait_after_load_ms)

        page.screenshot(path=screenshot_path)
        capture_data["screenshot_path"] = screenshot_path if os.path.exists(screenshot_path) else None

        if record_seconds > 0:
            page.wait_for_timeout(record_seconds * 1000)

        capture_data["html"] = page.content()
        capture_data["page_title"] = page.title()
        capture_data["final_url"] = page.url
        capture_data["http_status"] = response.status if response else None

        page.close()
        context.close()
        browser.close()

    capture_data["video_path"] = find_recorded_video(output_dir, existing_webm_files)
    return capture_data


def save_json(data, filepath):
    """将数据保存为 JSON 文件。"""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_error_log(task_dir, task_id, url, status, start_time, end_time, retry_count, errors):
    """保存任务错误日志，成功和失败任务都写出一份可追溯记录。"""
    elapsed_time = (end_time - start_time).total_seconds()
    error_log_path = os.path.join(task_dir, "error.log")

    with open(error_log_path, "w", encoding="utf-8") as f:
        f.write(f"任务 ID: {task_id}\n")
        f.write(f"URL: {url}\n")
        f.write(f"状态：{status}\n")
        f.write(f"开始时间：{start_time.isoformat()}\n")
        f.write(f"结束时间：{end_time.isoformat()}\n")
        f.write(f"耗时：{elapsed_time:.2f} 秒\n")
        f.write(f"重试次数：{retry_count}\n")
        if errors:
            f.write("\n错误信息:\n")
            for error in errors:
                f.write(f"- {error}\n")


def build_alignment(events, video_start_time):
    """计算事件时间与视频时间对齐信息。"""
    video_start_timestamp = video_start_time.timestamp()
    video_recording_started_time = None
    video_recording_completed_time = None

    for event in events:
        if event["name"] == "video_recording_started":
            video_recording_started_time = datetime.fromisoformat(event["timestamp"])
        elif event["name"] == "video_recording_completed":
            video_recording_completed_time = datetime.fromisoformat(event["timestamp"])

    if video_recording_started_time and video_recording_completed_time:
        video_duration_seconds = round(
            (video_recording_completed_time - video_recording_started_time).total_seconds(),
            3,
        )
    else:
        video_duration_seconds = None

    event_offsets = []
    for event in events:
        event_time = datetime.fromisoformat(event["timestamp"])
        offset_seconds = event_time.timestamp() - video_start_timestamp
        event_offsets.append(
            {
                "event_name": event["name"],
                "absolute_time": event_time.isoformat(),
                "offset_seconds": round(offset_seconds, 3),
            }
        )

    return {
        "video_start_time": video_start_timestamp,
        "event_offsets": event_offsets,
        "alignment_version": "6.1",
        "video_duration_seconds": video_duration_seconds,
    }


def load_rooms_config(task):
    """加载 site_rooms 的配置并补齐默认值。"""
    rooms = dict(DEFAULT_ROOMS_CONFIG)
    rooms.update(task.get("rooms", {}) or {})

    raw_selectors = rooms.get("viewer_link_selectors")
    if raw_selectors is None:
        raw_selectors = rooms.get("viewer_link_selector", DEFAULT_ROOMS_CONFIG["viewer_link_selectors"])

    if isinstance(raw_selectors, str):
        selectors = [part.strip() for part in raw_selectors.split(",") if part.strip()]
    else:
        selectors = [part for part in raw_selectors if part]

    rooms["viewer_link_selectors"] = selectors or list(DEFAULT_ROOMS_CONFIG["viewer_link_selectors"])
    rooms["limit"] = parse_int(rooms.get("limit"), DEFAULT_ROOMS_CONFIG["limit"])
    rooms["load_wait_ms"] = parse_int(rooms.get("load_wait_ms"), DEFAULT_ROOMS_CONFIG["load_wait_ms"])
    rooms["include_terms"] = [str(item).strip() for item in rooms.get("include_terms", []) if str(item).strip()]
    rooms["exclude_terms"] = [str(item).strip() for item in rooms.get("exclude_terms", []) if str(item).strip()]
    rooms["search_text"] = str(rooms.get("search_text", "")).strip()
    rooms["require_priority_group_match"] = parse_bool(
        rooms.get("require_priority_group_match"),
        DEFAULT_ROOMS_CONFIG["require_priority_group_match"],
    )

    priority_groups = []
    for index, group in enumerate(rooms.get("priority_groups", []) or []):
        if isinstance(group, dict):
            name = str(group.get("name") or f"group_{index + 1}").strip()
            keywords = group.get("keywords", []) or group.get("terms", [])
        elif isinstance(group, str):
            name = f"group_{index + 1}"
            keywords = [group]
        else:
            continue

        normalized_keywords = [
            normalize_whitespace(keyword).lower()
            for keyword in keywords
            if normalize_whitespace(keyword)
        ]
        if normalized_keywords:
            priority_groups.append({"name": name, "keywords": normalized_keywords})

    rooms["priority_groups"] = priority_groups
    return rooms


def load_monitor_config(task):
    """加载持续监控配置。"""
    monitor = dict(DEFAULT_MONITOR_CONFIG)
    monitor.update(task.get("monitor", {}) or {})
    monitor["enabled"] = parse_bool(monitor.get("enabled"), DEFAULT_MONITOR_CONFIG["enabled"])
    monitor["poll_interval_seconds"] = max(
        parse_int(monitor.get("poll_interval_seconds"), DEFAULT_MONITOR_CONFIG["poll_interval_seconds"]),
        1,
    )
    monitor["max_cycles"] = max(parse_int(monitor.get("max_cycles"), DEFAULT_MONITOR_CONFIG["max_cycles"]), 0)
    monitor["empty_cycles_before_stop"] = max(
        parse_int(
            monitor.get("empty_cycles_before_stop"),
            DEFAULT_MONITOR_CONFIG["empty_cycles_before_stop"],
        ),
        0,
    )
    monitor["summary_prefix"] = normalize_whitespace(
        monitor.get("summary_prefix", DEFAULT_MONITOR_CONFIG["summary_prefix"])
    ) or DEFAULT_MONITOR_CONFIG["summary_prefix"]
    return monitor


def extract_competition_name(*texts):
    """从比赛文本中尽量提取赛事名称。"""
    lines = []
    for text in texts:
        for raw_line in str(text or "").splitlines():
            line = normalize_whitespace(raw_line)
            if line:
                lines.append(line)

    for line in reversed(lines):
        cleaned = re.sub(r"\b\d{1,2}:\d{2}\b", "", line).strip(" -\t")
        if cleaned and cleaned.lower() not in {"x"}:
            return cleaned
    return ""


def candidate_priority_key(candidate, rooms_config):
    """根据甲级/乙级/页面顺序生成排序键。"""
    group_index, group_name = resolve_candidate_priority_group(candidate, rooms_config)
    candidate["priority_group"] = group_name
    return (
        group_index,
        candidate.get("discovery_index", 0),
        normalize_whitespace(candidate.get("competition_name", "")).lower(),
        normalize_whitespace(candidate.get("room_name", "")).lower(),
    )


def resolve_candidate_priority_group(candidate, rooms_config):
    """解析比赛属于哪个优先级分组。"""
    haystack = " ".join(
        normalize_whitespace(part).lower()
        for part in [
            candidate.get("competition_name", ""),
            candidate.get("room_name", ""),
            candidate.get("row_text", ""),
        ]
        if normalize_whitespace(part)
    )

    group_index = len(rooms_config.get("priority_groups", []) or [])
    group_name = "page_order"
    for index, group in enumerate(rooms_config.get("priority_groups", []) or []):
        if any(keyword in haystack for keyword in group.get("keywords", [])):
            return index, group.get("name", f"group_{index + 1}")

    return group_index, group_name


def is_login_url(url):
    """判断当前 URL 是否是登录页。"""
    return "/login" in (url or "").lower()


def room_page_ready(page, rooms_config):
    """判断房间列表页是否已进入可用状态。"""
    try:
        if is_login_url(page.url):
            return False
        if page.locator(rooms_config["row_selector"]).count() > 0:
            return True
        return page.locator(rooms_config["table_selector"]).count() > 0
    except Exception:
        return False


def validate_storage_state(task, rooms_config, storage_state_path):
    """验证缓存的登录态是否仍然有效。"""
    from playwright.sync_api import sync_playwright

    if not os.path.exists(storage_state_path):
        return False

    rooms_url = normalize_url(task.get("rooms_url") or task.get("url") or task.get("login_url"))
    discovery_headless = parse_bool(task.get("discovery_headless"), True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                **build_browser_launch_kwargs(task, discovery_headless)
            )
            context = browser.new_context(
                viewport=DEFAULT_VIEWPORT,
                storage_state=storage_state_path,
            )
            page = context.new_page()
            page.goto(rooms_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(rooms_config["load_wait_ms"])
            valid = room_page_ready(page, rooms_config)
            page.close()
            context.close()
            browser.close()
            return valid
    except Exception:
        return False


def create_discovery_session(playwright, task):
    """为监控循环创建一个可复用的常驻赛程监听页面。"""
    rooms_config = load_rooms_config(task)
    rooms_url = normalize_url(task.get("rooms_url") or task.get("url") or task.get("login_url"))
    storage_state_path = ensure_storage_state(task)

    if use_pinchtab_discovery(task):
        pinchtab_config = load_pinchtab_config(task)
        try_attach_pinchtab_debug_chrome(task, pinchtab_config)
        return {
            "backend": "pinchtab",
            "task": task,
            "rooms_config": rooms_config,
            "rooms_url": rooms_url,
            "storage_state_path": storage_state_path,
            "pinchtab_config": pinchtab_config,
            "tab_id": None,
            "initialized": False,
            "notification_sent": False,
            "opened_rooms_window": False,
        }

    return {
        "backend": "playwright",
        "task": task,
        "playwright": playwright,
        "rooms_config": rooms_config,
        "rooms_url": rooms_url,
        "storage_state_path": storage_state_path,
        "browser": None,
        "context": None,
        "page": None,
        "initialized": False,
    }


def ensure_discovery_session_open(discovery_session):
    """确保常驻发现会话对应的浏览器上下文始终可用。"""
    if discovery_session.get("backend") == "pinchtab":
        return discovery_session

    page = discovery_session.get("page")
    if page is not None and not page.is_closed():
        return discovery_session

    task = discovery_session["task"]
    playwright = discovery_session["playwright"]
    discovery_headless = parse_bool(task.get("discovery_headless"), True)

    if block_new_window_when_visible(task) and has_visible_sftraders_window():
        raise DiscoveryWindowGuardError(
            "检测到已有可见的 SF Traders 窗口；为避免再次触发风控，脚本不会启动新的发现浏览器。"
        )

    discovery_profile_dir = None
    if task.get("discovery_profile_dir"):
        discovery_profile_dir = resolve_task_path(task, task.get("discovery_profile_dir"))
    elif parse_bool(task.get("discovery_persistent_context"), False):
        if task.get("profile_dir"):
            discovery_profile_dir = resolve_task_path(task, task.get("profile_dir"))
        else:
            discovery_profile_dir = resolve_task_path(
                task,
                os.path.join("browser_profile", f"{task['task_id']}_discovery"),
            )

    browser = None
    if discovery_profile_dir:
        os.makedirs(discovery_profile_dir, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=discovery_profile_dir,
            viewport=DEFAULT_VIEWPORT,
            **build_browser_launch_kwargs(task, discovery_headless),
        )
        storage_state_payload = load_storage_state_payload(discovery_session["storage_state_path"])
        if storage_state_payload.get("cookies"):
            try:
                context.add_cookies(storage_state_payload["cookies"])
            except Exception:
                pass
        page = context.pages[0] if context.pages else context.new_page()
    else:
        browser = playwright.chromium.launch(
            **build_browser_launch_kwargs(task, discovery_headless)
        )
        context = browser.new_context(
            viewport=DEFAULT_VIEWPORT,
            storage_state=discovery_session["storage_state_path"],
        )
        page = context.new_page()

    discovery_session["browser"] = browser
    discovery_session["context"] = context
    discovery_session["page"] = page
    discovery_session["initialized"] = False
    discovery_session["discovery_profile_dir"] = discovery_profile_dir

    log_message(f"▶ 已启动常驻赛程监听窗口 task={task['task_id']}")
    return discovery_session


def close_discovery_session(discovery_session):
    """关闭常驻发现会话，释放浏览器资源。"""
    if discovery_session.get("backend") == "pinchtab":
        discovery_session["tab_id"] = None
        discovery_session["initialized"] = False
        return

    page = discovery_session.get("page")
    context = discovery_session.get("context")
    browser = discovery_session.get("browser")

    try:
        if page is not None and not page.is_closed():
            page.close()
    except Exception:
        pass

    try:
        if context is not None:
            context.close()
    except Exception:
        pass

    try:
        if browser is not None:
            browser.close()
    except Exception:
        pass

    discovery_session["page"] = None
    discovery_session["context"] = None
    discovery_session["browser"] = None
    discovery_session["initialized"] = False


def refresh_pinchtab_discovery_page(discovery_session):
    """复用 PinchTab 已打开的赛程标签页。"""
    task = discovery_session["task"]
    rooms_url = discovery_session["rooms_url"].rstrip("/")
    pinchtab_config = discovery_session["pinchtab_config"]
    rooms_config = discovery_session["rooms_config"]
    manual_session_timeout_seconds = parse_int(task.get("manual_session_timeout_seconds"), 1800)
    manual_session_poll_interval_seconds = parse_int(
        task.get("manual_session_poll_interval_seconds"),
        5,
    )

    deadline = time.time() + manual_session_timeout_seconds
    tab = None
    current_url = ""
    while time.time() < deadline:
        try_attach_pinchtab_debug_chrome(task, pinchtab_config)
        tab = select_pinchtab_tab(task, pinchtab_config)
        current_url = normalize_whitespace((tab or {}).get("url", "")).rstrip("/")

        if tab and current_url and rooms_url in current_url and not is_login_url(current_url):
            break

        if (
            not tab
            and pinchtab_config.get("open_rooms_window_when_missing")
            and not discovery_session.get("opened_rooms_window")
        ):
            if has_visible_sftraders_window():
                discovery_session["opened_rooms_window"] = True
                log_message("▶ 检测到已有可见的 SF Traders 窗口，跳过自动新开。")
                time.sleep(2)
                continue
            if open_visible_chrome_window(rooms_url):
                discovery_session["opened_rooms_window"] = True
                time.sleep(2)
                continue

        if not discovery_session.get("notification_sent") and parse_bool(
            task.get("notify_before_manual_session"), False
        ):
            send_telegram_notification(task, discovery_session["storage_state_path"])
            discovery_session["notification_sent"] = True

        if tab and is_login_url(current_url):
            log_message("▶ PinchTab 已识别到登录页，等待人工完成登录并切到赛程页。")
        else:
            log_message("▶ PinchTab 尚未识别到赛程页，等待人工把受控 Chrome 停在 /schedules。")

        time.sleep(max(manual_session_poll_interval_seconds, 1))
    else:
        raise TimeoutError(
            "PinchTab 长时间未识别到可复用的 /schedules 标签页。"
            " 脚本未进行自动导航，请先在受控 Chrome 中手动打开赛程页后再继续。"
        )

    discovery_session["tab_id"] = tab["id"]
    discovery_session["notification_sent"] = False
    discovery_session["opened_rooms_window"] = False

    if not discovery_session.get("initialized"):
        log_message(f"▶ 已接管 PinchTab 常驻赛程标签页 task={task['task_id']}")
    else:
        run_pinchtab_command(
            pinchtab_config,
            ["reload", "--tab", discovery_session["tab_id"]],
        )

    ready_selector = rooms_config.get("table_selector") or rooms_config.get("row_selector")
    if ready_selector:
        try:
            run_pinchtab_command(
                pinchtab_config,
                [
                    "wait",
                    ready_selector,
                    "--tab",
                    discovery_session["tab_id"],
                    "--timeout",
                    "30000",
                ],
            )
        except Exception:
            pass

    discovery_session["initialized"] = True
    return discovery_session["tab_id"]


def refresh_discovery_page(discovery_session):
    """复用常驻页面刷新赛程，不再每轮新开浏览器。"""
    if discovery_session.get("backend") == "pinchtab":
        return refresh_pinchtab_discovery_page(discovery_session)

    from playwright.sync_api import Error
    last_error = None
    for _ in range(2):
        ensure_discovery_session_open(discovery_session)

        page = discovery_session["page"]
        rooms_url = discovery_session["rooms_url"]
        rooms_config = discovery_session["rooms_config"]

        try:
            current_url = (page.url or "").split("#", 1)[0].rstrip("/")
            target_url = rooms_url.rstrip("/")

            try:
                if not discovery_session.get("initialized") or current_url != target_url:
                    page.goto(rooms_url, wait_until="domcontentloaded", timeout=60000)
                else:
                    page.reload(wait_until="domcontentloaded", timeout=60000)
            except Error:
                page.goto(rooms_url, wait_until="domcontentloaded", timeout=60000)

            discovery_session["initialized"] = True

            if is_login_url(page.url):
                raise ValueError(
                    "发现比赛阶段跳回登录页，当前登录态不可用。脚本已停止自动操作，请先由人工接管刷新会话后再继续。"
                )

            if not room_page_ready(page, rooms_config):
                page.wait_for_timeout(rooms_config["load_wait_ms"])

            return page
        except Exception as exc:
            last_error = exc
            close_discovery_session(discovery_session)

    raise last_error


def bootstrap_storage_state(task, rooms_config, storage_state_path):
    """启动带界面的浏览器，完成首次登录并保存 storage_state。"""
    from playwright.sync_api import Error, sync_playwright

    login_url = normalize_url(task.get("login_url") or task.get("rooms_url") or task.get("url"))
    rooms_url = normalize_url(task.get("rooms_url") or task.get("url") or login_url)
    email_env = task.get("email_env", "SFTRADERS_EMAIL")
    password_env = task.get("password_env", "SFTRADERS_PASSWORD")
    email = os.getenv(email_env, "")
    password = os.getenv(password_env, "")
    login_headless = parse_bool(task.get("login_headless"), False)
    prefill_credentials = parse_bool(task.get("prefill_credentials"), True)
    auto_submit_login = parse_bool(task.get("auto_submit_login"), False)
    manual_login_timeout_seconds = parse_int(task.get("manual_login_timeout_seconds"), 180)
    profile_dir = None
    if task.get("profile_dir"):
        profile_dir = resolve_task_path(task, task.get("profile_dir"))

    os.makedirs(os.path.dirname(storage_state_path), exist_ok=True)
    if profile_dir:
        os.makedirs(profile_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = None
        if profile_dir:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                viewport=DEFAULT_VIEWPORT,
                **build_browser_launch_kwargs(task, login_headless),
            )
            page = context.pages[0] if context.pages else context.new_page()
        else:
            browser = p.chromium.launch(**build_browser_launch_kwargs(task, login_headless))
            context = browser.new_context(viewport=DEFAULT_VIEWPORT)
            page = context.new_page()

        try:
            initial_url = rooms_url if profile_dir else login_url
            page.goto(initial_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)

            if room_page_ready(page, rooms_config):
                page.wait_for_timeout(rooms_config["load_wait_ms"])
                context.storage_state(path=storage_state_path)
                log_message(f"✓ 检测到已登录会话，登录态已保存到 {storage_state_path}")
                return storage_state_path

            if profile_dir and not is_login_url(page.url):
                try:
                    page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
                except Error:
                    if page.is_closed():
                        raise RuntimeError(
                            "登录浏览器窗口在初始化阶段已被关闭，请重新运行脚本，并保持该窗口打开。"
                        )

            if prefill_credentials:
                if email and page.locator("#email").count() > 0:
                    page.locator("#email").fill(email)
                if password and page.locator("#password").count() > 0:
                    page.locator("#password").fill(password)

            if prefill_credentials and email and password:
                log_message(f"▶ 已预填 {email_env}/{password_env} 对应的账号密码。")
            else:
                log_message(
                    f"▶ 未能预填账号密码，请在浏览器中手动输入 {email_env}/{password_env} 对应内容。"
                )

            login_button = page.locator("input.btn-submit")
            if auto_submit_login and login_button.count() > 0 and email and password:
                log_message("▶ 脚本将尝试自动提交登录。")
                try:
                    login_button.click()
                except Exception:
                    pass
            else:
                if login_button.count() > 0:
                    log_message("▶ 请在浏览器中手动完成 Cloudflare/验证码，然后手动点击 Login。")
                else:
                    log_message("▶ 请在浏览器中手动完成登录。")
        except Error:
            if page.is_closed():
                raise RuntimeError(
                    "登录浏览器窗口在初始化阶段已被关闭，请重新运行脚本，并保持该窗口打开。"
                )
            raise

        log_message(
            f"▶ 登录成功后脚本会自动保存登录态，并继续自动选择 6 场比赛。最长等待 {manual_login_timeout_seconds} 秒。"
        )

        try:
            deadline = time.time() + manual_login_timeout_seconds
            while time.time() < deadline:
                if page.is_closed():
                    raise RuntimeError(
                        "登录浏览器窗口已被关闭，请重新运行脚本，并保持该窗口打开直到看到“登录态已保存”日志。"
                    )

                if not is_login_url(page.url):
                    try:
                        page.goto(rooms_url, wait_until="domcontentloaded", timeout=30000)
                    except Error:
                        if page.is_closed():
                            raise RuntimeError(
                                "登录浏览器窗口已被关闭，请重新运行脚本，并保持该窗口打开直到看到“登录态已保存”日志。"
                            )

                if room_page_ready(page, rooms_config):
                    page.wait_for_timeout(rooms_config["load_wait_ms"])
                    context.storage_state(path=storage_state_path)
                    log_message(f"✓ 登录态已保存到 {storage_state_path}")
                    return storage_state_path

                page.wait_for_timeout(2000)
        finally:
            if not page.is_closed():
                page.close()
            context.close()
            if browser:
                browser.close()

    raise TimeoutError(
        f"登录超时，未能在 {manual_login_timeout_seconds} 秒内进入房间列表页。"
    )


def ensure_storage_state(task):
    """确保 site_rooms 任务存在可复用的登录态。"""
    rooms_config = load_rooms_config(task)
    storage_state_path = resolve_task_path(
        task,
        task.get("storage_state"),
        f"{task['task_id']}_storage_state.json",
    )

    if (
        skip_storage_validation_when_visible_window(task)
        and has_visible_sftraders_window()
        and os.path.exists(storage_state_path)
    ):
        log_message(
            "▶ 检测到已有可见的 SF Traders 窗口，跳过登录态预校验，直接复用现有会话文件。"
        )
        return storage_state_path

    validation_retry_count = max(parse_int(task.get("storage_state_validation_retries"), 3), 1)
    validation_retry_delay_seconds = max(
        parse_int(task.get("storage_state_validation_retry_delay_seconds"), 2),
        1,
    )

    for attempt in range(1, validation_retry_count + 1):
        if validate_storage_state(task, rooms_config, storage_state_path):
            if attempt > 1:
                log_message(
                    f"✓ 登录态在第 {attempt} 次校验时恢复可用：{storage_state_path}"
                )
            else:
                log_message(f"✓ 复用登录态 {storage_state_path}")
            return storage_state_path

        if attempt < validation_retry_count:
            log_message(
                f"▶ 登录态校验未通过，{validation_retry_delay_seconds} 秒后重试 "
                f"({attempt}/{validation_retry_count})。"
            )
            time.sleep(validation_retry_delay_seconds)

    if refresh_storage_state_from_debug_chrome(task, rooms_config, storage_state_path):
        return storage_state_path

    if os.path.exists(storage_state_path):
        if parse_bool(task.get("trust_existing_storage_state"), True):
            log_message(
                "⚠ 登录态预校验未稳定通过，先按软复用继续启动。"
                " 如果随后页面跳回登录页，脚本会停止并等待人工接管。"
            )
            return storage_state_path
        log_message(f"⚠ 检测到失效的登录态，等待人工重新接管: {storage_state_path}")

    notify_before_manual_session = parse_bool(task.get("notify_before_manual_session"), False)
    manual_session_only = parse_bool(task.get("manual_session_only"), True)
    wait_for_session_after_notify = parse_bool(task.get("wait_for_session_after_notify"), True)

    if notify_before_manual_session:
        send_telegram_notification(task, storage_state_path)

    if notify_before_manual_session and wait_for_session_after_notify:
        if wait_for_manual_session(task, rooms_config, storage_state_path):
            return storage_state_path

    log_message("⚠ 未检测到可用登录态，已停止自动登录流程，等待人工接管。")

    if manual_session_only:
        raise TimeoutError(
            "未检测到人工准备好的登录态。脚本不会自动打开登录页；请先由工人接管完成登录并准备会话后，再继续任务。"
        )

    return bootstrap_storage_state(task, rooms_config, storage_state_path)


def room_matches_filters(candidate, rooms_config):
    """按包含词/排除词过滤比赛。"""
    haystack = f"{candidate['room_name']} {candidate['row_text']}".lower()
    include_terms = [term.lower() for term in rooms_config.get("include_terms", [])]
    exclude_terms = [term.lower() for term in rooms_config.get("exclude_terms", [])]

    if include_terms and not any(term in haystack for term in include_terms):
        return False
    if exclude_terms and any(term in haystack for term in exclude_terms):
        return False
    return True


def collect_room_candidates(page, rooms_url, rooms_config):
    """从房间列表页提取比赛链接。"""
    try:
        ready_selector = rooms_config.get("table_selector") or rooms_config.get("row_selector")
        if ready_selector:
            try:
                page.wait_for_selector(ready_selector, timeout=60000)
            except Exception:
                pass
        page.wait_for_timeout(rooms_config["load_wait_ms"])

        search_text = rooms_config.get("search_text")
        search_input_selector = rooms_config.get("search_input_selector")
        if search_text and search_input_selector and page.locator(search_input_selector).count() > 0:
            page.locator(search_input_selector).fill(search_text)
            page.wait_for_timeout(1000)

        rows = page.locator(rooms_config["row_selector"])
        row_count = rows.count()
        candidates = []

        for index in range(row_count):
            row = rows.nth(index)
            text_parts = [part.strip() for part in row.all_inner_texts() if part.strip()]
            row_text = " ".join(text_parts)
            room_name = row.get_attribute("data-room-name") or (text_parts[0] if text_parts else f"room-{index + 1}")
            session_id = row.get_attribute("data-session-id") or str(index + 1)
            competition_name = extract_competition_name(row_text, room_name)

            room_url = None
            selector_used = None
            link_text = ""

            for selector in rooms_config["viewer_link_selectors"]:
                link_locator = row.locator(selector)
                if link_locator.count() == 0:
                    continue

                room_url = link_locator.first.get_attribute("href")
                if room_url:
                    selector_used = selector
                    try:
                        link_text = link_locator.first.inner_text().strip()
                    except Exception:
                        link_text = ""
                    break

            if not room_url:
                continue

            candidates.append(
                {
                    "session_id": session_id,
                    "room_name": room_name,
                    "row_text": row_text,
                    "competition_name": competition_name,
                    "url": urljoin(rooms_url, room_url),
                    "selector_used": selector_used,
                    "link_text": link_text,
                    "discovery_index": index,
                }
            )

        return candidates
    except Exception:
        return []


def discover_room_tasks(task, discovery_session=None):
    """登录站点并发现需要录制的比赛。"""
    if discovery_session is not None:
        rooms_config = discovery_session["rooms_config"]
        rooms_url = discovery_session["rooms_url"]
        storage_state_path = discovery_session["storage_state_path"]
        if discovery_session.get("backend") == "pinchtab":
            refresh_discovery_page(discovery_session)
            candidates = collect_pinchtab_room_candidates(discovery_session)
        else:
            page = refresh_discovery_page(discovery_session)
            candidates = collect_room_candidates(page, rooms_url, rooms_config)
    else:
        from playwright.sync_api import sync_playwright

        rooms_config = load_rooms_config(task)
        rooms_url = normalize_url(task.get("rooms_url") or task.get("url") or task.get("login_url"))
        storage_state_path = ensure_storage_state(task)
        discovery_headless = parse_bool(task.get("discovery_headless"), True)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                **build_browser_launch_kwargs(task, discovery_headless)
            )
            context = browser.new_context(
                viewport=DEFAULT_VIEWPORT,
                storage_state=storage_state_path,
            )
            page = context.new_page()

            page.goto(rooms_url, wait_until="domcontentloaded", timeout=60000)
            if is_login_url(page.url):
                page.close()
                context.close()
                browser.close()
                raise ValueError(
                    "发现比赛阶段跳回登录页，当前登录态不可用。脚本已停止自动操作，请先由人工接管刷新会话后再继续。"
                )

            candidates = collect_room_candidates(page, rooms_url, rooms_config)

            page.close()
            context.close()
            browser.close()

    filtered_candidates = []
    seen_urls = set()
    for candidate in candidates:
        if not room_matches_filters(candidate, rooms_config):
            continue
        if candidate["url"] in seen_urls:
            continue
        _, priority_group = resolve_candidate_priority_group(candidate, rooms_config)
        candidate["priority_group"] = priority_group
        if rooms_config.get("require_priority_group_match") and priority_group == "page_order":
            continue
        seen_urls.add(candidate["url"])
        filtered_candidates.append(candidate)

    filtered_candidates.sort(key=lambda item: candidate_priority_key(item, rooms_config))
    filtered_candidates = filtered_candidates[: rooms_config["limit"]]

    if not filtered_candidates:
        raise NoRoomCandidatesError("未在房间列表页发现可录制的比赛链接。")

    if len(filtered_candidates) < rooms_config["limit"]:
        log_message(
            f"⚠ 仅发现 {len(filtered_candidates)} 场比赛，少于期望的 {rooms_config['limit']} 场。"
        )

    discovered_tasks = []
    for index, candidate in enumerate(filtered_candidates, start=1):
        room_slug = slugify(candidate["room_name"], f"room_{candidate['session_id']}")
        child_task_id = f"{task['task_id']}_{index:02d}_{room_slug[:40]}"

        discovered_tasks.append(
            {
                "task_id": child_task_id,
                "url": candidate["url"],
                "fields": task.get("fields", ["headings", "paragraphs", "links"]),
                "storage_state": storage_state_path,
                "record_seconds": parse_int(task.get("record_seconds"), 300),
                "page_ready_selector": task.get("page_ready_selector"),
                "wait_until": task.get("wait_until", "domcontentloaded"),
                "wait_after_load_ms": parse_int(task.get("wait_after_load_ms"), 2000),
                "headless": parse_bool(task.get("headless"), True),
                "browser_channel": task.get("browser_channel"),
                "proxy_server": task.get("proxy_server"),
                "proxy_username": task.get("proxy_username"),
                "proxy_password": task.get("proxy_password"),
                "source_task_id": task.get("task_id"),
                "room_name": candidate["room_name"],
                "session_id": candidate["session_id"],
                "selector_used": candidate["selector_used"],
                "row_text": candidate["row_text"],
                "competition_name": candidate.get("competition_name", ""),
                "priority_group": candidate.get("priority_group", "page_order"),
                "__task_root_dir": task.get("__task_root_dir"),
            }
        )

    log_message(
        f"✓ 站点任务 {task['task_id']} 已发现 {len(discovered_tasks)} 场比赛，准备并发录制。"
    )
    return discovered_tasks


def expand_tasks(tasks, discovery_sessions=None):
    """展开任务列表，将 site_rooms 扩展为多个子任务。"""
    expanded_tasks = []
    discovery_sessions = discovery_sessions or {}
    for task in tasks:
        mode = task.get("mode")
        if mode == "site_rooms":
            expanded_tasks.extend(
                discover_room_tasks(
                    task,
                    discovery_session=discovery_sessions.get(task.get("task_id")),
                )
            )
            continue

        cloned_task = dict(task)
        if "url" in cloned_task:
            cloned_task["url"] = normalize_url(cloned_task["url"])
        expanded_tasks.append(cloned_task)

    return expanded_tasks


def process_single_task(task, base_output_dir, retry_count=0):
    """处理单个任务，支持重试。"""
    task_id = task["task_id"]
    url = normalize_url(task["url"])
    fields = task.get("fields", [])
    storage_state_path = None
    if task.get("storage_state"):
        storage_state_path = resolve_task_path(task, task.get("storage_state"))

    task_dir = os.path.join(base_output_dir, f"task_{task_id}")
    os.makedirs(task_dir, exist_ok=True)

    start_time = datetime.now()
    timestamp = start_time.strftime("%Y%m%d_%H%M%S")

    result = {
        "status": "success",
        "data": {},
        "errors": [],
        "events": [],
        "retry_count": retry_count,
    }

    result["events"].append(
        {
            "name": "task_started",
            "timestamp": start_time.isoformat(),
            "status": "started",
        }
    )
    log_message(f"▶ 开始任务 {task_id} (retry={retry_count}) url={url}")

    try:
        result["events"].append(
            {
                "name": "screenshot_started",
                "timestamp": datetime.now().isoformat(),
                "status": "started",
            }
        )

        video_start_time = datetime.now()
        result["events"].append(
            {
                "name": "video_recording_started",
                "timestamp": video_start_time.isoformat(),
                "status": "started",
            }
        )

        capture_data = capture_page(
            url=url,
            output_dir=task_dir,
            timestamp=timestamp,
            storage_state_path=storage_state_path,
            headless=parse_bool(task.get("headless"), True),
            browser_channel=task.get("browser_channel"),
            proxy_server=task.get("proxy_server"),
            proxy_username=task.get("proxy_username"),
            proxy_password=task.get("proxy_password"),
            record_seconds=parse_int(task.get("record_seconds"), 5),
            page_ready_selector=task.get("page_ready_selector"),
            wait_until=task.get("wait_until", "domcontentloaded" if storage_state_path else "load"),
            wait_after_load_ms=parse_int(task.get("wait_after_load_ms"), 2000),
        )

        result["events"].append(
            {
                "name": "screenshot_completed",
                "timestamp": datetime.now().isoformat(),
                "status": "completed",
            }
        )
        result["events"].append(
            {
                "name": "video_recording_completed",
                "timestamp": datetime.now().isoformat(),
                "status": "completed",
            }
        )

        if storage_state_path and is_login_url(capture_data["final_url"]):
            raise ValueError("登录态失效，页面跳回登录页。")

        result["events"].append(
            {
                "name": "data_extraction_started",
                "timestamp": datetime.now().isoformat(),
                "status": "started",
            }
        )

        result["data"].update(extract_fields_from_html(capture_data["html"], fields))

        result["events"].append(
            {
                "name": "data_extraction_completed",
                "timestamp": datetime.now().isoformat(),
                "status": "completed",
            }
        )

        result["data"]["screenshot_file"] = capture_data["screenshot_path"]
        result["data"]["video_file"] = capture_data["video_path"]
        result["data"]["page_title"] = capture_data["page_title"]
        result["data"]["final_url"] = capture_data["final_url"]
        result["data"]["http_status"] = capture_data["http_status"]

        if task.get("source_task_id"):
            result["data"]["source_task_id"] = task.get("source_task_id")
        if task.get("room_name"):
            result["data"]["room_name"] = task.get("room_name")
        if task.get("session_id"):
            result["data"]["session_id"] = task.get("session_id")
        if task.get("selector_used"):
            result["data"]["selector_used"] = task.get("selector_used")
        if task.get("competition_name"):
            result["data"]["competition_name"] = task.get("competition_name")
        if task.get("priority_group"):
            result["data"]["priority_group"] = task.get("priority_group")

        result["alignment"] = build_alignment(result["events"], video_start_time)

        data_output_file = os.path.join(task_dir, "data.json")
        result["data"]["_output_file"] = data_output_file
        result["data"]["alignment"] = result["alignment"]
        save_json(result["data"], data_output_file)

        result["output_file"] = data_output_file
        result["timestamp"] = timestamp

        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()

        screenshot_path = capture_data["screenshot_path"]
        video_path = capture_data["video_path"]
        screenshot_status = "completed" if screenshot_path and os.path.exists(screenshot_path) else "failed"
        video_status = (
            "completed"
            if video_path and os.path.exists(video_path) and os.path.getsize(video_path) > 0
            else "failed"
        )

        report = {
            "status": result["status"],
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "duration": elapsed_time,
            "url": capture_data["final_url"],
            "requested_url": url,
            "items_processed": len(result["data"].get("headings", [])) + len(result["data"].get("paragraphs", [])),
            "screenshot_status": screenshot_status,
            "video_status": video_status,
            "retry_count": retry_count,
            "room_name": task.get("room_name", ""),
            "session_id": task.get("session_id", ""),
            "source_task_id": task.get("source_task_id", ""),
            "competition_name": task.get("competition_name", ""),
            "priority_group": task.get("priority_group", ""),
        }

        report_path = os.path.join(task_dir, "report.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("执行报告\n")
            f.write("=" * 40 + "\n\n")
            for key, value in report.items():
                f.write(f"{key}: {value}\n")

        write_error_log(
            task_dir,
            task_id,
            url,
            result["status"],
            start_time,
            end_time,
            retry_count,
            result["errors"],
        )

        result["report"] = report
        result["elapsed_time"] = elapsed_time
        result["screenshot_status"] = screenshot_status
        result["video_status"] = video_status

        result["events"].append(
            {
                "name": "task_completed",
                "timestamp": end_time.isoformat(),
                "status": "completed",
            }
        )

        log_message(
            f"✓ 任务完成 {task_id} status={result['status']} "
            f"screenshot={screenshot_status} video={video_status} duration={elapsed_time:.2f}s"
        )

        return result

    except Exception as e:
        if retry_count == 0:
            log_message(f"⚠️ 任务 {task_id} 第一次执行失败，开始重试...")
            result["errors"].append(f"第一次执行错误：{str(e)}")
            retried_result = process_single_task(task, base_output_dir, retry_count=1)
            retried_result["errors"].append("重试完成")
            return retried_result

        result["status"] = "error"
        result["errors"].append(f"错误：{str(e)}")
        end_time = datetime.now()
        write_error_log(
            task_dir,
            task_id,
            url,
            result["status"],
            start_time,
            end_time,
            retry_count,
            result["errors"],
        )
        log_message(f"✗ 任务失败 {task_id} retry={retry_count} error={str(e)}")
        return result


def load_prepared_tasks(tasks_file):
    """加载任务并补充任务根目录。"""
    tasks_file = os.path.abspath(tasks_file)
    task_root_dir = infer_task_root_dir(tasks_file)

    with open(tasks_file, "r", encoding="utf-8") as f:
        loaded_tasks = json.load(f)

    if not isinstance(loaded_tasks, list):
        raise ValueError("任务清单必须是 JSON 数组。")

    prepared_tasks = []
    for task in loaded_tasks:
        cloned_task = apply_runtime_overrides(task)
        cloned_task["__task_root_dir"] = task_root_dir
        prepared_tasks.append(cloned_task)

    return tasks_file, prepared_tasks


def execute_batch(prepared_tasks, tasks_file, base_output_dir, max_workers=3, summary_filename="batch_summary.json", cycle_index=None, discovery_sessions=None):
    """执行一轮批处理。"""
    if max_workers < 1:
        max_workers = 1
    elif max_workers > 12:
        max_workers = 12

    expanded_tasks = expand_tasks(prepared_tasks, discovery_sessions=discovery_sessions)

    if not expanded_tasks:
        raise NoRoomCandidatesError("没有可执行的任务。")

    os.makedirs(base_output_dir, exist_ok=True)

    batch_start = datetime.now()
    cycle_label = f" cycle={cycle_index}" if cycle_index is not None else ""
    log_message(
        f"▶ 批量任务启动 tasks_file={tasks_file} source_tasks={len(prepared_tasks)} "
        f"expanded_tasks={len(expanded_tasks)} max_workers={max_workers}{cycle_label}"
    )

    task_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(process_single_task, task, base_output_dir): idx
            for idx, task in enumerate(expanded_tasks)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            task_results.append((idx, future.result()))

    task_results.sort(key=lambda item: item[0])

    batch_end = datetime.now()
    batch_duration = (batch_end - batch_start).total_seconds()

    summary = {
        "batch_start": batch_start.isoformat(),
        "batch_end": batch_end.isoformat(),
        "batch_duration_seconds": round(batch_duration, 2),
        "duration": round(batch_duration, 2),
        "start_time": batch_start.isoformat(),
        "end_time": batch_end.isoformat(),
        "source_tasks": len(prepared_tasks),
        "total_tasks": len(expanded_tasks),
        "successful_tasks": len([r for _, r in task_results if r["status"] == "success"]),
        "failed_tasks": len([r for _, r in task_results if r["status"] == "error"]),
        "max_concurrent_workers": max_workers,
        "tasks": [],
    }
    if cycle_index is not None:
        summary["cycle_index"] = cycle_index

    for task_idx, result in task_results:
        original_task = expanded_tasks[task_idx]
        task_summary = {
            "task_id": original_task.get("task_id"),
            "source_task_id": original_task.get("source_task_id"),
            "room_name": original_task.get("room_name"),
            "session_id": original_task.get("session_id"),
            "competition_name": original_task.get("competition_name"),
            "priority_group": original_task.get("priority_group"),
            "status": result["status"],
            "url": result.get("data", {}).get("final_url", original_task.get("url")),
            "output_dir": f"task_{original_task['task_id']}",
            "screenshot_status": result.get("screenshot_status", "unknown"),
            "video_status": result.get("video_status", "unknown"),
            "elapsed_time": result.get("elapsed_time", 0),
            "errors": result.get("errors", []),
            "retry_count": result.get("retry_count", 0),
            "start_time": result.get("report", {}).get("start_time", ""),
            "end_time": result.get("report", {}).get("end_time", ""),
            "duration": result.get("elapsed_time", 0),
        }
        summary["tasks"].append(task_summary)

    summary_file = os.path.join(base_output_dir, summary_filename)
    save_json(summary, summary_file)

    log_message("✓ 批量处理完成！")
    log_message(f"  - 原始任务数：{len(prepared_tasks)}")
    log_message(f"  - 展开后任务数：{len(expanded_tasks)}")
    log_message(f"  - 成功：{summary['successful_tasks']}")
    log_message(f"  - 失败：{summary['failed_tasks']}")
    log_message(f"  - 总用时：{batch_duration:.2f} 秒")
    log_message(f"  - 最大并发数：{max_workers}")
    log_message(f"  - 总结文件：{summary_file}")

    return summary


def run_monitor_loop(prepared_tasks, tasks_file, base_output_dir, max_workers=3):
    """持续轮询赛程，按优先级补位录制。"""
    from playwright.sync_api import sync_playwright

    monitor_tasks = [task for task in prepared_tasks if load_monitor_config(task).get("enabled")]
    monitor_config = load_monitor_config(monitor_tasks[0] if monitor_tasks else prepared_tasks[0])
    site_room_tasks = [task for task in prepared_tasks if task.get("mode") == "site_rooms"]

    cycle_index = 0
    empty_cycles = 0
    cycle_summaries = []
    monitor_start = datetime.now()
    with sync_playwright() as playwright:
        discovery_sessions = {
            task["task_id"]: create_discovery_session(playwright, task)
            for task in site_room_tasks
        }

        try:
            while True:
                cycle_index += 1
                log_message(f"▶ 监控循环开始 cycle={cycle_index}")

                try:
                    cycle_summary = execute_batch(
                        prepared_tasks,
                        tasks_file,
                        base_output_dir,
                        max_workers=max_workers,
                        summary_filename=f"{monitor_config['summary_prefix']}_{cycle_index:03d}.json",
                        cycle_index=cycle_index,
                        discovery_sessions=discovery_sessions,
                    )
                    cycle_summaries.append(cycle_summary)
                    empty_cycles = 0
                except NoRoomCandidatesError as exc:
                    empty_cycles += 1
                    cycle_summaries.append(
                        {
                            "cycle_index": cycle_index,
                            "status": "empty",
                            "message": str(exc),
                            "timestamp": datetime.now().isoformat(),
                        }
                    )
                    log_message(f"⚠ 监控循环 {cycle_index} 未发现可录制比赛：{exc}")
                except DiscoveryWindowGuardError as exc:
                    empty_cycles += 1
                    cycle_summaries.append(
                        {
                            "cycle_index": cycle_index,
                            "status": "guarded",
                            "message": str(exc),
                            "timestamp": datetime.now().isoformat(),
                        }
                    )
                    log_message(f"⚠ 监控循环 {cycle_index} 已触发安全保护：{exc}")

                if monitor_config["max_cycles"] > 0 and cycle_index >= monitor_config["max_cycles"]:
                    log_message(f"✓ 已达到监控循环上限 {monitor_config['max_cycles']}，停止继续补位。")
                    break

                if (
                    monitor_config["empty_cycles_before_stop"] > 0
                    and empty_cycles >= monitor_config["empty_cycles_before_stop"]
                ):
                    log_message(
                        f"✓ 连续 {empty_cycles} 轮未发现可录制比赛，停止继续补位。"
                    )
                    break

                log_message(
                    f"▶ 等待 {monitor_config['poll_interval_seconds']} 秒后重新扫描赛程。"
                )
                time.sleep(monitor_config["poll_interval_seconds"])
        finally:
            for session in discovery_sessions.values():
                close_discovery_session(session)

    monitor_end = datetime.now()
    monitor_duration = (monitor_end - monitor_start).total_seconds()

    final_summary = {
        "mode": "monitor_loop",
        "monitor_start": monitor_start.isoformat(),
        "monitor_end": monitor_end.isoformat(),
        "monitor_duration_seconds": round(monitor_duration, 2),
        "cycles_completed": cycle_index,
        "empty_cycles": empty_cycles,
        "max_concurrent_workers": max_workers,
        "cycle_summaries": cycle_summaries,
    }

    summary_file = os.path.join(base_output_dir, "batch_summary.json")
    save_json(final_summary, summary_file)
    log_message("✓ 持续监控完成！")
    log_message(f"  - 监控循环数：{cycle_index}")
    log_message(f"  - 总结文件：{summary_file}")
    return final_summary


def run_batch(tasks_file, base_output_dir, max_workers=3):
    """根据任务配置执行单轮或持续监控。"""
    tasks_file, prepared_tasks = load_prepared_tasks(tasks_file)

    if any(load_monitor_config(task).get("enabled") for task in prepared_tasks):
        return run_monitor_loop(prepared_tasks, tasks_file, base_output_dir, max_workers=max_workers)

    return execute_batch(prepared_tasks, tasks_file, base_output_dir, max_workers=max_workers)


if __name__ == "__main__":
    import argparse

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_output_dir = os.path.join(project_root, "data")

    parser = argparse.ArgumentParser(description="Video Pipeline V6.2 - 单站点多比赛录制版")
    parser.add_argument("tasks_file", help="任务清单 JSON 文件路径")
    parser.add_argument(
        "--output-dir",
        default=default_output_dir,
        help="基础输出目录路径",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=3,
        help="最大并发数（默认 3，最多 12）",
    )

    args = parser.parse_args()

    run_batch(args.tasks_file, args.output_dir, args.max_workers)
