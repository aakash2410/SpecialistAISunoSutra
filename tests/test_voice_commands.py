"""Teacher voice commands: "repeat that" and "slower" replay the last answer without asking again."""

import io
import shutil
import wave

import pytest

from pocketinfer.specialist.pipeline import SentenceResult
from pocketinfer.specialist.session import Session


@pytest.mark.parametrize("said, command", [
    ("Repeat that.", "repeat"), ("please say it again", "repeat"), ("Again?", "repeat"),
    ("Tell me again", "repeat"), ("दोबारा बोलो", "repeat"), ("फिर से बताइए।", "repeat"), ("दोहराइए", "repeat"),
    ("Slower please", "slower"), ("Say that more slowly.", "slower"), ("Speak slowly", "slower"),
    ("धीरे बोलो", "slower"), ("धीरे से बताइए", "slower"),
])
def test_whole_utterance_commands_are_recognised(said, command):
    assert Session.command(said) == command


@pytest.mark.parametrize("question", [
    "Why is the sky blue again?", "Repeat the steps of photosynthesis", "What is a repeating decimal?",
    "Why do slow loris move slowly?", "फिर से उगने वाले पौधे कौन से हैं?", "What is photosynthesis?",
])
def test_real_questions_are_not_taken_as_commands(question):
    assert Session.command(question) is None


def _said(*texts):
    return [SentenceResult(index=i, text_en=t, text_localized="", audio=t.encode()) for i, t in enumerate(texts)]


def test_last_answer_is_kept_and_cleared_with_the_rest_of_memory():
    now = [0.0]
    session = Session(clock=lambda: now[0])
    session.keep_last(_said("Plants make food.", "They use sunlight."))
    assert session.last_text == ["Plants make food.", "They use sunlight."]
    assert session.last_audio == [b"Plants make food.", b"They use sunlight."]

    session.clear()
    assert not session.last_text and not session.last_audio

    session.keep_last(_said("Plants make food."))
    now[0] += 181
    session.expire_if_idle()
    assert not session.last_text


def test_an_empty_answer_does_not_wipe_what_can_be_repeated():
    session = Session()
    session.keep_last(_said("Plants make food."))
    session.keep_last([])
    assert session.last_text == ["Plants make food."]


def _tone(seconds=1.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(b"\x00\x10" * int(seconds * rate))
    return buf.getvalue()


def _seconds(data):
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def test_slow_audio_falls_back_to_normal_speed_without_ffmpeg(monkeypatch):
    from local import audio

    monkeypatch.setenv("PATH", "")
    clip = _tone()
    assert audio.slow_wav_bytes(clip) == clip


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg, installed on the device")
def test_slow_audio_is_longer():
    from local.audio import SLOW_TEMPO, slow_wav_bytes

    assert _seconds(slow_wav_bytes(_tone(2.0))) == pytest.approx(2.0 / SLOW_TEMPO, rel=0.05)


class _FakeTTS:
    def __init__(self):
        self.said = []

    def synthesize(self, text, language):
        self.said.append((text, language))
        return b"wav"


def _harness(out_lang="en"):
    from local.voice_loop import LoopHarness

    h = LoopHarness.__new__(LoopHarness)
    h.out_lang, h.session, h.tts_provider = out_lang, Session(), _FakeTTS()
    return h


def test_replay_plays_the_saved_audio_without_asking_again(monkeypatch):
    from local import audio

    played = []
    monkeypatch.setattr(audio, "play_wav_bytes", played.append)
    monkeypatch.setattr(audio, "slow_wav_bytes", lambda data: b"slow:" + data)
    monkeypatch.setattr(audio, "SLOW_GAP_SECONDS", 0)
    h = _harness()
    h.session.keep_last(_said("Plants make food.", "They use sunlight."))

    h._replay("repeat")
    h._replay("slower")
    assert played == [b"Plants make food.", b"They use sunlight.",
                      b"slow:Plants make food.", b"slow:They use sunlight."]
    assert h.tts_provider.said == []


def test_replay_with_nothing_said_yet_says_so_in_the_output_language(monkeypatch):
    from local import audio

    monkeypatch.setattr(audio, "play_wav_bytes", lambda data: None)
    h = _harness("hi")
    h._replay("repeat")
    assert h.tts_provider.said == [(h.NOTHING_TO_REPEAT["hi"], "hi")]
