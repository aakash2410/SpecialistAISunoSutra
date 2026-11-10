"""What starts and stops a recording: Enter in a terminal, a USB key, or a touch sensor.

The device runs as a service with no terminal, so it cannot wait for Enter. Two other
triggers work without one:

  * ``key``: any USB keyboard or one-key keypad. Enter (or keypad Enter) starts and stops
    recording. It reads the Linux input devices directly, so the user needs the
    ``input`` group.
  * ``gpio``: a touch sensor such as a TTP223 on the 40-pin header. Its signal goes high
    while touched. It uses the libgpiod tools (``gpiomon``), so the user needs access to
    ``/dev/gpiochip*``. Power the sensor from 3.3 V, never 5 V.

Each trigger has ``wait(timeout=None) -> bool``: it blocks until the next press and returns
True, or returns False if ``timeout`` seconds pass first.
"""

from __future__ import annotations

import logging
import os
import re
import select
import shutil
import struct
import subprocess
import time
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

# Linux input events: struct timeval (two longs), then type, code and value.
_EVENT = struct.Struct("llHHi")
_EV_KEY = 1
KEY_ENTER, KEY_KPENTER = 28, 96


class EnterTrigger:
    """Press Enter in the terminal. Used when someone runs the loop by hand."""

    name = "enter"

    def __init__(self, prompt: str = ""):
        self.prompt = prompt

    def wait(self, timeout: Optional[float] = None) -> bool:
        input(self.prompt)
        return True


def keyboard_devices(devices_file: str = "/proc/bus/input/devices") -> List[str]:
    """The /dev/input/event* paths of every device that has an Enter key."""
    try:
        with open(devices_file, encoding="utf-8") as f:
            blocks = f.read().split("\n\n")
    except OSError:
        return []
    paths = []
    for block in blocks:
        handlers = re.search(r"^H: Handlers=(.*)$", block, re.MULTILINE)
        keys = re.search(r"^B: KEY=(.*)$", block, re.MULTILINE)
        if not handlers or not keys:
            continue
        # KEY= is a bitmap printed as hex words, most significant first.
        bits = int("".join(word.zfill(16) for word in keys.group(1).split()), 16)
        if not any(bits >> code & 1 for code in (KEY_ENTER, KEY_KPENTER)):
            continue
        for handler in handlers.group(1).split():
            if handler.startswith("event"):
                paths.append(f"/dev/input/{handler}")
    return paths


class KeyTrigger:
    """Press Enter on any USB keyboard or keypad, without a terminal."""

    name = "key"

    def __init__(self, codes: Iterable[int] = (KEY_ENTER, KEY_KPENTER), devices: Optional[List[str]] = None):
        self.codes = set(codes)
        self.devices = devices

    def _open(self) -> List[int]:
        fds = []
        for path in self.devices or keyboard_devices():
            try:
                fds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
            except OSError as e:
                logger.warning("cannot read %s (%s). Is the user in the input group?", path, e)
        return fds

    def wait(self, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            fds = self._open()
            if not fds:
                # No keyboard plugged in yet: look again shortly, so plugging one in works.
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                time.sleep(1.0)
                continue
            try:
                for fd in fds:  # forget presses made before we started waiting
                    _drain(fd)
                while True:
                    left = None if deadline is None else max(0.0, deadline - time.monotonic())
                    ready, _, _ = select.select(fds, [], [], left)
                    if not ready:
                        return False
                    for fd in ready:
                        try:
                            data = os.read(fd, _EVENT.size * 64)
                        except OSError:  # unplugged
                            data = b""
                        if not data:
                            raise _Unplugged
                        if _pressed(data, self.codes):
                            return True
            except _Unplugged:
                time.sleep(0.2)
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                continue
            finally:
                for fd in fds:
                    os.close(fd)


class _Unplugged(Exception):
    pass


def _drain(fd: int) -> None:
    try:
        while os.read(fd, _EVENT.size * 64):
            pass
    except (BlockingIOError, OSError):
        pass


def _pressed(data: bytes, codes) -> bool:
    for i in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
        _, _, kind, code, value = _EVENT.unpack_from(data, i)
        if kind == _EV_KEY and code in codes and value == 1:  # 1 is key down, 2 is auto-repeat
            return True
    return False


class GpioTrigger:
    """Touch a sensor wired to a GPIO line. Find the chip and line with ``gpioinfo``."""

    name = "gpio"
    DEBOUNCE_SECONDS = 0.3

    def __init__(self, chip: str, line: int, run=subprocess.run):
        if not chip or line is None:
            raise ValueError("the gpio trigger needs a chip and a line, e.g. --gpio-chip gpiochip0 --gpio-line 105")
        self.chip, self.line, self._run = chip, int(line), run
        self._v2 = self._is_libgpiod_v2()
        self._last = 0.0

    def _is_libgpiod_v2(self) -> bool:
        try:
            out = self._run(["gpiomon", "--version"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return bool(re.search(r"v?2\.\d", out or ""))

    def command(self) -> List[str]:
        if self._v2:
            return ["gpiomon", "--num-events=1", "--edges=rising", "--chip", self.chip, str(self.line)]
        return ["gpiomon", "--num-events=1", "--rising-edge", self.chip, str(self.line)]

    def wait(self, timeout: Optional[float] = None) -> bool:
        while True:
            try:
                self._run(self.command(), capture_output=True, timeout=timeout, check=True)
            except subprocess.TimeoutExpired:
                return False
            now = time.monotonic()
            if now - self._last >= self.DEBOUNCE_SECONDS:  # one touch can bounce
                self._last = now
                return True


def make_trigger(name: str, gpio_chip: Optional[str] = None, gpio_line: Optional[int] = None, prompt: str = ""):
    if name == "enter":
        return EnterTrigger(prompt)
    if name == "key":
        return KeyTrigger()
    if name == "gpio":
        if shutil.which("gpiomon") is None:
            raise RuntimeError("gpiomon not found. Install the gpiod package.")
        return GpioTrigger(gpio_chip, gpio_line)
    raise ValueError(f"unknown trigger {name!r}: use enter, key or gpio")
