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


# --- switching off by voice, with a spoken yes. These must never switch off this computer:
# every test runs without SPECIALIST_ALLOW_POWEROFF, or with subprocess.run replaced.
from pocketinfer.specialist.session import (POWEROFF_ASK_REPLY, POWEROFF_CONFIRM_SECONDS,  # noqa: E402
                                            POWEROFF_FAILED_REPLY, POWEROFF_REPLY, STAY_ON_REPLY,
                                            is_confirmation)


@pytest.fixture(autouse=True)
def _never_power_off(monkeypatch):
    """No test switches the computer off, even if the shell running pytest has the opt-in."""
    import subprocess

    monkeypatch.delenv("SPECIALIST_ALLOW_POWEROFF", raising=False)

    def refuse(cmd, *args, **kwargs):
        if "poweroff" in cmd:
            raise AssertionError(f"a test tried to run {cmd}")
        return real_run(cmd, *args, **kwargs)

    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", refuse)


@pytest.mark.parametrize("said", [
    "switch off", "Switch off.", "Switch yourself off", "Turn off.", "Turn off the device", "Shut down",
    "Power off", "Okay, switch off now", "Please turn yourself off", "Can you switch off?",
    "That's all, switch off.", "बंद हो जाओ", "बंद करो", "डिवाइस बंद करो", "डिवाइस को बंद कर दो",
    "अब बंद हो जाइए जी", "स्विच ऑफ़", "स्विच ऑफ", "स्विच ऑफ़ करो", "switch off karo",
    "Switch off kar do", "band karo", "device band karo",
])
def test_switch_off_is_heard_in_english_hindi_and_hinglish(said):
    assert parse_command(said)[0] == "poweroff"


@pytest.mark.parametrize("question", [
    "What happens when you switch off the light?", "Why does the fan turn off?", "Switch off the light",
    "Turn off the tap", "How do I turn off a circuit?", "Why do plants shut down at night?",
    "What is shut down?", "पंखा बंद करो", "लाइट बंद करो", "स्विच ऑफ़ करने पर बल्ब क्यों बुझता है?",
])
def test_questions_about_switching_things_off_stay_questions(question):
    assert parse_command(question)[0] is None


@pytest.mark.parametrize("said, kind", [
    ("Stop", "close"), ("बस", "close"), ("बस करो", "close"), ("switch the topic", "reset"),
])
def test_switch_off_leaves_the_other_commands_alone(said, kind):
    assert parse_command(said)[0] == kind


def test_stop_the_quiz_is_still_for_the_quiz():
    from pocketinfer.specialist import quiz

    assert parse_command("stop the quiz")[0] is None
    assert quiz.is_stop("stop the quiz")


@pytest.mark.parametrize("said", [
    "yes", "Yes please", "Yes, please.", "yeah", "haan", "Haan ji", "हाँ", "हां", "जी हाँ", "हाँ जी",
    "ok switch off", "switch off", "हाँ, बंद करो", "Yes, switch off",
])
def test_a_yes_confirms(said):
    assert is_confirmation(said)


@pytest.mark.parametrize("said", [
    "no", "No, stay on", "okay", "नहीं", "हाँ नहीं", "yes no", "", "wait", "What is gravity?",
    "Yes, what is gravity?",
])
def test_anything_else_does_not_confirm(said):
    assert not is_confirmation(said)


def _power_harness(monkeypatch, out_lang="en"):
    h = _quiet_harness(monkeypatch, out_lang)
    now = [100.0]
    h.session.clock = lambda: now[0]
    return h, now


def _ask_then_reply(h, now, reply, wait=0.0):
    assert h._handle_command("Switch off") is True
    assert h.session.pending_poweroff is not None
    now[0] += wait
    return h._answer_poweroff(reply, now[0])


def test_switch_off_asks_first_and_a_yes_switches_off(monkeypatch):
    from local import voice_loop

    calls = []
    monkeypatch.setattr(voice_loop, "power_off", lambda: calls.append(1))  # returns None: not allowed
    h, now = _power_harness(monkeypatch)
    assert _ask_then_reply(h, now, "Yes please", wait=5) is True
    assert calls == [1]
    assert h.tts_provider.said == [(POWEROFF_ASK_REPLY["en"], "en"), (POWEROFF_REPLY["en"], "en")]
    assert h.session.pending_poweroff is None


def test_anything_else_cancels_and_is_not_answered(monkeypatch):
    from local import voice_loop

    monkeypatch.setattr(voice_loop, "power_off", lambda: pytest.fail("switched off without a yes"))
    h, now = _power_harness(monkeypatch, "hi")
    assert _ask_then_reply(h, now, "प्रकाश संश्लेषण क्या है?") is True  # True: not asked as a question
    assert h.tts_provider.said == [(POWEROFF_ASK_REPLY["hi"], "hi"), (STAY_ON_REPLY["hi"], "hi")]
    assert h.session.pending_poweroff is None
    # The next one is an ordinary turn again.
    assert h._answer_poweroff("yes", now[0]) is False


