#!/usr/bin/env python3
"""How often the language router picks the right language, and what it costs.

    python3 tests/benchmark/language_id.py <folder of WAVs> [--no-bhashini]

Name each file after what was spoken: en_*.wav for English (any accent), hi_*.wav for
Hindi and hinglish_*.wav for Hindi mixed with English words. Hinglish should go to Hindi.
Record 16 kHz mono 16-bit WAV, or convert with:
    ffmpeg -i voice-note.m4a -ar 16000 -ac 1 -sample_fmt s16 hi_01.wav

--no-bhashini fakes Hindi speech recognition, so the routing can be measured on a Mac.
Timings are for the machine it runs on.
"""

import glob
import os
import statistics as st
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "python"), ROOT]

from pocketinfer.models.langid import LanguageId  # noqa: E402
from pocketinfer.specialist.language import LanguageRouter  # noqa: E402
from pocketinfer.specialist.providers import AudioClip  # noqa: E402


def expected(name):
    return "en" if name.startswith("en") else "hi"


def main():
    folder = sys.argv[1]
    from local.providers import BhashiniASR, WhisperASR

    whisper = WhisperASR()
    langid = LanguageId()
    indic = (lambda clip, lang: "(Hindi speech recognition skipped)") if "--no-bhashini" in sys.argv \
        else BhashiniASR().transcribe
    router = LanguageRouter(langid.log_probs, whisper.transcribe_scored, indic)
    rows = []
    for path in sorted(glob.glob(os.path.join(folder, "*.wav"))):
        name = os.path.basename(path)
        clip = AudioClip.from_wav_file(path)
        t0 = time.time()
        r = router.route(clip)
        rows.append((name, expected(name), r, time.time() - t0, clip.duration_seconds))
    groups = sorted({n.rsplit("_", 1)[0] for n, *_ in rows})
    print("| Recordings | Right | Decided by language ID alone | Typical routing time (s) |")
    print("|---|---|---|---|")
    for g in groups:
        rs = [x for x in rows if x[0].rsplit("_", 1)[0] == g]
        right = sum(r.language == want for _, want, r, _, _ in rs)
        alone = sum(r.whisper_confidence is None for _, _, r, _, _ in rs)
        print(f"| {g} | {right} of {len(rs)} | {alone} of {len(rs)} | {st.median(x[3] for x in rs):.2f} |")
    wrong = [x for x in rows if x[2].language != x[1]]
    for name, want, r, _, _ in wrong:
        print(f"WRONG {name}: wanted {want}, got {r.language}: {r.reason}")
    print(f"\n{len(rows) - len(wrong)} of {len(rows)} right")
    return 0


if __name__ == "__main__":
    sys.exit(main())
