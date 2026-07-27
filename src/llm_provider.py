import re

import ollama

from config import get_ollama_base_url

_selected_model: str | None = None


def _client() -> ollama.Client:
    return ollama.Client(host=get_ollama_base_url())


def list_models() -> list[str]:
    """
    Lists all models available on the local Ollama server.

    Returns:
        models (list[str]): Sorted list of model names.
    """
    response = _client().list()
    return sorted(m.model for m in response.models)


def select_model(model: str) -> None:
    """
    Sets the model to use for all subsequent generate_text calls.

    Args:
        model (str): An Ollama model name (must be already pulled).
    """
    global _selected_model
    _selected_model = model


def get_active_model() -> str | None:
    """
    Returns the currently selected model, or None if none has been selected.
    """
    return _selected_model


def unload_model(model_name: str = None) -> None:
    """
    Asks Ollama to unload the model from VRAM immediately, freeing the GPU
    for other workloads (e.g. local image generation).

    Args:
        model_name (str): Optional model name override
    """
    model = model_name or _selected_model
    if not model:
        return
    try:
        _client().chat(model=model, messages=[], keep_alive=0)
    except Exception:
        pass


def generate_text(prompt: str, model_name: str = None) -> str:
    """
    Generates text using the local Ollama server.

    Args:
        prompt (str): User prompt
        model_name (str): Optional model name override

    Returns:
        response (str): Generated text
    """
    model = model_name or _selected_model
    if not model:
        raise RuntimeError(
            "No Ollama model selected. Call select_model() first or pass model_name."
        )

    try:
        response = _client().chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            think=False,
        )
    except ollama.ResponseError:
        # Some models don't accept the think parameter
        response = _client().chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )

    content = response["message"]["content"]
    # Reasoning models may embed their chain of thought in the content
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)

    return content.strip()
