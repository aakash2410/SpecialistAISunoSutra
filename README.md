# The Specialist v1

> A grounded, offline subject expert built on the **Suno Sutra** edge-AI platform.

The Specialist turns a Suno Sutra device into a subject expert the teacher can
call into the room. The teacher asks a question by **voice** or **camera** using
a textbook page, and the device **explains**, **simplifies**, or **answers** it
aloud in the local language — e.g. *"Mujhe photosynthesis samjhaye, Hindi mein."*

Every answer comes from **approved DIKSHA content**. When the corpus doesn't
cover the question, the device **says so** instead of guessing. The whole answer
loop runs **fully offline**.

**How this ships to hardware.** The device already has the Suno Sutra base
platform, so The Specialist deploys as a **purely additive overlay module** —
new files only, **no base file is modified or overwritten**. Build it with
`./scripts/make_module.sh --tar` and hand `dist/specialist-module/` to the
hardware team; see [module/README.md](module/README.md).

The base platform is vendored here (`python/`, `rootfs/`, `ioexpander/`,
`assets/`) purely so the laptop demo and voice loop can run against it — it is
kept **byte-identical to upstream**.

---

## Quick start (laptop demo — no device needed)

```bash
cd "Specialist v1"
pip install numpy                      # the only dep the offline demo needs
python ingest/build_index.py           # bake the DIKSHA index once
python demo.py                         # run the canned questions
```

The demo runs the real loop — **embed → retrieve → ground → answer → refuse** —
using the exact runtime the device uses (`pocketinfer.specialist`), entirely
offline. With no neural model installed it uses a deterministic TF-IDF embedder
and an extractive answerer, so it runs anywhere. Expected: photosynthesis / heat
/ respiration are answered **with DIKSHA citations**; cricket and the French
Revolution are **refused**.

```bash
python demo.py --ask "What is photosynthesis?"
python demo.py --interactive
python demo.py --ask "..." --ollama qwen3-vl:2b   # real grounded LLM (if ollama is up)
python demo.py --ask "..." --lang hi              # translate answer (needs BHASHINI service)
```

---

## Deploying to a device (overlay module)

The device already has the Suno Sutra base, so we ship an **additive overlay**.

1. **Bake the index with the production embedder** (off-device, online, once):
   ```bash
   pip install -r module/requirements-specialist.txt   # sentence-transformers
   python ingest/build_index.py --model intfloat/multilingual-e5-small
   ```

2. **Build the module:**
   ```bash
   ./scripts/make_module.sh --tar
   ```
   Produces `dist/specialist-module/` (+ tarball) containing the payload, the
   corpus, a standalone Ansible role, and a `MANIFEST.txt` listing exactly which
   file lands where.

3. **Install onto the device** (base must already be provisioned):
   ```bash
   cd dist/specialist-module/ansible
   cp inventory.ini.sample inventory.ini      # adjust for your device
   ansible-playbook -i inventory.ini install_specialist.yml
   ```

What lands where — **all new files, nothing overwritten**:

| Component | On-device destination |
|---|---|
| `embed.py`, `ocr.py` | `{device_root}/python/pocketinfer/models/` |
| `specialist/` runtime | `{device_root}/python/pocketinfer/specialist/` |
| `specialist_app.py` | `{device_root}/python/pocketinfer/applications/` |
| corpus + index (content) | `/opt/specialist/corpus/` |
| boot-into-Specialist drop-in | `/etc/systemd/system/pocketinfer.service.d/` |

Content sits **outside** the code tree, so the syllabus can be refreshed on its
own: `ansible-playbook ... install_specialist.yml --tags content`.

> **Confirm before installing:** the base platform is inconsistent about whether
> the service runs from the **venv** (`pocketinfer.service` unit) or
> **system python** (the base `app` role). The module's dependencies must land in
> whichever one actually runs, or the app import-fails on boot. Defaults assume
> the venv — override with `-e specialist_exec_path=... -e specialist_pip_executable=...`.
> Details in [module/README.md](module/README.md).

---

## How it works

### Run time (offline, always — on the device)

```
teacher request (voice → ASR, or camera → OCR)
   │   (translate to English if needed — MT)
   ▼
Embed.embed_one(query)                       ← on-device embedding model
   ▼
VectorIndex.search(top_k)                     ← local vector search, no network
   ▼
build_grounding()  ── best score < threshold ─→  REFUSE ("not in DIKSHA content")
   ▼ (grounded prompt: answer ONLY from retrieved passages)
LLM (stock quantised, grounded at inference) ── says "no answer in source" ─→ REFUSE
   ▼
MT → target language     →     TTS → spoken aloud
```

No step touches the network. The LLM, embedding model, and index all live on
local storage. Every non-refusal answer carries a **DIKSHA citation**
(`content_id · chapter · section · page`).

### Build time (online, once, off-device)

```
DIKSHA pull  →  chunk  →  embed  →  index   (baked into the device image)
```

`python ingest/build_index.py`. Re-run it during Suno Sutra's periodic update
window to refresh the corpus, like any other model or asset.

---

## Layout

Files marked **NEW** are ours; everything else is the vendored upstream base,
kept byte-identical.

