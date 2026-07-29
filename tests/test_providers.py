"""Provider contracts + laptop providers — the swappability layer.

Contract tests only depend on the stdlib-level Protocols; backend-specific tests
skip when their dependency/platform is absent, so the file is green everywhere.
"""

import sys

import pytest

from pocketinfer.specialist.providers import (
    AudioClip,
    ASRProvider,
    MTProvider,
    TTSProvider,
)


# ---------------------------------------------------------------- AudioClip
def test_audioclip_wav_roundtrip():
    pcm = bytes(range(256)) * 8
    clip = AudioClip(pcm=pcm, sample_rate=16000, sample_width=2, channels=1)
    wav = clip.get_wav_data()
    assert wav[:4] == b"RIFF"
    back = AudioClip.from_wav_bytes(wav)
    assert back.pcm == pcm
    assert back.sample_rate == 16000
    assert back.channels == 1


def test_audioclip_duration():
    # 16000 samples * 2 bytes = 1 second at 16 kHz mono
    clip = AudioClip(pcm=b"\x00\x00" * 16000, sample_rate=16000)
    assert clip.duration_seconds == pytest.approx(1.0, abs=1e-3)


# ------------------------------------------------------- contract conformance
def test_noop_mt_conforms_and_passes_through():
    from local.providers import NoopMT

    mt = NoopMT()
    assert isinstance(mt, MTProvider)          # runtime_checkable Protocol
    assert mt.translate("hello", "en", "hi") == "hello"
    assert mt.name == "none"


def test_factories_reject_unknown_names():
    from local.providers import make_asr, make_mt, make_tts

    for make in (make_asr, make_mt, make_tts):
        with pytest.raises(ValueError):
            make("does-not-exist")


def test_llm_factory_returns_callable():
    from local.providers import make_llm

    llm = make_llm("extractive")
    assert callable(llm)


# ----------------------------------------------------- backend-specific (skips)
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS 'say' TTS only")
def test_mac_say_tts_conforms_and_synthesizes():
    from local.providers import MacSayTTS

    tts = MacSayTTS()
    assert isinstance(tts, TTSProvider)
    wav = tts.synthesize("test one two", "en")
    assert wav and wav[:4] == b"RIFF"


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("vosk") is None,
    reason="vosk not installed",
)
def test_vosk_asr_conforms_when_model_present():
    from local.providers import VoskASR

    try:
        asr = VoskASR()
    except RuntimeError as e:
        pytest.skip(f"vosk model not present: {e}")
    assert isinstance(asr, ASRProvider)
