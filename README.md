# The Specialist v1

An offline subject expert for rural classrooms, built on the Suno Sutra platform. A teacher asks a question by voice or from a textbook page. The device answers aloud in Hindi or English, using only approved DIKSHA content, and says so when the content does not cover the question.

## Status, 11 November 2026

The full loop runs on a Jetson Orin Nano with the network off, from live Hindi or English speech to a spoken answer. Since the last Jetson install, the device has gained automatic language detection, lesson plans by learning level, village examples and a quiz. These are tested on the Mac only so far. It is not ready for classrooms: answers can still be wrong, the safety check misses some self-harm statements, and the safety replies have not had a safeguarding review.

### Journeys

Development days left are estimates of coding and testing work only. They leave out Jetson runs, recordings and the safeguarding review.

| Journey | Works now | Left | Dev days left |
|---|---|---|---|
| 1. Teacher prep | Noting what was covered, asking for a topic, lesson plans for a mixed class or one level, "repeat that" and "slower". English, on the Mac | Run in Hindi on the Jetson and fix the wording. Re-check plans on the Class 6 to 8 index | 1 |
| 2. Multi-grade revision | Explanations with a village example | Revision mode and answers limited to the named class (being built). Needs the Class 6 to 8 index | 2 |
| 3. Student group quiz | Quiz on a topic, with textbook questions and checked feedback, on typed input | About 1 in 4 right answers is marked "not quite", and many topics have no clean textbook question. Spoken answers untested. Students can't use it alone until the safeguarding review | 2 |
| 4. Student doubts in Hindi or English | Answers and follow-ups, with the language detected automatically | Set the language thresholds from real recordings. Children's voices untested. Students can't use it alone until the safeguarding review | 1 |

### Big tasks

| Task | Status | Dev days left |
|---|---|---|
| Automatic language detection | Done on the Mac, 54 of 54 synthetic clips. Waiting for the Jetson install and real recordings | 0.5 |
| Lesson plans and learning levels | Done on the Mac | 0 |
| Village examples | Done. Benchmark unchanged at 53 of 60 answered | 0 |
| Quiz | Built on the Mac. Answer judging needs work | 1.5 |
| Revision by class (journey 2) | Being built | 1.5 |
| Class 6 to 8 science index, CC BY 4.0 | Harvester ready, overnight run pending (see [ingest/README.md](ingest/README.md)). Then build, re-check thresholds and benchmark | 1 |
| Answer model | Qwen3.5-2B being benchmarked against Qwen2.5-1.5B | 0.5 |
| Safety: indirect self-harm statements | Being fixed | 1 |
| Wrong answers: Edison and the telephone, "how do I cook food" | Being fixed | 1 |
| Journey test runner, pass or fail per journey | Being built | 0.5 |
| Voice that sounds less robotic | Flite variants ready to compare on the Jetson | 1 |
| Talk button | Code done. Waiting for the touch sensor wiring and `gpioinfo` | 0.5 |
| Boot service | Done. Waiting for the Jetson install | 0 |
| Power and case | Power measurement and power bank pending. Cardboard case | 0 |
| Safeguarding review | Reviewer not yet found | 0 |

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

1. **Language:** a small language ID model and Whisper's confidence decide whether a spoken question is Hindi or English, so nobody sets it. The answer comes back in the same language unless the question names one.
2. **Translate:** a Hindi request becomes English, with a glossary fixing subject terms first.
3. **Safety check:** a flagged request gets a fixed, hand-written reply and never reaches the answer model.
4. **Retrieve:** e5 searches the local DIKSHA index. Questions about today are refused.
5. **Gate:** a passage must score at least 0.80 and share a key term with the request.
6. **Answer:** Qwen rephrases the passages, one sentence at a time, and is told not to add examples of its own.
7. **Check:** any sentence no passage supports is dropped, along with any that flips a number, name, direction or negation against its passage. The first sentence must also be close to the question. If none survive, the device searches once more for the question's own phrase, then refuses.
8. **Example:** after an explanation, Qwen is asked in a second call for one example from village life, such as fields, cattle or the weekly market. The example is its own, so it has its own check: it must be a case of a kept sentence, add no number or name, flip no fact, stay close to the question and pass the safety check.
9. **Respond:** each kept sentence is translated and spoken while the next is written.

