"""Adapter: the platform's stock quantised LLM as a grounded chat client.

The spec says reuse the platform's stock quantised LLM, grounded at inference
time — no retraining, no fine-tuning. Grounding is achieved purely through the
system/user prompts built in specialist.grounding, so all this adapter does is
pass those two messages to the model via the existing Ollama wrapper and return
the text.

Returns a callable matching pipeline.LLMClient: (system_prompt, user_prompt) -> str.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def ollama_grounded_client(model_name: str):
    """Build a grounded LLM client backed by the on-device Ollama model."""
    from pocketinfer.models.ollama import Ollama  # provided by the Suno Sutra platform

    model = Ollama(model_name=model_name)

    def _client(system_prompt: str, user_prompt: str) -> str:
        resp = model.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        # ollama.ChatResponse -> message.content
        try:
            return resp.message.content
        except AttributeError:
            return resp["message"]["content"]

    return _client
