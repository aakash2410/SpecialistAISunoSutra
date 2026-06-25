"""OCR wrapper — the camera/textbook-page capture path.

Mirrors the existing BHASHINI model wrappers (``Asr``/``Nmt``/``Tts``): it posts
to the local BHASHINI model service on port 11400 and returns recognized text.
The teacher can point the camera at a textbook page; the recognized text becomes
(or augments) the request that gets embedded and retrieved against.

The stock Suno Sutra image already ships BHASHINI ASR/NMT/TTS on this service;
OCR is declared "reuse (existing)" in the spec and is expected on the same
service under ``/ocr``.
"""

from __future__ import annotations

import base64
import logging
import time
from subprocess import check_output

import requests

logger = logging.getLogger(__name__)

BHASHINI_BASE = "http://localhost:11400"


class Ocr:
    def __init__(self):
        pass

    def infer(self, image_bytes: bytes, language: str = "en") -> dict:
        """Recognize text in a JPEG/PNG image. Returns {'text': ...}."""
        image_base64 = base64.b64encode(bytes(image_bytes)).decode("utf-8")
        payload = {"language": language, "image_base64": image_base64}
        response = requests.post(f"{BHASHINI_BASE}/ocr", json=payload, timeout=30)
        if response.status_code == 200:
            return response.json()
        raise RuntimeError(f"OCR inference failed: {response.text}")

    @classmethod
    def verify(cls, args):
        try:
            response = requests.get(f"{BHASHINI_BASE}/health")
            if response.status_code == 200:
                return True, "OCR service is available."
        except requests.exceptions.ConnectionError:
            logger.info("Connection error, trying to launch BHASHINI model service")
        check_output("systemctl restart bhashini_models.service", shell=True)
        start = time.time()
        while time.time() - start < 60.0:
            try:
                response = requests.get(f"{BHASHINI_BASE}/health")
                if response.status_code == 200:
                    return True, "OCR service is available."
            except requests.exceptions.ConnectionError:
                pass
            time.sleep(0.25)
        return False, "OCR service did not become available."

    @classmethod
    def update(cls, args):
        return True, "OK"