def test_a_late_yes_cancels_and_a_late_question_is_answered(monkeypatch):
    from local import voice_loop

    monkeypatch.setattr(voice_loop, "power_off", lambda: pytest.fail("switched off after the time ran out"))
    h, now = _power_harness(monkeypatch)
    assert _ask_then_reply(h, now, "yes", wait=POWEROFF_CONFIRM_SECONDS + 1) is True
    assert h.tts_provider.said[-1] == (STAY_ON_REPLY["en"], "en")

    assert _ask_then_reply(h, now, "What is friction?", wait=POWEROFF_CONFIRM_SECONDS + 1) is False
    assert h.session.pending_poweroff is None


def test_switch_off_after_the_time_ran_out_asks_again(monkeypatch):
    h, now = _power_harness(monkeypatch)
    assert _ask_then_reply(h, now, "switch off", wait=60) is False  # an ordinary turn: run_once asks again
    assert h._handle_command("switch off") is True
    assert h.session.pending_poweroff is not None


def test_reply_time_counts_from_when_recording_began(monkeypatch):
    h, now = _power_harness(monkeypatch)
    h._handle_command("Switch off")
    began = now[0] + 10
    now[0] += 40  # a slow transcription does not make the yes late
    assert h.session.answer_poweroff("yes", now=began) == "confirm"


def test_without_the_opt_in_no_command_is_run(monkeypatch, capsys):
    import subprocess

    from local import voice_loop

    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    assert voice_loop.power_off() is None
    assert ran == []
    assert "would switch off now" in capsys.readouterr().out


class _Done:
    def __init__(self, returncode=0, stderr=""):
        self.returncode, self.stderr, self.stdout = returncode, stderr, ""


def test_with_the_opt_in_systemctl_poweroff_is_run(monkeypatch):
    import subprocess

    from local import voice_loop

    ran = []
    monkeypatch.setenv("SPECIALIST_ALLOW_POWEROFF", "1")
    monkeypatch.setattr(voice_loop.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: ran.append((cmd, k)) or _Done())
    assert voice_loop.power_off() is True
    (cmd, kwargs), = ran
    assert cmd == ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "poweroff"]
    assert kwargs["timeout"] > 0


@pytest.mark.parametrize("outcome", [_Done(1, "sudo: a password is required"), None],
                         ids=["refused", "timed out"])
def test_a_failed_switch_off_is_reported_not_raised(monkeypatch, outcome):
    import subprocess

    from local import voice_loop

    def run(cmd, **kwargs):
        if outcome is None:
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return outcome

    monkeypatch.setenv("SPECIALIST_ALLOW_POWEROFF", "1")
    monkeypatch.setattr(subprocess, "run", run)
    assert voice_loop.power_off() is False


class _FakeScreen:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.calls.append((name, args))


def test_the_goodbye_is_shown_and_spoken_before_switching_off(monkeypatch):
    from local import voice_loop

    order = []
    screen = _FakeScreen()
    h, now = _power_harness(monkeypatch)
    h.screen = screen
    h.tts_provider.synthesize = lambda text, lang: order.append(("speak", text)) or b"wav"
    monkeypatch.setattr(voice_loop, "power_off", lambda: order.append(("power_off",)) or True)
    monkeypatch.setattr(voice_loop, "SHUTDOWN_WAIT_SECONDS", 0)
    h._switch_off()
    assert order[0] == ("speak", POWEROFF_REPLY["en"]) and order[1] == ("power_off",)
    names = [name for name, _ in screen.calls]
    assert names[:3] == ["switching_off", "flush", "close"]
    assert h.screen is None
    # Still running after the wait: the device says it could not switch off.
    assert order[-1] == ("speak", POWEROFF_FAILED_REPLY["en"])


def test_a_failed_switch_off_is_said_and_shown(monkeypatch):
    from local import voice_loop

    screen = _FakeScreen()
    h, now = _power_harness(monkeypatch, "hi")
    h.screen = screen
    monkeypatch.setattr(voice_loop, "power_off", lambda: False)
    h._switch_off()
    assert h.tts_provider.said[-1] == (POWEROFF_FAILED_REPLY["hi"], "hi")
    assert ("error", (POWEROFF_FAILED_REPLY["hi"], "hi")) in screen.calls
    assert h.screen is screen  # still in use: the device stays on


def test_run_once_text_turns_switch_off_then_cancel_then_yes(monkeypatch):
    """Through run_once, as typed text: the reply to the question never reaches the engine."""
    import types

    from local import voice_loop

    calls = []
    monkeypatch.setattr(voice_loop, "power_off", lambda: calls.append(1))
    h, now = _power_harness(monkeypatch)
    h.in_lang = h.turn_in = "en"
    h.args = types.SimpleNamespace(text=["x"], from_image=None)
    h._texts = ["Switch off", "What is photosynthesis?", "Switch off.", "Yes"]
    for _ in range(4):
        assert h.run_once(None) is True  # no engine: an answered question would fail here
    assert calls == [1]
    assert [text for text, _ in h.tts_provider.said] == [
        POWEROFF_ASK_REPLY["en"], STAY_ON_REPLY["en"], POWEROFF_ASK_REPLY["en"], POWEROFF_REPLY["en"]]
