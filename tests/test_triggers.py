"""The talk button: Enter in a terminal, any USB key, or a touch sensor on a GPIO pin."""

import io
import os
import struct
import subprocess
import threading
import time
import wave

import pytest

from local import audio
from local.triggers import KEY_ENTER, KEY_KPENTER, GpioTrigger, KeyTrigger, _pressed, keyboard_devices

EVENT = struct.Struct("llHHi")

DEVICES = """I: Bus=0003 Vendor=1a2c Product=4c5e Version=0110
N: Name="SEM USB Keyboard"
H: Handlers=sysrq kbd leds event2
B: EV=120013
B: KEY=1000000000007 ff9f207ac14057ff febeffdfffefffff fffffffffffffffe

I: Bus=0003 Vendor=046d Product=c077 Version=0111
N: Name="Logitech USB Optical Mouse"
H: Handlers=mouse0 event3
B: EV=17
B: KEY=1f0000 0 0 0 0

I: Bus=0003 Vendor=05ac Product=0001 Version=0111
N: Name="One key keypad"
H: Handlers=kbd event5
B: EV=3
B: KEY=1 0
"""


def test_only_devices_with_an_enter_key_are_watched(tmp_path):
    path = tmp_path / "devices"
    path.write_text(DEVICES)
    # The keypad's bitmap "1 0" sets bit 64, which is not Enter, so only the keyboard counts.
    assert keyboard_devices(str(path)) == ["/dev/input/event2"]
    path.write_text(DEVICES.replace("B: KEY=1 0", f"B: KEY=1 {1 << KEY_KPENTER - 64:x} 0"))
    assert keyboard_devices(str(path)) == ["/dev/input/event2", "/dev/input/event5"]


def _event(code, value, kind=1):
    return EVENT.pack(0, 0, kind, code, value)


def test_only_a_key_press_counts_not_a_release_or_repeat():
    assert _pressed(_event(KEY_ENTER, 1), {KEY_ENTER})
    assert not _pressed(_event(KEY_ENTER, 0) + _event(KEY_ENTER, 2), {KEY_ENTER})
    assert not _pressed(_event(30, 1), {KEY_ENTER})          # the A key
    assert not _pressed(_event(KEY_ENTER, 1, kind=4), {KEY_ENTER})  # not a key event


@pytest.fixture
def fake_keyboard(tmp_path):
    path = str(tmp_path / "event0")
    os.mkfifo(path)
    keep_open = os.open(path, os.O_RDWR)  # a writer, so reads block instead of ending
    yield path, keep_open
    os.close(keep_open)


def test_key_trigger_waits_for_enter_and_forgets_earlier_presses(fake_keyboard):
    path, writer = fake_keyboard
    os.write(writer, _event(KEY_ENTER, 1))  # pressed before anyone was waiting
    trigger = KeyTrigger(devices=[path])
    assert trigger.wait(timeout=0.3) is False

    threading.Timer(0.2, lambda: os.write(writer, _event(30, 1) + _event(KEY_KPENTER, 1))).start()
    started = time.monotonic()
    assert trigger.wait(timeout=3) is True
    assert time.monotonic() - started >= 0.15


class _Run:
    def __init__(self, version, edges=()):
        self.version, self.edges, self.calls = version, list(edges), []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, stdout=self.version)
        if not self.edges:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        time.sleep(self.edges.pop(0))
        return subprocess.CompletedProcess(cmd, 0)


@pytest.mark.parametrize("version, expected", [
    ("gpiomon (libgpiod) v1.6.3", ["gpiomon", "--num-events=1", "--rising-edge", "gpiochip0", "105"]),
    ("gpiomon (libgpiod) v2.1", ["gpiomon", "--num-events=1", "--edges=rising", "--chip", "gpiochip0", "105"]),
])
def test_gpio_trigger_uses_the_installed_libgpiod_syntax(version, expected):
    assert GpioTrigger("gpiochip0", 105, run=_Run(version)).command() == expected


def test_gpio_trigger_ignores_a_bounce_and_times_out():
    run = _Run("v1.6.3", edges=[0.0, 0.0, 0.35])
    trigger = GpioTrigger("gpiochip0", 105, run=run)
    assert trigger.wait() is True
    started = time.monotonic()
    assert trigger.wait() is True  # the second edge came at once and was a bounce
    assert time.monotonic() - started >= 0.3
    assert trigger.wait(timeout=1) is False
    assert len([c for c in run.calls if c[1] != "--version"]) == 4


def test_gpio_trigger_needs_a_chip_and_line():
    with pytest.raises(ValueError):
        GpioTrigger("", None, run=_Run("v1.6.3"))


class _Presses:
    """A trigger that is pressed after each delay in turn, then never again."""
    name = "test"

    def __init__(self, *delays):
        self.delays = list(delays)

    def wait(self, timeout=None):
        if not self.delays:
            time.sleep(timeout or 0)
            return False
        delay = self.delays.pop(0)
        if timeout is not None and delay > timeout:
            time.sleep(timeout)
            return False
        time.sleep(delay)
        return True


def test_recording_stops_on_the_next_press_but_not_a_bounce():
    mic = audio.AlsaArecordMic(trigger=_Presses(0.05, 0.6))
    started = time.monotonic()
    mic._wait_stop(started)
    assert 0.6 <= time.monotonic() - started < 1.0


def test_recording_stops_by_itself_at_the_time_limit():
    mic = audio.AlsaArecordMic(trigger=_Presses(), max_seconds=0.3)
    started = time.monotonic()
    mic._wait_stop(started)
    assert 0.3 <= time.monotonic() - started < 0.6


def test_beeps_only_with_a_hardware_trigger():
    assert audio.AlsaArecordMic().cues is False
    assert audio.AlsaArecordMic(trigger=_Presses()).cues is True


def test_beep_is_a_short_valid_wav():
    with wave.open(io.BytesIO(audio.beep_wav(audio.START_TONE))) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        assert w.getnframes() / w.getframerate() == pytest.approx(0.15, abs=0.01)
