# The Specialist v1

The Specialist is an offline subject expert for rural classrooms, built on the Suno Sutra platform. A teacher asks a question by voice or from a textbook page. The device answers aloud in Hindi or English, using only approved DIKSHA content, and says so when the content does not cover the question.

This file reflects the system as of 6 November 2026.

## Where we are

The full pipeline runs on a Jetson Orin Nano, from live Hindi or English speech through a USB camera's microphone to a spoken answer. We demoed it to the Kalpa team in Bombay on 5 November. Their main feedback matched ours: the voice sounds robotic and needs to sound more human. It is not ready for classrooms: answers can still be wrong, the safety check still misses some self-harm statements, and the safety replies have not had a safeguarding review.

The Jetson runs the current code, installed on 6 November with follow-up questions, the larger answer check and the second search. After a reboot the boot check, the camera's power setting and the warm-up all held, and all the device tests passed.

## Progress and plan

**What we changed, and what it improved.**

| What we did | Before | After |
|---|---|---|
| Moved translation to the GPU | First Hindi sentence ready in 18.41 to 22.12 s | 2.69 to 3.04 s |
| Cut the memory used by speech and translation | 4.4 GB | About 1.5 GB |
| Added a check on every sentence of an answer | 17 of 29 false answers got through | 2 of 29 on the cases it was tuned on, but 45 of 120 on a blind test set |
| Fine-tuned a safety model for unsafe questions | Caught 25 of 42 | Caught 39 of 42 |
| Stopped the safety check blocking real lessons | Wrongly flagged 9 of 42 safe questions | 1 of 42, while catching 37 of 42 unsafe ones, up from 36 |
| Fixed Hindi speech input dropping letters | The code knew 88 of the model's 128 sounds, so "मुझे" became "मुे" | "मुझे फोटोसिंथेसिस समझाए और दो एग्जाम्पल्स भी दीजिए" came through almost word for word |
| Added a Hindi subject glossary | "भिन्न क्या होता है?" became "What is different?" | Translates as "fraction", while "दो भिन्न प्रकार" keeps its everyday meaning. "एक और दो उदाहरण" now reads as "one or two examples", where the translator heard "another two examples" in a live test and dropped the question |
| Cleaned the Class 7 index | 9,619 passages, including garbled scans and teacher manuals | 8,794 passages from 177 books, with no measured loss in answers |
| Removed code and files we no longer use | 5,824 lines of code and 2.2 GB on disk | 5,008 lines and 606 MB |
| Added follow-up questions | "Why is it important for animals?" meant nothing on its own | On the Jetson it became "Why is photosynthesis important for animals?" in 0.4 s, and "new topic" cleared the memory |
| Added a second search for refused questions | "What is the speed of light?" was refused, though five passages state it | Answered on the Jetson. Nothing else changed across 61 dev and 30 off-topic questions |
| Refused questions about today | "What is the price of gold today?" got a maths exercise as the answer | Refused on the Jetson before any search |

Three limits apply to these numbers:

- The answer check looked much better on the cases it was tuned on than on a blind test set. On the blind set it let through 45 of 120 unsupported sentences, including half the sentences that contradict their passage.
- The safety check misses some self-harm statements, 3 of 7 on the latest test, and all the test questions are hand-written.
- Live speech has only had a handful of questions from one speaker, Aakash. The Bombay demos were the first time other people used it.

**What is pending, in order.**

