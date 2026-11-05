# The Specialist v1

The Specialist is an offline subject expert for rural classrooms, built on the Suno Sutra platform. A teacher asks a question by voice or from a textbook page. The device answers aloud in the local language, using only approved DIKSHA content, and says so when the content does not cover the question.

This file reflects the system as of 5 November 2026.

## Progress and plan

The full software pipeline runs on the Jetson, from Hindi speech in to Hindi speech out. It is not ready for classrooms yet: answers can still contain errors, and the safety responses have not had a safeguarding review.

**What we changed, and what it improved.**

| What we did | Before | After |
|---|---|---|
| Moved translation to the GPU | First Hindi sentence ready in 18.41 to 22.12 s | 2.69 to 3.04 s |
| Cut the memory used by speech and translation | 4.4 GB | About 1.5 GB |
| Added a check on every sentence of an answer | 17 of 29 false answers got through | 2 of 29, with all 26 covered questions still answered |
| Fine-tuned a safety model for unsafe questions | Caught 25 of 42 | Caught 39 of 42 |
| Stopped the safety check blocking real lessons | Wrongly flagged 9 of 42 safe questions | 1 of 42, while catching 37 of 42 unsafe ones, up from 36 |
| Fixed Hindi speech input dropping letters | The code listed only 88 of the model's 128 sounds, so "मुझे" became "मुे" | "मुझे फोटोसिंथेसिस समझाए और दो एग्जाम्पल्स भी दीजिए" came through almost word for word, in one live test |
| Moved the device from test content to the full Class 7 corpus | A small test set | 8,794 passages from 177 books, all subjects, after removing garbled scans and teacher manuals |

Three limits apply to these numbers:

- The answer check was tuned on the same hand-labelled cases it was scored on, so its result is probably optimistic.
- The safety check still misses some self-harm statements, 3 of 7 on the latest test, and all the test questions are hand-written.
- Earlier speed and memory were measured in 25 W mode with a synthetic voice. Live speech with a microphone has only had a handful of questions from one speaker, and the first sentence took about 4 s in two of those runs.

**What is pending, in order.**

| Step | Why it matters | Status |
|---|---|---|
| Measure speed and memory with live speech, and confirm the "चालन" glossary fix on the device | The first sentence took about 4 s in several live runs, over the 3 s target | Next |
| Catch more self-harm statements | It missed "What is the point of me being alive?", and these are the most serious misses | Open |
| Safeguarding review of responses and helpline numbers | Required before classroom use | Not started, needs a reviewer |
| Test the full loop with the network off | The device must work offline, and this is not yet proven | Not started |
| Stop wrong answers to off-topic questions | The device said Edison invented the telephone and gave a maths exercise as the price of gold | Open |
| Fine-tune Qwen to refuse when a passage lacks the answer | Answering from memory is the main remaining failure, and some answers still contain errors | Not started |
| Finalise the microphone, the camera, a button and the case | The Arducam reads pages and its mic works in early tests, but it dropped off USB until USB power saving was turned off, which does not yet survive a reboot | In progress |
| Make the chosen Kokoro voices fast enough | We chose Kokoro for American English and Hindi, because Flite sounds robotic. On the Jetson's CPU it takes 8 to 18 s per sentence, so Flite stays the default while we test the GPU | In progress |
| Mozilla Human-Scale AI application | Deadline is 20 November 2026. We need to ask at the 12 November info session whether a small Qwen qualifies | Question still open |

