#!/usr/bin/env python3
"""Auto-login to data site and fetch the main page HTML.

Outputs JSON: {"html": "...", "base_url": "...", "cookie": "...", "uid": "..."}
"""
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = SCRIPT_DIR.parents[1]
LIVE_DASHBOARD_DIR = RECORDINGS_DIR.parent / "live_dashboard"

if str(RECORDINGS_DIR) not in sys.path:
    sys.path.insert(0, str(RECORDINGS_DIR))
if str(LIVE_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(LIVE_DASHBOARD_DIR))

from auto_login import auto_login

ENV_PATHS = [
    RECORDINGS_DIR / "live_dashboard.env",
    LIVE_DASHBOARD_DIR / "live_dashboard.env",
]

SSL_CTX = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"


def load_env():
    username = os.environ.get("LOGIN_USERNAME", "")
    password = os.environ.get("LOGIN_PASSWORD", "")
    if username and password:
        return username, password
    for path in ENV_PATHS:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("LOGIN_USERNAME="):
                val = line[len("LOGIN_USERNAME="):]
                if val:
                    username = val
            elif line.startswith("LOGIN_PASSWORD="):
                val = line[len("LOGIN_PASSWORD="):]
                if val:
                    password = val
        if username and password:
            return username, password
    return username, password


def fetch_main_page(creds: dict) -> str:
    """Fetch the post-login main page HTML."""
    params = urllib.parse.urlencode({
        "cu": "Y", "cuipv6": "N", "ipv6": "N",
        "alias": creds.get("alias", ""),
        "status": "200", "msg": "", "code_message": "",
        "username": creds.get("username", ""),
        "mid": creds["mid"],
        "uid": creds["uid"],
        "ltype": "3", "currency": "RMB",
        "odd_f": "H,M,I,E",
        "domain": "199.26.100.165",
        "passwd_safe": creds.get("alias", ""),
        "blackBoxStatus": "N",
        "odd_f_type": "H",
        "timetype": "sysTime",
        "langx": "zh-cn",
        "iovationCnt": "1",
    })
    url = f"https://112.121.42.168/?{params}"
    req = urllib.request.Request(url)
    req.add_header("Cookie", creds["cookie"])
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main():
    username, password = load_env()
    if not username or not password:
        json.dump({"error": "no credentials in env"}, sys.stdout, ensure_ascii=False)
        return 1

    try:
        creds = auto_login(username, password)
    except Exception as e:
        json.dump({"error": f"auto_login failed: {e}"}, sys.stdout, ensure_ascii=False)
        return 1

    try:
        html = fetch_main_page({
            **creds,
            "alias": username,
            "username": "cegm8808",  # from login response
        })
    except Exception as e:
        json.dump({"error": f"fetch_main_page failed: {e}"}, sys.stdout, ensure_ascii=False)
        return 1

    json.dump({
        "html": html,
        "base_url": "https://112.121.42.168",
        "cookie": creds["cookie"],
        "uid": creds["uid"],
        "mid": creds["mid"],
    }, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
