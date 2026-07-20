# Local voice loop (laptop harness)

Run the full Specialist loop on a laptop — no device required:

```
ASR  →  MT (optional)  →  Specialist  →  MT (optional)  →  TTS
```

Every stage is a **swappable provider** implementing a contract in
[`pocketinfer/specialist/providers.py`](../python/pocketinfer/specialist/providers.py).
The laptop uses offline backends (Vosk / macOS `say` / no-op MT / extractive
LLM); selecting the `bhashini` providers runs the *same* loop against the
device's BHASHINI service. That's the interoperability goal: components are
interchangeable by name, not rewrites.

## Setup

```bash
pip install -r local/requirements-local.txt      # numpy, sounddevice, vosk

# offline speech-to-text model (once):
mkdir -p local/models && cd local/models
curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && cd ../..

python ingest/build_index.py                      # bake the DIKSHA index (once)
```

TTS uses the built-in macOS `say` command — nothing to install.

## Run it

```bash
# Live mic — press Enter to start, Enter to stop, speak your question:
python -m local.voice_loop

# Or feed a custom testcase without a mic:
python -m local.voice_loop --text "What is photosynthesis?"
python -m local.voice_loop --say-question "how is heat transferred"   # synthesized speech → ASR
python -m local.voice_loop --from-wav question.wav
```

Each turn prints every stage:

```
[providers] asr=vosk  mt=none  tts=say  llm=extractive  embed=tfidf  in=en out=en
[1 ASR ] (320ms, 2.1s audio) -> 'what is photosynthesis'
[3 SPEC] (explain) -> Photosynthesis is the process by which green plants make their own food...
          · DIKSHA do_31309878131235 · Chapter 1 — Nutrition in Plants · 1.2 Photosynthesis (p.2)
[5 TTS ] speaking via say (en)...
[time ] specialist: embed=0ms, retrieve=1ms, llm=0ms, total=1ms
```

Off-syllabus questions are refused (the Specialist only answers from DIKSHA).

## Swap components

```bash
--asr  vosk|bhashini        # speech-to-text
--mt   none|bhashini        # translation (optional; 'none' = passthrough)
--tts  say|bhashini         # text-to-speech
--llm  extractive|ollama    # answer generation (ollama = real grounded LLM)
```

Examples:

```bash
# Use a real grounded LLM (needs `ollama serve` + the model pulled):
python -m local.voice_loop --text "explain photosynthesis simply" --llm ollama --ollama-model qwen3-vl:2b

# Hindi in and out (needs the BHASHINI service on :11400, i.e. on the device):
python -m local.voice_loop --asr bhashini --mt bhashini --tts bhashini --in-lang hi --out-lang hi
```

## How the loop maps to code

| Stage | Contract | Laptop default | Device |
|-------|----------|----------------|--------|
| capture | `MicSource` | `SoundDeviceMic` | board mic |
| ASR | `ASRProvider` | `VoskASR` | `BhashiniASR` |
| MT | `MTProvider` | `NoopMT` | `BhashiniMT` |
| Specialist | `SpecialistEngine` | (same) | (same) |
| MT | `MTProvider` | `NoopMT` | `BhashiniMT` |
| TTS | `TTSProvider` | `MacSayTTS` | `BhashiniTTS` |
| LLM | `LLMProvider` | `extractive_llm` | `ollama_grounded_client` |

The Specialist core (embed → retrieve → ground → refuse) is identical in both;
only the surrounding providers change.
