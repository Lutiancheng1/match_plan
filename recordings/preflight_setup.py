#!/usr/bin/env python3
"""Runtime preflight for recording workflow."""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import urllib.request


ROOT = os.path.dirname(os.path.abspath(__file__))
CHROME_APP = "/Applications/Google Chrome.app"
CDP_BASE_URLS = [
    os.environ.get("CHROME_CDP_URL", "").strip(),
    "http://127.0.0.1:9222",
    "http://127.0.0.1:9223",
    "http://127.0.0.1:9333",
]


def log(message):
    print(f"[preflight] {message}", flush=True)


def has_module(name):
    return importlib.util.find_spec(name) is not None


def find_pinchtab():
    cli = shutil.which("pinchtab")
    if cli:
        return cli
    for path in (
        "/opt/homebrew/bin/pinchtab",
        "/usr/local/bin/pinchtab",
        os.path.expanduser("~/.local/bin/pinchtab"),
    ):
        if os.path.exists(path):
            return path
    nvm_root = os.path.expanduser("~/.nvm/versions/node")
    if os.path.isdir(nvm_root):
        for version in sorted(os.listdir(nvm_root)):
            path = os.path.join(nvm_root, version, "bin", "pinchtab")
            if os.path.exists(path):
                return path
    return None


def cdp_available():
    for base_url in [u for u in CDP_BASE_URLS if u]:
        try:
            with urllib.request.urlopen(f"{base_url}/json/version", timeout=2) as resp:
                if resp.status == 200:
                    return base_url
        except Exception:
            continue
    return None


def run(cmd):
    log("运行: " + " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=True)


def maybe_install_requirements(auto_install):
    missing = [name for name in ("psutil", "Quartz", "websocket") if not has_module(name)]
    if not missing:
        log("Python 依赖检查通过")
        return
    log(f"缺少 Python 模块: {', '.join(missing)}")
    if not auto_install:
        raise SystemExit("请先安装 requirements.txt 依赖，或使用 --auto-install")
    run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])


def maybe_install_ffmpeg(auto_install):
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        log(f"ffmpeg 已安装: {ffmpeg_bin}")
        return
    log("未找到 ffmpeg")
    if not auto_install:
        raise SystemExit("请先安装 ffmpeg，或使用 --auto-install")
    brew = shutil.which("brew")
    if not brew:
        raise SystemExit("未检测到 Homebrew，无法自动安装 ffmpeg")
    run([brew, "install", "ffmpeg"])


def main():
    parser = argparse.ArgumentParser(description="Preflight setup for recording workflow")
    parser.add_argument("--auto-install", action="store_true")
    args = parser.parse_args()

    log("开始环境检查")
    maybe_install_requirements(args.auto_install)
    maybe_install_ffmpeg(args.auto_install)

    if os.path.exists(CHROME_APP):
        log("Google Chrome 已安装")
    else:
        raise SystemExit("未找到 Google Chrome.app")

    pinchtab = find_pinchtab()
    if pinchtab:
        log(f"pinchtab 可用: {pinchtab}")
    else:
        log("pinchtab 未安装，录制链将仅使用 AppleScript 作为打开页面方式")

    cdp_url = cdp_available()
    if cdp_url:
        log(f"Chrome CDP 可用: {cdp_url}")
    else:
        log("Chrome CDP 不可用，浏览器会话数据源会回退到 dashboard/env")
        log("如需复用浏览器登录态抓投注数据，请先为 Chrome 开启远程调试并设置 CHROME_CDP_URL")

    log("环境检查完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
