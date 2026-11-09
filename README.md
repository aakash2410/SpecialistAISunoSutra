# The Specialist v1

An offline subject expert for rural classrooms, built on the Suno Sutra platform. A teacher asks a question by voice or from a textbook page. The device answers aloud in Hindi or English, using only approved DIKSHA content, and says so when the content does not cover the question.

## Status, 9 November 2026

The full loop runs on a Jetson Orin Nano with the network off, from live Hindi or English speech to a spoken answer. It is not ready for classrooms: answers can still be wrong, the safety check misses some self-harm statements, and the safety replies have not had a safeguarding review.

| Measure | Result |
|---|---|
| First Hindi sentence spoken, from a recorded question | 2.42 s |
| Device tests, 6 November | 113 passed, 1 expected failure, 2 skipped |
| Lowest free memory during those tests | 2,766 MB of 7.4 GB |
| Search and gate, full pipeline, Mac | 53 of 60 on-topic questions answered, 26 of 30 off-topic refused |
| Answer check, blind test, with fact and relevance checks | Keeps 62 of 80 supported on-question sentences, drops 23 of 40 off-question, lets through 24 of 120 unsupported |
| Safety check, fresh holdout written after the rules were frozen | Caught 12 of 14 unsafe, wrongly flagged 0 of 14 safe |

## How it works

![How The Specialist answers a question](docs/architecture.png)

1. **Translate:** a Hindi request becomes English, with a glossary fixing subject terms first.
2. **Safety check:** a flagged request gets a fixed, hand-written reply and never reaches the answer model.
3. **Retrieve:** e5 searches the local DIKSHA index. Questions about today are refused.
4. **Gate:** a passage must score at least 0.80 and share a key term with the request.
5. **Answer:** Qwen rephrases the passages, one sentence at a time.
6. **Check:** any sentence no passage supports is dropped, along with any that flips a number, name, direction or negation against its passage. The first sentence must also be close to the question. If none survive, the device searches once more for the question's own phrase, then refuses.
7. **Respond:** each kept sentence is translated and spoken while the next is written.

Follow-ups are rewritten from the last 3 turns, then go through every step. Memory stays in RAM and clears after 3 minutes, on "new topic" or "नया सवाल", at reboot, and after any safety flag.

![How The Specialist keeps students safe](docs/safety.png)

## What runs where

| Part | Choice | Runs on |
|---|---|---|
| Answer model | Qwen2.5-1.5B through Ollama, swappable with one setting | GPU |
| Retrieval | `multilingual-e5-small`, 8,794 Class 7 passages | CPU |
| Answer check | `nli-deberta-v3-base`, threshold 0.9 | CPU |
| Safety check | Patterns, a general question rule, then `nli-deberta-v3-small` fine-tuned for safety | CPU |
| Hindi speech and translation | Bhashini | GPU for translation |
| English speech | Whisper `tiny.en` | CPU |
| Spoken output | Flite through Bhashini | CPU |
| Page reading | Tesseract, Hindi and English | CPU |

**Tried and dropped:** `llama3.2:1b` (excluded by Mozilla's call), Pleias-RAG-1B (far slower, with basic errors), Kokoro (about 1 s slower), Piper's Rohan voice (stuttered in English, licence unclear).

## Known gaps

- The answer check can keep wrong details and common knowledge the passage never states.
- It still says "Thomas Alva Edison" for "Who invented the telephone?", and still answers "How do I cook food".
- The safety check still misses some indirect statements, such as "What happens if a person stops eating on purpose until the end?".
- The index covers only Class 7, so basics taught in other grades get refused.
- Live speech has been tested with very few speakers.

## Pending

1. Make the voice sound human, the top feedback from Bombay.
2. Test SmolLM3 as a fallback answer model, before 12 November.
3. Mozilla Human-Scale AI application, due 20 November. Ask at the 12 November session whether a small Qwen qualifies.
4. Add Class 6 and Class 8 science to the index.
5. Catch more self-harm statements.
6. Find a safeguarding reviewer for the replies and helpline numbers.
7. Test page reading with the camera in the full loop. Picking one question off a page is built, and tested only on sample text and one image.
8. Fine-tune the answer check. Qwen's refusal fine-tune is on hold.
9. Decide on a battery, and finish the case and talk button.
10. Later: profiles that make the module reusable for other jobs.

## Hardware

| Part | What we use |
|---|---|
| Computer | Jetson Orin Nano 8GB Super Developer Kit, 25 W mode, mains powered. Battery under discussion |
| Microphone and camera | Arducam 8MP USB camera (B029202) |
| Speaker | USB speaker |
| Case | 3D-printed. See the [case spec](https://claude.ai/artifact/3D3fcDWkQKapE3nWWFvjEt), private until shared |

## Run it

Install steps are in [module/README.md](module/README.md). Then, on the Jetson:

```bash
specialist-voice --in-lang hi --out-lang hi
```

Press Enter to start recording and Enter again to stop. To type a question:

```bash
specialist-voice --no-speak --text "What is photosynthesis?"
```

Use `--from-wav question.wav` for a recording or `--from-image page.jpg` for a page photo. Tests are in [tests/README.md](tests/README.md).

## Licence

Builds on [Suno Sutra](https://github.com/currentai-org/suno-sutra-sw) by CurrentAI (MIT). It is an independent derivative, not affiliated with or endorsed by CurrentAI. See [ATTRIBUTION.md](ATTRIBUTION.md) and [LICENSE](LICENSE).
