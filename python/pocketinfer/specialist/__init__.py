"""The Specialist — on-device runtime subpackage.

A grounded, offline subject expert built on Suno Sutra. The embedding model and
OCR wrapper live under ``pocketinfer.models`` (so the application manifest's
dependency verification resolves them like any other model); the retrieval,
grounding, and orchestration live here.
"""

from ..models.embed import Embed
from ..models.ocr import Ocr
from .vector_index import VectorIndex, Retrieved
from .grounding import build_grounding, GroundingResult, REFUSAL_TEXT
from .pipeline import SpecialistEngine, AnswerResult, extractive_llm
from .llm import ollama_grounded_client

__all__ = [
    "Embed",
    "Ocr",
    "VectorIndex",
    "Retrieved",
    "build_grounding",
    "GroundingResult",
    "REFUSAL_TEXT",
    "SpecialistEngine",
    "AnswerResult",
    "extractive_llm",
    "ollama_grounded_client",
]
