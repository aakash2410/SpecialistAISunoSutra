"""The 2.8 inch touch screen: shows what the device is doing, and works as a talk button.

The screen is a Waveshare 2.8 inch resistive touch LCD (ST7789 display, XPT2046 touch),
320 x 240, on spi1 of the Jetson 40-pin header:

    5V 2, GND 6, MOSI 19, MISO 21, SCLK 23, LCD_CS 24, TP_CS 26,
    LCD_DC 29, LCD_RST 31, LCD_BL 32, TP_IRQ 33 (not used)

Two setup steps on the Jetson, both done by the install with -e specialist_screen=true:
spi1 turned on with jetson-io, and pins 29, 31 and 32 allowed to drive (JetPack 6 leaves
them off; see tools/screen_pins.sh).

It shows a coloured bar with the state (tap to ask, listening, thinking, answer, switching
off) and, below it, the question as heard and the answer as it is spoken. The answer stays on the
screen until the next question, so a teacher can read it again. Tapping anywhere starts
and stops recording, like the talk button.

The screen is a backup: if it is missing or fails, the voice loop carries on without it.

By hand, with the service stopped:
    python -m local.screen demo        draw each state
    python -m local.screen touch       print where each press lands
    python -m local.screen calibrate   press four crosses, so presses map to pixels
"""

from __future__ import annotations

import ctypes
import fcntl
import json
import logging
import os
import queue
import struct
import sys
import threading
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Header pins (BOARD numbering) and SPI devices.
DC, RST, BL = 29, 31, 32
LCD_DEVICE, TOUCH_DEVICE = "/dev/spidev0.0", "/dev/spidev0.1"
LCD_SPEED, TOUCH_SPEED = 16_000_000, 1_000_000

# ST7789 memory access settings for each rotation. 90 and 270 are landscape, 320 x 240.
MADCTL = {0: 0x00, 90: 0x60, 180: 0xC0, 270: 0xA0}

# Touch: Z1 under 20 untouched, 113 and up for a press, measured on 12 Nov.
PRESS_Z1 = 60

CALIBRATION_FILE = "screen_calibration.json"


class ScreenUnavailable(RuntimeError):
    pass


# ----------------------------------------------------------------------- SPI, no build
# The spidev package only ships as source, which needs Python headers to build on the
# device. These few ioctls from linux/spi/spidev.h do the same.
_SPI_IOC_MESSAGE_1 = 0x40206B00
_SPI_IOC_WR_MODE = 0x40016B01
_SPI_IOC_WR_BITS_PER_WORD = 0x40016B03
_SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04
_SPI_CHUNK = 4096  # the spidev driver's default buffer size


class Spi:
    def __init__(self, path: str, speed_hz: int):
        self.speed = speed_hz
        self.fd = os.open(path, os.O_RDWR)
        fcntl.ioctl(self.fd, _SPI_IOC_WR_MODE, struct.pack("B", 0))
        fcntl.ioctl(self.fd, _SPI_IOC_WR_BITS_PER_WORD, struct.pack("B", 8))
        fcntl.ioctl(self.fd, _SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("I", speed_hz))

    def transfer(self, data: bytes, read: bool = False) -> bytes:
        """Send data with chip select held low; return what came back if read."""
        tx = ctypes.create_string_buffer(bytes(data), len(data))
        rx = ctypes.create_string_buffer(len(data)) if read else None
        # struct spi_ioc_transfer: tx_buf, rx_buf, len, speed_hz, delay_usecs,
        # bits_per_word, cs_change, tx_nbits, rx_nbits, word_delay_usecs, pad.
        msg = struct.pack("QQIIHBBBBBB", ctypes.addressof(tx), ctypes.addressof(rx) if rx else 0,
                          len(data), self.speed, 0, 8, 0, 0, 0, 0, 0)
        fcntl.ioctl(self.fd, _SPI_IOC_MESSAGE_1, msg)
        return rx.raw if rx else b""

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        for i in range(0, len(view), _SPI_CHUNK):
            self.transfer(view[i:i + _SPI_CHUNK].tobytes())

    def close(self) -> None:
        os.close(self.fd)


