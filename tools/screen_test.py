"""Test the Waveshare 2.8 inch SPI touch screen on the Jetson 40-pin header.

Wiring (Jetson header pin numbers): 5V 2, GND 6, MOSI 19, MISO 21, SCLK 23,
LCD_CS 24 (spidev0.0), TP_CS 26 (spidev0.1), LCD_DC 29, LCD_RST 31,
LCD_BL 32, TP_IRQ 33. SPI1 must be enabled with jetson-io.

Run on the Jetson:  sudo python3 tools/screen_test.py [--driver st7789|hx8347d]
Without --driver it tries ST7789, then HX8347D (the two chips Waveshare has
used on this board), filling red, green, blue, white and black with each.
Then it prints touch positions for 15 seconds.

HX8347D setup follows ST's open-source driver:
https://github.com/STMicroelectronics/stm32-hx8347d
"""
import argparse
import time

import Jetson.GPIO as GPIO
import spidev

DC, RST, BL, IRQ = 29, 31, 32, 33
W, H = 240, 320
COLOURS = [("red", 0xF800), ("green", 0x07E0), ("blue", 0x001F),
           ("white", 0xFFFF), ("black", 0x0000)]

HX8347D_INIT = [
    (0xEA, 0x00), (0xEB, 0x20), (0xEC, 0x0C), (0xED, 0xC4), (0xE8, 0x40),
    (0xE9, 0x38), (0x27, 0xA3),
    (0x40, 0x01), (0x41, 0x00), (0x42, 0x00), (0x43, 0x10), (0x44, 0x0E),
    (0x45, 0x24), (0x46, 0x04), (0x47, 0x50), (0x48, 0x02), (0x49, 0x13),
    (0x4A, 0x19), (0x4B, 0x19), (0x4C, 0x16), (0x50, 0x1B), (0x51, 0x31),
    (0x52, 0x2F), (0x53, 0x3F), (0x54, 0x3F), (0x55, 0x3E), (0x56, 0x2F),
    (0x57, 0x7B), (0x58, 0x09), (0x59, 0x06), (0x5A, 0x06), (0x5B, 0x0C),
    (0x5C, 0x1D), (0x5D, 0xCC),
    (0x1B, 0x1B), (0x1A, 0x01), (0x24, 0x2F), (0x25, 0x57), (0x23, 0x86),
    (0x18, 0x36), (0x19, 0x01), (0x1C, 0x06), (0x1D, 0x06), (0x1F, 0x90),
    (0x26, 0x01), "wait10",
    (0x17, 0x05), (0x36, 0x09), (0x28, 0x38), "wait100",
    (0x28, 0x3C), "wait100",
    (0x01, 0x00), (0x16, 0x00),
]


def command(lcd, cmd, *data):
    GPIO.output(DC, GPIO.LOW)
    lcd.writebytes([cmd])
    if data:
        GPIO.output(DC, GPIO.HIGH)
        lcd.writebytes(list(data))


def read_byte(lcd, cmd, count):
    GPIO.output(DC, GPIO.LOW)
    return lcd.xfer2([cmd] + [0] * count)[1:]


def hard_reset():
    GPIO.output(RST, GPIO.HIGH); time.sleep(0.01)
    GPIO.output(RST, GPIO.LOW); time.sleep(0.02)
    GPIO.output(RST, GPIO.HIGH); time.sleep(0.15)


class ST7789:
    name = "ST7789"

    def init(self, lcd):
        hard_reset()
        print("ST7789 ID (expect 85 85 52 after a dummy bit):",
              " ".join(f"{b:02x}" for b in read_byte(lcd, 0x04, 4)))
        command(lcd, 0x01); time.sleep(0.15)   # software reset
        command(lcd, 0x11); time.sleep(0.12)   # wake
        command(lcd, 0x3A, 0x55)               # 16-bit colour
        command(lcd, 0x36, 0x00)               # portrait
        command(lcd, 0x21)                     # IPS panels need inversion on
        command(lcd, 0x13)
        command(lcd, 0x29); time.sleep(0.05)   # display on

    def window(self, lcd):
        command(lcd, 0x2A, 0, 0, (W - 1) >> 8, (W - 1) & 0xFF)
        command(lcd, 0x2B, 0, 0, (H - 1) >> 8, (H - 1) & 0xFF)
        command(lcd, 0x2C)