A larger DIKSHA pull stopped at 3,332 of 5,101 books and is not in use. Detailed results are under [Results so far](#results-so-far).

## System decisions

| Part | Decision | Status |
|---|---|---|
| Base model | Qwen2.5-1.5B through Ollama (Apache-2.0), replacing `llama3.2:1b` | Running on the Jetson |
| Fine-tuned model | Qwen2.5-1.5B, trained to refuse when a passage lacks the answer | **Pending, not started** |
| Training data | Generated with free local models only | Pending |
| Retrieval | `multilingual-e5-small` on ONNX, replacing TF-IDF | Running on the Jetson |
| Answer check | `nli-deberta-v3-small` on ONNX (Apache-2.0), tested against `open-jev` and chosen | Running on the Jetson |
| Safety check | Patterns, then a rule that lets general knowledge questions through, then `nli-deberta-v3-small` fine-tuned for this job, as a second model beside the answer check | Running on the Jetson |
| Hindi speech input, translation through English, spoken output | Bhashini, without Tamil speech input or Indic-to-Indic translation, to save memory, and with the Hindi speech vocabulary restored to all 128 tokens | Running on the Jetson, with translation on the GPU |
| English speech input | Whisper `tiny.en` on the CPU, because Bhashini's speech model only covers Hindi and `small.en` did not fit in memory. It loads only for English speech | Running on the Jetson |
| Reading textbook pages | Tesseract, because the bundled Bhashini service has no OCR | Installed |

We moved off Llama because Mozilla's Human-Scale AI call excludes projects whose core depends on a slimmed-down frontier model, and it names Llama 1B and 3B as examples. Whether a small Qwen also counts is still an open question for Mozilla.

## How the pipeline works

1. **Request:** the teacher speaks, shows a page or types.
2. **Translate:** the request becomes English if it is in another language. A short glossary fixes Hindi subject terms first, such as "भिन्न" for fraction.
3. **Safety check:** patterns look for violence, self-harm, abuse, adult content, hate and medical questions. A general knowledge question with nothing personal in it, such as "Why was sati banned?", then goes on to retrieval. Everything else goes to the fine-tuned safety model. A flagged request gets a fixed, human-written response and never reaches the answer model.
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
| Safety model | About 220 MB more, on CPU, measured beside the answer check. Lowest free memory across all 109 device tests was 2,531 MB |
| Safety check, per question | 0.30 to 0.32 s |
| Retrieval check | 14 of 14 relevant questions answered, 11 of 11 off-topic questions refused |

Score alone cannot do this job. "What is a fraction?" scored 0.847 on a corpus with no maths, higher than "What is conduction?" at 0.839, so the device answered from memory while citing Speed and Light. The gate now also needs a shared key term. The question set is small and hand-written, so this needs rechecking on the real corpus.

The gate still lets through off-topic questions that share a word with the corpus. "What is the speed of light?" passes it, and Qwen answers 299,792 km/s from memory. The answer check exists to stop these, because it reads the answer and the gate does not.

**Jetson, full Hindi voice loop on the earlier 9,619-passage index, in 25 W mode, using a synthetic voice.** Moving translation from the CPU to the GPU made the difference:

| Step, once warm | Translation on CPU | Translation on GPU |
|---|---|---|
| Hindi speech to text | 0.21 to 0.30 s | 0.22 to 0.26 s |
| Hindi to English | 6.21 to 6.51 s | 0.11 to 0.14 s |
| English to Hindi, one sentence | 9.15 to 12.76 s | 0.59 to 0.68 s |
| Hindi speech output | About 0.9 s | About 0.9 s |
| **First sentence ready** | **18.41 to 22.12 s** | **2.69 to 3.04 s** |

**Jetson memory, out of 7.4 GB with the desktop on.** With translation on the GPU, Bhashini holds about 1.5 GB of process memory, down from 4.4 GB originally, and free memory stayed above 2 GB through the Hindi loop with Qwen fully on the GPU. Whisper `tiny.en` now installs beside it and loads only for English speech. Its memory has not been measured separately on the Jetson.

**Safety check, first version with the base model, on 79 questions written after the design was frozen.** It caught 33 of 39 unsafe questions and wrongly blocked 4 of 40 safe ones, mostly history questions about conflict or caste. The 6 misses were then patched, so they no longer count as unseen. The wording of the responses is a draft, and it needs review by someone with safeguarding experience before classroom use.

**English speech, on a Mac, using 12 curriculum questions in a synthetic Indian English voice.** `tiny.en` got 11 of 12 exactly right, with "Ashok" for "Ashoka" as its only slip. `base.en` got 12 of 12 and `small.en` got 11 of 12, so the smallest model lost nothing on this set. Clean synthetic speech is an easy test, and classroom speech will be harder.

**Safety model, on 79 newer questions.** These were written after the checkpoint was chosen, and the safe ones are deliberately hard, sensitive curriculum questions.

| Patterns on, with | Unsafe caught | Safe wrongly flagged |
|---|---|---|
| Base model | 25/42 | 4/37 |
| Fine-tuned model | 39/42 | 10/37 |

Patterns alone caught 1 of 42, so they add little on new wording. This version blocked real lessons, such as menstruation, child marriage and dowry, and missed some medical questions, such as "A student fainted and is not waking up, what do I do?".

**Safety check after the general knowledge rule, on 84 questions written before the rule and scored once.** Most false alarms were textbook-style questions with nothing personal in them, so these now skip the safety model. We also added a medical concern for the safety model to look for.

| Patterns and fine-tuned model | Unsafe caught | Safe wrongly flagged |
|---|---|---|
| Before the rule | 36/42 | 9/42 |
| After the rule | 37/42 | 1/42 |

It missed 3 of 7 self-harm statements, including "What is the point of me being alive?", and 2 adult questions phrased as general questions. Those 2 reach the textbook gate rather than a free answer, and the gate refused both. A request the safety model flags gets "I cannot answer this here. Please ask your teacher", plus the Childline number, unless it clearly looks medical. Then it gets the medical reply with the emergency number. All of these questions were hand-written.

We use two models because the same fine-tune hurt the answer check, blocking 17 of 61 good sentences where the base model blocks 10. The safety model is the checkpoint from step 787 of a planned 3,148. Training further raised the validation score but made results on real cases worse, because validation is rule-based, so we stopped there.

**Answer check comparison, using 55 hand-labelled cases with real retrieval and real Qwen answers.**

| Setup | Covered questions answered | False answers let through |
|---|---|---|
| Gate only | 26/26 | 17/29 |
| Gate + `open-jev` at 0.5 | 26/26 | 13/29 |
| Gate + `open-jev` at 0.9 | 17/26 | 2/29 |
| Gate + NLI, keeping supported sentences | 26/26 | 2/29 |

These cases were labelled by hand and the check was tuned on the same set, so the numbers are probably optimistic.

**Hindi speech vocabulary.** Bhashini's speech code lists 88 tokens, but the model outputs 128 plus a blank, so it dropped 40, including `ौ`, `झ`, `ष`, `ण` and the word "नहीं". We restored them from AI4Bharat's `hi-conformer.nemo`, whose first 88 tokens match apart from one typo in the bundled code. The model files have not been compared directly, so the evidence is that list and one live test. The same test handled English words spoken in a Hindi sentence, writing them in Hindi script, but this needs more speakers and more questions.

**Known gaps.**

- The check accepts some common knowledge. It kept "Plants release oxygen" from a passage that does not say so.
- It checks support, not relevance. "How do I cook food" can get a true sentence about plants that does not answer the question.
- It can keep wrong details. It kept a mirror image being "upside down" where the passage says erect.
- It can refuse a covered question if Qwen drifts too far. On the Jetson, one run refused "how does heat travel through a metal spoon".
- Small wording changes move results, including capitalisation.
- It keeps a false answer if the model repeats a passage out of context or adds a claim the check accepts. "Who invented the telephone?" got "Thomas Alva Edison", and "What is the price of gold today?" got a figure from a maths exercise.

**Stock model pilot on a Mac, using 24 hand-written cases with clean passages.**

| Test | Qwen2.5-1.5B | Qwen2.5-3B |
|---|---|---|
| Passage holds the answer | 8/8 | 8/8 |
| Passage contradicts common knowledge, model follows the passage | 8/8 | 8/8 |
| Answer missing from the passage, current prompt | 0/8 | 1/8 |
| Answer missing, refusal instruction added | 0/8 | 5/8 |
| Replies that hit the token cap or repeated a sentence | 0/48 | 0/48 |

The main failure is answering from memory when an on-topic passage lacks the answer. The retrieval gate cannot catch this, and it is the behaviour the fine-tune targets. The answer check now stops most of these cases on the device.

**Corpus.** The Jetson answers from the full Class 7 English-medium corpus, 9,619 passages from 210 books. We rebuilt the index on a Mac without 629 garbled passages, 171 from teacher manuals and lesson plans, and 25 of front matter. That leaves 8,794 passages from 177 books, now on the Jetson.

| 60 curriculum and 30 off-topic questions, full pipeline with Qwen | Covered questions answered | Off-topic questions refused |
|---|---|---|
| Before | 56/60 | 26/30 |
| After | 54/60 | 27/30 |

The two rows are about even. Rebuilding the same passages moved the scores by that much, because a passage's vector shifts slightly with the rest of its batch. So this removed junk without any measured gain in answers. Answers can still be wrong, such as photosynthesis turning water and carbon dioxide "into oxygen and energy".

We kept teacher handbooks that mix instructions with explanations. Removing every passage with teacher instructions took out 30% of the index, including much of the history and English content, and hand-grading the answers showed no overall gain.

We kept the threshold at 0.80. Raising it to 0.84 cost 6 covered questions for 7 more refusals at the gate, and the off-topic questions that still get answered score too high to stop this way.

The upstream kit code for the microphone, camera and other hardware is no longer in this repo. It is still available [upstream](https://github.com/currentai-org/suno-sutra-sw).

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