# ------------------------------------------------------------------------- the display
class Panel:
    """The ST7789 display. Draws whole PIL images."""

    def __init__(self, rotation: int = 90):
        try:
            import Jetson.GPIO as GPIO
        except Exception as e:  # noqa: BLE001  (not a Jetson, or no access)
            raise ScreenUnavailable(f"Jetson.GPIO: {e}") from e
        if rotation not in MADCTL:
            raise ValueError("rotation must be 0, 90, 180 or 270")
        self.gpio = GPIO
        self.rotation = rotation
        self.width, self.height = (320, 240) if rotation in (90, 270) else (240, 320)
        try:
            self.spi = Spi(LCD_DEVICE, LCD_SPEED)
        except OSError as e:
            raise ScreenUnavailable(f"{LCD_DEVICE}: {e}. Is spi1 on, and the user in the gpio group?") from e
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup([DC, RST, BL], GPIO.OUT, initial=GPIO.HIGH)
        self._init()

    def _command(self, cmd: int, *data: int) -> None:
        self.gpio.output(DC, self.gpio.LOW)
        self.spi.transfer(bytes([cmd]))
        if data:
            self.gpio.output(DC, self.gpio.HIGH)
            self.spi.transfer(bytes(data))

    def _init(self) -> None:
        g = self.gpio
        g.output(RST, g.LOW); time.sleep(0.02)
        g.output(RST, g.HIGH); time.sleep(0.15)
        self._command(0x01); time.sleep(0.15)        # software reset
        self._command(0x11); time.sleep(0.12)        # wake
        self._command(0x3A, 0x55)                    # 16-bit colour
        self._command(0x36, MADCTL[self.rotation])
        self._command(0x21)                          # IPS panel: inversion on
        self._command(0x13)
        self._command(0x29); time.sleep(0.05)        # display on

    def show(self, image) -> None:
        import numpy as np

        a = np.asarray(image.convert("RGB"), dtype=np.uint16)
        rgb565 = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
        w, h = self.width - 1, self.height - 1
        self._command(0x2A, 0, 0, w >> 8, w & 0xFF)
        self._command(0x2B, 0, 0, h >> 8, h & 0xFF)
        self._command(0x2C)
        self.gpio.output(DC, self.gpio.HIGH)
        self.spi.write(rgb565.astype(">u2").tobytes())

    def backlight(self, on: bool) -> None:
        self.gpio.output(BL, self.gpio.HIGH if on else self.gpio.LOW)

    def close(self) -> None:
        self.spi.close()


# --------------------------------------------------------------------------- the touch
class Touch:
    """The XPT2046 touch chip. Reads presses by pressure, so TP_IRQ is not needed."""

    def __init__(self, calibration: Optional[dict] = None):
        try:
            self.spi = Spi(TOUCH_DEVICE, TOUCH_SPEED)
        except OSError as e:
            raise ScreenUnavailable(f"{TOUCH_DEVICE}: {e}") from e
        self.matrix = (calibration or {}).get("matrix")

    def _axis(self, cmd: int) -> int:
        r = self.spi.transfer(bytes([cmd, 0, 0]), read=True)
        return ((r[1] << 8) | r[2]) >> 3

    def raw(self) -> Optional[Tuple[int, int]]:
        """The raw position while pressed, averaged over a few reads, else None."""
        if self._axis(0xB0) < PRESS_Z1:
            return None
        xs, ys = [], []
        for _ in range(4):
            xs.append(self._axis(0xD0))
            ys.append(self._axis(0x90))
        if self._axis(0xB0) < PRESS_Z1:  # let go while reading
            return None
        return sum(xs) // 4, sum(ys) // 4

    def pressed(self) -> bool:
        return self._axis(0xB0) >= PRESS_Z1

    def point(self) -> Optional[Tuple[int, int]]:
        """The pressed pixel, if calibrated; the raw reading otherwise."""
        raw = self.raw()
        if raw is None or not self.matrix:
            return raw
        (a, b, c), (d, e, f) = self.matrix
        x, y = raw
        return round(a * x + b * y + c), round(d * x + e * y + f)

    def close(self) -> None:
        self.spi.close()