class HX8347D:
    name = "HX8347D"

    def init(self, lcd):
        hard_reset()
        print("HX8347D ID (expect 47):",
              " ".join(f"{b:02x}" for b in read_byte(lcd, 0x00, 1)))
        for step in HX8347D_INIT:
            if step == "wait10":
                time.sleep(0.01)
            elif step == "wait100":
                time.sleep(0.1)
            else:
                command(lcd, step[0], step[1])

    def window(self, lcd):
        for reg, value in [(0x02, 0), (0x03, 0), (0x04, 0), (0x05, W - 1),
                           (0x06, 0), (0x07, 0), (0x08, (H - 1) >> 8),
                           (0x09, (H - 1) & 0xFF)]:
            command(lcd, reg, value)
        command(lcd, 0x22)


def fill(lcd, chip, rgb565):
    chip.window(lcd)
    GPIO.output(DC, GPIO.HIGH)
    lcd.writebytes2(bytes([rgb565 >> 8, rgb565 & 0xFF]) * (W * H))


def touch(tp):
    def axis(cmd):
        r = tp.xfer2([cmd, 0, 0])
        return ((r[1] << 8) | r[2]) >> 3
    return axis(0xD0), axis(0x90)


def poll_touch(tp):
    """Read the touch chip every 0.25 s, whatever TP_IRQ says.

    Z1 is the press strength: near 0 untouched, clearly higher when pressed.
    """
    print("Press firmly (a fingernail or pen cap works best) in different "
          "spots for 20 seconds. Columns: IRQ (0 = pressed), X, Y, Z1")
    end = time.time() + 20
    while time.time() < end:
        x, y = touch(tp)
        r = tp.xfer2([0xB0, 0, 0])
        z1 = ((r[1] << 8) | r[2]) >> 3
        print(f"IRQ={GPIO.input(IRQ)}  X={x:4d}  Y={y:4d}  Z1={z1:4d}")
        time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver", choices=["st7789", "hx8347d"])
    parser.add_argument("--touch", action="store_true",
                        help="skip the colours; poll the touch chip for 20 s")
    args = parser.parse_args()
    chips = {"st7789": [ST7789()], "hx8347d": [HX8347D()]}.get(
        args.driver, [ST7789(), HX8347D()])

    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup([DC, RST, BL], GPIO.OUT, initial=GPIO.HIGH)
    GPIO.setup(IRQ, GPIO.IN)
    lcd, tp = spidev.SpiDev(), spidev.SpiDev()
    lcd.open(0, 0); lcd.mode = 0; lcd.max_speed_hz = 8_000_000
    tp.open(0, 1); tp.mode = 0; tp.max_speed_hz = 1_000_000
    try:
        if args.touch:
            poll_touch(tp)
            return
        for chip in chips:
            print(f"--- Trying {chip.name}: watch for colours ---")
            chip.init(lcd)
            for name, colour in COLOURS:
                print("  filling", name)
                fill(lcd, chip, colour)
                time.sleep(1.2)
        print("Tap the screen for 15 seconds...")
        end, taps = time.time() + 15, 0
        while time.time() < end:
            if GPIO.input(IRQ) == GPIO.LOW:
                taps += 1
                print("touch x=%d y=%d" % touch(tp))
            time.sleep(0.1)
        if not taps:
            print("No touch seen on TP_IRQ (pin 33).")
    finally:
        lcd.close(); tp.close(); GPIO.cleanup()


if __name__ == "__main__":
    main()
