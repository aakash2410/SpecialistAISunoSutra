# The Specialist — Test Plan

Everything to test, from software units up to on-device I/O and the v1 acceptance
criteria. Three layers:

| Layer | What | How | Where |
|-------|------|-----|-------|
| A. Software | embed / retrieve / ground / refuse / pipeline / providers | **automated** `pytest` | laptop or device |
| B. Device I/O & services | mic, speaker, camera, screen, button, BHASHINI, ollama, index | `device_io_check.py` + manual | device |
| C. Integration & acceptance | full loop, latency, offline, refusal, languages | manual procedures below | device |

## How to run

```bash
# Layer A — automated (no hardware)
pip install -r tests/requirements-test.txt
pytest tests/ -q

# Layer B — on the Jetson
python3 tests/device_io_check.py                 # services + resources + index
python3 tests/device_io_check.py --interactive   # + mic/speaker/camera/screen/button

# Layer C — full loop on the device
pocketinfer-service --app TheSpecialist
```

---

## Layer A — Automated software tests (`pytest`)

Covered today (45+ assertions, environment-independent — builds a TF-IDF index in
a temp dir so it never needs the network):

- [ ] **Embedding** (`test_embed.py`) — L2-normalized output; deterministic for
  the same text; **build-time == run-time vectors** via saved state (the
  cross-process guarantee); on-topic > off-topic; backend-aware threshold.
- [ ] **Index integrity** (`test_index_integrity.py`) — shipped index: one vector
  per chunk, float32 + unit-norm rows, manifest fields present, **every chunk has
  DIKSHA citation metadata**; fails if a TF-IDF fallback index is shipped where
  the neural embedder is installed.
- [ ] **Vector search** (`test_vector_index.py`) — results ranked high→low;
  photosynthesis query hits the right chapter; off-topic ≈ 0; dim-mismatch raises.
- [ ] **Grounding & refusal** (`test_grounding.py`) — intent (explain/simplify/
  answer, incl. romanized Hindi); refuse when empty / below threshold; grounded
  prompt contains sources + refusal sentinel; generation-gate detection.
- [ ] **Pipeline** (`test_pipeline.py`) — in-corpus answered **with citations**;
  off-syllabus **refused**; LLM refusal honored; MT localizes answer *and*
  refusal; TTS hook gets spoken text; timings present; threshold from manifest.
- [ ] **Providers** (`test_providers.py`) — AudioClip WAV round-trip + duration;
  contract conformance (Protocols); no-op MT passthrough; factories reject unknown
  names; macOS `say` / Vosk skip cleanly when absent.

Run in CI on every change. Add a case here whenever a bug is found.

---

## Layer B — Device I/O & services

`python3 tests/device_io_check.py` automates these; `--interactive` adds physical
I/O. Tick each on real hardware:

**Automatic**
- [ ] Python ≥ 3.8, package imports succeed
- [ ] Embedding model loads as **neural** (`sentence-transformers`), not the
  TF-IDF fallback (a WARN means the shipped index will underperform)
- [ ] DIKSHA index present at `/opt/specialist/corpus/...`, chunks == vectors
- [ ] BHASHINI service healthy on `:11400`
- [ ] ollama healthy on `:11434` and the grounding model is pulled
- [ ] Disk free > 2 GB; RAM headroom sane

**Interactive I/O**
- [ ] **Screen** — status/top/bottom text render, legible
- [ ] **Speaker** — 440 Hz tone audible, no clipping/buzz
- [ ] **Microphone** — 3 s capture returns a non-trivial level (speak; confirm rms)
- [ ] **Camera** — captures a JPEG of a textbook page, in focus, readable
- [ ] **Trigger button** — press registers within timeout
- [ ] **Devanagari rendering** — Hindi text shows correctly (font present)

---

## Layer C — Integration (full loop, on device)

### Voice path (ASR → MT → Specialist → MT → TTS)
- [ ] Hold trigger, ask *"Mujhe photosynthesis samjhaye, Hindi mein"* → correct,
  simplified Hindi answer, spoken aloud, source shown on screen
- [ ] English in/out (`--setting input_language=en output_language=en`) → answered
- [ ] Ask something **in the syllabus but a different chapter** (e.g. heat) → correct chapter
- [ ] Ask something **off-syllabus** (e.g. cricket) → device **refuses aloud**, no guess
- [ ] Ambiguous / half-heard speech → sensible retrieval or a graceful "didn't catch that"

### Camera / OCR path
- [ ] Switch `input_mode=camera`, point at a textbook page → OCR text drives the
  answer, grounded + cited
- [ ] Blurry / off-angle page → graceful (no crash; retry prompt)

### Grounding correctness
- [ ] Every answer shows a DIKSHA citation (content_id · chapter · section · page)
- [ ] Spot-check 5 answers against the source passage — no invented facts
- [ ] Refusal wording is clear and spoken in the output language

---

## Layer D — v1 acceptance criteria (from the product spec)

- [ ] **Latency ~2–3 s**: full loop (ASR/OCR + embed + retrieve + LLM + MT + TTS)
  on the demo board. Measure with the per-stage timings the app logs; record
  worst-case over 10 runs. Note which stage dominates.
- [ ] **Fully offline at run time**: disconnect all networking (WiFi off /
  Ethernet unplugged / airplane), reboot, run the full loop end-to-end. It must
  work with **no** network. (Confirm nothing calls out — see Layer E.)
- [ ] **Traceable + refuses**: every answer maps to a DIKSHA source; missing
  content is refused (covered in Layer C).
- [ ] **Corpus fits the budget**: index + models within the device storage
  budget; record `du -sh` of `/opt/specialist/corpus` and the model sizes.

---

## Layer E — Robustness / edge cases
- [ ] Pull the network mid-session → answers keep working (proves offline)
- [ ] BHASHINI service down → app surfaces an error, recovers when it returns
- [ ] ollama model not loaded → clear failure, not a hang
- [ ] Empty / silent mic input → "didn't catch that", loops cleanly
- [ ] Very long spoken question → no crash, reasonable retrieval
- [ ] Reboot → device comes back up into The Specialist (systemd), index intact
- [ ] Rapid repeated triggers → no audio overlap / state corruption
- [ ] Low disk / low RAM behavior → degrades safely

---

## Layer F — Provisioning / module (overlay integrity)
- [ ] `ansible-playbook install_specialist.yml` on a base device installs **only**
  new files (no base file modified — `git status` on the base tree is clean)
- [ ] Corpus lands in `/opt/specialist/corpus`; `--tags content` refreshes it
  without touching code
- [ ] Device boots into The Specialist (systemd drop-in), `--list-apps` shows it
- [ ] **Dependencies land in the environment the service actually runs from**
  (venv vs system python — the known open question); the app imports on boot
- [ ] Rollback: remove the drop-in + `daemon-reload` → device returns to the
  previous default app; Specialist files inert

---

## Sign-off

| Layer | Owner | Date | Result |
|-------|-------|------|--------|
| A Software (pytest) | | | |
| B Device I/O & services | | | |
| C Integration | | | |
| D Acceptance criteria | | | |
| E Robustness | | | |
| F Provisioning | | | |