class TouchTrigger:
    """Tap anywhere on the screen: the talk button, for local.triggers."""

    name = "screen"
    POLL_SECONDS = 0.05

    def __init__(self, touch: Touch):
        self.touch = touch
        self._was_pressed = True  # a finger already down at start is not a new tap

    def wait(self, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while deadline is None or time.monotonic() < deadline:
            down = self.touch.pressed()
            new_tap = down and not self._was_pressed
            self._was_pressed = down
            if new_tap:
                return True
            time.sleep(self.POLL_SECONDS)
        return False


# ----------------------------------------------------------------------------- drawing
LATIN_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
               "/usr/share/fonts/truetype/freefont/FreeSans.ttf"]
DEVANAGARI_FONTS = ["/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
                    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
                    "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Regular.ttf",
                    "/usr/share/fonts/truetype/fonts-deva-extra/gargi.ttf"]

BACKGROUND = (12, 14, 20)
TEXT, QUESTION, EXAMPLE = (240, 240, 240), (150, 160, 175), (120, 210, 230)
BAR = {"ready": (35, 95, 200), "listening": (200, 45, 45), "thinking": (215, 145, 25),
       "answer": (35, 140, 75), "refused": (95, 95, 105), "error": (150, 30, 30),
       "poweroff": (110, 60, 160)}
LABELS = {
    "ready":     {"en": "Tap to ask",             "hi": "पूछने के लिए छुएँ"},
    "ready_key": {"en": "Press the button to ask", "hi": "पूछने के लिए बटन दबाएँ"},
    "listening": {"en": "Listening... tap to stop", "hi": "सुन रहा है... रोकने के लिए छुएँ"},
    "thinking":  {"en": "Thinking...",            "hi": "सोच रहा है..."},
    "answer":    {"en": "Answer",                 "hi": "उत्तर"},
    "refused":   {"en": "Not in the textbooks",   "hi": "किताबों में नहीं है"},
    "error":     {"en": "Something went wrong. Ask again", "hi": "कुछ गड़बड़ हुई। फिर से पूछिए"},
    "poweroff":  {"en": "Switching off",          "hi": "बंद हो रहा है"},
}


def _has_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in text)


class Fonts:
    def __init__(self):
        from PIL import ImageFont, features

        self.ImageFont = ImageFont
        # Pillow 9.1 renamed LAYOUT_RAQM to Layout.RAQM; JetPack's system Pillow is 9.0.
        layout = getattr(ImageFont, "Layout", None)
        raqm = layout.RAQM if layout else ImageFont.LAYOUT_RAQM
        basic = layout.BASIC if layout else ImageFont.LAYOUT_BASIC
        self.layout = raqm if features.check("raqm") else basic
        if self.layout != raqm:
            logger.warning("Pillow has no raqm, so Hindi letters may join wrongly on the screen")
        self.latin = next((p for p in LATIN_FONTS if os.path.exists(p)), None)
        self.deva = next((p for p in DEVANAGARI_FONTS if os.path.exists(p)), None)
        if not self.deva:
            logger.warning("no Devanagari font found; install fonts-lohit-deva")
        if not self.latin:
            logger.warning("no TrueType font found; install fonts-dejavu-core")
        self._cache = {}

    def clean(self, text: str) -> str:
        """Pillow's built-in bitmap font, the last fallback, only has Latin-1 letters."""
        if self.latin or (self.deva and _has_devanagari(text)):
            return text
        return text.encode("latin-1", "replace").decode("latin-1")

    def get(self, text: str, size: int):
        path = self.deva if _has_devanagari(text) and self.deva else self.latin
        key = (path, size)
        if key not in self._cache:
            if path:
                font = self.ImageFont.truetype(path, size, layout_engine=self.layout)
            else:
                try:
                    font = self.ImageFont.load_default(size)
                except TypeError:  # Pillow before 10.1 has one fixed-size default font
                    font = self.ImageFont.load_default()
            self._cache[key] = font
        return self._cache[key]


