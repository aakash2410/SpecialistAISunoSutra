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
    h.out_lang = h.turn_out = out_lang
    h.session, h.tts_provider = Session(), _FakeTTS()
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


# --- changing topic and finishing (from the device on 12 Nov: "Okay, new topic now" and
# "Okay, that's all. Thank you." were both rewritten into photosynthesis questions)
from pocketinfer.specialist.session import CLOSE_REPLY, RESET_REPLY, parse_command  # noqa: E402


@pytest.mark.parametrize("said, kind", [
    ("Okay, new topic now", "reset"), ("New topic.", "reset"), ("change the topic", "reset"),
    ("Let's talk about something else", "reset"), ("I have another question", "reset"),
    ("नया सवाल", "reset"), ("ठीक है, विषय बदलो अब", "reset"), ("चलो कुछ और", "reset"),
    ("Okay, that's all. Thank you.", "close"), ("Thank you so much", "close"), ("Stop", "close"),
    ("Thank you, bye", "close"), ("बस, धन्यवाद जी", "close"), ("बस इतना ही", "close"),
    ("Okay, repeat that please", "repeat"), ("अच्छा, दोबारा बोलो", "repeat"),
    ("okay", "ack"), ("Yes.", "ack"),
])
def test_commands_are_heard_through_filler_words(said, kind):
    assert parse_command(said)[0] == kind


@pytest.mark.parametrize("question", [
    "What makes the wind stop?", "What is a new topic?", "Tell me something else about plants",
    "So what is gravity?", "Please explain gravity", "अब प्रकाश संश्लेषण क्या है?",
    "Okay, let's end the quiz now.", "What is the use of a thermometer, please?",
])
def test_questions_with_command_words_stay_questions(question):
    assert parse_command(question)[0] is None


def test_new_topic_then_a_question_resets_and_keeps_the_question():
    assert parse_command("New topic. What is friction?") == ("reset", "What is friction")


def _quiet_harness(monkeypatch, out_lang="en"):
    from local import audio

    monkeypatch.setattr(audio, "play_wav_bytes", lambda data: None)
    h = _harness(out_lang)
    h.session.keep_last(_said("Plants make food."))
    h.session.turns.append(object())
    return h


def test_new_topic_clears_memory_and_says_so(monkeypatch):
    h = _quiet_harness(monkeypatch)
    assert h._handle_command("Okay, new topic now") is True
    assert not h.session.turns and not h.session.last_text
    assert h.tts_provider.said == [(RESET_REPLY["en"], "en")]


def test_finishing_clears_memory_and_says_goodbye_in_hindi(monkeypatch):
    h = _quiet_harness(monkeypatch, "hi")
    assert h._handle_command("बस, धन्यवाद") is True
    assert not h.session.turns
    assert h.tts_provider.said == [(CLOSE_REPLY["hi"], "hi")]


def test_okay_alone_is_skipped_but_answers_a_quiz(monkeypatch):
    h = _quiet_harness(monkeypatch)
    assert h._handle_command("Okay.") is True
    assert h.tts_provider.said == []
    h.session.pending_quiz = True
    assert h._handle_command("Okay.") is None
