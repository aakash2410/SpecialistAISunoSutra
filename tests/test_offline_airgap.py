"""Prove the whole loop works with every connection off the machine blocked."""

import base64
import os
import socket
import urllib.request

import httpx
import pytest
import requests

from conftest import FIXTURES

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCAL = ("127.0.0.1", "::1", "localhost")
MESSAGE = "External network call blocked in offline mode"


@pytest.fixture
def air_gap(monkeypatch):
    real_connect, real_lookup = socket.socket.connect, socket.getaddrinfo

    def connect(self, address):
        if isinstance(address, tuple) and address[0] not in LOCAL:
            raise RuntimeError(MESSAGE)
        return real_connect(self, address)

    def lookup(host, *args, **kwargs):
        if host not in LOCAL and host is not None:
            raise RuntimeError(MESSAGE)
        return real_lookup(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")


@pytest.mark.parametrize("call", [
    lambda: urllib.request.urlopen("http://8.8.8.8/", timeout=2),
    lambda: urllib.request.urlopen("http://example.com/", timeout=2),
    lambda: requests.get("http://8.8.8.8/", timeout=2),
    lambda: requests.get("https://huggingface.co/", timeout=2),
    lambda: httpx.get("http://8.8.8.8/", timeout=2),
    lambda: socket.create_connection(("8.8.8.8", 53), timeout=2),
])
def test_the_lock_blocks_every_way_out(air_gap, call):
    with pytest.raises(RuntimeError, match=MESSAGE):
        call()


def test_the_device_is_configured_to_stay_offline():
    with open(os.path.join(ROOT, "module/ansible/roles/specialist/templates/specialist.env.j2"), encoding="utf-8") as f:
        settings = f.read().split()
    assert "HF_HUB_OFFLINE=1" in settings and "TRANSFORMERS_OFFLINE=1" in settings
    if os.path.exists("/etc/default/specialist") and os.path.exists("/etc/nv_tegra_release"):
        with open("/etc/default/specialist", encoding="utf-8") as f:
            settings = f.read().split()
        assert "HF_HUB_OFFLINE=1" in settings and "TRANSFORMERS_OFFLINE=1" in settings


def _speech_and_translation():
    """Bhashini where it runs, otherwise stand-ins so the rest of the loop is still proven."""
    try:
        live = requests.get("http://127.0.0.1:11400/health", timeout=3).status_code == 200
    except requests.RequestException:
        live = False
    if live:
        from pocketinfer.models.nmt import Nmt
        from pocketinfer.models.tts import Tts

        nmt, tts = Nmt(), Tts()
        return (lambda text, src, tgt: nmt.infer(text, src.lower(), tgt.lower())["translated_text"],
                lambda text, lang: base64.b64decode(tts.infer(text, lang)["audio_base64"]))
    return (lambda text, src, tgt: "What is photosynthesis?" if src == "HI" else f"[hi] {text}",
            lambda text, lang: b"RIFF" + text.encode())


def test_the_whole_loop_runs_with_the_network_blocked(specialist, air_gap):
    from pocketinfer.specialist.glossary import apply_glossary, keep_terms
    from pocketinfer.specialist.page_questions import question_from_page
    from pocketinfer.specialist.session import Session, ask

    specialist.mt, specialist.tts = _speech_and_translation()
    session = Session()

    # Hindi in, through the glossary and translation, then safety, search, answer and checks.
    hindi, terms = apply_glossary("प्रकाश संश्लेषण क्या है?")
    question = keep_terms(specialist.mt(hindi, "HI", "EN"), terms)
    first = list(ask(specialist, session, question, "hi", llm=specialist.llm))
    assert first and not first[0].refused, first[0].reason
    assert first[0].audio[:4] == b"RIFF"
    assert any("ऀ" <= c <= "ॿ" or c == "[" for c in first[0].text_localized)

    # A follow-up, rewritten from memory.
    list(ask(specialist, session, "Why is it important for animals?", "hi", llm=specialist.llm))
    assert session.last_rewrite and "photosynthesis" in session.last_rewrite.lower()

    # A page, narrowed to one question.
    assert question_from_page("12\n1. What is digestion?\nFig. 2 A leaf") == "What is digestion?"


def test_reading_a_real_page_image_is_offline_too(air_gap, tesseract_ready):
    from pocketinfer.models.ocr import Ocr
    from pocketinfer.specialist.page_questions import question_from_page

    with open(os.path.join(FIXTURES, "page_question.png"), "rb") as f:
        text = Ocr().infer(f.read(), "en")["text"]
    assert question_from_page(text)