def wrap(draw, text: str, font, width: int) -> List[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def render(fonts: Fonts, size: Tuple[int, int], state: str, lang: str,
           question: str, answer: List[Tuple[str, bool]], note: str = ""):
    """One frame: the state bar, then the question and the answer, newest kept in view."""
    from PIL import Image, ImageDraw

    width, height = size
    image = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(image)
    pad, bar_h = 8, 34

    clean = fonts.clean
    question, note = clean(question), clean(note)
    answer = [(clean(text), is_example) for text, is_example in answer]
    label = clean(LABELS[state].get(lang) or LABELS[state]["en"])
    draw.rectangle([0, 0, width, bar_h], fill=BAR.get(state.split("_")[0], BAR["ready"]))
    bar_font = fonts.get(label, 18)
    draw.text((pad, bar_h // 2), label, font=bar_font, fill=(255, 255, 255), anchor="lm")

    # (text, colour, size) lines, then keep the newest that fit.
    rows = []
    if note:
        for line in wrap(draw, note, fonts.get(note, 20), width - 2 * pad):
            rows.append((line, TEXT, 20))
    if question:
        for line in wrap(draw, question, fonts.get(question, 15), width - 2 * pad)[:2]:
            rows.append((line, QUESTION, 15))
    for sentence, is_example in answer:
        for line in wrap(draw, sentence, fonts.get(sentence, 18), width - 2 * pad):
            rows.append((line, EXAMPLE if is_example else TEXT, 18))

    # Too long: drop lines from the top (the question first, then the oldest answer lines),
    # so the sentence being spoken is always in view.
    step = 7
    room = height - bar_h - pad
    heights = [size_ + step for _, _, size_ in rows]
    start = 0
    while sum(heights[start:]) > room and start < len(rows) - 1:
        start += 1
    y = bar_h + pad
    for text, colour, size_ in rows[start:]:
        draw.text((pad, y), text, font=fonts.get(text, size_), fill=colour)
        y += size_ + step
    return image


# ------------------------------------------------------------------- what the loop uses
class ScreenUI:
    """What the voice loop calls. Drawing happens on its own thread, newest state first,
    so a slow frame never holds up the answer or the speech."""

    def __init__(self, panel: Panel, touch: Optional[Touch], lang: str = "en"):
        self.panel, self.touch, self.lang = panel, touch, lang
        self.fonts = Fonts()
        self.state, self.question, self.answer, self.note = "ready", "", [], ""
        self._lock = threading.Lock()
        self._dirty = threading.Event()
        self._drawn = threading.Event()  # set when the newest state is on the screen
        self._stop = False
        self._thread = threading.Thread(target=self._draw_loop, name="screen", daemon=True)
        self._thread.start()
        self.ready()

    # --- states
    def ready(self) -> None:
        self._set(state="ready" if self.touch else "ready_key",
                  note="" if self.answer else self._both("ready" if self.touch else "ready_key"))

    def listening(self) -> None:
        self._set(state="listening", question="", answer=[], note="")

    def heard(self, text: str, lang: Optional[str] = None) -> None:
        self._set(state="thinking", question=text, lang=lang or self.lang)

    def sentence(self, text: str, example: bool = False, lang: Optional[str] = None) -> None:
        with self._lock:
            self.answer = self.answer + [(text, example)]
        self._set(state="answer", lang=lang or self.lang)

    def refused(self, text: str = "", lang: Optional[str] = None) -> None:
        with self._lock:
            if text:
                self.answer = self.answer + [(text, False)]
        self._set(state="refused", lang=lang or self.lang)

    def error(self, text: str = "", lang: Optional[str] = None) -> None:
        """Something failed. With text, that is shown in place of the answer."""
        if text:
            self._set(state="error", question="", answer=[(text, False)], note="", lang=lang or self.lang)
        else:
            self._set(state="error")

    def switching_off(self, text: str, lang: Optional[str] = None) -> None:
        """The goodbye before the device switches off. It stays on the screen afterwards."""
        self._set(state="poweroff", question="", answer=[(text, False)], note="", lang=lang or self.lang)

    def flush(self, timeout: float = 3.0) -> bool:
        """Wait until the newest state has been drawn. False if that took too long."""
        return self._drawn.wait(timeout)

    def show_answer(self, sentences: List[str], lang: Optional[str] = None) -> None:
        """A repeated answer."""
        self._set(state="answer", answer=[(s, False) for s in sentences], note="", lang=lang or self.lang)

    # --- internals
    @staticmethod
    def _both(state: str) -> str:
        return f"{LABELS[state]['en']}\n{LABELS[state]['hi']}"

    def _set(self, **changes) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self, key, value)
            self._drawn.clear()
        self._dirty.set()

    def _draw_loop(self) -> None:
        while True:
            self._dirty.wait()
            self._dirty.clear()
            with self._lock:
                frame = (self.state, self.lang, self.question, list(self.answer), self.note)
            try:
                state, lang, question, answer, note = frame
                if note and "\n" in note:  # the welcome: English and Hindi on their own lines
                    answer, note = [(line, False) for line in note.split("\n")], ""
                self.panel.show(render(self.fonts, (self.panel.width, self.panel.height),
                                       state, lang, question, answer, note))
            except Exception:  # noqa: BLE001  the screen must never stop the voice loop
                logger.exception("could not draw the screen")
                time.sleep(1.0)
            if not self._dirty.is_set():  # nothing newer arrived while drawing
                self._drawn.set()
            if self._stop:
                return

    def close(self) -> None:
        """Draw what is pending, then let go. The last frame stays on the screen."""
        self._stop = True
        self._dirty.set()
        self._thread.join(timeout=3.0)
        self.panel.close()
        if self.touch:
            self.touch.close()


def calibration_path() -> str:
    """$SPECIALIST_SCREEN_CALIBRATION, else in the cache folder the teacher examples use."""
    if os.environ.get("SPECIALIST_SCREEN_CALIBRATION"):
        return os.environ["SPECIALIST_SCREEN_CALIBRATION"]
    folder = os.environ.get("SPECIALIST_CACHE_DIR")
    if not folder:
        folder = "/opt/specialist/cache"
        if not os.access(folder, os.W_OK):
            folder = os.path.join(os.path.expanduser("~"), ".cache", "specialist")
    return os.path.join(folder, CALIBRATION_FILE)


def load_calibration(rotation: int) -> Optional[dict]:
    try:
        with open(calibration_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if data.get("rotation") == rotation else None


def rotation_from_env() -> int:
    return int(os.environ.get("SPECIALIST_SCREEN_ROTATION", "90"))


def open_screen(lang: str = "en", touch: bool = True) -> Optional[ScreenUI]:
    """The screen, or None (with a warning) if it is not there or cannot be opened."""
    try:
        rotation = rotation_from_env()
        panel = Panel(rotation)
        pad = Touch(load_calibration(rotation)) if touch else None
        return ScreenUI(panel, pad, lang)
    except Exception as e:  # noqa: BLE001
        logger.warning("no screen (%s); carrying on with voice only", e)
        return None


# ------------------------------------------------------------------------ by-hand tools
def _demo() -> None:
    ui = open_screen("en")
    if ui is None:
        sys.exit("The screen did not open. See the warning above.")
    steps = [
        (ui.ready, ()),
        (ui.listening, ()),
        (ui.heard, ("What is photosynthesis?",)),
        (ui.sentence, ("Photosynthesis is how green plants make their food using sunlight.",)),
        (ui.sentence, ("It happens in the leaves, which take in carbon dioxide and water.",)),
        (ui.sentence, ("For example, the mango tree in the school yard makes its food this way.", True)),
        (ui.ready, ()),
        (ui.listening, ()),
        (ui.heard, ("प्रकाश संश्लेषण क्या है?", "hi")),
        (ui.sentence, ("प्रकाश संश्लेषण वह क्रिया है जिससे हरे पौधे सूर्य के प्रकाश से अपना भोजन बनाते हैं।", False, "hi")),
        (ui.refused, ("I can only answer from the textbooks.",)),
        (ui.switching_off, ("Switching off. Wait for the fan to stop before you unplug me.",)),
    ]
    for step, args in steps:
        print(step.__name__, *args)
        step(*args)
        time.sleep(2.5)
    ui.close()


def _touch_test() -> None:
    rotation = rotation_from_env()
    pad = Touch(load_calibration(rotation))
    print("Calibrated." if pad.matrix else "Not calibrated: raw readings. Run: python -m local.screen calibrate")
    print("Press the screen; Ctrl-C to stop.")
    trigger = TouchTrigger(pad)
    try:
        while True:
            if trigger.wait(timeout=1.0):
                print("press at", pad.point())
    except KeyboardInterrupt:
        pass
    finally:
        pad.close()


def _calibrate() -> None:
    import numpy as np
    from PIL import Image, ImageDraw

    rotation = rotation_from_env()
    panel = Panel(rotation)
    pad = Touch()
    w, h = panel.width, panel.height
    targets = [(20, 20), (w - 20, 20), (w - 20, h - 20), (20, h - 20)]
    raws = []
    for n, (tx, ty) in enumerate(targets, 1):
        image = Image.new("RGB", (w, h), BACKGROUND)
        d = ImageDraw.Draw(image)
        d.line([tx - 12, ty, tx + 12, ty], fill=(255, 255, 255), width=2)
        d.line([tx, ty - 12, tx, ty + 12], fill=(255, 255, 255), width=2)
        d.text((w // 2, h // 2), f"Press the cross firmly ({n} of 4)", fill=TEXT, anchor="mm")
        panel.show(image)
        while pad.pressed():  # wait for the last press to end
            time.sleep(0.05)
        samples = []
        while len(samples) < 8:
            raw = pad.raw()
            if raw:
                samples.append(raw)
            time.sleep(0.03)
        raw = tuple(int(np.median([s[i] for s in samples])) for i in (0, 1))
        print(f"cross {n} at {tx},{ty}: raw {raw}")
        raws.append(raw)
    a = np.array([[x, y, 1.0] for x, y in raws])
    sx = np.linalg.lstsq(a, np.array([t[0] for t in targets], float), rcond=None)[0]
    sy = np.linalg.lstsq(a, np.array([t[1] for t in targets], float), rcond=None)[0]
    path = calibration_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"rotation": rotation, "matrix": [sx.tolist(), sy.tolist()], "raw": raws}, f)
    print("Saved", path)
    image = Image.new("RGB", (w, h), BACKGROUND)
    ImageDraw.Draw(image).text((w // 2, h // 2), "Calibrated", fill=TEXT, anchor="mm")
    panel.show(image)
    pad.close()
    panel.close()


def main() -> None:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    command = sys.argv[1] if len(sys.argv) > 1 else "demo"
    {"demo": _demo, "touch": _touch_test, "calibrate": _calibrate}.get(
        command, lambda: sys.exit("use: python -m local.screen demo|touch|calibrate"))()


if __name__ == "__main__":
    main()
