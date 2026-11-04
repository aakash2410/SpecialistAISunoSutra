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
| Answer check | `nli-deberta-v3-small` on ONNX (Apache-2.0), tested against `open-jev` and chosen | Running on the Jetson |
| Hindi speech input, translation through English, spoken output | Bhashini, without Tamil speech input or Indic-to-Indic translation, to save memory | Installed, memory under the full stack not yet measured |
| English speech input | Left out: Whisper does not fit in memory beside the rest of the pipeline, and Bhashini's speech model only covers Hindi | Removed. Typed English still works |
| Reading textbook pages | Tesseract, because the bundled Bhashini service has no OCR | Installed |

We moved off Llama because Mozilla's Human-Scale AI call excludes projects whose core depends on a slimmed-down frontier model, and it names Llama 1B and 3B as examples. Whether a small Qwen also counts is still an open question for Mozilla.

## How the pipeline works

1. **Request:** the teacher speaks, shows a page or types.
2. **Translate:** the request becomes English if it is in another language.
3. **Safety check:** patterns and the NLI model look for violence, self-harm, abuse, adult content, hate and medical questions. A flagged request gets a fixed, human-written response and never reaches the answer model.
4. **Retrieve:** e5 embeds the request and searches the local DIKSHA index.
5. **Gate:** the device refuses unless a passage both scores above the threshold (0.80) and shares a key term with the request. Only those passages reach the model and the citations.
6. **Answer:** Qwen rephrases the retrieved passages in English.
7. **Check:** each sentence must be supported by a passage. Unsupported sentences get dropped, and the device refuses if none survive.
8. **Respond:** the kept sentences get translated, spoken aloud and cited. Only the passages that support them are cited.

Knowledge lives in the index, not the model, so new content needs no retraining. No step needs the internet at run time by design, but we have not yet tested this with the network off.

## Results so far

**Jetson Orin Nano 8GB Super in 25 W mode, typed English.**

| Measure | Result |
|---|---|
| First sentence, with the answer check | 0.73 to 1.14 s |
| Full answer, with the answer check | 1.64 to 2.45 s |
| Answer check, per answer | 0.29 to 0.82 s |
| Refusal after the check | 1.47 to 2.09 s |
| Embedding a question, on CPU | About 11 ms |
| Answer model | 100% on GPU |
| Answer check model | About 600 MB of memory, on CPU |
| Retrieval check | 14 of 14 relevant questions answered, 11 of 11 off-topic questions refused |

Score alone cannot do this job. "What is a fraction?" scored 0.847 on a corpus with no maths, higher than "What is conduction?" at 0.839, so the device answered from memory while citing Speed and Light. The gate now also needs a shared key term. The question set is small and hand-written, so this needs rechecking on the real corpus.

The gate still lets through off-topic questions that share a word with the corpus. "What is the speed of light?" passes it, and Qwen answers 299,792 km/s from memory. The answer check exists to stop these, because it reads the answer and the gate does not.

**Jetson, full Hindi voice loop on the real 9,619-passage index, in 25 W mode, using a synthetic voice.** Moving translation from the CPU to the GPU made the difference:

| Step, once warm | Translation on CPU | Translation on GPU |
|---|---|---|
| Hindi speech to text | 0.21 to 0.30 s | 0.22 to 0.26 s |
| Hindi to English | 6.21 to 6.51 s | 0.11 to 0.14 s |
| English to Hindi, one sentence | 9.15 to 12.76 s | 0.59 to 0.68 s |
| Hindi speech output | About 0.9 s | About 0.9 s |
| **First sentence ready** | **18.41 to 22.12 s** | **2.69 to 3.04 s** |

**Jetson memory, out of 7.4 GB with the desktop on.** With translation on the GPU, Bhashini holds about 1.5 GB of process memory, down from 4.4 GB originally, and free memory stayed above 2 GB through the Hindi loop with Qwen fully on the GPU. Whisper was left out when Bhashini was larger, because it pushed Qwen 31% onto the CPU, and it has not been retested since this change.

**Safety check, on 79 questions written after the design was frozen.** It caught 33 of 39 unsafe questions and wrongly blocked 4 of 40 safe ones, mostly history questions about conflict or caste. The 6 misses were then patched, so they no longer count as unseen. The wording of the responses is a draft, and it needs review by someone with safeguarding experience before classroom use.

**Answer check comparison, using 55 hand-labelled cases with real retrieval and real Qwen answers.**

| Setup | Covered questions answered | False answers let through |
|---|---|---|
| Gate only | 26/26 | 17/29 |
| Gate + `open-jev` at 0.5 | 26/26 | 13/29 |
| Gate + `open-jev` at 0.9 | 17/26 | 2/29 |
| Gate + NLI, keeping supported sentences | 26/26 | 2/29 |

These cases were labelled by hand and the check was tuned on the same set, so the numbers are probably optimistic.

**Known gaps.**

- The check accepts some common knowledge. It kept "Plants release oxygen" from a passage that does not say so.
- It checks support, not relevance. "How do I cook food" can get a true sentence about plants that does not answer the question.
- It can keep wrong details. It kept a mirror image being "upside down" where the passage says erect.
- It can refuse a covered question if Qwen drifts too far. On the Jetson, one run refused "how does heat travel through a metal spoon".
- Small wording changes move results, including capitalisation.

**Stock model pilot on a Mac, using 24 hand-written cases with clean passages.**

| Test | Qwen2.5-1.5B | Qwen2.5-3B |
|---|---|---|
| Passage holds the answer | 8/8 | 8/8 |
| Passage contradicts common knowledge, model follows the passage | 8/8 | 8/8 |
| Answer missing from the passage, current prompt | 0/8 | 1/8 |
| Answer missing, refusal instruction added | 0/8 | 5/8 |
| Replies that hit the token cap or repeated a sentence | 0/48 | 0/48 |

The main failure is answering from memory when an on-topic passage lacks the answer. The retrieval gate cannot catch this, and it is the behaviour the fine-tune targets. The answer check now stops most of these cases on the device.

**Corpus.** The Jetson now answers from the full Class 7 English-medium corpus: 9,619 passages from 3,454 pages across 210 books and all subjects. A spot check of 16 questions refused all 5 off-topic ones, but some sources are noisy scanned text or teacher manuals, and some answers still contain errors the answer check missed, such as conduction described with the direction of heat flow wrong.

## Next steps

Software first, with no hardware needed:

1. Run the system integration and acceptance tests on the Jetson.
2. Test voice using audio files.
3. Test the full loop with the network off.
4. Clean and index the real corpus, then retune the threshold and recheck the answer check.
5. Resume fine-tuning when we decide to.

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
| `python/pocketinfer/` | Retrieval, grounding, answer check, answer engine, model wrappers |
| `run_specialist.py` | Typed questions in, grounded answers out |
| `local/` | The full voice loop, from a WAV file, page image or microphone |
| `corpus/`, `ingest/` | DIKSHA content and the pipeline that builds the index |
| `module/`, `scripts/`, `tools/` | Device install, packaging, model downloads |
| `tests/` | System integration and user acceptance tests, no unit tests |

## Licence

This project builds on [Suno Sutra](https://github.com/currentai-org/suno-sutra-sw) by CurrentAI (MIT). It is an independent derivative, not affiliated with or endorsed by CurrentAI. See [ATTRIBUTION.md](ATTRIBUTION.md) and [LICENSE](LICENSE).
