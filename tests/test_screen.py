"""The 2.8 inch screen, without the hardware: drawing, taps, joined triggers, SPI layout."""

import struct
import threading
import time

import pytest

pytest.importorskip("PIL")

from local import screen  # noqa: E402
from local.triggers import AnyTrigger, make_trigger  # noqa: E402


class FakePanel:
    width, height = 320, 240

    def __init__(self):
        self.frames = []

    def show(self, image):
        self.frames.append(image)

    def close(self):
        pass


class FakeTouch:
    """Pressed for the readings listed, then not."""

    def __init__(self, presses):
        self.presses = list(presses)

    def pressed(self):
        return self.presses.pop(0) if self.presses else False

    def close(self):
        pass


def test_spi_message_matches_the_kernel_struct():
    # struct spi_ioc_transfer is 32 bytes; SPI_IOC_MESSAGE(1) encodes that size.
    assert struct.calcsize("QQIIHBBBBBB") == 32
    assert (screen._SPI_IOC_MESSAGE_1 >> 16) & 0x3FFF == 32


def test_each_state_draws_a_full_frame():
    fonts = screen.Fonts()
    for state in screen.LABELS:
        image = screen.render(fonts, (320, 240), state, "en", "What is a leaf?",
                              [("A leaf makes food.", False)])
        assert image.size == (320, 240)


def test_a_long_answer_keeps_the_newest_sentence_in_view():
    fonts = screen.Fonts()
    sentences = [(f"Sentence number {i} about plants and their leaves.", False) for i in range(30)]
    sentences[-1] = ("LAST", False)
    image = screen.render(fonts, (320, 240), "answer", "en", "Q", sentences)
    # The bottom half has text drawn on it, so the last lines were kept, not the first.
    bottom = image.crop((0, 140, 320, 240)).convert("L")
    assert max(bottom.getdata()) > 100


def test_screen_ui_draws_on_its_own_thread_and_flushes_on_close():
    panel = FakePanel()
    ui = screen.ScreenUI(panel, touch=None)
    ui.listening()
    ui.heard("What is photosynthesis?")
    ui.sentence("Plants make food from sunlight.")
    ui.close()
    assert panel.frames, "nothing was drawn"
    assert ui.state == "answer"
    assert ui.answer == [("Plants make food from sunlight.", False)]


def test_ready_keeps_the_last_answer_and_listening_clears_it():
    ui = screen.ScreenUI(FakePanel(), touch=None)
    ui.sentence("Roots take in water.")
    ui.ready()
    assert ui.answer and ui.note == ""
    ui.listening()
    assert ui.answer == [] and ui.question == ""
    ui.close()


def test_a_tap_counts_once_and_a_held_finger_is_not_a_new_tap():
    touch = FakeTouch([True, True, False, False, True, True])
    trigger = screen.TouchTrigger(touch)
    trigger.POLL_SECONDS = 0
    # Already down when watching starts: wait for it to lift, then the next press.
    assert trigger.wait(timeout=1.0) is True
    assert touch.presses == [True]
    assert trigger.wait(timeout=0.05) is False  # still held: not a new tap


class FakeTrigger:
    def __init__(self, name, fire_after=None):
        self.name, self.fire_after = name, fire_after
        self.started = time.monotonic()
        self.fired = False

    def wait(self, timeout=None):
        if self.fire_after is not None and not self.fired and time.monotonic() - self.started >= self.fire_after:
            self.fired = True
            return True
        time.sleep(min(timeout or 0.01, 0.01))
        return False


def test_any_trigger_returns_when_either_button_is_pressed():
    trigger = AnyTrigger([FakeTrigger("gpio"), FakeTrigger("screen", fire_after=0.05)])
    assert trigger.name == "gpio,screen"
    assert trigger.wait(timeout=2.0) is True


def test_any_trigger_times_out_with_no_press():
    trigger = AnyTrigger([FakeTrigger("gpio"), FakeTrigger("screen")])
    assert trigger.wait(timeout=0.1) is False


def test_screen_trigger_needs_the_screen_and_a_joined_list_skips_it():
    with pytest.raises(RuntimeError):
        make_trigger("screen")
    # key needs no hardware to construct, so key,screen falls back to key alone.
    assert make_trigger("key,screen").name == "key"


def test_switching_off_shows_the_goodbye_and_flush_waits_for_it():
    panel = FakePanel()
    ui = screen.ScreenUI(panel, touch=None)
    ui.heard("Yes")
    ui.switching_off("Switching off. Wait for the fan to stop before you unplug me.")
    assert ui.flush(timeout=3.0)
    assert ui.state == "poweroff" and ui.question == ""
    assert ui.answer == [("Switching off. Wait for the fan to stop before you unplug me.", False)]
    drawn = len(panel.frames)
    ui.close()
    assert drawn and panel.frames, "the goodbye was not drawn"
    assert screen.LABELS["poweroff"]["hi"] and screen.BAR["poweroff"]


def test_an_error_can_say_what_went_wrong():
    ui = screen.ScreenUI(FakePanel(), touch=None)
    ui.sentence("Roots take in water.")
    ui.error()
    assert ui.state == "error" and ui.answer  # the answer stays when nothing is said
    ui.error("Sorry, I could not switch off. I'll stay on.")
    assert ui.answer == [("Sorry, I could not switch off. I'll stay on.", False)]
    ui.close()
