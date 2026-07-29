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
        # Return the FULL body — callers parse it as JSON, so it must not be truncated.
        return r.status == 200, r.read().decode(errors="replace")


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
            hint = ""
            if "specialist" in mod or mod.endswith((".embed", ".ocr")):
                hint = " — overlay not installed here; deploy module/ or re-run with --repo"
            record(f"import {mod}", FAIL, f"{e}{hint}")


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

        repo_local = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "corpus",
            "diksha_g7_science", "index"))
        candidates = [(index_dir, ""), (repo_local, " (repo copy — not yet deployed to /opt; "
                                                    "run the module install to place it there)")]
        for cand, note in candidates:
            if os.path.exists(os.path.join(cand, "embeddings.npy")):
                idx = VectorIndex(cand)
                ok = len(idx.chunks) == idx.embeddings.shape[0] > 0
                return record("DIKSHA index", PASS if ok else FAIL,
                              f"{len(idx.chunks)} chunks, dim={idx.dim}, "
                              f"embedder={idx.embedder_name}{note}")
        record("DIKSHA index", FAIL,
               f"not found at {index_dir} (nor a repo copy). Deploy the module or pass --index.")
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
        if not ok:
            record("ollama service (:11434)", FAIL)
        else:
            try:
                names = [m.get("model", "") for m in json.loads(body).get("models", [])]
                record("ollama service (:11434)", PASS,
                       f"models: {', '.join(n for n in names if n) or 'none pulled'}")
            except json.JSONDecodeError as je:
                record("ollama service (:11434)", WARN, f"up, but model list unparseable: {je}")
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


def check_board_light():
    """Report the board WITHOUT instantiating it.

    Constructing the board starts the camera/UI (and a multiprocessing
    forkserver that prints a noisy traceback at exit), which a health check
    shouldn't do — so for the auto pass we just read the device-tree model. The
    full board is only built for the interactive I/O tests.
    """
    model_path = "/proc/device-tree/model"
    if not os.path.exists(model_path):
        return record("board", SKIP, "not on device hardware (no /proc/device-tree/model)")
    try:
        with open(model_path, "rb") as f:
            model = f.read().replace(b"\x00", b"").decode(errors="replace").strip()
        record("board", PASS, model)
    except Exception as e:  # noqa: BLE001
        record("board", WARN, str(e))


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
    ap.add_argument("--repo", action="store_true",
                    help="import from THIS repo checkout instead of the installed platform "
                         "(pre-deploy testing; note the checkout omits LFS fonts so the board "
                         "check may not load).")
    args = ap.parse_args()

    # Prefer the platform INSTALLED on the device (it has the LFS fonts and is
    # where the overlay module gets installed). Only fall back to this repo
    # checkout if the platform isn't importable, or when --repo is given.
    here = os.path.dirname(os.path.abspath(__file__))
    repo_python = os.path.join(here, "..", "python")
    if args.repo:
        sys.path.insert(0, repo_python)
    else:
        try:
            import pocketinfer  # noqa: F401
        except ImportError:
            sys.path.insert(0, repo_python)

    print(f"Specialist device check @ {socket.gethostname()}  ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    print("\n-- automatic checks --")
    check_python()
    check_imports()
    check_embedder()
    check_index(args.index)
    check_services()
    check_resources()
    check_board_light()  # lightweight — does not start the camera/UI

    if args.interactive:
        # Only now do we build the real board (starts camera/UI) for physical I/O.
        board = None
        try:
            board = get_board()
        except Exception as e:  # noqa: BLE001
            record("interactive I/O", SKIP, f"board unavailable: {e}")
        if board is not None:
            run_interactive(board)

    fails = sum(1 for r in RESULTS if r["status"] == FAIL)
    warns = sum(1 for r in RESULTS if r["status"] == WARN)
    print(f"\nSummary: {len(RESULTS)} checks, {fails} FAIL, {warns} WARN, "
          f"{sum(1 for r in RESULTS if r['status']==SKIP)} SKIP")

    if args.json:
        print(json.dumps(RESULTS, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    _code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # The board/UI starts background workers (camera, a multiprocessing forkserver)
    # with no clean shutdown hook; os._exit avoids a noisy teardown traceback
    # printing after the results.
    os._exit(_code)
