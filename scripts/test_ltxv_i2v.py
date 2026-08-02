"""
Standalone test: animate one FLUX still into a short vertical clip using
LTX-Video 2B distilled on the local ComfyUI server.

Usage (from project root):
    python scripts/test_ltxv_i2v.py [path/to/image.png]

If no image is given, picks the newest PNG in .mp/.
"""
import os
import sys
import time
import json
import random
import requests

BASE_URL = "http://127.0.0.1:8188"
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# LTXV latent grid is /32, so the frame must be divisible by 32.
# 576x1024 is exactly 9:16.
WIDTH, HEIGHT = 576, 1024
LENGTH = 97          # frames, must be 8n+1  -> ~3.9s at 25fps
FPS = 25.0
STEPS = 8            # distilled checkpoint: 8 steps, cfg 1.0

MOTION_PROMPT = (
    "Cinematic live scene. The camera drifts slowly forward with subtle "
    "parallax. Elements in the scene move naturally and smoothly: hair and "
    "clothing sway, light flickers, particles float through the air. "
    "High quality, coherent motion, no distortion."
)
NEGATIVE_PROMPT = (
    "worst quality, inconsistent motion, blurry, jittery, distorted, "
    "warping, morphing, extra limbs, text, watermark"
)


def upload_image(path: str) -> str:
    with open(path, "rb") as f:
        r = requests.post(
            f"{BASE_URL}/upload/image",
            files={"image": (os.path.basename(path), f, "image/png")},
            data={"overwrite": "true"},
            timeout=60,
        )
    r.raise_for_status()
    return r.json()["name"]


def build_workflow(image_name: str, seed: int) -> dict:
    return {
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
            "inputs": {"text": MOTION_PROMPT, "clip": ["clip", 0]},
        },
        "negative": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": NEGATIVE_PROMPT, "clip": ["clip", 0]},
        },
        "image": {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
        },
        "i2v": {
            "class_type": "LTXVImgToVideo",
            "inputs": {
                "positive": ["positive", 0],
                "negative": ["negative", 0],
                "vae": ["ckpt", 2],
                "image": ["image", 0],
                "width": WIDTH,
                "height": HEIGHT,
                "length": LENGTH,
                "batch_size": 1,
                "strength": 1.0,
            },
        },
        "cond": {
            "class_type": "LTXVConditioning",
            "inputs": {
                "positive": ["i2v", 0],
                "negative": ["i2v", 1],
                "frame_rate": FPS,
            },
        },
        "sampler_sel": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler"},
        },
        "sigmas": {
            "class_type": "LTXVScheduler",
            "inputs": {
                "steps": STEPS,
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
                "noise_seed": seed,
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
            "inputs": {"images": ["decode", 0], "fps": FPS},
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


def main():
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
    else:
        mp_dir = os.path.join(ROOT_DIR, ".mp")
        pngs = sorted(
            (os.path.join(mp_dir, f) for f in os.listdir(mp_dir) if f.endswith(".png")),
            key=os.path.getmtime,
            reverse=True,
        )
        if not pngs:
            print("No PNGs in .mp/ — pass an image path explicitly.")
            sys.exit(1)
        image_path = pngs[0]

    print(f"Input image: {image_path}")
    image_name = upload_image(image_path)
    print(f"Uploaded as: {image_name}")

    workflow = build_workflow(image_name, random.randint(0, 2**32 - 1))
    t0 = time.time()
    r = requests.post(f"{BASE_URL}/prompt", json={"prompt": workflow}, timeout=30)
    if r.status_code != 200:
        print(f"Queue rejected ({r.status_code}):")
        print(json.dumps(r.json(), indent=2)[:4000])
        sys.exit(1)
    prompt_id = r.json()["prompt_id"]
    print(f"Queued: {prompt_id}")

    # First run includes model load; allow up to 30 minutes
    for _ in range(360):
        time.sleep(5)
        h = requests.get(f"{BASE_URL}/history/{prompt_id}", timeout=30).json()
        if prompt_id not in h:
            continue
        entry = h[prompt_id]
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            print("Execution ERROR:")
            print(json.dumps(status, indent=2)[:6000])
            sys.exit(1)
        for node_output in entry.get("outputs", {}).values():
            for value in node_output.values():
                if not isinstance(value, list):
                    continue
                for item in value:
                    if isinstance(item, dict) and str(item.get("filename", "")).endswith(".mp4"):
                        elapsed = time.time() - t0
                        clip = requests.get(
                            f"{BASE_URL}/view",
                            params={
                                "filename": item["filename"],
                                "subfolder": item.get("subfolder", ""),
                                "type": item.get("type", "output"),
                            },
                            timeout=120,
                        )
                        clip.raise_for_status()
                        out_path = os.path.join(ROOT_DIR, ".mp", "ltxv_test.mp4")
                        with open(out_path, "wb") as f:
                            f.write(clip.content)
                        print(f"OK in {elapsed:.1f}s -> {out_path} ({len(clip.content)/1e6:.1f} MB)")
                        return
        print("Finished but no .mp4 in outputs:")
        print(json.dumps(entry.get("outputs", {}), indent=2)[:4000])
        sys.exit(1)

    print("Timed out waiting for the clip.")
    sys.exit(1)


if __name__ == "__main__":
    main()
