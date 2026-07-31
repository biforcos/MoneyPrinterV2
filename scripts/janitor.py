"""Housekeeping: keeps disk usage bounded.

- videos/ older than 14 days -> deleted (they are already on YouTube)
- logs/upload_timeout_*.png older than 7 days -> deleted
- big text logs (> 5 MB) -> truncated to their last 2000 lines

Wired into the nightly batch; safe to run any time.
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

VIDEO_MAX_AGE_DAYS = 14
SCREENSHOT_MAX_AGE_DAYS = 7
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_KEEP_LINES = 2000


def prune_old(folder, pattern_suffix, max_age_days):
    removed = 0
    if not os.path.isdir(folder):
        return removed
    cutoff = time.time() - max_age_days * 86400
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if (
            os.path.isfile(path)
            and name.endswith(pattern_suffix)
            and os.path.getmtime(path) < cutoff
        ):
            os.remove(path)
            removed += 1
    return removed


def truncate_log(path):
    if not os.path.isfile(path) or os.path.getsize(path) <= LOG_MAX_BYTES:
        return False
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines[-LOG_KEEP_LINES:])
    return True


def main():
    videos = prune_old(os.path.join(ROOT, "videos"), ".mp4", VIDEO_MAX_AGE_DAYS)
    shots = prune_old(
        os.path.join(ROOT, "logs"), ".png", SCREENSHOT_MAX_AGE_DAYS
    )
    truncated = [
        name
        for name in ("nightly.log", "news_cycle.log", "reupload.log")
        if truncate_log(os.path.join(ROOT, "logs", name))
    ]
    print(
        f"[janitor] {videos} vídeos antiguos, {shots} capturas viejas, "
        f"logs truncados: {truncated or 'ninguno'}"
    )


if __name__ == "__main__":
    main()
