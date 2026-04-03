#!/usr/bin/env python3
"""Probe sing-box nodes against the data site and rebuild config with working nodes.

At startup (or on 404 failover), this module:
1. Reads the sing-box config to enumerate all VLESS/outbound nodes
2. Spins up a temporary sing-box instance per node on ports 17920+
3. Tests each node by fetching https://112.121.42.168/ through its temp proxy
4. Rewrites recording_data_pool to only include nodes that pass the probe
5. Restarts the main sing-box instance (port 17897) with the updated config

Usage from other modules:
    from data_site_node_prober import ensure_data_site_nodes
    working = ensure_data_site_nodes()  # returns list of working node tags
"""
from __future__ import annotations

import json
import os
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SINGBOX_BIN = SCRIPT_DIR / ".bin" / "sing-box"
SINGBOX_CONFIG = SCRIPT_DIR / "watch_runtime" / "proxy_runtime" / "recording_singbox.json"
SINGBOX_CONFIG_BACKUP = SINGBOX_CONFIG.with_suffix(".json.bak")
SINGBOX_STATE = SCRIPT_DIR / "watch_runtime" / "proxy_runtime" / "recording_singbox.state.json"
SINGBOX_LOG = SCRIPT_DIR / "watch_runtime" / "proxy_runtime" / "sing-box.log"

DATA_SITE_IP = "112.121.42.168"
DATA_SITE_URL = f"https://{DATA_SITE_IP}/"
PROBE_BASE_PORT = 17920
PROBE_TIMEOUT_SECONDS = 12
MAIN_SINGBOX_PORT = 17897
SSL_CTX = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Tags that should never be removed from the config (non-node outbounds)
_STRUCTURAL_TAGS = {"direct", "block", "recording_data_pool", "recording_live_pool"}

# Cache: last probe result so callers can check without re-probing
_last_working_tags: list[str] = []
_last_probe_time: float = 0.0
PROBE_CACHE_TTL_SECONDS = 300  # 5 minutes


def _log(msg: str) -> None:
    print(f"[node_prober] {msg}", file=sys.stderr, flush=True)


def _load_config() -> dict[str, Any]:
    return json.loads(SINGBOX_CONFIG.read_text(encoding="utf-8"))


def _save_config(config: dict[str, Any]) -> None:
    SINGBOX_CONFIG.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _extract_node_tags(config: dict[str, Any]) -> list[str]:
    """Return tags of all actual proxy nodes (excluding structural outbounds)."""
    tags = []
    for ob in config.get("outbounds", []):
        tag = ob.get("tag", "")
        if tag and tag not in _STRUCTURAL_TAGS:
            tags.append(tag)
    return tags


def _find_outbound_by_tag(config: dict[str, Any], tag: str) -> dict[str, Any] | None:
    for ob in config.get("outbounds", []):
        if ob.get("tag") == tag:
            return ob
    return None


def _build_probe_config(node_outbound: dict[str, Any], port: int) -> dict[str, Any]:
    """Build a minimal sing-box config that routes all traffic through one node."""
    node = deepcopy(node_outbound)
    node_tag = node["tag"]
    return {
        "log": {"level": "error", "timestamp": True},
        "inbounds": [
            {
                "type": "mixed",
                "tag": "probe_mixed",
                "listen": "127.0.0.1",
                "listen_port": port,
                "set_system_proxy": False,
            }
        ],
        "outbounds": [
            node,
            {"type": "direct", "tag": "direct"},
        ],
        "route": {
            "auto_detect_interface": True,
            "final": node_tag,
        },
    }


