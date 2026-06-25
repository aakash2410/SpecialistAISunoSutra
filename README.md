# The Specialist v1

> A grounded, offline subject expert built on the **Suno Sutra** edge-AI platform.

The Specialist turns a Suno Sutra device into a subject expert the teacher can
call into the room. The teacher asks a question by **voice** or **camera** using
a textbook page, and the device **explains**, **simplifies**, or **answers** it
aloud in the local language — e.g. *"Mujhe photosynthesis samjhaye, Hindi mein."*

Every answer comes from **approved DIKSHA content**. When the corpus doesn't
cover the question, the device **says so** instead of guessing. The whole answer
loop runs **fully offline**.

**This folder is a complete, self-contained, flashable bundle.** It contains the
entire Suno Sutra platform (`rootfs/`, `python/`, `ioexpander/`, `assets/`) with
The Specialist integrated into it as the default application — plus the
off-device DIKSHA ingestion pipeline (`ingest/`) and a laptop demo (`demo.py`).
You can provision it straight onto a Jetson; nothing outside this folder is
required.

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

## Flashing it onto a Jetson

The Specialist is the **default application**, so once provisioned the device
boots straight into it.

1. **Bake the index with the production embedder** (off-device, online, once):
   ```bash
   pip install -r python/requirements.txt        # installs sentence-transformers
   python ingest/build_index.py --model intfloat/multilingual-e5-small
   ```
   This embeds the corpus with the neural model and writes the index into
   `python/pocketinfer/specialist/corpus/diksha_g7_science/index/`, where it
   ships with the rest of the code.

2. **Flash the base image** with NVIDIA SDK Manager, then **provision** over USB
   (see [rootfs/Jetson_Flash.md](rootfs/Jetson_Flash.md) and
   [rootfs/README.md](rootfs/README.md)):
   ```bash
   cd rootfs
   ansible-playbook -i inventory.ini install_all_usb.yml
   ```
   The playbook runs the platform roles plus the new **`specialist`** role:
   `app` rsyncs `python/` (code **and** the baked corpus) to the device and
   pip-installs it; `specialist` pre-fetches the embedding model so run time is
   offline. To push code/corpus updates later: `ansible-playbook -i inventory.ini
   update_only_usb.yml`.

3. **It boots into The Specialist.** The teacher holds the trigger, asks by voice
   (or sets `input_mode` to `camera` to read a textbook page), and the device
   answers aloud — grounded, cited, and offline. To run a different app or test
   manually:
   ```bash
   pocketinfer-service --list-apps
   pocketinfer-service --app TheSpecialist
   pocketinfer-service --app TheSpecialist --setting input_language=en --setting output_language=en
   ```

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

```
Specialist v1/                     ← self-contained, flashable bundle
├── README.md                      ← this file (the product)
├── PLATFORM_README.md             ← the original Suno Sutra platform README
├── demo.py                        ← run the loop on a laptop
│
├── ingest/                        ← OFF-DEVICE build-time pipeline
│   ├── diksha_pull.py             ←   harvest a grade+subject slice (local | online)
│   ├── chunk.py                   ←   split sections into overlapping passages
│   └── build_index.py             ←   pull → chunk → embed → index
│
├── python/                        ← the Suno Sutra python platform (+ Specialist)
│   ├── requirements.txt           ←   + sentence-transformers
│   └── pocketinfer/
│       ├── service.py             ←   default app = TheSpecialist
│       ├── models/
│       │   ├── embed.py           ←   NEW embedding model (e5-small | TF-IDF fallback)
│       │   ├── ocr.py             ←   NEW BHASHINI OCR (camera → text) wrapper
│       │   └── asr.py / nmt.py / tts.py / ollama.py / vosk.py / piper.py   (reused)
│       ├── specialist/            ←   NEW runtime subpackage
│       │   ├── vector_index.py    ←     local vector search over the baked index
│       │   ├── grounding.py       ←     grounded-prompt construction + refusal
│       │   ├── pipeline.py        ←     SpecialistEngine: the board-agnostic loop
│       │   ├── llm.py             ←     stock LLM → grounded chat client
│       │   └── corpus/diksha_g7_science/{raw,index}   ← ships on device
│       └── applications/
│           └── specialist_app.py  ←   NEW @RegisterApplication device app
│
├── rootfs/                        ← provisioning / flashing (Ansible)
│   ├── install_all_usb.yml        ←   + the `specialist` role
│   ├── update_only_usb.yml        ←   + the `specialist` role
│   ├── Jetson_Flash.md            ←   step-by-step flashing guide
│   └── roles/specialist/          ←   NEW role: pre-fetch embedder, verify index
│
├── ioexpander/   assets/          ← reused platform firmware & images
```

### What's reused vs. new

| Reused from the platform (no retraining)            | New in The Specialist |
|-----------------------------------------------------|-----------------------|
| BHASHINI ASR / MT / TTS wrappers (`models/`)        | Embedding model (`models/embed.py`) |
| Stock quantised LLM via the `Ollama` wrapper        | Local vector search (`specialist/vector_index.py`) |
| Board HAL, audio, UI, application/registry/service  | OCR capture wrapper (`models/ocr.py`) |
| `hear_the_world` app structure (template)           | Grounding + refusal (`specialist/grounding.py`) |
| `rootfs` roles + flashing pipeline                  | Offline engine (`specialist/pipeline.py`), `specialist` role, DIKSHA ingestion (`ingest/`) |

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
