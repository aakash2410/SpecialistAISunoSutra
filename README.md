# The Specialist v1

The Specialist is an offline subject expert for rural classrooms, built on the Suno Sutra platform. A teacher asks a question by voice or from a textbook page. The device answers aloud in the local language, using only approved DIKSHA content, and says so when the content does not cover the question.

This file reflects the system as of 4 November 2026.

## System decisions

| Part | Decision | Status |
|---|---|---|
| Base model | Qwen2.5-1.5B through Ollama (Apache-2.0), replacing `llama3.2:1b` | Running on the Jetson |
| Fine-tuned model | Qwen2.5-1.5B, trained to refuse when a passage lacks the answer | **Pending, not started** |
| Training data | Generated with free local models only | Pending |
| Retrieval | `multilingual-e5-small` on ONNX, replacing TF-IDF | Running on the Jetson |
| Hindi and Tamil speech input, translation, spoken output | Bhashini | Downloading |
| English speech input | Whisper `small.en` | Not installed |
| Reading textbook pages | Tesseract, because the bundled Bhashini service has no OCR | Code updated, not installed |

We moved off Llama because Mozilla's Human-Scale AI call excludes projects whose core depends on a slimmed-down frontier model, and it names Llama 1B and 3B as examples. Whether a small Qwen also counts is still an open question for Mozilla.

## How the pipeline works

1. **Request:** the teacher speaks, shows a page or types.
2. **Translate:** the request becomes English if it is in another language.
3. **Retrieve:** e5 embeds the request and searches the local DIKSHA index.
4. **Gate:** the device refuses if the best passage scores below the threshold (0.84).
5. **Answer:** Qwen rephrases the retrieved passages in English.
6. **Respond:** the answer gets translated, spoken aloud and cited.

Knowledge lives in the index, not the model, so new content needs no retraining. No step needs the internet at run time by design, but we have not yet tested this with the network off.

## Results so far

**Jetson Orin Nano 8GB Super, typed English only. Voice has not been measured.**

| Measure | Result |
|---|---|
| First sentence | 1.09 s |
| Full answer | 1.83 to 1.94 s |
| Embedding a question, on CPU | About 11 ms |
| Answer model | 100% on GPU |
| Retrieval check | 5 of 5 relevant questions answered, 4 of 4 off-topic questions refused |

The margins are thin. A romanised Hindi question scored exactly 0.84, and one off-topic question reached 0.804. The threshold was tuned on the same 10 passages and needs retuning on the real corpus.

**Stock model pilot on a Mac, using 24 hand-written cases with clean passages.**

| Test | Qwen2.5-1.5B | Qwen2.5-3B |
|---|---|---|
| Passage holds the answer | 8/8 | 8/8 |
| Passage contradicts common knowledge, model follows the passage | 8/8 | 8/8 |
| Answer missing from the passage, current prompt | 0/8 | 1/8 |
| Answer missing, refusal instruction added | 0/8 | 5/8 |
| Replies that hit the token cap or repeated a sentence | 0/48 | 0/48 |

The main failure is answering from memory when an on-topic passage lacks the answer. The retrieval gate cannot catch this, and it is the behaviour the fine-tune targets. The acceptance tests record this as a known failure until the fine-tune clears it.

**Corpus.** The device holds a 10-passage starter set. We have 5,000 harvested pages from 232 books that are not indexed yet, and about 36% of the CC BY pages look garbled. DIKSHA lists 5,101 Class 7 English PDFs.

## Next steps

Software first, with no hardware needed:

1. Finish the Bhashini download (1.65 GB).
2. Run the Ansible role to install Bhashini, Whisper and Tesseract. It has not been run on a device yet.
3. Test voice using audio files.
4. Test the full loop with the network off.
5. Clean and index the real corpus, then retune the threshold.
6. Resume fine-tuning when we decide to.

Hardware after that: a microphone, speaker and camera, plus a decision on a button and screen. The upstream kit code for these is no longer in this repo. It is still available [upstream](https://github.com/currentai-org/suno-sutra-sw).

## Run it

After the install, on the Jetson:

```bash
specialist "What is photosynthesis?"
```

```bash
specialist-voice --in-lang hi --out-lang hi --from-wav question.wav
```

Install steps are in [module/README.md](module/README.md).

## What is in this repo

| Folder | Purpose |
|---|---|
| `python/pocketinfer/` | Retrieval, grounding, answer engine, model wrappers |
| `run_specialist.py` | Typed questions in, grounded answers out |
| `local/` | The full voice loop, from a WAV file, page image or microphone |
| `corpus/`, `ingest/` | DIKSHA content and the pipeline that builds the index |
| `module/`, `scripts/`, `tools/` | Device install, packaging, model downloads |
| `tests/` | System integration and user acceptance tests, no unit tests |

## Licence

This project builds on [Suno Sutra](https://github.com/currentai-org/suno-sutra-sw) by CurrentAI (MIT). It is an independent derivative, not affiliated with or endorsed by CurrentAI. See [ATTRIBUTION.md](ATTRIBUTION.md) and [LICENSE](LICENSE).
