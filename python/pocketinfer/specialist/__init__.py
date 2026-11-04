"""The Specialist — on-device runtime subpackage.

A grounded, offline subject expert built on Suno Sutra. The embedding model, verifier
and OCR wrapper live under ``pocketinfer.models``; the retrieval, grounding, and
orchestration live here.
"""

from ..models.embed import Embed
from ..models.ocr import Ocr
from ..models.verify import Verifier
from .vector_index import VectorIndex, Retrieved
from .grounding import build_grounding, GroundingResult, REFUSAL_TEXT
from .pipeline import SpecialistEngine, AnswerResult, SentenceResult, extractive_llm, speak_stream
from .llm import ollama_grounded_client, ollama_grounded_stream_client

__all__ = [
    "Embed",
    "Ocr",
    "Verifier",
    "VectorIndex",
    "Retrieved",
    "build_grounding",
    "GroundingResult",
    "REFUSAL_TEXT",
    "SpecialistEngine",
    "AnswerResult",
    "SentenceResult",
    "speak_stream",
    "extractive_llm",
    "ollama_grounded_client",
    "ollama_grounded_stream_client",
]
