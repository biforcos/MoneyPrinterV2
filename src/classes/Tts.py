import asyncio
import os
import random
import subprocess

import soundfile as sf

from config import ROOT_DIR, get_tts_provider, get_tts_voice

KITTEN_MODEL = "KittenML/kitten-tts-mini-0.8"
KITTEN_SAMPLE_RATE = 24000
KOKORO_MODEL = os.path.join(ROOT_DIR, "models", "kokoro-v1.0.onnx")
KOKORO_VOICES = os.path.join(ROOT_DIR, "models", "voices-v1.0.bin")

class TTS:
    def __init__(self) -> None:
        self._provider = get_tts_provider()
        self._voice = get_tts_voice()
        if self._provider == "kitten":
            from kittentts import KittenTTS as KittenModel

            self._model = KittenModel(KITTEN_MODEL)
        elif self._provider == "kokoro":
            from kokoro_onnx import Kokoro

            self._kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)

    @staticmethod
    def _kokoro_lang(voice: str) -> str:
        # Kokoro voice ids encode the language in the first letter
        return {"e": "es", "a": "en-us", "b": "en-gb"}.get(voice[:1], "es")

    def synthesize(self, text, output_file=os.path.join(ROOT_DIR, ".mp", "audio.wav")):
        if self._provider == "edge":
            return self._synthesize_edge(text, output_file)

        if self._provider == "kokoro":
            samples, rate = self._kokoro.create(
                text, voice=self._voice, speed=1.05, lang=self._kokoro_lang(self._voice)
            )
            sf.write(output_file, samples, rate)
            return output_file

        audio = self._model.generate(text, voice=self._voice)
        sf.write(output_file, audio, KITTEN_SAMPLE_RATE)
        return output_file

    def synthesize_dialogue(self, segments, output_file):
        """
        Synthesizes a two-host dialogue: each segment is (voice_name, text).
        Supported with the edge provider (ffmpeg concat) and kokoro
        (sample-level concat with short gaps).
        """
        if self._provider == "kokoro":
            import numpy as np

            chunks = []
            rate = 24000
            for voice, text in segments:
                samples, rate = self._kokoro.create(
                    text, voice=voice, speed=1.05, lang=self._kokoro_lang(voice)
                )
                chunks.append(samples)
                chunks.append(np.zeros(int(rate * 0.25), dtype=samples.dtype))
            sf.write(output_file, np.concatenate(chunks), rate)
            return output_file

        import edge_tts

        rate = f"+{random.randint(4, 8)}%"
        part_files = []
        try:
            for i, (voice, text) in enumerate(segments):
                part = f"{output_file}.seg{i}.mp3"
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                asyncio.run(communicate.save(part))
                part_files.append(part)

            list_file = output_file + ".list.txt"
            with open(list_file, "w", encoding="utf-8") as f:
                for part in part_files:
                    escaped = os.path.abspath(part).replace("'", "'\\''")
                    f.write(f"file '{escaped}'\n")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", list_file,
                    "-ar", "44100", output_file,
                ],
                check=True,
            )
        finally:
            for part in part_files:
                if os.path.exists(part):
                    os.remove(part)
            if os.path.exists(output_file + ".list.txt"):
                os.remove(output_file + ".list.txt")
        return output_file

    def _synthesize_edge(self, text, output_file):
        import edge_tts

        # edge-tts outputs mp3; convert to wav so downstream consumers
        # (moviepy, whisper) get the format the pipeline expects
        mp3_path = output_file + ".tmp.mp3"
        # Shorts narration reads better slightly faster than natural pace;
        # small per-video variation keeps deliveries from sounding identical
        rate = f"+{random.randint(4, 10)}%"
        communicate = edge_tts.Communicate(text, self._voice, rate=rate)
        asyncio.run(communicate.save(mp3_path))

        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", mp3_path, "-ar", "44100", output_file],
            check=True,
        )
        os.remove(mp3_path)
        return output_file
