"""Adapter: the platform's stock quantised LLM as a grounded chat client.

The spec says reuse the platform's stock quantised LLM, grounded at inference
time — no retraining, no fine-tuning. Grounding is achieved purely through the
system/user prompts built in specialist.grounding.

We call ollama directly (rather than via the base Ollama wrapper) so we can bound
the work for a real-time classroom loop:
  * num_predict caps generated tokens (answers are meant to be short + read aloud),
  * temperature=0 keeps grounded answers deterministic,
  * a request timeout means a slow/stuck model fails cleanly instead of hanging.

Returns a callable matching pipeline.LLMClient: (system_prompt, user_prompt) -> str.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def ollama_grounded_client(model_name: str, num_predict: int = 128,
                           timeout: float = 90.0, temperature: float = 0.0,
                           keep_alive="30m"):
    """Build a grounded LLM client backed by the on-device Ollama model.

    ``keep_alive`` keeps the model resident between queries so only the first
    query pays the load cost ('-1' = keep loaded indefinitely; a duration like
    '30m' balances responsiveness against RAM on a memory-tight device).
    """
    import ollama

    client = ollama.Client(timeout=timeout)

    def _client(system_prompt: str, user_prompt: str) -> str:
        resp = client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"num_predict": num_predict, "temperature": temperature},
            keep_alive=keep_alive,
        )
        try:
            return resp.message.content
        except AttributeError:
            return resp["message"]["content"]

    return _client


def ollama_grounded_stream_client(model_name: str, num_predict: int = 128,
                                  timeout: float = 90.0, temperature: float = 0.0,
                                  keep_alive="30m"):
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
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"num_predict": num_predict, "temperature": temperature},
            keep_alive=keep_alive,
            stream=True,
        )
        for chunk in stream:
            try:
                yield chunk.message.content
            except AttributeError:
                yield chunk["message"]["content"]

    return _client
