"""OCR wrapper: the camera and textbook-page capture path.

Runs Tesseract on the device. The Bhashini service bundled with Suno Sutra has no
OCR endpoint, so the spec's "reuse existing OCR" could not be met as written. The
image goes in over a pipe and the text comes back over a pipe, so nothing is written
to disk and nothing leaves the device.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

# Language codes used across the Specialist -> Tesseract language packs.
TESSERACT_LANG = {"en": "eng", "hi": "hin"}


class Ocr:
    def __init__(self):
        pass

    def infer(self, image_bytes: bytes, language: str = "en") -> dict:
        """Recognize text in a JPEG/PNG image. Returns {'text': ...}."""
        lang = TESSERACT_LANG.get(language)
        if lang is None:
            raise ValueError(f"OCR does not support language '{language}'")
        # Textbook pages often mix English terms into the local language.
        packs = lang if lang == "eng" else f"{lang}+eng"
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", packs],
            input=bytes(image_bytes), capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"OCR failed: {result.stderr.decode(errors='replace')[:200]}")
        return {"text": result.stdout.decode("utf-8", errors="replace").strip()}
