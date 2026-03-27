#!/usr/bin/env python3
"""Auto-login module: obtain fresh cookie and body template from credentials."""

from __future__ import annotations

import base64
import http.cookiejar
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.client import IncompleteRead
from typing import Any

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
DEFAULT_ENTRY = "https://hga035.com"
SSL_CTX = ssl._create_unverified_context()
FALLBACK_VER = "2026-03-19-fireicon_142"


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def _post_detection(
    entry_url: str,
    opener: urllib.request.OpenerDirector,
) -> tuple[str, str, str]:
    """POST detection=Y and return the resolved base URL plus page metadata."""
    body = urllib.parse.urlencode({
        "detection": "Y",
        "sub_doubleLogin": "",
        "isapp": "",
        "q": "",
        "appversion": "",
    })
    req = urllib.request.Request(entry_url, data=body.encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", UA)
    html = ""
    final_url = entry_url
    last_err: Exception | None = None
    for _ in range(3):
        try:
            raw = opener.open(req, timeout=30)
            final_url = raw.geturl() or entry_url
            html = raw.read().decode("utf-8", errors="replace")
            last_err = None
            break
        except IncompleteRead as exc:
            html = exc.partial.decode("utf-8", errors="replace")
            break
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            last_err = exc
            time.sleep(2)
    if last_err and not html:
        raise last_err

    html_lower = html.lower()
    if (
        "access to this site is blocked from your current location" in html_lower
        or "<title>forbidden</title>" in html_lower
    ):
        raise RuntimeError(
            f"entry page blocked from current location: {final_url}"
        )

    m = re.search(r"top\.ver\s*=\s*'([^']+)'", html)
    ver = m.group(1) if m else ""
    if not ver:
        ver = FALLBACK_VER
        print(f"[auto_login] ver not found in HTML, using fallback: {ver}", file=sys.stderr)

    m2 = re.search(r"top\.iovationKey\s*=\s*'([^']+)'", html)
    iovation_key = m2.group(1) if m2 else "BZBFCB"

    parsed = urllib.parse.urlsplit(final_url)
    resolved_entry_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return resolved_entry_url, ver, iovation_key


def _chk_login(
    entry_url: str,
    ver: str,
    iovation_key: str,
    username: str,
    password: str,
    opener: urllib.request.OpenerDirector,
) -> dict[str, str]:
    """POST chk_login to authenticate. Returns parsed XML fields."""
    url = f"{entry_url}/transform_nl.php?ver={ver}"
    body = urllib.parse.urlencode({
        "p": "chk_login",
        "langx": "zh-cn",
        "ver": ver,
        "username": username,
        "password": password,
        "app": "N",
        "auto": iovation_key,
        "blackbox": "",
        "userAgent": _b64(UA),
    })
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "*/*")
    req.add_header("User-Agent", UA)
    req.add_header("Origin", entry_url)
    raw = ""
    for _ in range(3):
        try:
            with opener.open(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            break
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            print(f"[auto_login] chk_login retry: {exc}", file=sys.stderr)
            time.sleep(2)
            if _ == 2:
                raise
    if not raw:
        raise RuntimeError("chk_login: no response after retries")

    root = ET.fromstring(raw)
    result: dict[str, str] = {}
    for tag in ("status", "msg", "username", "mid", "uid", "passwd_safe",
                "ltype", "currency", "odd_f", "pay_type", "domain", "blackBoxStatus"):
        el = root.find(tag)
        if el is not None and el.text:
            result[tag] = el.text.strip()
    return result


def _memset_check(
    entry_url: str,
    ver: str,
    uid: str,
    opener: urllib.request.OpenerDirector,
) -> Any:
    """POST memSet action=check to handle the passcode dialog."""
    url = f"{entry_url}/transform_nl.php?ver={ver}"
    body = urllib.parse.urlencode({
        "p": "memSet",
        "ver": ver,
        "uid": uid,
        "langx": "zh-cn",
        "action": "check",
    })
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "*/*")
    req.add_header("User-Agent", UA)
    req.add_header("Origin", entry_url)
    for _ in range(3):
        try:
            with opener.open(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError, OSError):
            time.sleep(2)
            if _ == 2:
                raise