```
Specialist v1/
├── README.md                      ← this file (the product)
├── PLATFORM_README.md             ← the original Suno Sutra platform README
├── demo.py                        ← NEW  run the loop on a laptop (text)
│
├── corpus/                        ← NEW  CONTENT (deployed to /opt/specialist/corpus)
│   └── diksha_g7_science/{raw,index}
│
├── ingest/                        ← NEW  OFF-DEVICE build-time pipeline
│   ├── diksha_pull.py             ←        harvest a grade+subject slice
│   ├── chunk.py                   ←        split sections into passages
│   └── build_index.py             ←        pull → chunk → embed → index
│
├── local/                         ← NEW  laptop voice-loop harness
│   ├── providers.py               ←        Vosk / say / no-op MT / BHASHINI backends
│   ├── audio.py                   ←        mic capture, WAV, playback
│   └── voice_loop.py              ←        ASR→MT→Specialist→MT→TTS runner
│
├── module/                        ← NEW  the device overlay (source of truth)
│   ├── requirements-specialist.txt←        module's OWN deps (base untouched)
│   └── ansible/                   ←        standalone role + install playbook
│
├── scripts/make_module.sh         ← NEW  exports dist/specialist-module/
├── dist/                          ←      generated overlay (gitignored)
│
├── python/                        ←      vendored Suno Sutra base (pristine)
│   └── pocketinfer/
│       ├── models/
│       │   ├── embed.py           ← NEW    embedding model (e5-small | TF-IDF)
│       │   ├── ocr.py             ← NEW    BHASHINI OCR (camera → text)
│       │   └── asr.py / nmt.py / tts.py / ollama.py / vosk.py   (base, reused)
│       ├── specialist/            ← NEW    runtime subpackage
│       │   ├── vector_index.py    ←          local vector search
│       │   ├── grounding.py       ←          grounded prompt + refusal
│       │   ├── pipeline.py        ←          SpecialistEngine
│       │   ├── providers.py       ←          swappable-component contracts
│       │   └── llm.py             ←          stock LLM → grounded chat client
│       └── applications/
│           └── specialist_app.py  ← NEW    @RegisterApplication device app
│
├── rootfs/  ioexpander/  assets/  ←      vendored base (pristine, untouched)
```

### What's reused vs. new

| Reused from the platform (no retraining)            | New in The Specialist |
|-----------------------------------------------------|-----------------------|
| BHASHINI ASR / MT / TTS wrappers (`models/`)        | Embedding model (`models/embed.py`) |
| Stock quantised LLM via the `Ollama` wrapper        | Local vector search (`specialist/vector_index.py`) |
| Board HAL, audio, UI, application/registry/service  | OCR capture wrapper (`models/ocr.py`) |
| `hear_the_world` app structure (template)           | Grounding + refusal (`specialist/grounding.py`) |
| `rootfs` roles + flashing pipeline (unmodified)     | Offline engine (`specialist/pipeline.py`), overlay module (`module/`), DIKSHA ingestion (`ingest/`), laptop harness (`local/`) |

The LLM is **grounded at inference time** purely through the system/user prompts
— no fine-tuning, no retraining, as the spec requires.

---

## The embedding model (new)

The production backend is
[`intfloat/multilingual-e5-small`](https://huggingface.co/intfloat/multilingual-e5-small)
(~118M params, 384-d normalized vectors, covers major Indic scripts) — small
enough for the RAM/latency budget and a natural addition to the benchmark tab.

For environments without it (and for the offline demo), `embed.py` falls back to
a **dependency-free, deterministic TF-IDF embedder**, fitted on the corpus at
build time (vocabulary + IDF, with stopword and over-common-term pruning), its
state persisted next to the index and reloaded at run time. The same embedder
(and fitted state) is used at build and run time, so vectors are always
comparable. The refusal threshold is backend-aware and baked into
`manifest.json`.

## Grounding & refusal (two gates)

1. **Retrieval gate** — if the best passage's similarity is below the threshold,
   refuse before spending an LLM call.
2. **Generation gate** — the system prompt forbids outside knowledge and tells
   the LLM to emit `NO_ANSWER_IN_SOURCE` when the passages are insufficient;
   `pipeline.py` detects it and returns the standard refusal.

The request is classified into an intent — **explain / simplify / answer** —
which shapes the instruction given to the LLM.

---

## Success criteria mapped to this build

| v1 criterion | Where it's met |
|---|---|
| Full loop (ASR/OCR + embed + retrieve + LLM + MT + TTS) ~2–3 s | `pipeline.py` reports per-stage timings; retrieval is sub-ms |
| Runs fully offline at run time | No network in the runtime; index + models are local |
| Every answer traces to a DIKSHA source; refuses when missing | `grounding.py` citations + two-gate refusal (see demo) |
| Starter corpus: one grade, one subject, within storage budget | `pocketinfer/specialist/corpus/diksha_g7_science/` (Grade 7 Science) |

## Out of scope (v1)

Open-world answers beyond DIKSHA, fine-tuning, child-speech input, languages
beyond the chosen set, and scoring/assessment — per the spec.

## Open item

Confirm **bulk-download and offline-redistribution licensing** for the specific
DIKSHA assets before enabling the online harvest in `ingest/diksha_pull.py`
(currently gated). The starter corpus uses paraphrased, NCERT-style text purely
to exercise the loop; replace `.../corpus/diksha_g7_science/raw/chapters.json`
with a vetted harvest and re-run `build_index.py`.

---

## License & attribution

This project builds on the **Suno Sutra** edge-AI platform by **CurrentAI**
([upstream](https://github.com/currentai-org/suno-sutra-sw), MIT). It is an
independent derivative, not affiliated with or endorsed by CurrentAI. The
platform's large binary assets (BHASHINI models, wheels, fonts — `*.zip/*.deb/
*.whl/*.pcf`) are git-LFS files that are **not re-hosted here**; fetch them from
upstream when provisioning a device. See [ATTRIBUTION.md](ATTRIBUTION.md) for
details and [LICENSE](LICENSE) (MIT).
