"""Throttled terminal or newline-delimited JSON transfer progress on stderr."""
import json
import sys
import time


def human_bytes(value):
    value = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


class Progress:
    def __init__(self, enabled=True, structured=False, stream=None):
        self.enabled, self.structured = enabled, structured
        self.stream = stream or sys.stderr
        self.name = None
        self.last = self.started = 0
        self.active_line = False
        self.start_count = 0

    def __call__(self, name, count, expected):
        if not self.enabled:
            return
        now = time.monotonic()
        changed = name != self.name or count == 0
        if changed:
            self.finish()
            self.name, self.started = name, now
            self.start_count = count
        complete = expected is not None and count == expected
        if not changed and not complete and now - self.last < 0.2:
            return
        self.last = now
        speed = max(0, count - self.start_count) / max(now - self.started, 0.001)
        if self.structured:
            print(json.dumps({"event": "progress", "name": name, "bytes": count,
                              "total": expected, "bytes_per_second": speed}), file=self.stream, flush=True)
            return
        title = json.dumps(str(name), ensure_ascii=True)
        if len(title) > 80:
            title = title[:77] + '..."'
        detail = human_bytes(count)
        if expected is not None:
            percent = 100 if expected == 0 else min(100, count * 100 / expected)
            detail += f" / {human_bytes(expected)} ({percent:.0f}%)"
        line = f"{title}  {detail}  {human_bytes(speed)}/s"
        if self.stream.isatty():
            print("\r\033[2K" + line, end="", file=self.stream, flush=True)
            self.active_line = True
        else:
            print(line, file=self.stream, flush=True)

    def finish(self):
        if self.active_line:
            print(file=self.stream, flush=True)
            self.active_line = False
