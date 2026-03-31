#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from auto_login import auto_login
from poll_get_game_list import (
    DEFAULT_URL,
    build_game_list_body,
    fetch_xml,
    parse_form_body,
)
from recording_proxy_runtime import (
    PROXY_POLICY_SAFARI,
    apply_proxy_env,
    check_chrome_automation,
    read_recent_events,
    status_payload,
)


SCRIPT_DIR = Path(__file__).resolve().parent
LIVE_ENV = SCRIPT_DIR / "live_dashboard.env"
LIVE_ENV_FALLBACK = SCRIPT_DIR.parent / "live_dashboard" / "live_dashboard.env"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose recording login/feed and proxy routing.")
    parser.add_argument("--policy", default=PROXY_POLICY_SAFARI)
    parser.add_argument("--gtype", default="FT")
    args = parser.parse_args()

    load_env_file(LIVE_ENV)
    load_env_file(LIVE_ENV_FALLBACK)
    proxy_state = apply_proxy_env(args.policy)
    chrome_state = check_chrome_automation()

    username = os.environ.get("LOGIN_USERNAME", "").strip()
    password = os.environ.get("LOGIN_PASSWORD", "").strip()
    entry_url = os.environ.get("ENTRY_URL", "https://hga035.com").strip()
    if not username or not password:
        print(json.dumps({"error": "missing LOGIN_USERNAME/LOGIN_PASSWORD", "proxy": proxy_state}, ensure_ascii=False, indent=2))
        return 1

    started_at = time.time()
    creds = auto_login(username, password, entry_url)
    template = parse_form_body(creds["body_template"])
    body = build_game_list_body(template, gtype=args.gtype.upper(), showtype="live", rtype="rb")
    raw = fetch_xml(DEFAULT_URL, body, creds["cookie"], timeout=10)
    payload = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "proxy": proxy_state,
        "chrome": chrome_state,
        "login": {
            "entry_url": entry_url,
            "uid": creds.get("uid", ""),
            "mid": creds.get("mid", ""),
            "ver": creds.get("ver", ""),
        },
        "feed": {
            "url": DEFAULT_URL,
            "gtype": args.gtype.upper(),
            "response_chars": len(raw),
        },
        "runtime": status_payload(),
        "recent_events": read_recent_events(20),
        "elapsed_seconds": round(time.time() - started_at, 3),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
