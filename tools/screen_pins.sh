#!/bin/bash
# Let the screen's control pins drive outputs on JetPack 6.
#
# JetPack 6 leaves header pins 29 (LCD_DC), 31 (LCD_RST) and 32 (LCD_BL)
# tristated, so Jetson.GPIO can set them but nothing reaches the wire.
# This adds them, as outputs, to the overlay jetson-io made when spi1 was
# enabled. Running jetson-io again rewrites that overlay, so run this again
# afterwards. Reboot to apply.
#
# Run on the Jetson:  sudo bash tools/screen_pins.sh
set -euo pipefail

DTBO=/boot/jetson-io-hdr40-user-custom.dtbo
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

[ -f "$DTBO" ] || { echo "No $DTBO. Enable spi1 with jetson-io first."; exit 1; }
command -v dtc >/dev/null || apt-get install -y device-tree-compiler

dtc -q -I dtb -O dts -o "$WORK/in.dts" "$DTBO"
grep -q 'spi1_cs0_pz6' "$WORK/in.dts" || { echo "spi1 is not in $DTBO."; exit 1; }
if grep -q 'hdr40-pin29' "$WORK/in.dts"; then
    echo "Pins 29, 31 and 32 are already in $DTBO. Nothing to do."
    exit 0
fi

python3 - "$WORK/in.dts" "$WORK/out.dts" <<'EOF'
import re, sys
text = open(sys.argv[1]).read()
pins = [("29", "soc_gpio32_pq5", "rsvd0"),   # LCD_DC
        ("31", "soc_gpio33_pq6", "rsvd0"),   # LCD_RST
        ("32", "soc_gpio19_pg6", "rsvd1")]   # LCD_BL
nodes = "".join(
    f'\n\t\t\t\thdr40-pin{n} {{\n'
    f'\t\t\t\t\tnvidia,pins = "{pin}";\n'
    f'\t\t\t\t\tnvidia,function = "{func}";\n'
    f'\t\t\t\t\tnvidia,pull = <0x00>;\n'
    f'\t\t\t\t\tnvidia,tristate = <0x00>;\n'
    f'\t\t\t\t\tnvidia,enable-input = <0x00>;\n'
    f'\t\t\t\t}};\n'
    for n, pin, func in pins)
match = re.search(r'hdr40-pin26 \{.*?\n\t*\};\n', text, re.S)
if not match:
    sys.exit("Could not find the pin 26 entry to add after.")
open(sys.argv[2], "w").write(text[:match.end()] + nodes + text[match.end():])
EOF

dtc -q -I dts -O dtb -o "$WORK/out.dtbo" "$WORK/out.dts"
cp "$WORK/out.dtbo" "$DTBO"
dtc -q -I dtb -O dts "$DTBO" | grep -A5 -E 'hdr40-pin(29|31|32) '
echo "Done. Reboot to apply: sudo reboot"