Follow-ups are rewritten from the last 3 turns, then go through every step. Memory stays in RAM and clears after 3 minutes, on "new topic" or "नया सवाल", at reboot, and after any safety flag.

**Lesson plans:** a teacher can say what the last class covered ("Yesterday we covered photosynthesis"), then ask for a lesson plan on a topic. If no topic is named, the device asks for one. By default a plan covers three learning levels, since most classes mix them:

1. **Recap:** a fixed sentence built from the teacher's words about the last class.
2. **Key ideas for everyone:** the normal checked answer to "Explain <topic>".
3. **For students who need more help:** the checked answer to "Explain <topic> in simple words".
4. **Check:** a question quoted from the textbook that asks for a fact.
5. **Stretch for students who are ahead:** a textbook question that asks why or how. Without one, the teacher is told to have them explain the topic to a classmate.
6. **Close:** a fixed sentence.

A teacher can ask for one level instead, as in "for weak students" or "for students who are ahead". Textbook questions are used only when they read cleanly and make sense without the page, and fixed prompts replace them otherwise. If the textbook does not cover the topic, the device refuses and gives no plan. There are no activities, because almost none in the scanned textbooks can be quoted cleanly.

**Quiz:** a small group of students can say "Quiz me on photosynthesis". If no topic is named, the device asks for one. Every question is quoted from the textbook pages on the topic, chosen the same way as lesson plan questions, and the answer model never writes one. If there is no such question, the device says so and no quiz starts. A student answers out loud, and the answer goes through the safety check like any question. The device checks the answer against the textbook passages for the question. If they support it, the device says "That's right." If not, it says "Not quite" and gives the normal checked answer to the question, so the feedback always comes from the textbook. If even that answer is refused, the device says it can't check this one and moves on. Then it asks the next question. "Stop the quiz" or "बस" ends it. No scores are kept or read out, and the quiz clears with the rest of the memory.

![How The Specialist keeps students safe](docs/safety.png)

## What runs where

| Part | Choice | Runs on |
|---|---|---|
| Answer model | Qwen2.5-1.5B through Ollama, swappable with one setting | GPU |
| Retrieval | `multilingual-e5-small`, 8,794 Class 7 passages | CPU |
| Answer check | `nli-deberta-v3-base`, threshold 0.9 | CPU |
| Safety check | Patterns, a general question rule, then `nli-deberta-v3-small` fine-tuned for safety | CPU |
| Hindi speech and translation | Bhashini | GPU for translation |
| Spoken language ID | SpeechBrain ECAPA, exported to ONNX | CPU |
| English speech | Whisper `tiny.en` | CPU |
| Spoken output | Flite through Bhashini | CPU |
| Page reading | Tesseract, Hindi and English | CPU |

