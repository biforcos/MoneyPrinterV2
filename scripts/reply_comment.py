"""Replies to a comment thread in the Studio inbox, as the channel.

Meant for the human-approved reply workflow: you decide the text, the
bot types it. The submit button is the LAST "Responder" in the thread
(the first one just opens the box). Success is verified by the thread
leaving the unresponded inbox.

Usage, from the project root:
    python scripts/reply_comment.py --author "@usuario" --text "respuesta"
"""

import argparse
import os
import sys
import time

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--author", required=True, help="autor del comentario, ej: @usuario")
    parser.add_argument("--text", required=True, help="texto de la respuesta")
    args = parser.parse_args()

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
    try:
        browser.get("https://studio.youtube.com")
        time.sleep(8)
        channel_id = browser.current_url.split("/channel/")[-1].split("/")[0]
        inbox = f"https://studio.youtube.com/channel/{channel_id}/comments/inbox"
        browser.get(inbox)
        time.sleep(10)

        threads = browser.find_elements(By.TAG_NAME, "ytcp-comment-thread")
        target = next((t for t in threads if args.author in t.text), None)
        if not target:
            raise SystemExit(f"No encontré ningún hilo pendiente de {args.author}")

        open_btn = next(
            el
            for el in target.find_elements(
                By.XPATH, ".//*[normalize-space(text())='Responder']"
            )
            if el.is_displayed()
        )
        browser.execute_script(
            "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();",
            open_btn,
        )
        time.sleep(2)

        box = target.find_element(By.CSS_SELECTOR, "textarea#textarea")
        box.send_keys(args.text)
        time.sleep(1)

        # The submit is the LAST visible "Responder" button in the thread
        candidates = [
            el
            for el in target.find_elements(By.TAG_NAME, "ytcp-button")
            if el.is_displayed() and "Responder" in (el.text or "")
        ]
        browser.execute_script("arguments[0].click();", candidates[-1])
        time.sleep(5)

        # Verify: a replied thread leaves the pending inbox. Studio can
        # take a while to reflect it, so poll before declaring failure.
        still_there = True
        for _ in range(4):
            browser.get(inbox)
            time.sleep(10)
            still_there = any(
                args.author in t.text
                for t in browser.find_elements(By.TAG_NAME, "ytcp-comment-thread")
            )
            if not still_there:
                break
        if still_there:
            raise SystemExit(
                "El hilo sigue en pendientes tras 4 comprobaciones: "
                "la respuesta NO se envió"
            )
        print(f"RESPONDIDO a {args.author}: {args.text}")
    finally:
        browser.quit()


if __name__ == "__main__":
    main()