def _probe_single_node(
    node_outbound: dict[str, Any],
    port: int,
    singbox_bin: str,
) -> tuple[str, bool, str]:
    """Probe one node. Returns (tag, success, reason)."""
    tag = node_outbound["tag"]
    config = _build_probe_config(node_outbound, port)

    # Write temp config
    tmp_config = SINGBOX_CONFIG.parent / f"_probe_{tag}.json"
    proc: subprocess.Popen | None = None
    try:
        tmp_config.write_text(json.dumps(config, indent=2), encoding="utf-8")

        # Start temp sing-box
        proc = subprocess.Popen(
            [singbox_bin, "run", "-c", str(tmp_config)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(1.0)  # wait for sing-box to bind port

        if proc.poll() is not None:
            return tag, False, "sing-box exited immediately"

        # Fetch data site through this node's proxy
        proxy_handler = urllib.request.ProxyHandler({
            "http": f"http://127.0.0.1:{port}",
            "https": f"http://127.0.0.1:{port}",
        })
        opener = urllib.request.build_opener(
            proxy_handler,
            urllib.request.HTTPSHandler(context=SSL_CTX),
        )
        req = urllib.request.Request(DATA_SITE_URL, headers={"User-Agent": UA})
        try:
            with opener.open(req, timeout=PROBE_TIMEOUT_SECONDS) as resp:
                body = resp.read(8192).decode("utf-8", errors="replace")
        except Exception as e:
            return tag, False, f"fetch error: {e}"

        body_lower = body.lower()
        # Check for blocking indicators
        if "baidu" in body_lower:
            return tag, False, "baidu redirect (geo-blocked)"
        if "<title>forbidden</title>" in body_lower:
            return tag, False, "forbidden page"
        if "access to this site is blocked" in body_lower:
            return tag, False, "access blocked"
        if len(body) < 200:
            return tag, False, f"response too short ({len(body)} bytes)"

        return tag, True, "ok"

    except Exception as e:
        return tag, False, f"probe error: {e}"
    finally:
        # Always kill the temp sing-box
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        # Clean up temp config
        try:
            tmp_config.unlink(missing_ok=True)
        except OSError:
            pass


def probe_all_nodes(config: dict[str, Any] | None = None) -> list[tuple[str, bool, str]]:
    """Probe every node in the config. Returns list of (tag, success, reason)."""
    if config is None:
        config = _load_config()

    singbox_bin = str(SINGBOX_BIN)
    if not Path(singbox_bin).exists():
        _log(f"sing-box binary not found at {singbox_bin}")
        return []

    node_tags = _extract_node_tags(config)
    if not node_tags:
        _log("no nodes found in config")
        return []

    _log(f"probing {len(node_tags)} nodes against {DATA_SITE_IP}...")
    results: list[tuple[str, bool, str]] = []

    # Build work items: (node_outbound, port)
    work_items = []
    for i, tag in enumerate(node_tags):
        ob = _find_outbound_by_tag(config, tag)
        if ob and ob.get("type") not in _STRUCTURAL_TAGS:
            work_items.append((ob, PROBE_BASE_PORT + i))

    # Probe in parallel (max 6 concurrent to avoid port/resource exhaustion)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(_probe_single_node, ob, port, singbox_bin): ob["tag"]
            for ob, port in work_items
        }
        for future in as_completed(futures):
            tag, success, reason = future.result()
            status = "PASS" if success else "FAIL"
            _log(f"  {tag}: {status} — {reason}")
            results.append((tag, success, reason))

    return results


def rebuild_config_with_working_nodes(
    working_tags: list[str],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rewrite recording_data_pool to only include working nodes.

    recording_live_pool is left unchanged (video streaming uses different IPs).
    """
    if config is None:
        config = _load_config()
    config = deepcopy(config)

    if not working_tags:
        _log("WARNING: no working nodes found — keeping config unchanged")
        return config

    for ob in config["outbounds"]:
        if ob.get("tag") == "recording_data_pool":
            old_nodes = ob.get("outbounds", [])
            ob["outbounds"] = working_tags
            # Change urltest URL to actual data site for realistic latency
            ob["url"] = DATA_SITE_URL
            _log(
                f"recording_data_pool: {len(old_nodes)} nodes -> {len(working_tags)} nodes "
                f"({', '.join(working_tags)})"
            )
            break

    return config


def _read_singbox_pid() -> int:
    """Read the PID of the running main sing-box from state file."""
    try:
        state = json.loads(SINGBOX_STATE.read_text(encoding="utf-8"))
        return int(state.get("pid", 0))
    except Exception:
        return 0


def _is_pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _find_singbox_pid_by_port(port: int = MAIN_SINGBOX_PORT) -> int:
    """Find PID of the process LISTENING on the given port via lsof."""
    try:
        result = subprocess.run(
            ["lsof", "-nP", "-iTCP:" + str(port), "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().splitlines()[1:]:  # skip header
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[1])
    except Exception:
        pass
    return 0


def _kill_pid(pid: int) -> None:
    """SIGTERM then SIGKILL a PID."""
    if not _is_pid_alive(pid):
        return
    _log(f"stopping pid={pid}")
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + 5
    while time.time() < deadline and _is_pid_alive(pid):
        time.sleep(0.3)
    if _is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def restart_singbox() -> bool:
    """Kill existing sing-box and restart with current config.

    Returns True if the new instance started successfully.
    """
    singbox_bin = str(SINGBOX_BIN)
    if not Path(singbox_bin).exists():
        _log("sing-box binary not found, cannot restart")
        return False

    # Kill existing — check both state file and port
    old_pid = _read_singbox_pid()
    _kill_pid(old_pid)

    port_pid = _find_singbox_pid_by_port()
    if port_pid and port_pid != old_pid:
        _kill_pid(port_pid)

    # Wait for port to be fully released
    deadline = time.time() + 8
    while time.time() < deadline:
        if not _find_singbox_pid_by_port():
            break
        time.sleep(0.5)
    else:
        _log(f"port {MAIN_SINGBOX_PORT} still occupied after wait")

    # Validate config
    check = subprocess.run(
        [singbox_bin, "check", "-c", str(SINGBOX_CONFIG)],
        capture_output=True, text=True, timeout=10,
    )
    if check.returncode != 0:
        _log(f"config validation failed: {check.stderr.strip()}")
        return False

    # Start new instance
    SINGBOX_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SINGBOX_LOG.open("a", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            [singbox_bin, "run", "-c", str(SINGBOX_CONFIG)],
            cwd=str(SCRIPT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(1.5)

    if proc.poll() is not None:
        _log("sing-box exited immediately after restart")
        return False

    # Update state file
    from datetime import datetime
    state = {
        "pid": proc.pid,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "config_path": str(SINGBOX_CONFIG),
    }
    try:
        SINGBOX_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass

    _log(f"sing-box restarted: pid={proc.pid}")
    return True


def ensure_data_site_nodes(force: bool = False) -> list[str]:
    """Main entry point: probe nodes, rebuild config, restart sing-box.

    Returns the list of working node tags. Uses a cache to avoid re-probing
    within PROBE_CACHE_TTL_SECONDS unless force=True.
    """
    global _last_working_tags, _last_probe_time

    if not force and _last_working_tags and (time.time() - _last_probe_time) < PROBE_CACHE_TTL_SECONDS:
        _log(f"using cached probe result: {_last_working_tags}")
        return _last_working_tags

    config = _load_config()

    # Backup original config before first modification
    if not SINGBOX_CONFIG_BACKUP.exists():
        SINGBOX_CONFIG_BACKUP.write_text(
            SINGBOX_CONFIG.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    results = probe_all_nodes(config)
    working = [tag for tag, success, _ in results if success]

    if not working:
        _log("CRITICAL: no nodes can reach data site — config unchanged")
        _last_working_tags = []
        _last_probe_time = time.time()
        return []

    _log(f"working nodes: {working}")

    new_config = rebuild_config_with_working_nodes(working, config)
    _save_config(new_config)

    ok = restart_singbox()
    if ok:
        _log("sing-box restarted with filtered config")
    else:
        _log("WARNING: sing-box restart failed")

    _last_working_tags = working
    _last_probe_time = time.time()
    return working


def handle_data_site_failure() -> list[str]:
    """Called on 404 / connection failure to the data site.

    Forces a fresh probe and config rebuild.
    """
    _log("data site failure detected — forcing re-probe")
    return ensure_data_site_nodes(force=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Probe sing-box nodes for data site access")
    parser.add_argument("--probe-only", action="store_true", help="Probe without modifying config")
    args = parser.parse_args()

    if args.probe_only:
        results = probe_all_nodes()
        working = [tag for tag, success, _ in results if success]
        print(json.dumps({
            "working": working,
            "details": [{"tag": t, "ok": s, "reason": r} for t, s, r in results],
        }, indent=2))
    else:
        working = ensure_data_site_nodes(force=True)
        print(json.dumps({"working": working}, indent=2))
