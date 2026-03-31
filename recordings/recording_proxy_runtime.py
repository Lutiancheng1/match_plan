#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent
MATCH_PLAN_DIR = PROJECT_DIR.parent
PROXY_RUNTIME_DIR = PROJECT_DIR / "watch_runtime" / "proxy_runtime"
OBSERVED_DOMAINS_PATH = PROXY_RUNTIME_DIR / "observed_domains.json"
OBSERVED_EVENTS_PATH = PROXY_RUNTIME_DIR / "observed_events.jsonl"
SINGBOX_CONFIG_PATH = PROXY_RUNTIME_DIR / "recording_singbox.json"
SINGBOX_META_PATH = PROXY_RUNTIME_DIR / "recording_singbox.meta.json"
SINGBOX_STATE_PATH = PROXY_RUNTIME_DIR / "recording_singbox.state.json"
RUNTIME_ENV_PATH = PROXY_RUNTIME_DIR / "recording_proxy.env"
CHROME_CHECK_PATH = PROXY_RUNTIME_DIR / "chrome_automation_check.json"
DEFAULT_SINGBOX_PORT = 17897
DEFAULT_SINGBOX_TEST_URL = "https://cp.cloudflare.com/generate_204"
DEFAULT_SHADOWROCKET_CANDIDATES = [
    MATCH_PLAN_DIR / "目前在用的小火箭配置 ",
    MATCH_PLAN_DIR / "目前在用的小火箭配置",
]
KNOWN_DATA_DOMAINS = {"hga035.com"}
KNOWN_DATA_IP_CIDRS = {"112.121.42.168/32"}
KNOWN_LIVE_DOMAINS = {"sftraders.live"}
NETWORK_EVENT_LIMIT = 200
PROXY_POLICY_DISABLED = "disabled"
PROXY_POLICY_CHROME = "chrome_managed"
PROXY_POLICY_SAFARI = "safari_system_fallback"
SUPPORTED_POLICIES = {PROXY_POLICY_DISABLED, PROXY_POLICY_CHROME, PROXY_POLICY_SAFARI}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_runtime_dir() -> None:
    PROXY_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, payload: Any) -> None:
    ensure_runtime_dir()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def stable_tag(prefix: str, index: int) -> str:
    return f"{prefix}_{index:03d}"


def split_csv_line(text: str) -> list[str]:
    return next(csv.reader([text], skipinitialspace=True))


