"""Pins an audience-question comment on recently published videos.

Scans the channel's public Shorts, and for each one without a pinned
comment yet (tracked in .mp/pinned_comments.json): generates a short
provocative question with the LLM, posts it as a comment and pins it.
Scheduled videos get theirs on the first run after they go public.

Usage, from the project root (bot Firefox window closed):
    python scripts/pin_comments.py
"""

import json
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
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from webdriver_manager.firefox import GeckoDriverManager

from cache import get_accounts
from config import get_ollama_model
from llm_provider import select_model, generate_text

STATE_PATH = os.path.join(ROOT, ".mp", "pinned_comments.json")
MAX_PER_RUN = 4


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def js_click(browser, element):
    browser.execute_script(
        "arguments[0].scrollIntoView({block: 'center'}); arguments[0].click();",
        element,
    )


def click_by_text(browser, text):
    for el in browser.find_elements(
        By.XPATH, f"//*[normalize-space(text())='{text}']"
    ):
        try:
            if el.is_displayed():
                js_click(browser, el)
                return True
        except Exception:
            continue
    return False


def list_public_shorts(browser, channel_id):
    browser.get(f"https://studio.youtube.com/channel/{channel_id}/videos/short")
    rows = []
    for _ in range(8):
        time.sleep(5)
        rows = browser.find_elements(By.TAG_NAME, "ytcp-video-row")
        if rows:
            break
    videos = []
    for row in rows:
        if "Público" not in row.text:
            continue
        try:
            href = row.find_element(By.TAG_NAME, "a").get_attribute("href")
            video_id = href.split("/video/")[-1].split("/")[0]
            title = [l for l in row.text.split("\n") if l.strip()][1]
            videos.append({"id": video_id, "title": title})
        except Exception:
            continue
    return videos


def pin_comment(browser, video_id, question):
    browser.get(f"https://www.youtube.com/watch?v={video_id}")
    time.sleep(6)

    # Scroll to load the comments section
    for _ in range(6):
        browser.execute_script("window.scrollBy(0, 500);")
        time.sleep(1)
        if browser.find_elements(By.CSS_SELECTOR, "ytd-comments #placeholder-area"):
            break

    placeholder = browser.find_element(
        By.CSS_SELECTOR, "ytd-comments #placeholder-area"
    )
    js_click(browser, placeholder)
    time.sleep(2)

    box = browser.find_element(
        By.CSS_SELECTOR, "ytd-comments #contenteditable-root"
    )
    box.send_keys(question)
    time.sleep(1)

    submit = browser.find_element(By.CSS_SELECTOR, "ytd-comments #submit-button")
    js_click(browser, submit)
    time.sleep(4)

    # Our fresh comment appears first; open its action menu and pin it
    first_comment = browser.find_element(
        By.CSS_SELECTOR, "ytd-comment-thread-renderer"
    )
    menu_button = first_comment.find_element(
        By.CSS_SELECTOR, "#action-menu button"
    )
    js_click(browser, menu_button)
    time.sleep(2)

    if not (click_by_text(browser, "Fijar") or click_by_text(browser, "Pin")):
        raise RuntimeError("No encontré la opción Fijar en el menú del comentario")
    time.sleep(2)
    # Confirmation dialog uses the same label
    click_by_text(browser, "Fijar") or click_by_text(browser, "Pin")
    time.sleep(3)


def main():
    account = get_accounts("youtube")[0]
    select_model(get_ollama_model())
    state = load_state()

    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--width=1400")
    opts.add_argument("--height=1100")
    opts.add_argument("-profile")
    opts.add_argument(account["firefox_profile"])
    browser = webdriver.Firefox(
        service=Service(GeckoDriverManager().install()), options=opts
    )
    done = 0
    try:
        browser.get("https://studio.youtube.com")
        time.sleep(8)
        channel_id = browser.current_url.split("/channel/")[-1].split("/")[0]
        videos = list_public_shorts(browser, channel_id)
        pending = [v for v in videos if v["id"] not in state]
        print(f"[pin] Públicos: {len(videos)} | sin comentario fijado: {len(pending)}")

        for video in pending[:MAX_PER_RUN]:
            question = generate_text(
                "Escribe UNA pregunta corta (máximo 15 palabras) en español "
                "para fijar como comentario bajo un YouTube Short titulado "
                f'"{video["title"]}". Debe invitar a responder con opiniones '
                "o recuerdos. Cercana y directa, con un emoji al final. "
                "Devuelve solo la pregunta."
            ).strip().strip('"')
            try:
                pin_comment(browser, video["id"], question)
                state[video["id"]] = question
                save_state(state)
                done += 1
                print(f"[pin] Fijado en {video['id']}: {question}")
            except Exception as e:
                print(f"[pin] FALLO en {video['id']}: {e}")
    finally:
        browser.quit()
    print(f"[pin] {done} comentarios fijados.")


if __name__ == "__main__":
    main()