def _extract_cookie_string(jar: http.cookiejar.CookieJar, mid: str) -> str:
    """Build a semicolon-separated cookie string for data fetching.

    Some cookies are set by JavaScript in the browser (protocolstr, CookieChk,
    iorChgSw, myGameVer) and are not returned via Set-Cookie headers.
    These are fixed values that we supply directly.
    """
    parts: list[str] = []

    # Fixed cookies always required by transform.php
    parts.append("protocolstr=aHR0cHM=")          # base64("https")
    parts.append("CookieChk=WQ")                  # fixed marker
    parts.append("iorChgSw=WQ==")                 # base64("Y")

    # Build a name->value dict from the jar
    jar_dict: dict[str, str] = {}
    for cookie in jar:
        jar_dict[cookie.name] = cookie.value

    # Login timestamp cookie from jar (e.g., login_40835457=timestamp)
    login_ts = jar_dict.get(f"login_{mid}", "")
    if not login_ts:
        for name, value in jar_dict.items():
            if name.startswith("login_"):
                login_ts = value
                break
    if login_ts:
        parts.append(f"login_{mid}={_b64(login_ts)}")

    # myGameVer cookie: base64("_211228")
    parts.append(f"myGameVer_{mid}=XzIxMTIyOA==")

    # Cookies from jar (cu, cuipv6, ipv6) — base64 encode their values
    for name in ("cu", "cuipv6", "ipv6"):
        value = jar_dict.get(name)
        if value:
            parts.append(f"{name}={_b64(value)}")

    return "; ".join(parts)


def _build_body_template(uid: str, ver: str) -> str:
    """Build the GET_GAME_LIST_BODY template string."""
    return urllib.parse.urlencode({
        "uid": uid,
        "ver": ver,
        "langx": "zh-cn",
        "p": "get_game_list",
        "p3type": "",
        "date": "",
        "gtype": "ft",
        "showtype": "live",
        "rtype": "rb",
        "ltype": "3",
        "filter": "",
        "cupFantasy": "N",
        "sorttype": "L",
        "specialClick": "",
        "isFantasy": "N",
        "ts": "0",
        "chgSortTS": "0",
    })


def auto_login(
    username: str,
    password: str,
    entry_url: str = DEFAULT_ENTRY,
) -> dict[str, str]:
    """Perform the full login flow and return credentials for data fetching.

    Returns dict with keys:
        cookie        - cookie string for GET_GAME_LIST_COOKIE
        body_template - body string for GET_GAME_LIST_BODY
        uid           - session uid
        mid           - member id
        ver           - frontend version string
    """
    jar = http.cookiejar.CookieJar()
    # Create a single opener with cookie jar — reuse across all requests
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=SSL_CTX),
        urllib.request.HTTPCookieProcessor(jar),
    )

    # Step 1: POST detection to get the real login page (with ver & iovationKey)
    resolved_entry_url, ver, iovation_key = _post_detection(entry_url, opener)
    if resolved_entry_url != entry_url:
        print(
            f"[auto_login] resolved entry url: {entry_url} -> {resolved_entry_url}",
            file=sys.stderr,
        )
    print(f"[auto_login] ver={ver}, iovationKey={iovation_key}", file=sys.stderr)

    # Step 2: chk_login
    result = _chk_login(resolved_entry_url, ver, iovation_key, username, password, opener)
    if result.get("status") != "200":
        raise RuntimeError(
            f"chk_login failed: status={result.get('status')}, msg={result.get('msg')}"
        )
    uid = result["uid"]
    mid = result["mid"]
    print(f"[auto_login] logged in: username={result['username']}, mid={mid}, uid={uid}", file=sys.stderr)

    # Step 3: memSet check (handle passcode dialog) — non-critical
    try:
        memset_resp = _memset_check(resolved_entry_url, ver, uid, opener)
        print(f"[auto_login] memSet: {memset_resp}", file=sys.stderr)
    except Exception as exc:
        print(f"[auto_login] memSet skipped: {exc}", file=sys.stderr)

    # Step 4: build credentials
    cookie_str = _extract_cookie_string(jar, mid)
    body_template = _build_body_template(uid, ver)

    return {
        "cookie": cookie_str,
        "body_template": body_template,
        "uid": uid,
        "mid": mid,
        "ver": ver,
    }


if __name__ == "__main__":
    u = os.environ.get("LOGIN_USERNAME", "")
    p = os.environ.get("LOGIN_PASSWORD", "")
    entry = os.environ.get("ENTRY_URL", DEFAULT_ENTRY)
    if not u or not p:
        print("Usage: LOGIN_USERNAME=xx LOGIN_PASSWORD=xx python3 auto_login.py", file=sys.stderr)
        sys.exit(1)
    creds = auto_login(u, p, entry)
    print(json.dumps(creds, indent=2, ensure_ascii=False))