def find_shadowrocket_config(explicit: str | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_path = str(os.environ.get("MATCH_SHADOWROCKET_CONFIG", "")).strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(DEFAULT_SHADOWROCKET_CANDIDATES)
    for path in candidates:
        if path.exists():
            return path
    return None


def parse_kv_pairs(items: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parsed[key.strip()] = value.strip().strip('"')
    return parsed


def parse_shadowrocket_proxies(path: Path) -> list[dict[str, Any]]:
    section = ""
    proxies: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != "proxy" or "=" not in line:
            continue
        name, rest = line.split("=", 1)
        tokens = split_csv_line(rest.strip())
        if not tokens:
            continue
        proxy_type = tokens[0].strip().lower()
        if proxy_type == "direct":
            proxies.append({"name": name.strip(), "type": "direct"})
            continue
        if len(tokens) < 3:
            continue
        host = tokens[1].strip()
        port_text = tokens[2].strip()
        try:
            port = int(port_text)
        except ValueError:
            continue
        options = parse_kv_pairs(tokens[3:])
        proxies.append(
            {
                "name": name.strip(),
                "type": proxy_type,
                "server": host,
                "server_port": port,
                "options": options,
            }
        )
    return proxies


def is_hk_or_tw(name: str) -> bool:
    text = (name or "").lower()
    return any(token in text for token in ("香港", "hk", "taiwan", "台湾", "tw"))


def is_live_overseas(name: str) -> bool:
    text = (name or "").lower()
    if is_hk_or_tw(text):
        return False
    return any(
        token in text
        for token in (
            "新加坡",
            "sg",
            "日本",
            "jp",
            "韩国",
            "kr",
            "美国",
            "us",
            "英国",
            "uk",
            "德国",
            "de",
            "加拿大",
            "ca",
            "国外",
        )
    )


def build_singbox_outbound(proxy: dict[str, Any], index: int) -> dict[str, Any] | None:
    proxy_type = proxy.get("type", "")
    tag = stable_tag("proxy", index)
    if proxy_type == "hysteria2":
        options = proxy.get("options") or {}
        outbound: dict[str, Any] = {
            "type": "hysteria2",
            "tag": tag,
            "server": proxy["server"],
            "server_port": int(proxy["server_port"]),
            "password": options.get("password", ""),
            "tls": {
                "enabled": True,
                "server_name": options.get("sni") or proxy["server"],
                "insecure": str(options.get("skip-cert-verify", "")).lower() == "true",
            },
        }
        bandwidth = options.get("download-bandwidth", "")
        if bandwidth:
            try:
                outbound["up_mbps"] = int(float(bandwidth))
                outbound["down_mbps"] = int(float(bandwidth))
            except ValueError:
                pass
        return outbound
    if proxy_type == "vmess":
        options = proxy.get("options") or {}
        outbound = {
            "type": "vmess",
            "tag": tag,
            "server": proxy["server"],
            "server_port": int(proxy["server_port"]),
            "uuid": options.get("username", ""),
            "security": "auto",
        }
        if str(options.get("ws", "")).lower() == "true":
            headers = {}
            header_host = options.get("ws-headers", "")
            if header_host:
                match = re.search(r'host\s*:\s*"([^"]+)"', header_host, flags=re.I)
                if match:
                    headers["Host"] = match.group(1)
            outbound["transport"] = {
                "type": "ws",
                "path": options.get("ws-path", "/"),
            }
            if headers:
                outbound["transport"]["headers"] = headers
        return outbound
    if proxy_type == "direct":
        return {"type": "direct", "tag": tag}
    return None


def load_observed_domains() -> dict[str, Any]:
    payload = load_json(OBSERVED_DOMAINS_PATH, {"hosts": {}, "updated_at": ""})
    if not isinstance(payload, dict):
        return {"hosts": {}, "updated_at": ""}
    payload.setdefault("hosts", {})
    return payload


def classify_observed_host(host: str, source: str = "") -> str:
    host = (host or "").strip().lower()
    source = (source or "").strip().lower()
    if not host:
        return "unknown"
    if host in KNOWN_DATA_DOMAINS or host.endswith(".hga035.com") or "login" in source or "dashboard" in source or "feed" in source:
        return "data"
    if host in KNOWN_LIVE_DOMAINS or host.endswith(".sftraders.live"):
        return "live"
    return "data" if source.startswith("auto_login") or "poll" in source else "unknown"


def update_observed_domains(
    *,
    source: str,
    requested_url: str = "",
    final_url: str = "",
    redirect_chain: list[str] | None = None,
    error: str = "",
    elapsed_seconds: float | None = None,
) -> None:
    ensure_runtime_dir()
    payload = load_observed_domains()
    hosts = payload.setdefault("hosts", {})
    urls = [requested_url, final_url, *(redirect_chain or [])]
    observed_hosts = []
    for url in urls:
        if not url:
            continue
        from urllib.parse import urlsplit

        host = urlsplit(url).netloc.split("@")[-1].split(":")[0].strip().lower()
        if not host:
            continue
        observed_hosts.append(host)
        slot = hosts.setdefault(
            host,
            {
                "category": classify_observed_host(host, source),
                "first_seen_at": now_iso(),
                "last_seen_at": "",
                "count": 0,
                "sources": [],
                "last_requested_url": "",
                "last_final_url": "",
                "last_error": "",
                "last_elapsed_seconds": None,
            },
        )
        slot["last_seen_at"] = now_iso()
        slot["count"] = int(slot.get("count") or 0) + 1
        slot["category"] = slot.get("category") or classify_observed_host(host, source)
        slot["last_requested_url"] = requested_url or slot.get("last_requested_url", "")
        slot["last_final_url"] = final_url or slot.get("last_final_url", "")
        slot["last_error"] = error or ""
        if elapsed_seconds is not None:
            slot["last_elapsed_seconds"] = round(float(elapsed_seconds), 3)
        sources = [item for item in slot.get("sources", []) if item != source]
        sources.append(source)
        slot["sources"] = sources[-6:]
    payload["updated_at"] = now_iso()
    save_json(OBSERVED_DOMAINS_PATH, payload)

    event = {
        "timestamp": now_iso(),
        "source": source,
        "requested_url": requested_url,
        "final_url": final_url,
        "redirect_chain": redirect_chain or [],
        "error": error,
        "elapsed_seconds": round(float(elapsed_seconds), 3) if elapsed_seconds is not None else None,
        "hosts": observed_hosts,
    }
    with OBSERVED_EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    trim_observed_events()


def trim_observed_events(limit: int = NETWORK_EVENT_LIMIT) -> None:
    if not OBSERVED_EVENTS_PATH.exists():
        return
    lines = OBSERVED_EVENTS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    if len(lines) <= limit:
        return
    OBSERVED_EVENTS_PATH.write_text("\n".join(lines[-limit:]) + "\n", encoding="utf-8")


def build_observed_domain_rules(category: str) -> list[str]:
    payload = load_observed_domains()
    rules = []
    for host, slot in sorted((payload.get("hosts") or {}).items()):
        if (slot or {}).get("category") != category:
            continue
        if host in KNOWN_DATA_DOMAINS or host in KNOWN_LIVE_DOMAINS:
            continue
        rules.append(host)
    return rules


def build_singbox_config(
    *,
    shadowrocket_path: Path,
    policy: str,
    listen_port: int = DEFAULT_SINGBOX_PORT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    proxies = parse_shadowrocket_proxies(shadowrocket_path)
    outbounds: list[dict[str, Any]] = []
    data_pool_tags: list[str] = []
    live_pool_tags: list[str] = []
    named_tags: list[dict[str, str]] = []
    for index, proxy in enumerate(proxies, start=1):
        outbound = build_singbox_outbound(proxy, index)
        if not outbound:
            continue
        outbounds.append(outbound)
        named_tags.append({"name": proxy["name"], "tag": outbound["tag"], "type": proxy["type"]})
        if is_hk_or_tw(proxy["name"]):
            data_pool_tags.append(outbound["tag"])
        if is_live_overseas(proxy["name"]):
            live_pool_tags.append(outbound["tag"])

    if not data_pool_tags:
        raise RuntimeError("未能从小火箭配置中找到可用于抓数链的香港/台湾节点")
    if not live_pool_tags:
        raise RuntimeError("未能从小火箭配置中找到可用于直播链的海外节点")

    outbounds.extend(
        [
            {
                "type": "urltest",
                "tag": "recording_data_pool",
                "outbounds": data_pool_tags,
                "url": DEFAULT_SINGBOX_TEST_URL,
                "interval": "3m",
                "tolerance": 100,
            },
            {
                "type": "urltest",
                "tag": "recording_live_pool",
                "outbounds": live_pool_tags,
                "url": DEFAULT_SINGBOX_TEST_URL,
                "interval": "3m",
                "tolerance": 120,
            },
            {"type": "direct", "tag": "direct"},
            {"type": "block", "tag": "block"},
        ]
    )

    data_domains = sorted(KNOWN_DATA_DOMAINS | set(build_observed_domain_rules("data")))
    live_domains = sorted(KNOWN_LIVE_DOMAINS | set(build_observed_domain_rules("live")))

    meta = {
        "generated_at": now_iso(),
        "policy": policy,
        "shadowrocket_config": str(shadowrocket_path),
        "listen_port": listen_port,
        "proxy_env_url": f"http://127.0.0.1:{listen_port}",
        "data_pool": data_pool_tags,
        "live_pool": live_pool_tags,
        "proxy_tags": named_tags,
        "data_domains": data_domains,
        "live_domains": live_domains,
    }

    config: dict[str, Any] = {
        "log": {"level": "info", "timestamp": True},
        "inbounds": [
            {
                "type": "mixed",
                "tag": "recording_mixed",
                "listen": "127.0.0.1",
                "listen_port": listen_port,
                "set_system_proxy": policy == PROXY_POLICY_SAFARI,
            }
        ],
        "outbounds": outbounds,
        "route": {
            "auto_detect_interface": True,
            "final": "direct",
            "rules": [
                {"ip_cidr": sorted(KNOWN_DATA_IP_CIDRS), "outbound": "recording_data_pool"},
                {"domain_suffix": data_domains, "outbound": "recording_data_pool"},
                {"domain_suffix": live_domains, "outbound": "recording_live_pool"},
            ],
        },
    }
    return config, meta


def write_runtime_env(listen_port: int, policy: str) -> None:
    lines = [
        f"export MATCH_RECORDING_PROXY_POLICY={shlex.quote(policy)}",
        f"export MATCH_RECORDING_PROXY_URL=http://127.0.0.1:{listen_port}",
        f"export HTTP_PROXY=http://127.0.0.1:{listen_port}",
        f"export HTTPS_PROXY=http://127.0.0.1:{listen_port}",
        f"export ALL_PROXY=socks5://127.0.0.1:{listen_port}",
        "export NO_PROXY=127.0.0.1,localhost",
    ]
    ensure_runtime_dir()
    RUNTIME_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_singbox_binary() -> str | None:
    env_path = str(os.environ.get("MATCH_SINGBOX_BIN", "")).strip()
    if env_path and Path(env_path).exists():
        return env_path
    for candidate in (
        shutil.which("sing-box"),
        str(PROJECT_DIR / ".bin" / "sing-box"),
        "/opt/homebrew/bin/sing-box",
        "/usr/local/bin/sing-box",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def load_state() -> dict[str, Any]:
    return load_json(SINGBOX_STATE_PATH, {})


def save_state(payload: dict[str, Any]) -> None:
    save_json(SINGBOX_STATE_PATH, payload)


def is_pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def generate_runtime(policy: str) -> dict[str, Any]:
    ensure_runtime_dir()
    shadowrocket_path = find_shadowrocket_config()
    if not shadowrocket_path:
        raise FileNotFoundError("未找到小火箭配置文件")
    config, meta = build_singbox_config(shadowrocket_path=shadowrocket_path, policy=policy)
    save_json(SINGBOX_CONFIG_PATH, config)
    save_json(SINGBOX_META_PATH, meta)
    listen_port = int(meta.get("listen_port") or DEFAULT_SINGBOX_PORT)
    write_runtime_env(listen_port, policy)
    return {
        "config_path": str(SINGBOX_CONFIG_PATH),
        "env_path": str(RUNTIME_ENV_PATH),
        "meta": meta,
    }


def ensure_runtime(policy: str, logger=None) -> dict[str, Any]:
    if policy not in SUPPORTED_POLICIES:
        raise ValueError(f"不支持的 proxy policy: {policy}")
    if policy == PROXY_POLICY_DISABLED:
        return {"enabled": False, "policy": policy}
    generated = generate_runtime(policy)
    binary = find_singbox_binary()
    state = load_state()
    if not binary:
        return {
            "enabled": False,
            "policy": policy,
            "error": "sing-box binary not found",
            **generated,
        }
    if is_pid_alive(int(state.get("pid") or 0)) and state.get("policy") == policy:
        return {
            "enabled": True,
            "policy": policy,
            "pid": int(state.get("pid") or 0),
            "config_path": generated["config_path"],
            "env_path": generated["env_path"],
            "meta": generated["meta"],
        }
    if is_pid_alive(int(state.get("pid") or 0)):
        stop_runtime()
    check = subprocess.run(
        [binary, "check", "-c", str(SINGBOX_CONFIG_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode != 0:
        return {
            "enabled": False,
            "policy": policy,
            "error": check.stderr.strip() or check.stdout.strip() or "sing-box check failed",
            **generated,
        }
    log_path = PROXY_RUNTIME_DIR / "sing-box.log"
    with log_path.open("a", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            [binary, "run", "-c", str(SINGBOX_CONFIG_PATH)],
            cwd=str(PROJECT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(1.2)
    payload = {
        "pid": proc.pid,
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "policy": policy,
        "config_path": str(SINGBOX_CONFIG_PATH),
        "env_path": str(RUNTIME_ENV_PATH),
        "log_path": str(log_path),
    }
    save_state(payload)
    if logger:
        logger.log(f"已启动 recording sing-box: pid={proc.pid} policy={policy}")
    return {
        "enabled": is_pid_alive(proc.pid),
        "policy": policy,
        "pid": proc.pid,
        "config_path": generated["config_path"],
        "env_path": generated["env_path"],
        "meta": generated["meta"],
        "log_path": str(log_path),
    }


def stop_runtime() -> dict[str, Any]:
    state = load_state()
    pid = int(state.get("pid") or 0)
    if not is_pid_alive(pid):
        return {"stopped": False, "reason": "not_running"}
    os.kill(pid, 15)
    deadline = time.time() + 8
    while time.time() < deadline:
        if not is_pid_alive(pid):
            break
        time.sleep(0.2)
    if is_pid_alive(pid):
        os.kill(pid, 9)
    state["stopped_at"] = now_iso()
    state["updated_at"] = now_iso()
    save_state(state)
    return {"stopped": True, "pid": pid}


def apply_proxy_env(policy: str, logger=None) -> dict[str, Any]:
    runtime = ensure_runtime(policy, logger=logger)
    if not runtime.get("enabled"):
        if logger:
            logger.log(
                f"代理运行时未启用: policy={policy} reason={runtime.get('error', 'unknown')}",
                "WARN",
            )
        return runtime
    env_url = ((runtime.get("meta") or {}).get("proxy_env_url") or f"http://127.0.0.1:{DEFAULT_SINGBOX_PORT}").strip()
    os.environ["MATCH_RECORDING_PROXY_POLICY"] = policy
    os.environ["MATCH_RECORDING_PROXY_URL"] = env_url
    os.environ["HTTP_PROXY"] = env_url
    os.environ["HTTPS_PROXY"] = env_url
    os.environ["ALL_PROXY"] = env_url
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    return runtime


def clear_proxy_env() -> None:
    for key in ("MATCH_RECORDING_PROXY_POLICY", "MATCH_RECORDING_PROXY_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(key, None)


def read_recent_events(limit: int = 10) -> list[dict[str, Any]]:
    if not OBSERVED_EVENTS_PATH.exists():
        return []
    rows = []
    for line in OBSERVED_EVENTS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def check_chrome_automation() -> dict[str, Any]:
    ensure_runtime_dir()
    result = {
        "checked_at": now_iso(),
        "chrome_window_control": False,
        "chrome_js_from_apple_events": False,
        "window_control_output": "",
        "js_output": "",
    }
    window_cmd = ["osascript", "-e", 'tell application "Google Chrome" to get name of front window']
    js_script = '\n'.join(
        [
            'tell application "Google Chrome"',
            '  execute front window\'s active tab javascript "document.title"',
            "end tell",
        ]
    )
    try:
        completed = subprocess.run(window_cmd, capture_output=True, text=True, timeout=8, check=False)
        result["chrome_window_control"] = completed.returncode == 0
        result["window_control_output"] = (completed.stdout or completed.stderr).strip()
    except Exception as exc:
        result["window_control_output"] = str(exc)
    try:
        completed = subprocess.run(["osascript", "-e", js_script], capture_output=True, text=True, timeout=8, check=False)
        result["chrome_js_from_apple_events"] = completed.returncode == 0
        result["js_output"] = (completed.stdout or completed.stderr).strip()
    except Exception as exc:
        result["js_output"] = str(exc)
    save_json(CHROME_CHECK_PATH, result)
    return result


def status_payload() -> dict[str, Any]:
    state = load_state()
    return {
        "now": now_iso(),
        "running": is_pid_alive(int(state.get("pid") or 0)),
        "state": state,
        "meta": load_json(SINGBOX_META_PATH, {}),
        "observed_domains": load_observed_domains(),
        "recent_events": read_recent_events(),
        "chrome_check": load_json(CHROME_CHECK_PATH, {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage recording-specific sing-box runtime.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser("generate")
    gen.add_argument("--policy", default=PROXY_POLICY_SAFARI, choices=sorted(SUPPORTED_POLICIES))

    ensure = subparsers.add_parser("ensure")
    ensure.add_argument("--policy", default=PROXY_POLICY_SAFARI, choices=sorted(SUPPORTED_POLICIES))

    subparsers.add_parser("status")
    subparsers.add_parser("stop")
    subparsers.add_parser("check-chrome")
    args = parser.parse_args()

    if args.command == "generate":
        print(json.dumps(generate_runtime(args.policy), ensure_ascii=False, indent=2))
        return 0
    if args.command == "ensure":
        print(json.dumps(ensure_runtime(args.policy), ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(status_payload(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "stop":
        print(json.dumps(stop_runtime(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "check-chrome":
        print(json.dumps(check_chrome_automation(), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
