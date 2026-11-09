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
import re
from typing import Iterable, Iterator

logger = logging.getLogger(__name__)

_THINK = re.compile(r"<think>.*?(?:</think>\s*|$)", re.DOTALL)


def _system_for(model_name: str, system_prompt: str) -> str:
    """SmolLM3 reasons out loud unless told not to, which delays the first sentence."""
    return f"/no_think\n{system_prompt}" if "smollm3" in model_name.lower() else system_prompt


def strip_thinking(text: str) -> str:
    return _THINK.sub("", text or "")


def without_thinking(chunks: Iterable[str]) -> Iterator[str]:
    """Drop any <think>...</think> block from a stream, even if a tag spans two chunks."""
    buffer, thinking = "", False
    for chunk in chunks:
        buffer += chunk or ""
        while True:
            if thinking:
                end = buffer.find("</think>")
                if end < 0:
                    buffer = buffer[-7:]
                    break
                buffer, thinking = buffer[end + 8:].lstrip(), False
            else:
                start = buffer.find("<think>")
                if start >= 0:
                    if start:
                        yield buffer[:start]
                    buffer, thinking = buffer[start + 7:], True
                    continue
                # Hold back a tail that could be the start of a tag.
                keep = next((n for n in range(min(6, len(buffer)), 0, -1) if "<think>".startswith(buffer[-n:])), 0)
                if len(buffer) > keep:
                    yield buffer[:len(buffer) - keep]
                    buffer = buffer[len(buffer) - keep:]
                break
    if buffer and not thinking:
        yield buffer


def ollama_grounded_client(model_name: str, num_predict: int = 128,
                           timeout: float = 90.0, temperature: float = 0.0,
                           keep_alive=-1):
    """Build a grounded LLM client backed by the on-device Ollama model.

    ``keep_alive=-1`` keeps the model loaded for good. With a 30 minute limit, the first
    question after a quiet spell waited about 20 s for the model to load again.
    """
    import ollama

    client = ollama.Client(timeout=timeout)

    def _client(system_prompt: str, user_prompt: str) -> str:
        resp = client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": _system_for(model_name, system_prompt)},
                {"role": "user", "content": user_prompt},
            ],
            options={"num_predict": num_predict, "temperature": temperature},
            keep_alive=keep_alive,
        )
        try:
            return strip_thinking(resp.message.content)
        except AttributeError:
            return strip_thinking(resp["message"]["content"])

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

    def _client(system_prompt: str, user_prompt: str):
        stream = client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": _system_for(model_name, system_prompt)},
                {"role": "user", "content": user_prompt},
            ],
            options={"num_predict": num_predict, "temperature": temperature},
            keep_alive=keep_alive,
            stream=True,
        )

        def text():
            for chunk in stream:
                try:
                    yield chunk.message.content
                except AttributeError:
                    yield chunk["message"]["content"]

        yield from without_thinking(text())

    return _client


def preload_ollama_model(model_name: str, timeout: float = 120.0) -> None:
    """Load the answer model now and keep it loaded, so the first question is not slow."""
    import ollama

    ollama.Client(timeout=timeout).generate(model=model_name, prompt="", keep_alive=-1)