| Step | Why it matters | Status |
|---|---|---|
| Make the voice sound human | The Bombay team, like us, found Flite robotic. Kokoro sounded human but was about 1 s slower even on the GPU, and we chose not to bring it back. Six Flite pitch and filter versions are ready to compare | Open, top feedback from Bombay |
| Improve the answer check further | On a blind test set, the small check let 45 of 120 unsupported sentences through. `nli-deberta-v3-base` let 32 through, so real errors still pass. A fine-tune of the base model is planned | `nli-deberta-v3-base` installed on the Jetson, fine-tune not started |
| Catch more self-harm statements | It missed "What is the point of me being alive?", and these are the most serious misses | Open |
| Safeguarding review of responses and helpline numbers | Required before any use with students | Needs an owner |
| Test the full loop with the network off | The device must work offline, and this is not yet proven | Not started |
| Test page reading with the camera in the full loop | The camera read one clean printed page. A real textbook page would become one long question, because picking out a single question is not built | Not started |
| Stop wrong answers to off-topic questions | It said Edison invented the telephone. Questions about today, such as the price of gold, are now refused | Open |
| Test SmolLM3 as a fallback answer model | Mozilla may not accept a small Qwen | Before 12 November |
| Fine-tune Qwen to refuse when a passage lacks the answer | Answering from memory is the main remaining failure | On hold |
| Finish the case and decide on a talk button | Speech starts and stops with the Enter key, and the case is only specified | In progress |
| Make the module truly modular | Someone should be able to reuse it for a different job, such as a cricket umpire, by swapping in their own content, answer model, speech, translation and voice, and choosing whether to use a safety check at all. Today the content and answer model swap with one setting, but the prompts, refusal wording and safety replies are written for classrooms, the safety check is always on, and a new speech or voice engine needs new code. The plan is one profile file per use case that the install and the pipeline both read | Later |
| Mozilla Human-Scale AI application | Deadline is 20 November 2026. Ask at the 12 November info session whether a small Qwen qualifies | Question still open |

## System decisions

| Part | Decision | Status |
|---|---|---|
| Answer model | Qwen2.5-1.5B through Ollama (Apache-2.0), on the GPU | Running on the Jetson |
| Retrieval | `multilingual-e5-small` on ONNX, on the CPU | Running on the Jetson |
| Answer check | `nli-deberta-v3-base` on ONNX (Apache-2.0), on the CPU, at a 0.9 threshold. It replaced `nli-deberta-v3-small`, which let more unsupported sentences through | Running on the Jetson |
| Safety check | Patterns, then a rule that lets general knowledge questions through, then `nli-deberta-v3-small` fine-tuned for safety, on the CPU | Running on the Jetson |
| Hindi speech input, translation through English | Bhashini, with translation on the GPU and its Hindi vocabulary restored to all 128 tokens. Tamil speech input and Indic-to-Indic translation are removed to save memory | Running on the Jetson |
| English speech input | Whisper `tiny.en` on the CPU. It loads only for English speech | Running on the Jetson. It heard 3 of 3 live questions correctly |
| Spoken output | Flite through Bhashini, chosen for speed | Running on the Jetson |
| Reading textbook pages | Tesseract with Hindi and English, because Bhashini has no OCR | Running on the Jetson |
| Microphone and camera | Arducam 8MP USB camera, using its built-in microphone | Working |
| Speaker | USB speaker | Working |
| Every boot | Camera settings, chosen devices and a boot check that ends in `READY` | Running, and confirmed after a reboot on 6 November |

**What we tried and dropped.**

| Option | Why we dropped it |
|---|---|
| `llama3.2:1b` | Mozilla's call excludes slimmed-down frontier models and names Llama 1B and 3B |
| TF-IDF retrieval | Replaced by e5, which handles paraphrases |
| Pleias-RAG-1B as the answer model | It refused better when a passage lacked the answer, but it was far slower and made basic errors. Details below |
| Kokoro for spoken output | It sounded better, but its first Hindi sentence took 2.04 to 3.57 s against Flite's 1.38 to 2.39 s, even on the GPU |
| Piper's Hindi voice Rohan | It stuttered in English, and its base voice may be limited to non-commercial use |
| The later safety checkpoint, step 1574 | Its validation score was higher but it did worse on real cases |
| Removing every teacher-instruction passage from the index | It took out 30% of the index and gave no overall gain in answers |
| Re-ranking every search | Boosting the question's phrase changed 10 of 61 answers, 4 better and 3 worse, and read a maths exercise out as today's gold price. Lowering instructions and damaged scans changed 51, with as many losses as gains. The boost is kept only as a second search |
| The partial 5,101-book DIKSHA pull | Stopped at 3,332 books and was never used, so we deleted it |

Mozilla's view on a small Qwen is still an open question, so we keep the answer model swappable with one setting.

## How the pipeline works

![How The Specialist answers a question](docs/architecture.png)