**Tried and dropped:** `llama3.2:1b` (excluded by Mozilla's call), Pleias-RAG-1B (far slower, with basic errors), Kokoro (about 1 s slower), Piper's Rohan voice (stuttered in English, licence unclear).

## Known gaps

- The answer check can keep wrong details and common knowledge the passage never states.
- It still says "Thomas Alva Edison" for "Who invented the telephone?", and still answers "How do I cook food".
- The safety check still misses some indirect statements, such as "What happens if a person stops eating on purpose until the end?".
- The index covers only Class 7, so basics taught in other grades get refused.
- The quiz marks about 1 in 4 right answers as "not quite", and many topics have no clean textbook question to ask.
- Live speech has been tested with very few speakers, and the language thresholds were set on synthetic voices.
- Lesson plan key ideas are only as good as the answers. "Respiration in plants" picked up a sentence about plants releasing water.

## Decisions

| Question | Decision |
|---|---|
| Answer model | Qwen2.5-1.5B. Qwen3.5-2B replaces it only if it answers at least as many questions, refuses as many off-topic ones, and fits the Jetson's memory and speed. SmolLM3, Pleias-RAG and `llama3.2:1b` are dropped |
| Voice | Flite through Bhashini, tuned to sound less robotic. Kokoro and Piper are dropped |
| Languages | Hindi and English, detected for every request. Other Indian languages are not in v1 |
| Grades and subjects | Science for Classes 6 to 8, including books labelled Physics, Biology and Physical Science, in one index. Class 7 is pulled again the same way, because today's index is only the first 232 books DIKSHA listed. Classes 1 to 5 and other subjects are not in v1 |
| Content licence | CC BY 4.0 only. Today's index is 32% CC BY-NC-ND, mostly Andhra Pradesh teacher's books, and that content is dropped |
| Examples | After an explanation, Qwen adds one short example from village life in India, in a separate call. It must be a case of a checked sentence and bring in little new content. Direct answers get none |
| Learning levels | A lesson plan covers three levels by default: students who need more help, students on track and students who are ahead. A teacher can ask for one level instead |
| Lesson plan activities | Not in v1. Almost none in the scanned textbooks can be quoted cleanly |
| Printed plans | Not in v1. The device has no screen or printer, so teachers take notes, with "repeat that" and "slower" |
| Quiz (journey 3) | In v1. Questions are quoted from the textbook, and each spoken answer gets feedback from the answer check. No scores are stored |
| Class progress tracking | Not in v1. Nothing about students is stored |
| Memory | Stays in RAM and clears after 3 minutes, on a shared device. Per-grade memory for a whole class is not in v1 |
| Students using it alone (journeys 3 and 4) | Not until a safeguarding reviewer has approved the safety replies and helpline numbers. Until then, a teacher is present |
| Talk button | A touch sensor on the 40-pin header. Enter on a USB keyboard also works |
| Power | Both: the 19 V mains adapter, and a USB-C power bank through the ETZIN cable set to 15 V |
| Case | Cardboard for the prototype. The 3D-printed V3 case for classroom units |
| Answer check fine-tune and Qwen refusal fine-tune | Not in v1 |
| Reusable profiles for other jobs | Not in v1 |

## Plan to 16 November

| Day | Work |
|---|---|
| Wed 11 | Done: lesson plans by learning level, village examples, quiz on typed questions, harvester for Classes 6 to 8 |
| Thu 12 | Revision by class, safety and wrong-answer fixes, journey runner, answer model decision |
| Fri 13 | Overnight: download Class 6 to 8 science, CC BY 4.0 only (7.4 GB), then build the new index and benchmark it |
| Sat 14 to Sun 15 | On the Jetson: install, run all four journeys in Hindi, record 10 questions each in Hindi, English and Hinglish to set the language thresholds, and measure power. Buy the power bank from that measurement |
| Mon 16 | A pass or fail result for each journey, from the journey runner |

Finding a safeguarding reviewer runs alongside, since journeys 3 and 4 wait on it.

## Hardware

| Part | What we use |
|---|---|
| Computer | Jetson Orin Nano 8GB Super Developer Kit, 25 W mode |
| Power | The 19 V mains adapter, or a USB-C power bank (45 to 100 W, 15 V / 3 A, about 20,000 mAh, pass-through charging) through the ETZIN cable set to 15 V |
| Microphone and camera | Arducam 8MP USB camera (B0292) |
| Speaker | USB speaker |
| Talk button | Touch sensor on the 40-pin header, powered from 3.3 V |
| Case | Cardboard box for the prototype. 3D-printed V3 case for classroom units. See the [case spec](https://claude.ai/artifact/3D3fcDWkQKapE3nWWFvjEt), private until shared |

## Run it

Install steps are in [module/README.md](module/README.md). After the install, the loop starts at boot. Press the talk button (Enter on any USB keyboard, or a touch sensor) to record, and press it again to stop. Say "repeat that" or "slower" to hear the last answer again. Ask for a lesson plan with, for example, "Give me a lesson plan on photosynthesis for tomorrow's class".

To run it by hand on the Jetson, stop the service first, then press Enter in the terminal to record:

```bash
sudo systemctl stop specialist-voice
```

```bash
specialist-voice --in-lang hi --out-lang hi
```

To type a question:

```bash
specialist-voice --no-speak --text "What is photosynthesis?"
```

Use `--from-wav question.wav` for a recording or `--from-image page.jpg` for a page photo. Tests are in [tests/README.md](tests/README.md).

## Licence

Builds on [Suno Sutra](https://github.com/currentai-org/suno-sutra-sw) by CurrentAI (MIT). It is an independent derivative, not affiliated with or endorsed by CurrentAI. See [ATTRIBUTION.md](ATTRIBUTION.md) and [LICENSE](LICENSE).
