"""Lightweight session logger for pion_gst_dispatcher."""
from __future__ import annotations

from datetime import datetime


class SessionLogger:
    def __init__(self, log_path: str):
        self.log_path = log_path
        self._file = open(log_path, "a", encoding="utf-8")

    def log(self, msg: str, tag: str = ""):
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