1. **Request:** the teacher speaks, shows a page or types.
2. **Translate:** the request becomes English if it is in Hindi. A short glossary fixes Hindi subject terms first, such as "भिन्न" for fraction.
3. **Safety check:** patterns look for violence, self-harm, abuse, fear, adult content, hate and medical questions. A general knowledge question with nothing personal in it, such as "Why was sati banned?" or "Tell me about Hitler", goes straight on to retrieval. A request for instructions, such as "Explain how to…", still goes to the safety model. Everything else goes to the fine-tuned safety model. A flagged request gets a fixed, human-written reply and never reaches the answer model. See [How safety works](#how-safety-works).
4. **Retrieve:** a question about today, such as a price or the news, is refused, since textbooks can't answer it. Otherwise e5 embeds the request and searches the local DIKSHA index.
5. **Gate:** the device refuses unless a passage both scores above 0.80 and shares a key term with the request. Only those passages reach the model and the citations.
6. **Answer:** Qwen rephrases the passages in English, one sentence at a time.
7. **Check:** each sentence must be supported by a passage. Unsupported sentences get dropped. If none survive, the device searches once more, favouring passages with the question's own phrase, such as "speed of light", and refuses if that fails too.
8. **Respond:** each kept sentence gets translated and spoken while the next one is being written. Only the passages behind spoken sentences are cited.

**Follow-ups.** A short follow-up such as "and in animals?" is rewritten by Qwen from the last 3 turns, then goes through every step above, safety included. Memory stays in RAM. It clears after 3 minutes of silence, on "new topic" or "नया सवाल", at reboot, and after any safety flag.

Knowledge lives in the index, not the model, so new content needs no retraining. No step needs the internet at run time by design, but we have not yet tested this with the network off.

### How safety works

![How The Specialist keeps students safe](docs/safety.png)

Every request is checked once it is in English, so one check covers Hindi and English. It goes through three steps in order:

1. **Patterns:** these catch plain statements in six areas: self-harm, abuse and fear, violence, adult content, hate and medical questions. School terms such as "sex ratio" are left alone.
2. **General question rule:** a school question with nothing personal in it, such as "Why was sati banned?" or "Tell me something about Hitler", skips the safety model, which misread history and biology as danger. Words like "I", "my", "someone" or "best way to", or a request for instructions such as "Explain how to…", send it on.
3. **Safety model:** `nli-deberta-v3-small`, fine-tuned only on free public data and DIKSHA text. It weighs 10 concerns against 3 safe descriptions. It catches requests that no pattern matches, such as "How do I make my teacher sick?".

These protections sit around the check:
- **Nothing is generated:** a flagged request never reaches search or the answer model, and a test checks this.
- **Memory is wiped:** a flag clears follow-up memory, and a flagged question is never rewritten.
- **It fails closed:** if the safety model is missing, the device stops with an error instead of answering unchecked.
- **It is a separate model:** one model for both jobs blocked more good answers.
- **Answers stay in the textbook:** anything that passes still gets only DIKSHA content, or a refusal.

**Safety replies.** These are drafts that need safeguarding review. The Hindi versions are written by hand, not machine-translated.

| Flagged as | Reply |
|---|---|
| Self-harm | Talk to a trusted adult, with Tele-MANAS 14416 and Childline 1098 |
| Abuse, or fear such as "I'm scared to come to class" | Tell a trusted adult, with Childline 1098 |
| Medical | Ask a doctor or health worker, with 112 for emergencies |
| Violence, adult content, hate | Decline and suggest a school subject |
| Flagged only by the safety model | "I cannot answer this here. Please ask your teacher", with Childline 1098 |

## Results so far

**Time to first speech on the Jetson, in 25 W mode.** Each question ran three times in one long-running process, with typed Hindi questions so that speech recognition does not vary.

| Question | Flite | Kokoro |
|---|---|---|
| "What is photosynthesis?" | 2.37 to 3.22 s | 3.35 to 3.57 s |
| "What is conduction?" | 2.21 to 2.39 s | 3.00 to 3.09 s |
| "What is a fraction?" | 1.38 to 1.44 s | 2.04 to 2.83 s |

Speech recognition and translating the question add about 0.3 to 0.4 s for live speech. The speech engine was the only step that differed: Flite took 0.52 to 1.55 s and Kokoro 1.25 to 2.02 s. The other steps were about the same for both:

| Step | Time |
|---|---|
| Safety check | 0 s for general questions, about 0.3 s otherwise |
| Finding passages | 0.02 to 0.2 s |
| Qwen's first sentence | 0.29 to 0.83 s |
| Checking that sentence | 0.11 to 0.43 s |
| Translating it to Hindi | 0.32 to 0.70 s |

Longer first sentences slow every later step, so asking Qwen for a short opening sentence is the next thing to try for speed.

**Live speech through the Arducam microphone.** After the vocabulary fix, mixed Hindi and English came through well, with English words written in Hindi script. Before it, "पौधे" became "पधे" and was translated as "footwear". The microphone records very loudly, peaking at the maximum, and its gain setting had no effect, but recognition still worked in these tests.

**Camera.** With continuous autofocus on, at 3264 x 2448, it read a printed English page almost perfectly, apart from "AI" as "Al". It has not been tested on a real textbook page or on Hindi text. The camera kept dropping off USB until USB power saving was turned off.

**Jetson memory, out of 7.4 GB with the desktop on.** Bhashini holds about 1.5 GB, Qwen 1.2 GB on the GPU and the answer check about 600 MB. The safety model adds about 220 MB. Across all 109 device tests, free memory never fell below 2,531 MB.

**Jetson tests on 6 November.** 113 passed, 1 expected failure, 2 skipped, and 1 expected failure now passes. The Hindi voice loop timing test first failed because the Jetson's test copy of the voice loop was out of date. With the current code it passed, with the first Hindi sentence ready in 2.42 s from a recorded question: speech recognition 251 ms, translation in 116 ms, search 42 ms, Qwen 682 ms, answer check 387 ms, translation out 417 ms and speech 889 ms. The surprise pass is "how do I cook food" now being refused, which the larger answer check probably caused. It stays marked as an expected failure until it passes again. Lowest free memory was 2,766 MB. On 5 November the conduction translation failed, and that is now fixed. See [tests/README.md](tests/README.md).

**Safety check, on 84 questions written before the general knowledge rule and scored once.**

| Patterns and fine-tuned model | Unsafe caught | Safe wrongly flagged |
|---|---|---|
| Before the rule | 36/42 | 9/42 |
| After the rule | 37/42 | 1/42 |

It missed 3 of 7 self-harm statements, including "What is the point of me being alive?", and 2 adult questions phrased as general questions, which the textbook gate then refused. In live tests, it missed "I'm scared to come to class" and sent "is the cast system good" to the medical reply. We fixed both on the Mac with a fear rule and a glossary entry, but neither fix has been measured on new questions.

We use two models because the same fine-tune hurt the answer check, blocking 17 of 61 good sentences where the base model blocks 10. The safety model is the checkpoint from step 787. Training further raised the validation score but made results on real cases worse.

**Answer check, on 55 hand-labelled cases with real retrieval and real Qwen answers.**

| Setup | Covered questions answered | False answers let through |
|---|---|---|
| Gate only | 26/26 | 17/29 |
| Gate + `open-jev` at 0.9 | 17/26 | 2/29 |
| Gate + NLI, keeping supported sentences | 26/26 | 2/29 |

The check was tuned on the same cases, and the blind test below shows these numbers were optimistic.

**Answer check on a blind test set.** Claude wrote 240 sentences about 40 real passages from the index on 6 November 2026, before any model saw them, and we scored the check once. See [tests/blind/README.md](tests/blind/README.md).

| Sentence type | Should be | Kept |
|---|---|---|
| Restates the passage | Kept | 38/40 |
| Restates it in a child's words | Kept | 34/40 |
| Supported by the passage but off the question | Kept | 39/40 |
| True or plausible, but not in the passage | Dropped | 9/40 kept |
| Contradicts the passage | Dropped | 19/40 kept |
| One wrong name, number or direction | Dropped | 17/40 kept |

So the check keeps 111 of 120 supported sentences but lets through 45 of 120 unsupported ones. It is weakest when a wrong sentence reuses most of the passage's words, such as "Pollination is the transfer of pollen from the stigma to the anther". The earlier result of 2 of 29 came from real Qwen answers, which rarely contradict the passage this directly, but the gap is large enough that the check should be treated as a partial safeguard.

**Known gaps in the answers.**

- The check accepts some common knowledge. It kept "Plants release oxygen" from a passage that does not say so.
- It checks support, not relevance. "How do I cook food" can get a true sentence about plants.
- It can keep wrong details, such as a mirror image being "upside down" where the passage says erect.
- It kept "Thomas Alva Edison" for "Who invented the telephone?".
- Photosynthesis answers sometimes say it makes "oxygen and energy".
- Asked whether the caste system is good, it answered from a passage, calling it a factor in organisation and stability before noting discrimination. A safeguarding reviewer should decide whether questions like this get answers.

**Corpus.** We removed 629 garbled passages, 171 from teacher manuals and lesson plans, and 25 of front matter.

| 60 curriculum and 30 off-topic questions, full pipeline | Covered questions answered | Off-topic questions refused |
|---|---|---|
| Before cleaning | 56/60 | 26/30 |
| After cleaning | 54/60 | 27/30 |

The two rows are about even, because rebuilding the same passages moves scores by that much. We kept the gate at 0.80, because raising it to 0.84 cost 6 covered questions for 7 more refusals.

**Pleias-RAG-1B compared with Qwen, on a Mac.**

| Measure | Qwen2.5-1.5B | Pleias-RAG-1B |
|---|---|---|
| Covered questions answered | 54/60 | 52/60 |
| Off-topic questions refused | 27/30 | 28/30 |
| Refused when the passage lacked the answer | 0/8 | 4/8 |
| Typical time per question | 2.59 s | 33.31 s |
| Typical answer length | 34 words | 116 words |

Pleias also made basic errors, such as adding fractions by multiplying numerators. Answering from memory when a passage lacks the answer remains Qwen's main failure.

**English speech, on a Mac, with 12 questions in a synthetic voice.** `tiny.en` got 11 of 12 right, the same as `small.en`, which needs about three times the memory.

## Hardware

| Part | What we use | Notes |
|---|---|---|
| Computer | Jetson Orin Nano 8GB Super Developer Kit | Plugged in, 25 W mode. A battery is under discussion, because two of the user journeys need the device to move around the classroom |
| Microphone and camera | Arducam 8MP USB camera (B029202) | Settings applied at every boot |
| Speaker | USB speaker | Chosen by name in the settings |
| Case | 3D-printed, desk use. V3 is modelled around NVIDIA's dev kit model, with the display, LED button and camera from V2 | [Specialist Case Spec](https://claude.ai/artifact/3D3fcDWkQKapE3nWWFvjEt), which needs updating for V3, the microphone and the battery question |

The case spec page is private until shared.

## Run it

After the install, on the Jetson:

```bash
specialist-voice --in-lang hi --out-lang hi
```

Press Enter to start recording and Enter again to stop. To type a question instead, or to use a recording or a page photo:

```bash
specialist-voice --no-speak --text "What is photosynthesis?"
```

```bash
specialist-voice --in-lang hi --out-lang hi --from-wav question.wav
```

```bash
specialist-voice --in-lang en --out-lang en --from-image page.jpg
```

Install steps are in [module/README.md](module/README.md).

## What is in this repo

| Folder | Purpose |
|---|---|
| `python/pocketinfer/` | Retrieval, grounding, safety check, glossary, answer check, answer engine and model wrappers |
| `local/` | The voice loop, from a microphone, WAV file, page image or typed text |
| `corpus/`, `ingest/` | DIKSHA content and the pipeline that builds the index |
| `module/`, `scripts/`, `tools/` | Device install, packaging and model downloads |
| `training/` | How the safety model was fine-tuned and exported |
| `tests/` | System integration and user acceptance tests, with no unit tests, and a blind test set in `tests/blind/` |
| `docs/` | The architecture diagram |

## Licence

This project builds on [Suno Sutra](https://github.com/currentai-org/suno-sutra-sw) by CurrentAI (MIT). It is an independent derivative, not affiliated with or endorsed by CurrentAI. See [ATTRIBUTION.md](ATTRIBUTION.md) and [LICENSE](LICENSE).
