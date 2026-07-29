import asyncio
import os
import random
import subprocess

import soundfile as sf

from config import ROOT_DIR, get_tts_provider, get_tts_voice

KITTEN_MODEL = "KittenML/kitten-tts-mini-0.8"
KITTEN_SAMPLE_RATE = 24000

class TTS:
    def __init__(self) -> None:
        self._provider = get_tts_provider()
        self._voice = get_tts_voice()
        if self._provider == "kitten":
            from kittentts import KittenTTS as KittenModel

            self._model = KittenModel(KITTEN_MODEL)

    def synthesize(self, text, output_file=os.path.join(ROOT_DIR, ".mp", "audio.wav")):
        if self._provider == "edge":
            return self._synthesize_edge(text, output_file)

        audio = self._model.generate(text, voice=self._voice)
        sf.write(output_file, audio, KITTEN_SAMPLE_RATE)
        return output_file

    def synthesize_dialogue(self, segments, output_file):
        """
        Synthesizes a two-host dialogue: each segment is (voice_name, text).
        Only supported with the edge provider; concatenates per-segment
        audio with ffmpeg.
        """
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
