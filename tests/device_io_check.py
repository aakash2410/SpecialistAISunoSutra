#!/usr/bin/env python3
"""On-device health + I/O check for The Specialist.

Run this ON the Jetson after installing the module. It verifies the things the
automated pytest suite can't: the model services, the baked index in its device
location, system resources, and — with --interactive — the physical I/O
(microphone, speaker, camera, screen, trigger button).

    python3 tests/device_io_check.py                 # automatic checks only
    python3 tests/device_io_check.py --interactive   # also test mic/speaker/camera/screen/button
    python3 tests/device_io_check.py --json

Exit code is non-zero if any non-skipped check FAILS, so it can gate a smoke run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time

PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"
RESULTS: list[dict] = []


def record(name, status, detail=""):
    RESULTS.append({"check": name, "status": status, "detail": detail})
    icon = {PASS: "✓", FAIL: "✗", SKIP: "–", WARN: "!"}[status]
    print(f"  {icon} [{status}] {name}" + (f" — {detail}" if detail else ""))
    return status


def _http_ok(url, timeout=3.0):
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status == 200, r.read().decode(errors="replace")[:200]


# ------------------------------------------------------------ automatic checks
def check_python():
    v = sys.version_info
    record("python >= 3.8", PASS if v >= (3, 8) else FAIL, f"{v.major}.{v.minor}.{v.micro}")


def check_imports():
    for mod in ("numpy", "pocketinfer.specialist", "pocketinfer.models.embed",
                "pocketinfer.specialist.providers"):
        try:
            __import__(mod)
            record(f"import {mod}", PASS)
        except Exception as e:  # noqa: BLE001
            record(f"import {mod}", FAIL, str(e))


def check_embedder():
    try:
        from pocketinfer.models.embed import DEFAULT_MODEL_NAME, Embed

        e = Embed(model_name=DEFAULT_MODEL_NAME)
        if e.backend == "sentence-transformers":
            record("embedding model (neural)", PASS, f"{DEFAULT_MODEL_NAME}, dim={e.dim}")
        else:
            record("embedding model", WARN,
                   "using TF-IDF fallback — install sentence-transformers + rebuild "
                   "the index for production retrieval quality")
    except Exception as e:  # noqa: BLE001
        record("embedding model", FAIL, str(e))


def check_index(index_dir):
    try:
        from pocketinfer.specialist.vector_index import VectorIndex

        if not os.path.exists(os.path.join(index_dir, "embeddings.npy")):
            return record("DIKSHA index", FAIL, f"not found at {index_dir}")
        idx = VectorIndex(index_dir)
        ok = len(idx.chunks) == idx.embeddings.shape[0] > 0
        record("DIKSHA index", PASS if ok else FAIL,
               f"{len(idx.chunks)} chunks, dim={idx.dim}, embedder={idx.embedder_name}")
    except Exception as e:  # noqa: BLE001
        record("DIKSHA index", FAIL, str(e))


def check_services():
    # BHASHINI ASR/OCR/MT/TTS service
    try:
        ok, _ = _http_ok("http://localhost:11400/health")
        record("BHASHINI service (:11400)", PASS if ok else FAIL)
    except Exception as e:  # noqa: BLE001
        record("BHASHINI service (:11400)", FAIL, f"{e} — systemctl status bhashini_models")
    # Ollama LLM
    try:
        ok, body = _http_ok("http://localhost:11434/api/tags")
        models = [m.get("model") for m in json.loads(body).get("models", [])] if ok else []
        record("ollama service (:11434)", PASS if ok else FAIL,
               f"models: {', '.join(models) or 'none pulled'}")
    except Exception as e:  # noqa: BLE001
        record("ollama service (:11434)", FAIL, f"{e} — systemctl status ollama")


def check_resources():
    total, used, free = shutil.disk_usage("/")
    gb = free / 1e9
    record("disk free", PASS if gb > 2 else WARN, f"{gb:.1f} GB free")
    try:
        import psutil

        vm = psutil.virtual_memory()
        record("RAM", PASS, f"{vm.available/1e9:.1f} GB available of {vm.total/1e9:.1f} GB")
    except Exception:  # noqa: BLE001
        record("RAM", SKIP, "psutil not available")


def get_board():
    from pocketinfer.boards.base import Board

    return Board.get_board()


def check_board():
    try:
        board = get_board()
        record("board detected", PASS, type(board).__name__)
        return board
    except Exception as e:  # noqa: BLE001
        record("board detected", SKIP, f"not on device hardware ({e})")
        return None


# --------------------------------------------------------- interactive I/O
def _yesno(prompt):
    try:
        return input(f"    ?? {prompt} [y/N] ").strip().lower().startswith("y")
    except (EOFError, KeyboardInterrupt):
        return False


def io_speaker(board):
    """Play a 1-second tone through the speaker."""
    try:
        import math
        import struct

        from pocketinfer.audio import AudioPlayer

        rate = 22050
        pcm = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(rate)
        )
        print("    playing a 440 Hz tone...")
        with AudioPlayer(rate, board.ALSA_PLAYBACK_DEVICE) as p:
            p.play(pcm)
        record("speaker (I/O)", PASS if _yesno("Did you hear the tone?") else FAIL)
    except Exception as e:  # noqa: BLE001
        record("speaker (I/O)", FAIL, str(e))


def io_mic(board):
    """Record ~3 s and report the captured level."""
    try:
        import audioop

        print("    recording 3 seconds — please speak...")
        board.audio.start()
        time.sleep(3)
        board.audio.stop()
        wav = board.audio.to_audio_data().get_wav_data()
        # crude level from the PCM payload (skip 44-byte WAV header)
        rms = audioop.rms(wav[44:], 2) if len(wav) > 44 else 0
        status = PASS if rms > 100 else WARN
        record("microphone (I/O)", status, f"captured level rms={rms} ({len(wav)} bytes)")
    except Exception as e:  # noqa: BLE001
        record("microphone (I/O)", FAIL, str(e))


def io_camera(board):
    try:
        jpg = board.camera_frame_jpg()
        if not jpg:
            return record("camera (I/O)", FAIL, "no frame captured")
        out = "/tmp/specialist_camera_test.jpg"
        with open(out, "wb") as f:
            f.write(jpg)
        record("camera (I/O)", PASS, f"{len(jpg)} bytes -> {out}")
    except Exception as e:  # noqa: BLE001
        record("camera (I/O)", FAIL, str(e))


def io_screen(board):
    try:
        board.statusbar("Specialist I/O test")
        board.top_text("Screen check")
        board.bottom_text("Can you read this?")
        record("screen (I/O)", PASS if _yesno("Is the text on the screen?") else FAIL)
    except Exception as e:  # noqa: BLE001
        record("screen (I/O)", FAIL, str(e))


def io_button(board):
    try:
        print("    press the trigger button within 10 s...")
        board.wait_for_trigger_button_down(timeout=10)
        # If it returned quickly, assume a press; confirm with the operator.
        record("trigger button (I/O)", PASS if _yesno("Did you press it (and it registered)?") else FAIL)
    except Exception as e:  # noqa: BLE001
        record("trigger button (I/O)", FAIL, str(e))


def run_interactive(board):
    print("\n-- interactive I/O (follow the prompts) --")
    io_screen(board)
    io_speaker(board)
    io_mic(board)
    io_camera(board)
    io_button(board)


def main():
    ap = argparse.ArgumentParser(description="On-device health + I/O check for The Specialist.")
    ap.add_argument("--index", default=os.environ.get(
        "SPECIALIST_INDEX_DIR", "/opt/specialist/corpus/diksha_g7_science/index"))
    ap.add_argument("--interactive", action="store_true",
                    help="also test physical I/O (mic, speaker, camera, screen, button)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # Make the device package importable when run from the repo checkout.
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(here, "..", "python"))

    print(f"Specialist device check @ {socket.gethostname()}  ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print("\n-- automatic checks --")
    check_python()
    check_imports()
    check_embedder()
    check_index(args.index)
    check_services()
    check_resources()
    board = check_board()

    if args.interactive:
        if board is None:
            record("interactive I/O", SKIP, "no board detected")
        else:
            run_interactive(board)

    fails = sum(1 for r in RESULTS if r["status"] == FAIL)
    warns = sum(1 for r in RESULTS if r["status"] == WARN)
    print(f"\nSummary: {len(RESULTS)} checks, {fails} FAIL, {warns} WARN, "
          f"{sum(1 for r in RESULTS if r['status']==SKIP)} SKIP")

    if args.json:
        print(json.dumps(RESULTS, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
