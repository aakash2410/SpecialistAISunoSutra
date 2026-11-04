#!/usr/bin/env python3
"""Talk to the Jetson from this Mac: you speak here, it answers through its own speaker.

Stands in for a microphone on the device. Records from the Mac's microphone, sends the
clip to the Jetson, and runs the real voice loop there. Refuses to start while tests are
running or memory is low, because the board has no room for two jobs at once.

    python3 tools/ask_by_voice.py --speaker plughw:0,0
    python3 tools/ask_by_voice.py --speaker plughw:0,0 --wav tests/fixtures/question_hi.wav

Needs numpy and sounddevice on the Mac. macOS will ask for microphone permission once.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import wave

HOST = "suno-sutra@suno-sutra"
RATE = 16000
MIN_FREE_MB = 1500
REMOTE_WAV = "/tmp/ask_by_voice.wav"
READY = (
    'grep -q ALLDONE /tmp/tests.log 2>/dev/null || ! pgrep -f "[r]unall.sh" >/dev/null; '
    'echo "done=$?"; awk \'/MemAvailable/{print "free=" int($2/1024)}\' /proc/meminfo'
)


def ssh(command: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-o", "ConnectTimeout=15", HOST, command], **kwargs)


def board_is_free() -> bool:
    out = ssh(READY, capture_output=True, text=True, timeout=30).stdout
    state = dict(line.split("=") for line in out.split())
    if state.get("done") != "0":
        print("The tests are still running on the Jetson. Try again when they finish.")
        return False
    if int(state.get("free", 0)) < MIN_FREE_MB:
        print(f"Only {state.get('free')} MB free on the Jetson. Is something else running?")
        return False
    return True


def record(path: str) -> None:
    import numpy as np
    import sounddevice as sd

    frames = []
    input("\nPress Enter, then ask your question in Hindi...")
    stop = threading.Event()
    threading.Thread(target=lambda: (input("Recording. Press Enter to stop.\n"), stop.set()),
                     daemon=True).start()
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                        callback=lambda data, _n, _t, _s: frames.append(data.copy())):
        while not stop.is_set():
            sd.sleep(50)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.concatenate(frames).tobytes() if frames else b"")


def ask(path: str, speaker: str, language: str) -> None:
    subprocess.run(["scp", "-q", path, f"{HOST}:{REMOTE_WAV}"], check=True)
    ssh(f"cd ~/specialist && SPECIALIST_ALSA_DEVICE={speaker} /opt/specialist/venv/bin/python -W ignore "
        f"-m local.voice_loop --in-lang hi --out-lang {language} --stream --from-wav {REMOTE_WAV} "
        f"2>&1 | grep -v device_discovery")


def main() -> int:
    ap = argparse.ArgumentParser(description="Ask the Jetson a question by voice from this Mac.")
    ap.add_argument("--speaker", required=True, help="the Jetson's ALSA speaker, e.g. plughw:0,0")
    ap.add_argument("--wav", help="send this 16 kHz mono WAV instead of recording")
    ap.add_argument("--answer-in", default="hi", help="answer language (default hi)")
    args = ap.parse_args()

    if not board_is_free():
        return 1
    if args.wav:
        ask(args.wav, args.speaker, args.answer_in)
        return 0
    print("Ask a Class 7 science question in Hindi. Ctrl-C to quit. "
          "The first answer takes longer while the models load.")
    try:
        while True:
            record("/tmp/ask_by_voice.wav")
            if not board_is_free():
                return 1
            ask("/tmp/ask_by_voice.wav", args.speaker, args.answer_in)
    except KeyboardInterrupt:
        print("\nbye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
