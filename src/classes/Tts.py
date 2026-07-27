import asyncio
import os
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

    def _synthesize_edge(self, text, output_file):
        import edge_tts

        # edge-tts outputs mp3; convert to wav so downstream consumers
        # (moviepy, whisper) get the format the pipeline expects
        mp3_path = output_file + ".tmp.mp3"
        communicate = edge_tts.Communicate(text, self._voice)
        asyncio.run(communicate.save(mp3_path))

        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", mp3_path, "-ar", "44100", output_file],
            check=True,
        )
        os.remove(mp3_path)
        return output_file
