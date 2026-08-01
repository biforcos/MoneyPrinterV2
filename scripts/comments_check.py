"""Collects viewer comments pending a reply, for the morning briefing.

Scrapes the Studio comments inbox, filters out the channel's own pinned
questions (known from .mp/pinned_comments.json), and writes
logs/comments_pending.json plus a copy next to the briefing export so
the assistant can read it.

Usage, from the project root (bot Firefox window closed):
    python scripts/comments_check.py
"""

import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from webdriver_manager.firefox import GeckoDriverManager

from cache import get_accounts
from config import get_summary_export_path

OUT_PATH = os.path.join(ROOT, "logs", "comments_pending.json")


def own_questions():
    try:
        with open(
            os.path.join(ROOT, ".mp", "pinned_comments.json"), encoding="utf-8"
        ) as fh:
            return set(json.load(fh).values())
    except Exception:
        return set()


def main():
    account = get_accounts("youtube")[0]
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--width=1500")
    opts.add_argument("--height=1200")
    opts.add_argument("-profile")
    opts.add_argument(account["firefox_profile"])
    browser = webdriver.Firefox(
        service=Service(GeckoDriverManager().install()), options=opts
    )
    pending = []
    try:
        browser.get("https://studio.youtube.com")
        time.sleep(8)
        channel_id = browser.current_url.split("/channel/")[-1].split("/")[0]
        browser.get(
            f"https://studio.youtube.com/channel/{channel_id}/comments/inbox"
        )
        time.sleep(10)

        threads = browser.find_elements(By.TAG_NAME, "ytcp-comment-thread")
        pinned = own_questions()
        for thread in threads[:30]:
            lines = [l.strip() for l in thread.text.split("\n") if l.strip()]
            if not lines:
                continue
            text = " | ".join(lines[:4])[:220]
            # Skip our own pinned questions (they appear in the inbox too)
            if any(q[:40] in text for q in pinned if q):
                continue
            pending.append(text)
    finally:
        browser.quit()

    payload = {
        "checked": datetime.now().isoformat(timespec="minutes"),
        "pending_count": len(pending),
        "comments": pending,
    }
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    export = get_summary_export_path().strip()
    if export:
        try:
            import shutil

            shutil.copy2(
                OUT_PATH,
                os.path.join(os.path.dirname(export), "comments_pending.json"),
            )
        except Exception as e:
            print(f"[comments] No se pudo exportar: {e}")

    print(f"[comments] {len(pending)} comentarios de espectadores pendientes")
    for text in pending[:10]:
        print("  -", text[:120])


if __name__ == "__main__":
    main()
