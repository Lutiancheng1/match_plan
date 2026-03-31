#!/usr/bin/env python3
"""Local reverse proxy for the data site (112.121.42.168).

Forwards HTTP requests from localhost:18780 to https://112.121.42.168,
skipping SSL certificate verification. This allows WKWebView to access
the data site without SSL issues.

Also handles auto-login: on first request to /, redirects to the
logged-in main page with all session parameters.
"""
from __future__ import annotations

import http.server
import json
import os
import ssl
import sys
import threading
import urllib.parse
import urllib.request
from http.client import IncompleteRead
from pathlib import Path

PROXY_PORT = 18780
TARGET_HOST = "112.121.42.168"
TARGET_BASE = f"https://{TARGET_HOST}"
SSL_CTX = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"

# Use sing-box proxy (mixed HTTP/SOCKS on 17897) via env vars
# to reach the upstream data site which is blocked in mainland China.
os.environ["http_proxy"] = "http://127.0.0.1:17897"
os.environ["https_proxy"] = "http://127.0.0.1:17897"

SCRIPT_DIR = Path(__file__).resolve().parent
RECORDINGS_DIR = SCRIPT_DIR.parents[1]
LIVE_DASHBOARD_DIR = RECORDINGS_DIR.parent / "live_dashboard"
for p in (str(RECORDINGS_DIR), str(LIVE_DASHBOARD_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

_login_creds: dict | None = None
_login_lock = threading.Lock()
_login_redirected = False  # Only auto-login redirect once to avoid loops

ENV_PATHS = [
    RECORDINGS_DIR / "live_dashboard.env",
    LIVE_DASHBOARD_DIR / "live_dashboard.env",
]


def load_env_credentials() -> tuple[str, str]:
    username = os.environ.get("LOGIN_USERNAME", "")
    password = os.environ.get("LOGIN_PASSWORD", "")
    if username and password:
        return username, password
    for path in ENV_PATHS:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("LOGIN_USERNAME=") and line[15:]:
                username = line[15:]
            elif line.startswith("LOGIN_PASSWORD=") and line[15:]:
                password = line[15:]
        if username and password:
            break
    return username, password


def get_login_creds() -> dict | None:
    global _login_creds
    with _login_lock:
        if _login_creds:
            return _login_creds
    username, password = load_env_credentials()
    if not username or not password:
        return None
    try:
        from auto_login import auto_login
        creds = auto_login(username, password)
        with _login_lock:
            _login_creds = creds
            _login_creds["alias"] = username
        return _login_creds
    except Exception as e:
        print(f"[proxy] auto_login failed: {e}", file=sys.stderr)
        return None


def build_login_redirect_path(creds: dict) -> str:
    params = urllib.parse.urlencode({
        "cu": "Y", "cuipv6": "N", "ipv6": "N",
        "alias": creds.get("alias", ""),
        "status": "200", "msg": "", "code_message": "",
        "username": "cegm8808",
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
    return f"/?{params}"


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[req] {self.requestline}", file=sys.stderr)

    _js_errors: list = []

    def do_GET(self):
        if self.path == "/jstest":
            body = b'<html><head><script>fetch("/_report_error",{method:"POST",body:"jstest_ok"});</script></head><body><h1>JS Test</h1></body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/ping":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if self.path == "/_errors":
            body = json.dumps(ProxyHandler._js_errors[-50:]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._proxy("GET")

    def do_POST(self):
        if self.path == "/_report_error":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
            ProxyHandler._js_errors.append(body)
            print(f"[JS_ERROR] {body}", file=sys.stderr)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self._proxy("POST")

    def _proxy(self, method: str):
        global _login_redirected
        path = self.path

        # Auto-login redirect on bare root — only once to avoid redirect loops
        if (path == "/" or path == "") and not _login_redirected:
            creds = get_login_creds()
            if creds:
                _login_redirected = True
                path = build_login_redirect_path(creds)

        target_url = f"{TARGET_BASE}{path}"

        # Read request body for POST
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                body = self.rfile.read(length)

        # Build upstream request
        req = urllib.request.Request(target_url, data=body, method=method)
        req.add_header("User-Agent", UA)

        # Forward relevant headers
        for header in ("Content-Type", "Accept", "Accept-Language", "Referer"):
            val = self.headers.get(header)
            if val:
                # Rewrite localhost references to target
                val = val.replace(f"http://127.0.0.1:{PROXY_PORT}", TARGET_BASE)
                val = val.replace(f"http://localhost:{PROXY_PORT}", TARGET_BASE)
                req.add_header(header, val)

        # Add login cookie
        creds = get_login_creds()
        if creds:
            req.add_header("Cookie", creds.get("cookie", ""))

        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
                resp_body = resp.read()
                content_type = resp.getheader("Content-Type") or "text/html"

                # Rewrite URLs in HTML/JS responses
                if "text/" in content_type or "javascript" in content_type:
                    text = resp_body.decode("utf-8", errors="replace")
                    text = text.replace(f"https://{TARGET_HOST}", f"http://127.0.0.1:{PROXY_PORT}")
                    text = text.replace(f"//{TARGET_HOST}", f"//127.0.0.1:{PROXY_PORT}")
                    # Fix: getWebDomain() uses dom.domain which omits port
                    text = text.replace(
                        "getWebDomain=function(){return dom.domain}",
                        "getWebDomain=function(){return dom.location.host}",
                    )
                    resp_body = text.encode("utf-8")

                self.send_response(resp.status)
                for key, val in resp.getheaders():
                    lower = key.lower()
                    if lower in ("transfer-encoding", "connection", "content-encoding", "content-length"):
                        continue
                    if lower == "set-cookie":
                        # Rewrite cookie domain to localhost
                        val = val.replace(f"domain={TARGET_HOST}", f"domain=127.0.0.1")
                        val = val.replace(f"Domain={TARGET_HOST}", f"Domain=127.0.0.1")
                        # Remove Secure flag (we're HTTP)
                        val = val.replace("; Secure", "").replace("; secure", "")
                    if lower == "location":
                        val = val.replace(f"https://{TARGET_HOST}", f"http://127.0.0.1:{PROXY_PORT}")
                    self.send_header(key, val)
                # Also inject login cookies for localhost
                creds_cookies = get_login_creds()
                if creds_cookies and creds_cookies.get("cookie"):
                    for part in creds_cookies["cookie"].split("; "):
                        if "=" in part:
                            self.send_header("Set-Cookie", f"{part}; Path=/; SameSite=Lax")
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except IncompleteRead as exc:
            resp_body = exc.partial
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        except Exception as exc:
            error_msg = f"Proxy error: {exc}".encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(error_msg)))
            self.end_headers()
            self.wfile.write(error_msg)


def main():
    server = http.server.HTTPServer(("127.0.0.1", PROXY_PORT), ProxyHandler)
    print(f"[proxy] listening on http://127.0.0.1:{PROXY_PORT}", file=sys.stderr)
    print(json.dumps({"ok": True, "port": PROXY_PORT, "url": f"http://127.0.0.1:{PROXY_PORT}"}))
    sys.stdout.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
