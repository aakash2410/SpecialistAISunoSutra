"""Adapter: the quantised answer model in Ollama as a grounded chat client.

Grounding is achieved through the system/user prompts built in
specialist.grounding. We bound the work for a real-time classroom loop:
  * num_predict caps generated tokens (answers are meant to be short + read aloud),
  * temperature=0 keeps grounded answers deterministic,
  * a request timeout means a slow/stuck model fails cleanly instead of hanging.

Returns a callable matching pipeline.LLMClient: (system_prompt, user_prompt) -> str.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _thinking_off(client, model_name: str) -> dict:
    """``think=False`` for a model with a thinking mode, such as Qwen3.5, which thinks by
    default. Reasoning first would delay the first spoken sentence and use up num_predict.
    Models without one are sent nothing, since some Ollama versions reject the option."""
    try:
        capabilities = client.show(model_name).capabilities or []
    except Exception:  # noqa: BLE001
        return {}
    return {"think": False} if "thinking" in capabilities else {}


def ollama_grounded_client(model_name: str, num_predict: int = 128,
                           timeout: float = 90.0, temperature: float = 0.0,
                           keep_alive=-1):
    """Build a grounded LLM client backed by the on-device Ollama model.

    ``keep_alive=-1`` keeps the model loaded for good. With a 30 minute limit, the first
    question after a quiet spell waited about 20 s for the model to load again.
    """
    import ollama

    client = ollama.Client(timeout=timeout)
    extra = _thinking_off(client, model_name)

    def _client(system_prompt: str, user_prompt: str) -> str:
        resp = client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"num_predict": num_predict, "temperature": temperature},
            keep_alive=keep_alive,
            **extra,
        )
        try:
            return resp.message.content
        except AttributeError:
            return resp["message"]["content"]

    return _client


def ollama_grounded_stream_client(model_name: str, num_predict: int = 128,
                                  timeout: float = 90.0, temperature: float = 0.0,
                                  keep_alive=-1):
    """Streaming variant: returns (system, user) -> iterator of text chunks.

    Lets the caller assemble sentences and pipeline MT/TTS as the model generates,
    so the first sentence can be spoken while the rest is still being produced.
    """
    import ollama

    client = ollama.Client(timeout=timeout)
    extra = _thinking_off(client, model_name)

    def _client(system_prompt: str, user_prompt: str):
        stream = client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"num_predict": num_predict, "temperature": temperature},
            keep_alive=keep_alive,
            stream=True,
            **extra,
        )

        for chunk in stream:
            try:
                yield chunk.message.content
            except AttributeError:
                yield chunk["message"]["content"]

    return _client


def preload_ollama_model(model_name: str, timeout: float = 120.0) -> None:
    """Load the answer model now and keep it loaded, so the first question is not slow."""
    import ollama

    ollama.Client(timeout=timeout).generate(model=model_name, prompt="", keep_alive=-1)
