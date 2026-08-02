import difflib
import math
import re
import base64
import json
import time
import os
import random
import shutil
import srt as srt_lib
import subprocess
import traceback
import requests
import assemblyai as aai

from utils import *
from cache import *
from .Tts import TTS
from llm_provider import generate_text
from config import *
from status import *
from uuid import uuid4
from constants import *
from typing import List
from moviepy.editor import *
from termcolor import colored
from selenium import webdriver
from moviepy.video.fx.all import crop
from moviepy.config import change_settings
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from moviepy.video.tools.subtitles import SubtitlesClip
from webdriver_manager.firefox import GeckoDriverManager
from datetime import datetime, timedelta

# Set ImageMagick Path
change_settings({"IMAGEMAGICK_BINARY": get_imagemagick_path()})

# MoviePy 1.x uses Image.ANTIALIAS, removed in Pillow 10
import PIL.Image

if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS


class YouTube:
    """
    Class for YouTube Automation.

    Steps to create a YouTube Short:
    1. Generate a topic [DONE]
    2. Generate a script [DONE]
    3. Generate metadata (Title, Description, Tags) [DONE]
    4. Generate AI Image Prompts [DONE]
    4. Generate Images based on generated Prompts [DONE]
    5. Convert Text-to-Speech [DONE]
    6. Show images each for n seconds, n: Duration of TTS / Amount of images [DONE]
    7. Combine Concatenated Images with the Text-to-Speech [DONE]
    """

    def __init__(
        self,
        account_uuid: str,
        account_nickname: str,
        fp_profile_path: str,
        niche: str,
        language: str,
    ) -> None:
        """
        Constructor for YouTube Class.

        Args:
            account_uuid (str): The unique identifier for the YouTube account.
            account_nickname (str): The nickname for the YouTube account.
            fp_profile_path (str): Path to the firefox profile that is logged into the specificed YouTube Account.
            niche (str): The niche of the provided YouTube Channel.
            language (str): The language of the Automation.

        Returns:
            None
        """
        self._account_uuid: str = account_uuid
        self._account_nickname: str = account_nickname
        self._fp_profile_path: str = fp_profile_path
        self._niche: str = niche
        self._language: str = language

        self.images = []
        # image path -> animated clip path (img2vid), filled when enabled
        self.scene_clips = {}

        # Initialize the Firefox profile
        self.options: Options = Options()

        # Set headless state of browser
        if get_headless():
            self.options.add_argument("--headless")

        if not os.path.isdir(self._fp_profile_path):
            raise ValueError(
                f"Firefox profile path does not exist or is not a directory: {self._fp_profile_path}"
            )

        self.options.add_argument("-profile")
        self.options.add_argument(self._fp_profile_path)

        # The browser is started lazily (see _ensure_browser): generation can
        # take many minutes and an idle window opened up-front tends to get
        # closed or die before the upload needs it
        self.browser: webdriver.Firefox = None

    def _next_schedule_slot(self) -> datetime:
        """
        Computes the next free publication slot: the earliest configured
        daily hour (with random minute jitter) after both now+1h and the
        last slot handed out. Persists state so consecutive batch videos
        get consecutive slots.

        Returns:
            slot (datetime): The datetime to schedule the next video at.
        """
        state_path = os.path.join(ROOT_DIR, ".mp", "schedule_state.json")
        floor = datetime.now() + timedelta(hours=1)
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                last = datetime.fromisoformat(json.load(f)["last"])
            # 3h exclusion so jitter can't drop two videos in the same
            # daily slot window
            floor = max(floor, last + timedelta(hours=3))
        except Exception:
            pass

        hours = sorted(get_schedule_hours() or [13, 20])
        for day_offset in range(0, 60):
            day = datetime.now().date() + timedelta(days=day_offset)
            for hour in hours:
                slot = datetime.combine(day, datetime.min.time()) + timedelta(
                    hours=hour, minutes=random.randint(0, 44)
                )
                if slot > floor:
                    with open(state_path, "w", encoding="utf-8") as f:
                        json.dump({"last": slot.isoformat()}, f)
                    return slot

        return floor

    @staticmethod
    def _dismiss_popups(driver) -> bool:
        """
        Dismisses blocking confirmation popups Studio sometimes shows
        (e.g. "Aún estamos comprobando tu contenido" -> "Entendido").

        Returns:
            dismissed (bool): True if a popup was dismissed.
        """
        dismissed = False
        for label in (
            "Publicar de todas formas",
            "Publish anyway",
            "Entendido",
            "Got it",
            "Aceptar",
            "OK",
        ):
            for el in driver.find_elements(
                By.XPATH, f"//*[normalize-space(text())='{label}']"
            ):
                try:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        dismissed = True
                        time.sleep(1)
                except Exception:
                    continue
        return dismissed

    def _ensure_browser(self) -> webdriver.Firefox:
        """
        Returns a live browser, starting or restarting it if needed.

        Returns:
            browser (webdriver.Firefox): A usable browser instance.
        """
        if self.browser is not None:
            try:
                _ = self.browser.current_url
                return self.browser
            except Exception:
                try:
                    self.browser.quit()
                except Exception:
                    pass
                self.browser = None

        self.service: Service = Service(GeckoDriverManager().install())
        self.browser = webdriver.Firefox(service=self.service, options=self.options)
        return self.browser

    @property
    def niche(self) -> str:
        """
        Getter Method for the niche.

        Returns:
            niche (str): The niche
        """
        return self._niche

    @property
    def language(self) -> str:
        """
        Getter Method for the language to use.

        Returns:
            language (str): The language
        """
        return self._language

    def generate_response(self, prompt: str, model_name: str = None) -> str:
        """
        Generates an LLM Response based on a prompt and the user-provided model.

        Args:
            prompt (str): The prompt to use in the text generation.

        Returns:
            response (str): The generated AI Repsonse.
        """
        return generate_text(prompt, model_name=model_name)

    def _topic_history_path(self) -> str:
        # A .json file survives rem_temp_files(), which only removes non-JSON
        return os.path.join(ROOT_DIR, ".mp", f"topic_history_{self._account_uuid}.json")

    def _load_topic_history(self) -> List[str]:
        try:
            with open(self._topic_history_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_topic_history(self, history: List[str]) -> None:
        with open(self._topic_history_path(), "w", encoding="utf-8") as f:
            json.dump(history[-100:], f, ensure_ascii=False, indent=2)

    NEWS_MAX_AGE_HOURS = 20

    @classmethod
    def _news_expired(cls, item: dict) -> bool:
        """
        News items carry their harvest timestamp; past NEWS_MAX_AGE_HOURS
        they are stale and must be discarded — an old news video is worse
        than no video.
        """
        try:
            harvested = datetime.fromisoformat(item.get("fecha", ""))
        except (TypeError, ValueError):
            return False
        return (datetime.now() - harvested) > timedelta(
            hours=cls.NEWS_MAX_AGE_HOURS
        )

    def _pop_queued_news(self) -> dict:
        """
        Takes (and removes) the first fresh item from news_queue.json, the
        harvester-managed news queue. Expired items are purged on the way.
        Returns None when there is nothing fresh.
        """
        path = os.path.join(ROOT_DIR, "news_queue.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                queue = json.load(f)
        except Exception:
            return None

        picked, fresh = None, []
        for item in queue:
            if self._news_expired(item):
                warning(f"Noticia caducada, descartada: {item.get('tema', '')[:70]}")
                continue
            if picked is None:
                picked = item
            else:
                fresh.append(item)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(fresh, f, ensure_ascii=False, indent=1)

        return picked

    def restore_consumed_topic(self) -> None:
        """
        Puts the topic consumed by generate_topic back at the front of its
        queue, so a failed generation doesn't burn it.
        """
        news_item = getattr(self, "_consumed_news_item", None)
        line = getattr(self, "_consumed_topic_line", None)
        try:
            if news_item:
                path = os.path.join(ROOT_DIR, "news_queue.json")
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        queue = json.load(f)
                except Exception:
                    queue = []
                queue.insert(0, news_item)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(queue, f, ensure_ascii=False, indent=1)
            elif line:
                path = os.path.join(ROOT_DIR, "topics.txt")
                lines = []
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8-sig") as f:
                        lines = f.readlines()
                head, rest = [], lines
                while rest and (
                    rest[0].strip().startswith("#") or not rest[0].strip()
                ):
                    head.append(rest.pop(0))
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(head)
                    f.write(line + "\n")
                    f.writelines(rest)
            self._consumed_news_item = None
            self._consumed_topic_line = None
        except Exception as e:
            warning(f"No se pudo devolver el tema a la cola: {e}")

    def _pop_queued_topic(self) -> str:
        """
        Takes (and removes) the first pending topic from topics.txt, the
        user-maintained evergreen queue. Returns None when the file is
        missing or has no pending entries.
        """
        path = os.path.join(ROOT_DIR, "topics.txt")
        if not os.path.exists(path):
            return None

        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()

        picked, remaining = None, []
        for line in lines:
            stripped = line.strip()
            if picked is None and stripped and not stripped.startswith("#"):
                picked = stripped
                continue
            remaining.append(line)

        if picked is not None:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(remaining)

        return picked

    def _load_audience_insights(self) -> List[str]:
        """
        Returns the winning themes from the latest channel report
        (scripts/channel_report.py), or [] if missing or older than 14 days.
        """
        path = os.path.join(ROOT_DIR, ".mp", "audience_insights.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            updated = datetime.fromisoformat(data.get("updated", "2000-01-01T00:00"))
            if (datetime.now() - updated).days > 14:
                return []
            return [str(t) for t in data.get("temas_ganadores", [])][:5]
        except Exception:
            return []

    def _peek_queued_topic(self) -> str:
        """
        Returns the next pending topic from topics.txt without consuming it,
        or None if the queue is missing or empty.
        """
        # Same order as consumption: fresh news first, then evergreen
        try:
            with open(
                os.path.join(ROOT_DIR, "news_queue.json"), "r", encoding="utf-8"
            ) as f:
                for item in json.load(f):
                    if not self._news_expired(item) and item.get("tema"):
                        return item["tema"]
        except Exception:
            pass

        path = os.path.join(ROOT_DIR, "topics.txt")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    # Only the topic itself: context stays out of teasers
                    return stripped.split("||")[0].strip()
        return None

    def generate_topic(self) -> str:
        """
        Generates a topic: first from the user's topics.txt queue if it has
        pending entries, otherwise auto-generated with a random angle,
        avoiding already-covered topics.

        Returns:
            topic (str): The generated topic.
        """
        history = self._load_topic_history()
        self.topic_context = None
        # Only queue-sourced news skip the schedule; hand-grounded
        # evergreen topics keep their slot in the cascade
        self._news_immediate = False

        news_item = self._pop_queued_news()
        # Remember what was consumed so a failed generation can put it back
        self._consumed_news_item = news_item
        self._consumed_topic_line = None
        if news_item:
            queued = news_item.get("tema", "").strip()
            self.topic_context = news_item.get("contexto") or None
            self._news_immediate = True
        else:
            queued = self._pop_queued_topic()
            self._consumed_topic_line = queued
            # A hand-written topic may carry grounding facts after "||":
            # the script must then stick to those facts
            if queued and "||" in queued:
                parts = [p.strip() for p in queued.split("||")]
                queued = parts[0]
                for part in parts[1:]:
                    if re.match(r"(?i)^CONTEXTO\s*:", part):
                        self.topic_context = (
                            re.sub(r"(?i)^CONTEXTO\s*:\s*", "", part) or None
                        )

        if queued:
            if get_verbose():
                info(f" => Using queued topic: {queued}")
                if self.topic_context:
                    info(f" => Grounding context: {self.topic_context[:100]}...")
            prompt = (
                "Generate a specific video idea based on this instruction from "
                f'the channel owner: "{queued}". '
                f"The channel niche is: {self.niche}. "
                "Keep every detail the owner asked for. Make it exactly one "
                "sentence. Only return the topic, nothing else."
            )
            completion = generate_text(prompt, temperature=0.9)
        else:
            angle = random.choice(TOPIC_ANGLES)
            if get_verbose():
                info(f" => Topic angle: {angle}")
            prompt = (
                f"Please generate a specific video idea about the following niche: {self.niche}. "
                f"Approach it from this angle: {angle}. "
                "Make it exactly one sentence. Only return the topic, nothing else."
            )
            if history:
                recent = "\n- ".join(history[-20:])
                prompt += (
                    "\nDo NOT repeat or rephrase any of these already covered topics:"
                    f"\n- {recent}"
                )
            insights = self._load_audience_insights()
            if insights:
                prompt += (
                    "\nThe channel's audience has responded best to themes "
                    f"like: {', '.join(insights)}. When it fits the angle, "
                    "prefer ideas with similar appeal."
                )
            completion = generate_text(prompt, temperature=1.15)

        if not completion:
            error("Failed to generate Topic.")

        self.subject = completion

        history.append(completion)
        self._save_topic_history(history)

        return completion

    def generate_script(self) -> str:
        """
        Generate a script for a video, depending on the subject of the video, the number of paragraphs, and the AI model.

        Returns:
            script (str): The script of the video.
        """
        length_range = get_script_sentence_length_range()
        if len(length_range) == 2:
            sentence_length = random.randint(length_range[0], length_range[1])
        else:
            sentence_length = get_script_sentence_length()

        # Some videos come out as a two-host dialogue (needs the edge
        # provider and a second voice configured)
        self._dialogue = (
            get_tts_provider() in ("edge", "kokoro")
            and bool(get_tts_voice_b())
            and random.random() < get_dialogue_ratio()
        )
        # Remembered so generate_prompts derives the image count from the
        # actual length of this video
        self._sentence_length = sentence_length
        prompt = f"""
        Generate a script for a video in {sentence_length} sentences, depending on the subject of the video.

        The script is to be returned as a string with the specified number of paragraphs.

        Here is an example of a string:
        "This is an example string."

        Do not under any circumstance reference this prompt in your response.

        Get straight to the point, don't start with unnecessary things like, "welcome to this video".

        IMITATE THE STRUCTURE AND RHYTHM (not the content) of these two
        example scripts that performed exceptionally well:

        EXAMPLE 1 (news style): "La beta de Mortal Shell 2 llega a consolas...
        y esta vez sin exclusividad de PC. Los jugadores de PlayStation y Xbox
        podrán probarla desde el día uno. El estudio confirma mejoras en el
        combate y un nuevo sistema de armas. Una decisión que cambia las
        reglas: las betas ya no son territorio exclusivo del PC."

        EXAMPLE 2 (curiosity style): "Casi la mitad de las ventas de Final
        Fantasy siguen siendo en formato físico. Mientras la industria entera
        empuja hacia lo digital, sus fans llenan estanterías. Los coleccionistas
        pagan hasta el doble por ediciones limitadas. Y por eso, aunque todo
        sea digital algún día... Final Fantasy seguirá vendiéndose en cajas."

        STICK TO WELL-KNOWN, VERIFIABLE FACTS. NEVER invent specific numbers,
        dates, names or events you are not sure about — vague but true beats
        precise but false.

        Use punctuation to shape the narrator's rhythm: strategic commas, and
        an occasional ellipsis (...) right before a reveal or surprising fact,
        so the voice pauses dramatically. One or two ellipses per script, max.

        THE FIRST SENTENCE MUST BE A POWERFUL HOOK: a surprising fact, a bold claim
        or an intriguing question directly about the subject, so the viewer stays.
        NEVER open with generic phrases like "La evolución de...", "En este video..."
        or "¿Sabías que...". Be specific and punchy from the first word.

        THE LAST SENTENCE must connect back to the idea of the first sentence,
        so the video feels seamless when it loops and replays.

        Obviously, the script should be related to the subject of the video.
        
        YOU MUST NOT EXCEED THE {sentence_length} SENTENCES LIMIT. MAKE SURE THE {sentence_length} SENTENCES ARE SHORT.
        YOU MUST NOT INCLUDE ANY TYPE OF MARKDOWN OR FORMATTING IN THE SCRIPT, NEVER USE A TITLE.
        YOU MUST WRITE THE SCRIPT IN THE LANGUAGE SPECIFIED IN [LANGUAGE].
        ONLY RETURN THE RAW CONTENT OF THE SCRIPT. DO NOT INCLUDE "VOICEOVER", "NARRATOR" OR SIMILAR INDICATORS OF WHAT SHOULD BE SPOKEN AT THE BEGINNING OF EACH PARAGRAPH OR LINE. YOU MUST NOT MENTION THE PROMPT, OR ANYTHING ABOUT THE SCRIPT ITSELF. ALSO, NEVER TALK ABOUT THE AMOUNT OF PARAGRAPHS OR LINES. JUST WRITE THE SCRIPT
        
        Subject: {self.subject}
        Language: {self.language}
        """
        if self._dialogue:
            prompt += """

        WRITE THE SCRIPT AS A LIVELY DIALOGUE BETWEEN TWO HOSTS.
        Prefix every sentence with "A: " or "B: ", alternating naturally
        (A speaks first). A is curious, asks and reacts with energy; B knows
        the facts and reveals them. Keep every line short and punchy. All the
        other rules (hook first line, loop-back last line, sentence limit)
        still apply to the dialogue as a whole.
        """
            if get_verbose():
                info(" => Formato: diálogo a dos voces")

        # News/context topics: force the script to stay inside the
        # verified facts instead of the model's memory
        if getattr(self, "topic_context", None):
            prompt += (
                "\n\nBASE THE SCRIPT EXCLUSIVELY ON THESE VERIFIED FACTS "
                "(do not invent or add anything beyond them, but write it "
                "as an exciting story):\n" + self.topic_context
            )

        # Reasoning enabled here on purpose: the script is where writing
        # quality matters most and the extra minutes are local-only cost
        completion = generate_text(prompt, think=True)

        # Apply regex to remove *
        completion = re.sub(r"\*", "", completion)

        if not completion:
            error("The generated script is empty.")
            return

        if len(completion) > 5000:
            if get_verbose():
                warning("Generated Script is too long. Retrying...")
            return self.generate_script()

        self.script = completion

        return completion

    def _clean_metadata_text(self, text: str) -> str:
        """
        Strips the wrapping quotes, markdown and label prefixes LLMs tend to
        add, which look obviously machine-generated on the channel page.
        """
        text = (text or "").strip().strip("\"'«»“”").strip()
        text = re.sub(
            r"^(t[íi]tulo|title|descripci[óo]n|description)\s*:\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"[*_`]", "", text)
        # Models sometimes number the title ("1. ...") or split it across
        # lines; a title is a single clean line
        text = re.sub(r"^\d+\s*[.)]\s*", "", text)
        text = " ".join(text.split())
        return text.strip().strip("\"'«»“”").strip()

    def generate_metadata(self) -> dict:
        """
        Generates Video metadata for the to-be-uploaded YouTube Short (Title, Description).

        Returns:
            metadata (dict): The generated metadata.
        """
        title_style = random.choice(TITLE_STYLES)
        title = ""
        for _ in range(3):
            title = self._clean_metadata_text(
                self.generate_response(
                    f"Please generate a YouTube Video Title for the following subject, including hashtags: {self.subject}. "
                    f"Write it as {title_style}. "
                    f"The title MUST be written in this language: {self.language}. "
                    "Do not wrap it in quotes. Only return the title, nothing else. "
                    "Limit the title under 100 characters."
                )
            )
            if len(title) <= 100:
                break
            if get_verbose():
                warning("Generated Title is too long. Retrying...")

        if len(title) > 100:
            # Cut on a word boundary and drop any half-written hashtag
            title = title[:97].rsplit(" ", 1)[0].rstrip("#").rstrip() + "..."

        plain_script = re.sub(
            r"^\s*[AB]\s*[:.\-]\s*", "", self.script, flags=re.MULTILINE
        )
        description = self._clean_metadata_text(
            self.generate_response(
                f"Please generate a YouTube Video Description for the following script: {plain_script}. "
                "It must have: a short engaging summary (2 sentences max), then one short "
                "question inviting viewers to answer in the comments, and end with 2-3 "
                "relevant hashtags on the last line. "
                f"The description MUST be written in this language: {self.language}. "
                "Do not use markdown formatting or quotes. Only return the description, nothing else."
            )
        )

        # News topics: credit the source in the description
        context = getattr(self, "topic_context", None)
        if context:
            source_match = re.search(r"Fuente:\s*([^)|]+)", context)
            if source_match:
                description += f"\n\nFuente: {source_match.group(1).strip()}"

        self.metadata = {"title": title, "description": description}

        return self.metadata

    def generate_prompts(self) -> List[str]:
        """
        Generates AI Image Prompts based on the provided Video Script.

        Returns:
            image_prompts (List[str]): Generated List of image prompts.
        """
        # Aim for a scene change every ~3s of speech (Spanish TTS averages
        # ~15 characters per second), capped to keep generation time sane
        estimated_seconds = max(len(self.script) / 15, 12)
        n_prompts = max(6, min(14, round(estimated_seconds / 3)))
        # One coherent visual style per video, varied across videos
        self._image_style = random.choice(IMAGE_STYLES)
        if get_verbose():
            info(f" => Image style: {self._image_style}")

        prompt = f"""
        Generate {n_prompts} Image Prompts for AI Image Generation,
        depending on the subject of a video.
        Subject: {self.subject}

        The image prompts are to be returned as
        a JSON-Array of strings.

        Each search term should consist of a full sentence,
        always add the main subject of the video.

        Be emotional and use interesting adjectives to make the
        Image Prompt as detailed as possible.

        CRITICAL RULES FOR THE PROMPTS:
        - NEVER depict or name specific characters, people or franchises.
          Evoke the WORLD instead: environments, landscapes, objects,
          atmosphere and mood that feel like the subject without showing
          any recognizable character or logo.
        - If a character matters, show them as a distant silhouette,
          from behind, or represent them through an iconic object.
        - The images must contain NO readable text, signs or logos.

        YOU MUST ONLY RETURN THE JSON-ARRAY OF STRINGS.
        YOU MUST NOT RETURN ANYTHING ELSE.
        YOU MUST NOT RETURN THE SCRIPT.

        The search terms must be related to the subject of the video.
        Here is an example of a JSON-Array of strings:
        ["image prompt 1", "image prompt 2", "image prompt 3"]

        For context, here is the full text:
        {self.script}
        """

        completion = (
            str(self.generate_response(prompt))
            .replace("```json", "")
            .replace("```", "")
        )

        image_prompts = []

        if "image_prompts" in completion:
            image_prompts = json.loads(completion)["image_prompts"]
        else:
            try:
                image_prompts = json.loads(completion)
                if get_verbose():
                    info(f" => Generated Image Prompts: {image_prompts}")
            except Exception:
                if get_verbose():
                    warning(
                        "LLM returned an unformatted response. Attempting to clean..."
                    )

                # Get everything between [ and ], and turn it into a list
                r = re.compile(r"\[.*\]")
                image_prompts = r.findall(completion)
                if len(image_prompts) == 0:
                    if get_verbose():
                        warning("Failed to generate Image Prompts. Retrying...")
                    return self.generate_prompts()

        if len(image_prompts) > n_prompts:
            image_prompts = image_prompts[: int(n_prompts)]

        self.image_prompts = [
            f"{p}, {self._image_style}" for p in image_prompts
        ]

        success(f"Generated {len(image_prompts)} Image Prompts.")

        return image_prompts

    def _persist_image(self, image_bytes: bytes, provider_label: str) -> str:
        """
        Writes generated image bytes to a PNG file in .mp.

        Args:
            image_bytes (bytes): Image payload
            provider_label (str): Label for logging

        Returns:
            path (str): Absolute image path
        """
        image_path = os.path.join(ROOT_DIR, ".mp", str(uuid4()) + ".png")

        with open(image_path, "wb") as image_file:
            image_file.write(image_bytes)

        if get_verbose():
            info(f' => Wrote image from {provider_label} to "{image_path}"')

        self.images.append(image_path)
        return image_path

    def generate_image_nanobanana2(self, prompt: str) -> str:
        """
        Generates an AI Image using Nano Banana 2 API (Gemini image API).

        Args:
            prompt (str): Prompt for image generation

        Returns:
            path (str): The path to the generated image.
        """
        print(f"Generating Image using Nano Banana 2 API: {prompt}")

        api_key = get_nanobanana2_api_key()
        if not api_key:
            error("nanobanana2_api_key is not configured.")
            return None

        base_url = get_nanobanana2_api_base_url().rstrip("/")
        model = get_nanobanana2_model()
        aspect_ratio = get_nanobanana2_aspect_ratio()

        endpoint = f"{base_url}/models/{model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": aspect_ratio},
            },
        }

        try:
            body = None
            for attempt in range(3):
                response = requests.post(
                    endpoint,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=300,
                )
                if response.status_code == 429:
                    message = ""
                    try:
                        message = response.json()["error"]["message"]
                    except Exception:
                        pass
                    # No point retrying when the account has no credits left
                    if "depleted" in message.lower() or "billing" in message.lower():
                        warning(f"Nano Banana 2 credits exhausted: {message}")
                        return None
                    if attempt < 2:
                        warning("Nano Banana 2 rate limit hit, retrying in 15s...")
                        time.sleep(15)
                        continue
                response.raise_for_status()
                body = response.json()
                break

            candidates = body.get("candidates", [])
            for candidate in candidates:
                content = candidate.get("content", {})
                for part in content.get("parts", []):
                    inline_data = part.get("inlineData") or part.get("inline_data")
                    if not inline_data:
                        continue
                    data = inline_data.get("data")
                    mime_type = inline_data.get("mimeType") or inline_data.get("mime_type", "")
                    if data and str(mime_type).startswith("image/"):
                        image_bytes = base64.b64decode(data)
                        return self._persist_image(image_bytes, "Nano Banana 2 API")

            if get_verbose():
                warning(f"Nano Banana 2 did not return an image payload. Response: {body}")
            return None
        except Exception as e:
            if get_verbose():
                warning(f"Failed to generate image with Nano Banana 2 API: {str(e)}")
            return None

    def generate_image_comfyui(self, prompt: str) -> str:
        """
        Generates an AI Image using a local ComfyUI server (FLUX.1-schnell GGUF).

        Args:
            prompt (str): Prompt for image generation

        Returns:
            path (str): The path to the generated image.
        """
        print(f"Generating Image using local ComfyUI (FLUX): {prompt}")

        base_url = get_comfyui_base_url().rstrip("/")
        workflow = {
            "unet": {
                "class_type": "UnetLoaderGGUF",
                "inputs": {"unet_name": "flux1-schnell-Q4_K_S.gguf"},
            },
            "clip": {
                "class_type": "DualCLIPLoaderGGUF",
                "inputs": {
                    "clip_name1": "t5-v1_1-xxl-encoder-Q5_K_M.gguf",
                    "clip_name2": "clip_l.safetensors",
                    "type": "flux",
                },
            },
            "positive": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["clip", 0]},
            },
            "negative": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "", "clip": ["clip", 0]},
            },
            "latent": {
                "class_type": "EmptySD3LatentImage",
                # A/B tested vs 768x1344@4: visibly richer light and detail
                "inputs": {"width": 832, "height": 1472, "batch_size": 1},
            },
            "sampler": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["unet", 0],
                    "positive": ["positive", 0],
                    "negative": ["negative", 0],
                    "latent_image": ["latent", 0],
                    "seed": random.randint(0, 2**32 - 1),
                    "steps": 6,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": 1.0,
                },
            },
            "vae": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": "ae.safetensors"},
            },
            "decode": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["sampler", 0], "vae": ["vae", 0]},
            },
            # RealESRGAN x2 (768x1344 -> 1536x2688) so the frame reaches
            # 1080x1920 sharp instead of stretched
            "upscale_model": {
                "class_type": "UpscaleModelLoader",
                "inputs": {"model_name": "RealESRGAN_x2plus.pth"},
            },
            "upscale": {
                "class_type": "ImageUpscaleWithModel",
                "inputs": {
                    "upscale_model": ["upscale_model", 0],
                    "image": ["decode", 0],
                },
            },
            "save": {
                "class_type": "SaveImage",
                "inputs": {"images": ["upscale", 0], "filename_prefix": "mpv2"},
            },
        }

        try:
            response = requests.post(
                f"{base_url}/prompt", json={"prompt": workflow}, timeout=30
            )
            response.raise_for_status()
            prompt_id = response.json()["prompt_id"]

            # First generation includes model load; allow up to ~10 minutes
            for _ in range(120):
                time.sleep(5)
                history = requests.get(
                    f"{base_url}/history/{prompt_id}", timeout=30
                ).json()
                if prompt_id not in history:
                    continue
                for node_output in history[prompt_id]["outputs"].values():
                    for image_info in node_output.get("images", []):
                        image = requests.get(
                            f"{base_url}/view",
                            params={
                                "filename": image_info["filename"],
                                "subfolder": image_info.get("subfolder", ""),
                                "type": image_info.get("type", "output"),
                            },
                            timeout=60,
                        )
                        image.raise_for_status()
                        return self._persist_image(image.content, "ComfyUI FLUX")
                break

            warning("ComfyUI did not produce an image in time.")
            return None
        except Exception as e:
            if get_verbose():
                warning(f"Failed to generate image with ComfyUI: {str(e)}")
            return None

    def animate_image_comfyui(self, image_path: str, scene_prompt: str = None) -> str:
        """
        Animates a still into a ~4s vertical clip using LTX-Video 2B
        distilled (img2vid) on the local ComfyUI server.

        Args:
            image_path (str): The still to animate.
            scene_prompt (str): The image prompt, used to ground the motion.

        Returns:
            path (str): Path to the MP4 clip, or None so the caller can
            fall back to the Ken Burns still.
        """
        print(f"Animating image with LTX-Video: {os.path.basename(image_path)}")

        base_url = get_comfyui_base_url().rstrip("/")
        motion_prompt = (
            (scene_prompt.strip().rstrip(".") + ". " if scene_prompt else "")
            + "Cinematic live scene. The camera drifts slowly with subtle "
            "parallax. Elements in the scene move naturally and smoothly: "
            "hair and clothing sway, light flickers, particles float "
            "through the air. High quality, coherent motion, no distortion."
        )
        negative_prompt = (
            "worst quality, inconsistent motion, blurry, jittery, distorted, "
            "warping, morphing, extra limbs, text, watermark"
        )

        try:
            with open(image_path, "rb") as f:
                upload = requests.post(
                    f"{base_url}/upload/image",
                    files={"image": (os.path.basename(image_path), f, "image/png")},
                    data={"overwrite": "true"},
                    timeout=60,
                )
            upload.raise_for_status()
            image_name = upload.json()["name"]

            workflow = {
                "ckpt": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": "ltxv-2b-0.9.8-distilled.safetensors"},
                },
                "clip": {
                    "class_type": "CLIPLoaderGGUF",
                    "inputs": {
                        "clip_name": "t5-v1_1-xxl-encoder-Q5_K_M.gguf",
                        "type": "ltxv",
                    },
                },
                "positive": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": motion_prompt, "clip": ["clip", 0]},
                },
                "negative": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": negative_prompt, "clip": ["clip", 0]},
                },
                "image": {
                    "class_type": "LoadImage",
                    "inputs": {"image": image_name},
                },
                # 576x1024 is exactly 9:16; LTXV needs /32 dimensions and
                # 8n+1 frames: 97 frames -> ~3.9s at 25fps
                "i2v": {
                    "class_type": "LTXVImgToVideo",
                    "inputs": {
                        "positive": ["positive", 0],
                        "negative": ["negative", 0],
                        "vae": ["ckpt", 2],
                        "image": ["image", 0],
                        "width": 576,
                        "height": 1024,
                        "length": 97,
                        "batch_size": 1,
                        "strength": 1.0,
                    },
                },
                "cond": {
                    "class_type": "LTXVConditioning",
                    "inputs": {
                        "positive": ["i2v", 0],
                        "negative": ["i2v", 1],
                        "frame_rate": 25.0,
                    },
                },
                "sampler_sel": {
                    "class_type": "KSamplerSelect",
                    "inputs": {"sampler_name": "euler"},
                },
                "sigmas": {
                    "class_type": "LTXVScheduler",
                    "inputs": {
                        "steps": 8,
                        "max_shift": 2.05,
                        "base_shift": 0.95,
                        "stretch": True,
                        "terminal": 0.1,
                        "latent": ["i2v", 2],
                    },
                },
                "sample": {
                    "class_type": "SamplerCustom",
                    "inputs": {
                        "model": ["ckpt", 0],
                        "add_noise": True,
                        "noise_seed": random.randint(0, 2**32 - 1),
                        "cfg": 1.0,
                        "positive": ["cond", 0],
                        "negative": ["cond", 1],
                        "sampler": ["sampler_sel", 0],
                        "sigmas": ["sigmas", 0],
                        "latent_image": ["i2v", 2],
                    },
                },
                "decode": {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["sample", 0], "vae": ["ckpt", 2]},
                },
                "video": {
                    "class_type": "CreateVideo",
                    "inputs": {"images": ["decode", 0], "fps": 25.0},
                },
                "save": {
                    "class_type": "SaveVideo",
                    "inputs": {
                        "video": ["video", 0],
                        "filename_prefix": "mpv2_anim",
                        "format": "mp4",
                        "codec": "h264",
                    },
                },
            }

            response = requests.post(
                f"{base_url}/prompt", json={"prompt": workflow}, timeout=30
            )
            response.raise_for_status()
            prompt_id = response.json()["prompt_id"]

            # First clip includes the LTXV model load; allow up to ~20 minutes
            for _ in range(240):
                time.sleep(5)
                history = requests.get(
                    f"{base_url}/history/{prompt_id}", timeout=30
                ).json()
                if prompt_id not in history:
                    continue
                entry = history[prompt_id]
                if entry.get("status", {}).get("status_str") == "error":
                    warning("LTX-Video animation errored; falling back to the still.")
                    return None
                for node_output in entry.get("outputs", {}).values():
                    for value in node_output.values():
                        if not isinstance(value, list):
                            continue
                        for item in value:
                            if not (
                                isinstance(item, dict)
                                and str(item.get("filename", "")).endswith(".mp4")
                            ):
                                continue
                            clip = requests.get(
                                f"{base_url}/view",
                                params={
                                    "filename": item["filename"],
                                    "subfolder": item.get("subfolder", ""),
                                    "type": item.get("type", "output"),
                                },
                                timeout=120,
                            )
                            clip.raise_for_status()
                            clip_path = os.path.join(
                                ROOT_DIR, ".mp", str(uuid4()) + "_anim.mp4"
                            )
                            with open(clip_path, "wb") as f:
                                f.write(clip.content)
                            return clip_path
                break

            warning("ComfyUI did not produce an animation in time; using the still.")
            return None
        except Exception as e:
            if get_verbose():
                warning(f"Failed to animate image: {str(e)}")
            return None

    def generate_image(self, prompt: str) -> str:
        """
        Generates an AI Image based on the given prompt using the configured provider.

        Args:
            prompt (str): Reference for image generation

        Returns:
            path (str): The path to the generated image.
        """
        if get_image_provider() == "comfyui":
            return self.generate_image_comfyui(prompt)
        return self.generate_image_nanobanana2(prompt)

    @staticmethod
    def _parse_dialogue(script: str):
        """
        Parses "A: ..." / "B: ..." dialogue lines. Returns a list of
        (speaker, text) tuples, or None if the script isn't a dialogue.
        """
        segments = []
        for raw_line in script.splitlines():
            match = re.match(r"^\s*([AB])\s*[:.\-]\s*(.+)$", raw_line.strip())
            if match:
                segments.append((match.group(1), match.group(2).strip()))
        return segments if len(segments) >= 2 else None

    @staticmethod
    def _apply_pronunciation_glossary(text: str) -> str:
        """
        Deterministic first pass: replaces known gaming terms with their
        Spanish-phonetic respelling from pronunciacion.json (longest terms
        first, whole words, case-insensitive). The LLM polish then only
        has to improvise on terms the glossary doesn't know yet.
        """
        path = os.path.join(ROOT_DIR, "pronunciacion.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                glossary = json.load(f)
        except Exception:
            return text
        for term in sorted(glossary, key=len, reverse=True):
            text = re.sub(
                rf"\b{re.escape(term)}\b",
                glossary[term],
                text,
                flags=re.IGNORECASE,
            )
        return text

    def _classify_music_mood(self) -> str:
        """
        Asks the LLM which soundtrack mood fits the script, out of the
        mood keys defined in Songs/moods.json. Returns the mood key, or
        None (keep the fully random pick) if anything fails.
        """
        try:
            with open(
                os.path.join(ROOT_DIR, "Songs", "moods.json"), "r", encoding="utf-8"
            ) as f:
                moods = list(json.load(f).keys())
        except Exception:
            return None
        if not moods:
            return None
        try:
            answer = generate_text(
                "Eres el director musical de un canal de YouTube sobre "
                "videojuegos. Lee este guion y elige el ambiente de la "
                "música de fondo que mejor le pega.\n\n"
                f"{self.script}\n\n"
                f"Responde SOLO con una palabra de esta lista: {', '.join(moods)}",
                temperature=0.2,
            ).strip().lower()
            for mood in moods:
                if mood in answer:
                    if get_verbose():
                        info(f" => Música de fondo: mood '{mood}'")
                    return mood
        except Exception as e:
            if get_verbose():
                warning(f"Music mood classification failed: {str(e)}")
        return None

    @staticmethod
    def _fix_caption_text(text: str) -> str:
        """
        Whisper writes what it hears, and en español eso pierde haches
        mudas ("hadas" -> "adas") o deforma anglicismos. Replaces known
        mishearings with the canonical spelling from
        correcciones_subtitulos.json (longest first, whole words,
        case-insensitive) before the text reaches captions or overlays.
        """
        path = os.path.join(ROOT_DIR, "correcciones_subtitulos.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                corrections = json.load(f)
        except Exception:
            return text
        for wrong in sorted(corrections, key=len, reverse=True):
            text = re.sub(
                rf"\b{re.escape(wrong)}\b",
                corrections[wrong],
                text,
                flags=re.IGNORECASE,
            )
        return text

    def _speech_polish(self, script: str) -> str:
        """
        Rewrites the script for narration only: punctuation tuned for
        spoken rhythm and foreign terms respelled with approximate Spanish
        phonetics so the Spanish TTS pronounces them naturally. Content
        must stay identical; subtitles are unaffected (Whisper transcribes
        the audio and restores canonical spellings).
        """
        script = self._apply_pronunciation_glossary(script)
        polished = generate_text(
            "Eres el corrector de locución de un canal español de YouTube. "
            "Reescribe este guion SOLO para ser leído en voz alta por un "
            "sintetizador de voz español:\n"
            "1) Puntuación de locutor: parte toda frase de más de 20 palabras "
            "en frases cortas; coma donde el narrador respiraría; conserva "
            "los puntos suspensivos dramáticos.\n"
            "2) OBLIGATORIO: adapta TODOS los términos y títulos en inglés, "
            "sin excepción, a transcripción fonética española aproximada con "
            "tildes. Ejemplos del estilo: gameplay -> guéimplei; open world "
            "-> óupen uorld; speedrun -> espídran; Breath of the Wild -> "
            "Brez of de Wáild; weapon durability system -> uépon durabíliti "
            "sístem; streaming -> estrímin. NUNCA traduzcas al español un "
            "término inglés (weapon durability system NO es 'arma "
            "durabilidad sistema'): solo cambia su ESCRITURA para que un "
            "locutor español lo PRONUNCIE como en inglés. Repasa palabra a "
            "palabra: si es inglés, se adapta. Siglas como DLC, RPG o PS5 "
            "se dejan tal cual. Las reglas 1 y 2 son AMBAS obligatorias: "
            "aplica la puntuación Y la fonética.\n"
            "3) NO cambies el contenido: ni añadas, ni quites, ni resumas "
            'frases. Si hay prefijos "A:" o "B:" al inicio de línea, '
            "consérvalos exactamente.\n"
            "Devuelve SOLO el guion corregido, nada más.\n\n" + script,
            think=True,
        ).strip()

        # Sanity check: a polish that halves or doubles the script is a
        # rewrite, not a polish - fall back to the original
        if polished and 0.6 < len(polished) / max(len(script), 1) < 1.6:
            return polished
        warning("Speech polish result discarded (length mismatch)")
        return script

    def generate_script_to_speech(self, tts_instance: TTS) -> str:
        """
        Converts the generated script into Speech using KittenTTS and returns the path to the wav file.

        Args:
            tts_instance (tts): Instance of TTS Class.

        Returns:
            path_to_wav (str): Path to generated audio (WAV Format).
        """
        path = os.path.join(ROOT_DIR, ".mp", str(uuid4()) + ".wav")

        # Kept for the QC gate: Whisper restores canonical spellings, so
        # the transcript compares best against the pre-phonetics script
        self._script_source = self.script

        # Narration-only rewrite: spoken punctuation + Spanish phonetics
        # for foreign terms
        try:
            self.script = self._speech_polish(self.script)
        except Exception as e:
            warning(f"Speech polish failed, using raw script: {e}")

        # End every video with the subscribe call-to-action (next-topic teaser
        # when available) so it gets spoken and picked up by the subtitles
        cta = (getattr(self, "cta", "") or get_subscribe_cta()).strip()

        def clean(text):
            # Keep commas/ellipses (narration pauses) and Spanish ¿¡
            return re.sub(r"[^\w\s.,?!¿¡]", "", text)

        segments = (
            self._parse_dialogue(self.script)
            if getattr(self, "_dialogue", False)
            else None
        )

        if segments and get_tts_provider() in ("edge", "kokoro") and get_tts_voice_b():
            if cta:
                last_speaker = segments[-1][0]
                segments.append(("B" if last_speaker == "A" else "A", cta))
            voices = {"A": get_tts_voice(), "B": get_tts_voice_b()}
            voiced = [(voices[s], clean(t)) for s, t in segments]
            self.script = " ".join(text for _, text in voiced)
            tts_instance.synthesize_dialogue(voiced, path)
        else:
            # Strip stray dialogue tags if the model added them anyway
            self.script = re.sub(
                r"^\s*[AB]\s*[:.\-]\s*", "", self.script, flags=re.MULTILINE
            )
            if cta:
                self.script = f"{self.script.rstrip()} {cta}"
            self.script = clean(self.script)
            tts_instance.synthesize(self.script, path)

        self.tts_path = path

        if get_verbose():
            info(f' => Wrote TTS to "{path}"')

        return path

    def add_video(self, video: dict) -> None:
        """
        Adds a video to the cache.

        Args:
            video (dict): The video to add

        Returns:
            None
        """
        videos = self.get_videos()
        videos.append(video)

        cache = get_youtube_cache_path()

        with open(cache, "r", encoding="utf-8") as file:
            previous_json = json.loads(file.read())

            # Find our account
            accounts = previous_json["accounts"]
            for account in accounts:
                if account["id"] == self._account_uuid:
                    account["videos"].append(video)

            # Commit changes
            with open(cache, "w", encoding="utf-8") as f:
                f.write(json.dumps(previous_json))

    def generate_subtitles(self, audio_path: str) -> str:
        """
        Generates subtitles for the audio using the configured STT provider.

        Args:
            audio_path (str): The path to the audio file.

        Returns:
            path (str): The path to the generated SRT File.
        """
        provider = str(get_stt_provider() or "local_whisper").lower()

        if provider == "local_whisper":
            return self.generate_subtitles_local_whisper(audio_path)

        if provider == "third_party_assemblyai":
            return self.generate_subtitles_assemblyai(audio_path)

        warning(f"Unknown stt_provider '{provider}'. Falling back to local_whisper.")
        return self.generate_subtitles_local_whisper(audio_path)

    def generate_subtitles_assemblyai(self, audio_path: str) -> str:
        """
        Generates subtitles using AssemblyAI.

        Args:
            audio_path (str): Audio file path

        Returns:
            path (str): Path to SRT file
        """
        aai.settings.api_key = get_assemblyai_api_key()
        config = aai.TranscriptionConfig()
        transcriber = aai.Transcriber(config=config)
        transcript = transcriber.transcribe(audio_path)
        subtitles = transcript.export_subtitles_srt()

        srt_path = os.path.join(ROOT_DIR, ".mp", str(uuid4()) + ".srt")

        with open(srt_path, "w", encoding="utf-8") as file:
            file.write(subtitles)

        # AssemblyAI emits sentence-level entries; split into short chunks
        # (the whisper path already produces word-timed karaoke chunks)
        equalize_subtitles(srt_path, 16)

        return srt_path

    def _format_srt_timestamp(self, seconds: float) -> str:
        """
        Formats a timestamp in seconds to SRT format.

        Args:
            seconds (float): Seconds

        Returns:
            ts (str): HH:MM:SS,mmm
        """
        total_millis = max(0, int(round(seconds * 1000)))
        hours = total_millis // 3600000
        minutes = (total_millis % 3600000) // 60000
        secs = (total_millis % 60000) // 1000
        millis = total_millis % 1000
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def generate_subtitles_local_whisper(self, audio_path: str) -> str:
        """
        Generates subtitles using local Whisper (faster-whisper).

        Args:
            audio_path (str): Audio file path

        Returns:
            path (str): Path to SRT file
        """
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            error(
                "Local STT selected but 'faster-whisper' is not installed. "
                "Install it or switch stt_provider to third_party_assemblyai."
            )
            raise

        model = WhisperModel(
            get_whisper_model(),
            device=get_whisper_device(),
            compute_type=get_whisper_compute_type(),
        )
        segments, _ = model.transcribe(
            audio_path, vad_filter=True, word_timestamps=True
        )

        # Karaoke-style captions: groups of 2-3 words timed to the voice
        words = [w for segment in segments for w in (segment.words or [])]
        # Word-level timings for the accent-word overlay in combine()
        self._word_timings = [
            (self._fix_caption_text(w.word.strip()), float(w.start), float(w.end))
            for w in words
        ]
        chunks, current = [], []
        for word in words:
            current.append(word)
            text = "".join(w.word for w in current).strip()
            if len(current) >= 3 or len(text) >= 16:
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)

        lines = []
        for idx, chunk in enumerate(chunks, start=1):
            text = self._fix_caption_text("".join(w.word for w in chunk).strip())
            if not text:
                continue
            lines.append(str(idx))
            lines.append(
                f"{self._format_srt_timestamp(chunk[0].start)} --> "
                f"{self._format_srt_timestamp(chunk[-1].end)}"
            )
            lines.append(text)
            lines.append("")

        subtitles = "\n".join(lines)
        srt_path = os.path.join(ROOT_DIR, ".mp", str(uuid4()) + ".srt")
        with open(srt_path, "w", encoding="utf-8") as file:
            file.write(subtitles)

        return srt_path

    def combine(self) -> str:
        """
        Combines everything into the final video.

        Returns:
            path (str): The path to the generated MP4 File.
        """
        combined_image_path = os.path.join(ROOT_DIR, ".mp", str(uuid4()) + ".mp4")
        threads = get_threads()
        tts_clip = AudioFileClip(self.tts_path)
        max_duration = tts_clip.duration

        # Make a generator that returns a TextClip when called with consecutive
        # ImageMagick on Windows swallows backslashes in -font paths and
        # silently falls back to its default font, so use forward slashes
        subtitle_font = os.path.join(get_fonts_dir(), get_font()).replace("\\", "/")
        generator = lambda txt: TextClip(
            txt.upper(),
            font=subtitle_font,
            fontsize=85,
            color="white",
            stroke_color="black",
            stroke_width=3,
            # Narrower than the frame so long chunks wrap with side margins
            size=(920, None),
            method="caption",
        )

        print(colored("[+] Combining images...", "blue"))

        CROSSFADE = 0.4

        def _ken_burns_scene(
            image_path: str, duration: float, dramatic: bool = False
        ) -> CompositeVideoClip:
            """
            Builds one scene: the image cropped to 9:16 with a slow random
            zoom and pan (Ken Burns) so nothing on screen is ever static.
            The dramatic variant (opening scene) always pushes in, harder.
            """
            base = ImageClip(image_path)

            # Crop to a 9:16 area
            if round((base.w / base.h), 4) < 0.5625:
                base = crop(
                    base,
                    width=base.w,
                    height=round(base.w / 0.5625),
                    x_center=base.w / 2,
                    y_center=base.h / 2,
                )
            else:
                base = crop(
                    base,
                    width=round(0.5625 * base.h),
                    height=base.h,
                    x_center=base.w / 2,
                    y_center=base.h / 2,
                )

            # Oversize so zoom/pan never shows the frame edge
            base = base.resize((1244, 2212))

            if dramatic:
                z0, z1 = (1.0, 1.16)
            else:
                zoom_in = random.random() < 0.5
                z0, z1 = (1.0, 1.10) if zoom_in else (1.10, 1.0)
            pan_x = random.randint(-30, 30)
            pan_y = random.randint(-45, 45)

            moving = base.resize(lambda t: z0 + (z1 - z0) * (t / duration))

            def position(t):
                progress = t / duration
                x = -(1244 - 1080) / 2 + pan_x * (2 * progress - 1)
                y = -(2212 - 1920) / 2 + pan_y * (2 * progress - 1)
                return (x, y)

            scene = CompositeVideoClip(
                [moving.set_position(position)], size=(1080, 1920)
            )
            return scene.set_duration(duration).set_fps(30)

        def _animated_scene(clip_path: str, duration: float) -> CompositeVideoClip:
            """
            Builds one scene from an img2vid clip (576x1024, exactly 9:16),
            scaled to fill the 1080x1920 frame. Clips shorter than the scene
            are slowed slightly instead of freeze-framing.
            """
            clip = VideoFileClip(clip_path, audio=False)
            if clip.duration < duration:
                clip = clip.fx(vfx.speedx, clip.duration / duration)
            else:
                clip = clip.subclip(0, duration)
            scale = max(1080 / clip.w, 1920 / clip.h)
            clip = clip.resize((round(clip.w * scale), round(clip.h * scale)))
            if (clip.w, clip.h) != (1080, 1920):
                clip = crop(
                    clip,
                    width=1080,
                    height=1920,
                    x_center=clip.w / 2,
                    y_center=clip.h / 2,
                )
            scene = CompositeVideoClip([clip], size=(1080, 1920))
            return scene.set_duration(duration).set_fps(30)

        # One scene every ~3s, cycling through the images if there are
        # fewer than needed; crossfades overlap so durations compensate
        n_scenes = max(len(self.images), int(round(max_duration / 3)))
        scene_dur = (max_duration + CROSSFADE * (n_scenes - 1)) / n_scenes
        image_cycle = [self.images[i % len(self.images)] for i in range(n_scenes)]

        scene_clips = getattr(self, "scene_clips", {}) or {}
        clips = []
        for i, image_path in enumerate(image_cycle):
            anim_path = scene_clips.get(image_path)
            if get_verbose():
                kind = "anim" if anim_path else "still"
                info(f" => Building scene {i + 1}/{n_scenes} ({kind}): {os.path.basename(image_path)}")
            if anim_path and os.path.exists(anim_path):
                scene = _animated_scene(anim_path, scene_dur)
            else:
                scene = _ken_burns_scene(image_path, scene_dur, dramatic=(i == 0))
            if i > 0:
                scene = scene.crossfadein(CROSSFADE)
            clips.append(scene)

        final_clip = concatenate_videoclips(clips, method="compose", padding=-CROSSFADE)
        final_clip = final_clip.set_fps(30)
        random_song = choose_random_song(getattr(self, "music_mood", None))

        subtitles = None
        try:
            subtitles_path = self.generate_subtitles(self.tts_path)
            # Parse the SRT ourselves: moviepy opens it with the locale
            # encoding (cp1252 on Windows), which mangles accents
            with open(subtitles_path, "r", encoding="utf-8") as srt_file:
                parsed_subs = [
                    ((s.start.total_seconds(), s.end.total_seconds()), s.content)
                    for s in srt_lib.parse(srt_file.read())
                ]
            # Keep the opening frames text-free: thumbnails often sample
            # them and a half-sentence caption looks broken on the grid
            SUBS_START = 0.6
            parsed_subs = [
                ((max(start, SUBS_START), end), text)
                for (start, end), text in parsed_subs
                if end > SUBS_START
            ]
            subtitles = SubtitlesClip(parsed_subs, generator)
        except Exception as e:
            warning(f"Failed to generate subtitles, continuing without subtitles: {e}")

        random_song_clip = AudioFileClip(random_song).set_fps(44100)

        # Turn down volume, trim to the video and fade out at the end
        random_song_clip = random_song_clip.fx(afx.volumex, 0.1)
        if random_song_clip.duration > max_duration:
            random_song_clip = random_song_clip.subclip(0, max_duration)
        random_song_clip = random_song_clip.fx(afx.audio_fadeout, 2.5)

        audio_layers = [tts_clip.set_fps(44100), random_song_clip]

        # Subtle whoosh on every scene change
        sfx_dir = os.path.join(ROOT_DIR, "assets", "sfx")
        sfx_files = (
            [os.path.join(sfx_dir, f) for f in os.listdir(sfx_dir) if f.endswith(".wav")]
            if os.path.isdir(sfx_dir)
            else []
        )
        if sfx_files:
            for i in range(1, n_scenes):
                boundary = i * (scene_dur - CROSSFADE)
                if boundary >= max_duration:
                    break
                audio_layers.append(
                    AudioFileClip(random.choice(sfx_files))
                    .set_fps(44100)
                    .fx(afx.volumex, 0.18)
                    .set_start(max(0.0, boundary - 0.25))
                )

        comp_audio = CompositeAudioClip(audio_layers)

        final_clip = final_clip.set_audio(comp_audio)
        final_clip = final_clip.set_duration(tts_clip.duration)

        def _karaoke_layers(word_timings):
            """
            Pro-style captions: 2-3 word white line with the word being
            spoken overlaid in gold at its exact position.
            """
            SUBS_START = 0.6
            ACCENT = "#FFD700"
            y_pos = int(1920 * 0.63)

            chunks, current = [], []
            for word, w_start, w_end in word_timings:
                if not word:
                    continue
                current.append((word.upper(), w_start, w_end))
                text = " ".join(x[0] for x in current)
                if len(current) >= 3 or len(text) >= 16:
                    chunks.append(current)
                    current = []
            if current:
                chunks.append(current)

            def make_text(text, color):
                return TextClip(
                    text,
                    font=subtitle_font,
                    fontsize=85,
                    color=color,
                    stroke_color="black",
                    stroke_width=3,
                    method="label",
                )

            space_w = make_text("i i", "white").w - make_text("ii", "white").w
            built = []
            for chunk in chunks:
                start = max(chunk[0][1], SUBS_START)
                end = max(chunk[-1][2], start + 0.3)
                if end <= SUBS_START:
                    continue
                line = " ".join(x[0] for x in chunk)
                base = make_text(line, "white")
                x0 = (1080 - base.w) / 2
                built.append(
                    base.set_position((x0, y_pos)).set_start(start).set_end(end)
                )
                offset = 0
                for word, w_start, w_end in chunk:
                    accent = make_text(word, ACCENT)
                    a_start = max(w_start, start)
                    a_end = max(min(w_end, end), a_start + 0.1)
                    built.append(
                        accent.set_position((x0 + offset, y_pos))
                        .set_start(a_start)
                        .set_end(a_end)
                    )
                    offset += accent.w + space_w
                del base
            return built

        layers = [final_clip]

        # Burned-in opening title (hashtags stripped): anchors the topic
        # visually and improves the frame YouTube samples for the grid
        opening_title = (
            re.sub(r"#\S+", "", getattr(self, "metadata", {}).get("title", ""))
            .strip()
            .rstrip(" .-:")
        )
        if opening_title:
            title_clip = TextClip(
                opening_title.upper(),
                font=subtitle_font,
                fontsize=92,
                color="white",
                stroke_color="black",
                stroke_width=4,
                size=(950, None),
                method="caption",
            )
            layers.append(
                title_clip.set_position(("center", 0.16), relative=True)
                .set_start(0)
                .set_duration(min(2.4, max_duration))
                .crossfadeout(0.4)
            )

        word_timings = getattr(self, "_word_timings", None)
        if word_timings:
            # Accent-word karaoke replaces the plain subtitles
            try:
                layers.extend(_karaoke_layers(word_timings))
                subtitles = None
            except Exception as e:
                warning(f"Karaoke captions failed, using plain subtitles: {e}")

        if subtitles is not None:
            # Below-center keeps faces/subjects visible; relative so it
            # holds for any resolution
            layers.append(subtitles.set_position(("center", 0.63), relative=True))

        if len(layers) > 1:
            final_clip = CompositeVideoClip(layers)

        # Kinetic emphasis: a subtle zoom punch on key words (numbers,
        # long/rare terms), synced via the word timestamps. Only the punch
        # windows pay the resize cost.
        emphasis_times = []
        if word_timings:
            last_punch = -10.0
            for word, w_start, _ in word_timings:
                clean = re.sub(r"\W", "", word)
                if w_start < 1.0 or w_start - last_punch < 2.5:
                    continue
                if any(c.isdigit() for c in clean) or len(clean) >= 9:
                    emphasis_times.append(w_start)
                    last_punch = w_start
                if len(emphasis_times) >= 4:
                    break

        if emphasis_times:
            PUNCH = 0.25
            pieces, cursor = [], 0.0
            for punch_at in emphasis_times:
                if punch_at >= final_clip.duration - PUNCH:
                    break
                if punch_at > cursor:
                    pieces.append(final_clip.subclip(cursor, punch_at))
                window = final_clip.subclip(punch_at, punch_at + PUNCH).resize(
                    lambda t: 1 + 0.05 * math.sin(math.pi * t / PUNCH)
                )
                pieces.append(
                    CompositeVideoClip(
                        [window.set_position("center")], size=(1080, 1920)
                    )
                )
                cursor = punch_at + PUNCH
            pieces.append(final_clip.subclip(cursor))
            final_clip = concatenate_videoclips(pieces)
            if get_verbose():
                info(f" => Énfasis cinético en {len(emphasis_times)} palabras clave")

        raw_path = combined_image_path + ".raw.mp4"
        # Keep MoviePy's temp audio inside .mp/ so a crash never strands
        # a TEMP_MPY_wvf_snd.mp3 in the project root (rem_temp_files only
        # sweeps .mp/)
        final_clip.write_videofile(
            raw_path,
            threads=threads,
            temp_audiofile=os.path.join(
                ROOT_DIR, ".mp", str(uuid4()) + "_temp_audio.mp3"
            ),
        )

        # Normalize loudness to YouTube's -14 LUFS reference so every
        # video plays at the same professional level
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", raw_path,
                    "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                    # Subtle film grain + vignette: hides AI smoothness and
                    # glues scenes of different styles together
                    "-vf", "noise=alls=5:allf=t,vignette=a=PI/7",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    combined_image_path,
                ],
                check=True,
            )
            os.remove(raw_path)
        except Exception as e:
            warning(f"Loudness normalization failed, keeping raw audio: {e}")
            shutil.move(raw_path, combined_image_path)

        success(f'Wrote Video to "{combined_image_path}"')

        return combined_image_path

    def generate_video(self, tts_instance: TTS) -> str:
        """
        Generates a YouTube Short based on the provided niche and language.

        Args:
            tts_instance (TTS): Instance of TTS Class.

        Returns:
            path (str): The path to the generated MP4 File.
        """
        # Reset per-video state so a reused instance never mixes assets
        # from a previous generation (whose temp files are already deleted)
        self.images = []
        self.image_prompts = []
        self.scene_clips = {}

        # Generate the Topic
        self.generate_topic()

        # Generate the Script
        self.generate_script()

        # Generate the Metadata
        self.generate_metadata()

        # Generate the Image Prompts
        self.generate_prompts()

        # Build the closing CTA while the LLM is still loaded: tease the next
        # queued topic when there is one, else fall back to the fixed CTA
        self.cta = get_subscribe_cta().strip()
        next_topic = self._peek_queued_topic()
        if next_topic:
            bell_hint = (
                ' Refer to the notification bell as "la campanita".'
                if "spanish" in str(self.language).lower()
                else ""
            )
            teaser = generate_text(
                "Write exactly one short, natural closing sentence for a video, "
                f"in this language: {self.language}. It must invite viewers to "
                "subscribe and hit the bell, teasing that the next video will "
                f'be about: "{next_topic}". Only return the sentence, nothing else.'
                + bell_hint,
                temperature=0.9,
            ).strip()
            if teaser:
                self.cta = teaser
                if get_verbose():
                    info(f" => CTA teaser for next topic: {teaser}")

        # Pick the soundtrack mood while the LLM is still loaded; combine()
        # turns it into a matching background song
        self.music_mood = self._classify_music_mood()

        # With local image generation, free Ollama's VRAM first: the LLM and
        # the diffusion model don't fit together on a 12 GB GPU
        if get_image_provider() == "comfyui":
            from llm_provider import unload_model

            unload_model()

        # Generate the Images
        prompt_by_image = {}
        for prompt in self.image_prompts:
            image_path = self.generate_image(prompt)
            if image_path:
                prompt_by_image[image_path] = prompt

        if not self.images:
            error("No images were generated. Check your image provider (ComfyUI server or API credits).")
            raise RuntimeError("Cannot build a video without images")

        # Animate the stills into real motion clips (img2vid). ComfyUI swaps
        # FLUX out for LTX-Video on its own; any failure keeps the still and
        # combine() falls back to Ken Burns for that scene.
        if get_animate_scenes() and get_image_provider() == "comfyui":
            for image_path in self.images:
                clip_path = self.animate_image_comfyui(
                    image_path, prompt_by_image.get(image_path)
                )
                if clip_path:
                    self.scene_clips[image_path] = clip_path

        # Mirror of the pre-image unload: free ComfyUI's VRAM so the next
        # LLM phase (or the next batch iteration) can load Ollama on GPU
        if get_image_provider() == "comfyui":
            try:
                requests.post(
                    f"{get_comfyui_base_url().rstrip('/')}/free",
                    json={"unload_models": True, "free_memory": True},
                    timeout=15,
                )
            except Exception:
                pass

        # Generate the TTS
        self.generate_script_to_speech(tts_instance)

        # Combine everything
        path = self.combine()

        # QC gate: what the voice actually said (Whisper transcript) must
        # match the script. Catches garbled narration before it uploads.
        transcript = " ".join(
            w for w, _, _ in getattr(self, "_word_timings", []) or []
        )
        source = getattr(self, "_script_source", "")
        if transcript and source:

            def norm(text):
                return re.sub(r"[^\wáéíóúñü ]", " ", text.lower()).split()

            ratio = difflib.SequenceMatcher(
                None, norm(source), norm(transcript)
            ).ratio()
            if get_verbose():
                info(f" => QC locución vs guion: {ratio:.0%}")
            if ratio < 0.55:
                raise RuntimeError(
                    f"QC: la locución no coincide con el guion "
                    f"(similitud {ratio:.0%}); vídeo descartado"
                )
            if ratio < 0.7:
                warning(
                    f"QC: similitud locución/guion baja ({ratio:.0%}), "
                    "revisa el vídeo si puedes"
                )

        if get_verbose():
            info(f" => Generated Video: {path}")

        self.video_path = os.path.abspath(path)

        # Keep a permanent copy: .mp/ is wiped on every menu loop
        videos_dir = os.path.join(ROOT_DIR, "videos")
        os.makedirs(videos_dir, exist_ok=True)
        keep_path = os.path.join(videos_dir, os.path.basename(path))
        shutil.copy2(self.video_path, keep_path)
        success(f'Saved a permanent copy to "{keep_path}"')

        return path

    def get_channel_id(self) -> str:
        """
        Gets the Channel ID of the YouTube Account.

        Returns:
            channel_id (str): The Channel ID.
        """
        driver = self._ensure_browser()
        driver.get("https://studio.youtube.com")
        time.sleep(2)
        channel_id = driver.current_url.split("/")[-1]
        self.channel_id = channel_id

        return channel_id

    def upload_video(self) -> bool:
        """
        Uploads the video to YouTube.

        Returns:
            success (bool): Whether the upload was successful or not.
        """
        try:
            self.get_channel_id()

            driver = self._ensure_browser()
            verbose = get_verbose()

            # Go to youtube.com/upload
            driver.get("https://www.youtube.com/upload")

            # Set video file
            FILE_PICKER_TAG = "ytcp-uploads-file-picker"
            file_picker = driver.find_element(By.TAG_NAME, FILE_PICKER_TAG)
            INPUT_TAG = "input"
            file_input = file_picker.find_element(By.TAG_NAME, INPUT_TAG)
            file_input.send_keys(self.video_path)

            # Wait for the upload dialog to render its metadata textboxes
            WebDriverWait(driver, 60).until(
                lambda d: len(d.find_elements(By.ID, YOUTUBE_TEXTBOX_ID)) >= 2
            )
            time.sleep(2)

            # Fail fast when YouTube is refusing uploads: the dialog still
            # lets you walk every step, but Done stays disabled at the end
            page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
            if "límite diario" in page_text or "daily upload limit" in page_text:
                raise RuntimeError(
                    "YouTube: límite diario de subida alcanzado. Verifica el "
                    "canal (youtube.com/features desde el perfil del bot) o "
                    "espera 24h. El vídeo queda en videos/."
                )

            # Set title
            textboxes = driver.find_elements(By.ID, YOUTUBE_TEXTBOX_ID)

            title_el = textboxes[0]
            description_el = textboxes[-1]

            if verbose:
                info("\t=> Setting title...")

            title_el.click()
            time.sleep(1)
            # .clear() sets innerHTML, which YouTube Studio's CSP blocks;
            # clear via keyboard instead
            title_el.send_keys(Keys.CONTROL, "a")
            title_el.send_keys(Keys.DELETE)
            title_el.send_keys(self.metadata["title"])

            if verbose:
                info("\t=> Setting description...")

            # Set description
            time.sleep(10)
            # Re-locate: typing the title can re-render the dialog, leaving
            # the reference captured earlier stale. The hashtag-suggestion
            # overlay may still cover the box, so click through it with JS.
            WebDriverWait(driver, 30).until(
                lambda d: len(d.find_elements(By.ID, YOUTUBE_TEXTBOX_ID)) >= 2
            )
            description_el = driver.find_elements(By.ID, YOUTUBE_TEXTBOX_ID)[-1]
            driver.execute_script("arguments[0].scrollIntoView(); arguments[0].click();", description_el)
            time.sleep(0.5)
            description_el.send_keys(Keys.CONTROL, "a")
            description_el.send_keys(Keys.DELETE)
            description_el.send_keys(self.metadata["description"])

            time.sleep(0.5)

            # Set `made for kids` option
            if verbose:
                info("\t=> Setting `made for kids` option...")

            is_for_kids_checkbox = driver.find_element(
                By.NAME, YOUTUBE_MADE_FOR_KIDS_NAME
            )
            is_not_for_kids_checkbox = driver.find_element(
                By.NAME, YOUTUBE_NOT_MADE_FOR_KIDS_NAME
            )

            # JS clicks throughout: native clicks fail whenever a scrim or
            # overlay covers the dialog (transitions, promo popups)
            if not get_is_for_kids():
                driver.execute_script(
                    "arguments[0].scrollIntoView(); arguments[0].click();",
                    is_not_for_kids_checkbox,
                )
            else:
                driver.execute_script(
                    "arguments[0].scrollIntoView(); arguments[0].click();",
                    is_for_kids_checkbox,
                )

            time.sleep(0.5)

            # Disclose AI-generated (altered/synthetic) content
            if verbose:
                info("\t=> Setting AI content disclosure...")
            try:
                show_more = driver.find_element(By.ID, YOUTUBE_SHOW_MORE_BUTTON_ID)
                driver.execute_script(
                    "arguments[0].scrollIntoView(); arguments[0].click();", show_more
                )
                time.sleep(2)
                altered_yes = driver.find_element(
                    By.NAME, YOUTUBE_ALTERED_CONTENT_YES_NAME
                )
                driver.execute_script(
                    "arguments[0].scrollIntoView(); arguments[0].click();", altered_yes
                )
                time.sleep(0.5)
            except Exception as disclosure_err:
                warning(f"Could not set AI content disclosure: {disclosure_err}")

            # Click next
            if verbose:
                info("\t=> Clicking next...")

            next_button = driver.find_element(By.ID, YOUTUBE_NEXT_BUTTON_ID)
            driver.execute_script("arguments[0].click();", next_button)

            # Click next again
            if verbose:
                info("\t=> Clicking next again...")
            next_button = driver.find_element(By.ID, YOUTUBE_NEXT_BUTTON_ID)
            driver.execute_script("arguments[0].click();", next_button)

            # Wait for 2 seconds
            time.sleep(2)

            # Click next again
            if verbose:
                info("\t=> Clicking next again...")
            next_button = driver.find_element(By.ID, YOUTUBE_NEXT_BUTTON_ID)
            driver.execute_script("arguments[0].click();", next_button)

            # News-queue videos skip the schedule: by the time the slot
            # cascade reaches them the story is stale
            is_news = bool(getattr(self, "_news_immediate", False))

            if get_publish_mode() == "schedule" and not is_news:
                # Schedule for the next free daily slot instead of
                # publishing immediately
                slot = self._next_schedule_slot()
                self.scheduled_for = slot
                if verbose:
                    info(f"\t=> Scheduling for {slot:%d/%m/%Y %H:%M}...")

                expand = driver.find_element(By.ID, YOUTUBE_SCHEDULE_EXPAND_ID)
                driver.execute_script(
                    "arguments[0].scrollIntoView(); arguments[0].click();", expand
                )
                time.sleep(2)

                datepicker = driver.find_element(By.ID, YOUTUBE_DATEPICKER_TRIGGER_ID)
                driver.execute_script(
                    "arguments[0].scrollIntoView(); arguments[0].click();", datepicker
                )
                time.sleep(2)
                date_input = next(
                    e
                    for e in driver.find_elements(By.CSS_SELECTOR, "input")
                    if e.is_displayed() and "202" in (e.get_attribute("value") or "")
                )
                date_input.send_keys(Keys.CONTROL, "a")
                date_input.send_keys(slot.strftime("%d/%m/%Y"))
                date_input.send_keys(Keys.ENTER)
                time.sleep(1)

                time_container = driver.find_element(By.ID, YOUTUBE_TIME_CONTAINER_ID)
                time_input = time_container.find_element(By.TAG_NAME, "input")
                driver.execute_script(
                    "arguments[0].scrollIntoView(); arguments[0].click();", time_input
                )
                time.sleep(1)
                time_input.send_keys(Keys.CONTROL, "a")
                time_input.send_keys(slot.strftime("%H:%M"))
                time_input.send_keys(Keys.ENTER)
                time.sleep(1)
            else:
                # Publish immediately as public
                if verbose:
                    suffix = " (noticia: publicación inmediata)" if is_news else ""
                    info(f"\t=> Setting as public...{suffix}")

                radio_button = driver.find_elements(By.XPATH, YOUTUBE_RADIO_BUTTON_XPATH)
                driver.execute_script("arguments[0].click();", radio_button[2])

            if verbose:
                info("\t=> Clicking done button...")

            # Click done button
            done_button = driver.find_element(By.ID, YOUTUBE_DONE_BUTTON_ID)
            if (
                done_button.get_attribute("disabled")
                or done_button.get_attribute("aria-disabled") == "true"
            ):
                raise RuntimeError(
                    "El botón de publicar/programar está deshabilitado: "
                    "YouTube está rechazando la subida (¿límite diario?)"
                )
            # Capture the video URL from the dialog's own share link: the
            # content-list scrape misreports scheduled videos
            dialog_video_id = ""
            try:
                for anchor in driver.find_elements(
                    By.CSS_SELECTOR, "a[href*='/shorts/'], a[href*='watch?v=']"
                ):
                    href = anchor.get_attribute("href") or ""
                    if "/shorts/" in href:
                        dialog_video_id = href.rstrip("/").split("/")[-1].split("?")[0]
                        break
                    if "watch?v=" in href:
                        dialog_video_id = href.split("watch?v=")[-1].split("&")[0]
                        break
            except Exception:
                pass

            self._dismiss_popups(driver)
            driver.execute_script("arguments[0].click();", done_button)

            # Confirmation that YouTube accepted the upload: the dialog
            # either closes or switches to its share/confirmation panel
            # (where the done button no longer exists). Meanwhile, dismiss
            # any blocking popup ("Aún estamos comprobando tu contenido")
            # that would otherwise hang the flow forever.
            # Studio's content checks can take several minutes at night
            deadline = time.time() + 300
            confirmed = False
            while time.time() < deadline:
                dialog_gone = not any(
                    el.is_displayed()
                    for el in driver.find_elements(By.TAG_NAME, "ytcp-uploads-dialog")
                )
                done_gone = not any(
                    el.is_displayed()
                    for el in driver.find_elements(By.ID, YOUTUBE_DONE_BUTTON_ID)
                )
                if dialog_gone or done_gone:
                    confirmed = True
                    break
                if self._dismiss_popups(driver):
                    # Popup dismissed; the done click may need repeating
                    try:
                        driver.execute_script(
                            "arguments[0].click();", done_button
                        )
                    except Exception:
                        pass
                time.sleep(2)

            if not confirmed:
                # Leave visual evidence of what blocked the dialog
                try:
                    os.makedirs(os.path.join(ROOT_DIR, "logs"), exist_ok=True)
                    shot = os.path.join(
                        ROOT_DIR,
                        "logs",
                        f"upload_timeout_{datetime.now():%Y%m%d_%H%M%S}.png",
                    )
                    driver.save_screenshot(shot)
                    warning(f"Captura del bloqueo guardada en {shot}")
                except Exception:
                    pass
                raise RuntimeError(
                    "YouTube no confirmó la publicación tras 300s "
                    "(diálogo aún abierto)"
                )
            time.sleep(2)

            # Get latest video
            if verbose:
                info("\t=> Getting video URL...")

            # Get the latest uploaded video URL. The video is already
            # published at this point, so a failure here must not fail the
            # upload — Studio can take a while to list a fresh video.
            url = build_url(dialog_video_id) if dialog_video_id else ""
            if not url:
                # Fallback: scrape the content list (unreliable for
                # scheduled videos, hence the dialog capture above)
                try:
                    driver.get(
                        f"https://studio.youtube.com/channel/{self.channel_id}/videos/short"
                    )
                    videos = []
                    for attempt in range(12):
                        time.sleep(5)
                        videos = driver.find_elements(By.TAG_NAME, "ytcp-video-row")
                        if videos:
                            break
                        if attempt % 4 == 3:
                            driver.refresh()
                    first_video = videos[0]
                    anchor_tag = first_video.find_element(By.TAG_NAME, "a")
                    href = anchor_tag.get_attribute("href")
                    if verbose:
                        info(f"\t=> Extracting video ID from URL: {href}")
                    video_id = href.split("/")[-2]
                    url = build_url(video_id)
                except Exception as url_err:
                    warning(
                        f"Video was published, but its URL could not be retrieved: {url_err}"
                    )

            self.uploaded_video_url = url

            if verbose:
                success(f" => Uploaded Video: {url or '(URL pending, check YouTube Studio)'}")

            # Add video to cache
            self.add_video(
                {
                    "title": self.metadata["title"],
                    "description": self.metadata["description"],
                    "url": url,
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

            # Close the browser
            driver.quit()

            return True
        except Exception:
            error(f"YouTube upload failed:\n{traceback.format_exc()}")
            if self.browser is not None:
                try:
                    self.browser.quit()
                except Exception:
                    pass
            return False

    def get_videos(self) -> List[dict]:
        """
        Gets the uploaded videos from the YouTube Channel.

        Returns:
            videos (List[dict]): The uploaded videos.
        """
        if not os.path.exists(get_youtube_cache_path()):
            # Create the cache file
            with open(get_youtube_cache_path(), "w", encoding="utf-8") as file:
                json.dump({"videos": []}, file, indent=4)
            return []

        videos = []
        # Read the cache file
        with open(get_youtube_cache_path(), "r", encoding="utf-8") as file:
            previous_json = json.loads(file.read())
            # Find our account
            accounts = previous_json["accounts"]
            for account in accounts:
                if account["id"] == self._account_uuid:
                    videos = account["videos"]

        return videos
