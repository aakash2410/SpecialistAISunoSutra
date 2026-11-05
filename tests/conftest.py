"""Shared fixtures for the system integration and acceptance tests.

These run the real system: real ingest, real e5 retrieval, the real commands, and
whichever real services are present. A test that needs a service this machine does
not have is skipped and says which one, so a full pass needs the device install.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Import the device runtime + off-device tooling from the repo.
sys.path.insert(0, os.path.join(ROOT, "python"))
sys.path.insert(0, ROOT)

CORPUS_DIR = os.path.join(ROOT, "corpus", "diksha_g7_science")
SHIPPED_INDEX = os.path.join(CORPUS_DIR, "index")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
LLM_MODEL = os.environ.get("SPECIALIST_LLM_MODEL", "qwen2.5:1.5b")


def run_command(*args, timeout=300):
    """Run a repo command the way an operator would, and return its result."""
    return subprocess.run([sys.executable, "-W", "ignore", *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope="session")
def fixtures_dir():
    return FIXTURES


@pytest.fixture(scope="session")
def e5_ready():
    from pocketinfer.models.embed import _resolve_onnx_dir

    if not _resolve_onnx_dir():
        pytest.skip("e5 model missing. Run: python3 tools/fetch_onnx_embedder.py")


@pytest.fixture(scope="session")
def verifier():
    from pocketinfer.models.verify import Verifier, _resolve_dir

    if not _resolve_dir():
        pytest.skip("Answer verifier missing. Run: python3 tools/fetch_onnx_verifier.py")
    return Verifier()


@pytest.fixture(scope="session")
def safety_model():
    from pocketinfer.specialist.safety import load_safety_model

    try:
        return load_safety_model()
    except RuntimeError as e:
        pytest.skip(str(e))


@pytest.fixture(scope="session")
def shipped_index_dir(e5_ready):
    if not os.path.exists(os.path.join(SHIPPED_INDEX, "embeddings.npy")):
        pytest.skip("No baked index. Run: python3 ingest/build_index.py")
    return SHIPPED_INDEX


@pytest.fixture(scope="session")
def ollama_ready():
    if importlib.util.find_spec("ollama") is None:
        pytest.skip("The ollama Python client is not installed")
    try:
        models = requests.get("http://127.0.0.1:11434/api/tags", timeout=3).json()["models"]
    except requests.RequestException:
        pytest.skip("Ollama is not running")
    if LLM_MODEL not in [m["name"] for m in models]:
        pytest.skip(f"{LLM_MODEL} is not pulled")
    return LLM_MODEL


@pytest.fixture(scope="session")
def bhashini_ready():
    try:
        if requests.get("http://127.0.0.1:11400/health", timeout=3).status_code == 200:
            return True
    except requests.RequestException:
        pass
    pytest.skip("The Bhashini service is not running")


@pytest.fixture(scope="session")
def whisper_ready():
    if importlib.util.find_spec("faster_whisper") is None:
        pytest.skip("faster-whisper is not installed")


@pytest.fixture(scope="session")
def kokoro_ready():
    if importlib.util.find_spec("phonemizer") is None or importlib.util.find_spec("espeakng_loader") is None:
        pytest.skip("phonemizer or espeakng-loader is not installed")
    from pocketinfer.models.kokoro import _resolve_dir

    if not _resolve_dir():
        pytest.skip("Kokoro model missing")


@pytest.fixture(scope="session")
def tesseract_ready():
    if not shutil.which("tesseract"):
        pytest.skip("Tesseract is not installed")


@pytest.fixture(scope="session")
def jetson():
    if not os.path.exists("/etc/nv_tegra_release"):
        pytest.skip("Only meaningful on the Jetson")


@pytest.fixture
def specialist(shipped_index_dir, ollama_ready, verifier, safety_model):
    """The answer engine as the device runs it: shipped index, e5, verifier, real Qwen."""
    from pocketinfer.specialist import SpecialistEngine
    from pocketinfer.specialist.llm import ollama_grounded_client, ollama_grounded_stream_client

    return SpecialistEngine(
        index_dir=shipped_index_dir,
        llm=ollama_grounded_client(ollama_ready),
        stream_llm=ollama_grounded_stream_client(ollama_ready),
        verifier=verifier,
        safety_model=safety_model,
    )
