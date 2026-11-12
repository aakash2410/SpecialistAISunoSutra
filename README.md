# The Specialist v1

An offline subject expert for rural classrooms, built on the Suno Sutra platform. A teacher asks a question by voice or from a textbook page. The device answers aloud in Hindi or English, using only approved DIKSHA content, and says so when the content does not cover the question.

## What's new this sprint

The Specialist is an offline voice expert for rural classrooms. A teacher or student asks in Hindi or English, and a small box on the desk answers aloud from approved DIKSHA textbooks, with no internet. This sprint turned a question-answering box into a classroom assistant covering all four journeys Honey mapped out.

- **It works out the language itself.** A small language ID model and Whisper's confidence decide Hindi or English for every question. Nobody sets anything, and the answer comes back in the language you asked in.
- **Teachers get lesson plans.** Tell it what the class covered yesterday and ask for tomorrow's plan. It gives a recap, the key ideas, a simpler version for students who need more help, a question to check understanding, one to stretch students who are ahead. The questions get quoted from the textbook, never invented.
- **Students get a quiz.** Questions come straight from the textbook. Every spoken answer gets checked against it, and a wrong answer hears the checked textbook explanation. No scores get kept.
- **Multi-grade classes get revision by class.** "Revise force with class 6", then "now do the same for class 8", draws on each class's own books.
- **Examples come from the children's own lives.** After an explanation, the device adds one example from village life. Teachers can hand it their own list of 30 to 40 everyday examples to draw from. Every example gets checked: it must show a textbook fact and add nothing new.
- **Three classes of science, one open licence.** The index now covers Class 6 to 8 science: 42,426 passages from 1,499 books, all CC BY 4.0. It answers 21 of 22 science questions and refuses 28 of 30 off-topic ones.
- **Safer and more honest.** It catches indirect self-harm statements, 13 of 14 on the frozen holdout. It refuses "Who invented the telephone?" instead of crediting Edison, and turns away everyday how-to requests the textbook can't answer.
- **Hands-free and lighter.** It is set up to start at boot, records on a tap of its touch screen or any USB key, repeats or slows its last answer on request, and installs only what the Jetson needs.

We also tested a bigger answer model, Qwen3.5-2B, and kept Qwen2.5-1.5B. The smaller model answered more questions, refused more off-topic ones, and spoke its first sentence about four times faster. Everything above passes on the Mac. The Jetson run is next.

## Status, 11 November 2026

The full loop runs on a Jetson Orin Nano with the network off, from live Hindi or English speech to a spoken answer. Since the last Jetson install, the device has gained automatic language detection, lesson plans by learning level, village examples, a quiz, revision by class, and better self-harm detection. These are tested on the Mac only so far. It is not ready for classrooms: answers can still be wrong, and the safety replies have not had a safeguarding review.

### Journeys

Development days left are estimates of coding and testing work only. They leave out Jetson runs, recordings and the safeguarding review.

Mac run of the journey runner, English, 11 November: journey 1 pass, journey 2 fail, journeys 3 and 4 partial. See [tests/journeys/report_mac_en.md](tests/journeys/report_mac_en.md).

| Journey | Works now | Left | Dev days left |
|---|---|---|---|
| 1. Teacher prep | Noting what was covered, asking for a topic, lesson plans for a mixed class or one level, "repeat that" and "slower". English, on the Mac | Run in Hindi on the Jetson and fix the wording. Re-check plans on the Class 6 to 8 index | 1 |
| 2. Multi-grade revision | "Revise acids with class 7", then "now do the same for class 8": an explanation for that class with a village example, then textbook questions | On the Class 6 to 8 index each class now gets its own books (5 topics, 12 November). Still fails: some revisions come with no example, "light and shadows" is heard as "light", and acids for class 8 is refused. Make sure an example comes. Run in Hindi on the Jetson | 1 |
| 3. Student group quiz | Quiz on a topic, with textbook questions and checked feedback, on typed input | Short right answers such as "glucose and oxygen" are now judged right. On questions it was not tuned on, it judges 16 of 24 right answers right and 23 of 26 wrong answers wrong. On the Class 6 to 8 index, 37 of 42 common topics have 3 or more clean textbook questions. Spoken answers untested. Students can't use it alone until the safeguarding review | 1 |
| 4. Student doubts in Hindi or English | Answers and follow-ups, with the language detected automatically | Set the language thresholds from real recordings. Children's voices untested. Students can't use it alone until the safeguarding review | 1 |

### Big tasks

| Task | Status | Dev days left |
|---|---|---|
| Automatic language detection | Done on the Mac, 54 of 54 synthetic clips. Waiting for the Jetson install and real recordings | 0.5 |
| Lesson plans and learning levels | Done on the Mac | 0 |
| Village examples | Done. Benchmark unchanged at 53 of 60 answered | 0 |
| Quiz | Built on the Mac. Short answers now judged right; it checks against the device's own checked answer when the passages alone don't settle it | 0.5 |
| Revision by class (journey 2) | Done on the Mac. On the Class 6 to 8 index, books for the named class alone come first, then books it shares with other classes, then every class | 0.5 |
| Class 6 to 8 science index, CC BY 4.0 | Done. 42,426 passages from 1,499 books, at the 0.80 search score. Science 21 of 22 answered, off-topic 28 of 30 refused. Maths, English and social studies are mostly refused, as expected (see [ingest/README.md](ingest/README.md)) | 0 |
| Answer model | Decided: Qwen2.5-1.5B stays. Qwen3.5-2B lost on the Mac benchmark | 0 |
| Safety: indirect self-harm statements | Done. Holdout 13 of 14 caught, no new false alarms | 0 |
| Lesson aims read out as answers, and a misheard word (Jetson log, 12 November) | Done on the Mac. Teacher's handbooks and passages that state lesson aims are out of the index (4,512 passages), and the answer check drops a sentence such as "Students will ...". "Tell me about the heat endaction" now gets "Did you mean heat conduction? Please ask again." Benchmark unchanged: science 21 of 22, off-topic 28 of 30, quiz 37 of 42 topics | 0 |
| Wrong answers: Edison and the telephone, "how do I cook food" | Done. A credit for an invention or discovery must be in the passage, and everyday how-to requests are refused. Off-topic refused went from 26 to 29 of 30, with no answers lost | 0 |
| Journey test runner, pass or fail per journey | Done. Mac English run complete. Hindi run waits for the Jetson | 0 |
| Voice that sounds less robotic | Flite variants ready to compare on the Jetson | 1 |
| Talk button | A tap on the touch screen. Code done (`local/screen.py`), waiting for the Jetson demo and install | 0.5 |
| Boot service | Done. Waiting for the Jetson install | 0 |
| Power and case | Power measurement and power bank pending. Cardboard case | 0 |
| Screen as a backup to the voice | Waveshare 2.8 inch touch LCD wired and showing colours on 12 Nov (ST7789 chip, spi1 on, `tools/screen_pins.sh` lets pins 29, 31, 32 drive). Touch confirmed (press reads Z1 113 to 630, untouched under 20). Left: add the pin fix to the install, calibrate touch on screen, program what it shows (what was heard and the answer) | 1 |
| Safeguarding review | Reviewer not yet found | 0 |

| Measure | Result |
|---|---|
| First Hindi sentence spoken, from a recorded question | 2.42 s |
| Device tests, 6 November | 113 passed, 1 expected failure, 2 skipped |
| Lowest free memory during those tests | 2,766 MB of 7.4 GB |
| Search and gate, full pipeline, Mac, 11 November | 53 of 60 on-topic questions answered, 29 of 30 off-topic refused |
| Answer check, blind test, with fact and relevance checks | Keeps 62 of 80 supported on-question sentences, drops 23 of 40 off-question, lets through 24 of 120 unsupported |
| Safety check, fresh holdout written after the rules were frozen | Caught 13 of 14 unsafe, wrongly flagged 0 of 14 safe. It was 12 of 14, and the gained item is the known gap that was fixed, so it is not independent |

## How it works

![How The Specialist answers a question](docs/architecture.png)

1. **Language:** a small language ID model and Whisper's confidence decide whether a spoken question is Hindi or English, so nobody sets it. The answer comes back in the same language unless the question names one.
2. **Translate:** a Hindi request becomes English, with a glossary fixing subject terms first.
3. **Safety check:** a flagged request gets a fixed, hand-written reply and never reaches the answer model.
4. **Retrieve:** e5 searches the local DIKSHA index. Questions about today are refused.
5. **Gate:** a passage must score at least 0.80 and share a key term with the request. If a topic word of 5 letters or more is in no passage at all, as "endaction" was when "heat conduction" was misheard, the device does not answer from the other words. It offers a textbook word spelt like it that often goes with the other words ("Did you mean heat conduction?") and asks again. With no such word it asks the teacher to say it again, or refuses if the other words would not have been answered either.
6. **Answer:** Qwen rephrases the passages, one sentence at a time, and is told not to add examples of its own.
7. **Check:** any sentence no passage supports is dropped, along with any that flips a number, name, direction or negation against its passage. A sentence that states a lesson aim, such as "Students will ..." or "The teacher will ...", is dropped too. The first sentence must also be close to the question. If none survive, the device searches once more for the question's own phrase, then refuses.
8. **Example:** after an explanation, Qwen is asked in a second call for one example from village life, such as fields, cattle or the weekly market. The example is its own, so it has its own check: it must be a case of a kept sentence, add no number or name, flip no fact, stay close to the question and pass the safety check.
9. **Respond:** each kept sentence is translated and spoken while the next is written.

**Teacher examples:** a teacher can make the examples about their own children's lives. They fill in a plain text file, `data/teacher_examples.txt`, with 30 to 40 everyday things the children see and do, one per line, in English. The device reads it at start and skips any line with a number or letters other than English. The lines come from a trusted adult, so they have their own, looser safety check: patterns for harm, then the safety model, whose flag counts only if the line has a word such as a person, a body part, fire or a blade, or if the check on children's questions also flags it. On 288 everyday lines it kept 269, where the check on children's questions kept 170. On 185 harmful lines it blocked 175. On the last 25, written after the check was finished, it blocked 18, so someone should still read the list before installing it. The checked lines and their e5 vectors are cached, so only the first start after a new file is slow. For each explanation, e5 finds up to three lines close to the topic, and Qwen is asked to use one only if it really is a case of a kept sentence. If no line is close, the device asks Qwen for a village example as before. It does the same if Qwen says none fits, or if its example only compares ("just like") or fails the check. The example has the same check as any other, including the full safety check, except that the teacher's words count as everyday words and a name the teacher wrote, such as Holi or the haat's village, is allowed. It is translated with the rest of the answer. A blank or missing file changes nothing. How to install a returned file is in [module/README.md](module/README.md).

Follow-ups are rewritten from the last 3 turns, then go through every step. Memory stays in RAM and clears after 3 minutes, on "new topic" or "नया सवाल", at reboot, and after any safety flag.

**Lesson plans:** a teacher can say what the last class covered ("Yesterday we covered photosynthesis"), then ask for a lesson plan on a topic. If no topic is named, the device asks for one. By default a plan covers three learning levels, since most classes mix them:

1. **Recap:** a fixed sentence built from the teacher's words about the last class.
2. **Key ideas for everyone:** the normal checked answer to "Explain <topic>".
3. **For students who need more help:** the checked answer to "Explain <topic> in simple words".
4. **Check:** a question quoted from the textbook that asks for a fact.
5. **Stretch for students who are ahead:** a textbook question that asks why or how. Without one, the teacher is told to have them explain the topic to a classmate.
6. **Close:** a fixed sentence.

A teacher can ask for one level instead, as in "for weak students" or "for students who are ahead". Textbook questions are used only when they read cleanly and make sense without the page, and fixed prompts replace them otherwise. If the textbook does not cover the topic, the device refuses and gives no plan. There are no activities, because almost none in the scanned textbooks can be quoted cleanly.

**Quiz:** a small group of students can say "Quiz me on photosynthesis". If no topic is named, the device asks for one. Every question is quoted from the textbook pages on the topic, chosen the same way as lesson plan questions, and the answer model never writes one. If there is no such question, the device says so and no quiz starts. A student answers out loud, and the answer goes through the safety check like any question. The device checks the answer against the textbook passages for the question. If they do not support it, the device makes its normal checked answer to the question and checks the student's answer against its first sentence too. So "glucose and oxygen" counts when the device's answer says "oxygen and carbohydrates". Numbers count however they are said, so "22nd March" and "twenty second March" are both 22 March. An answer with several parts needs every part in the passages, so "oxygen and nitrogen" is not right because a passage says "food and oxygen". If the answer is supported, the device says "That's right." If not, it says "Not quite" and gives that checked answer, so the feedback always comes from the textbook. If even that answer is refused, the device says it can't check this one and moves on. Then it asks the next question. "Stop the quiz" or "बस" ends it. No scores are kept or read out, and the quiz clears with the rest of the memory.

**Revision:** in a multi-grade class, a teacher who has just finished a topic with one class can say "Revise acids with class 8" or "I just taught acids to class 8, revise it with them". The device works with Classes 6 to 8. It searches only that class's textbook passages, and uses every passage if that class has none on the topic. It says which topic and class it is revising, then gives the normal checked explanation, with its checked example from village life. Then it reads one or two recall questions quoted from that class's textbook, or asks the teacher to have students explain the topic in their own words. A fixed sentence closes it. If the textbook does not cover the topic, the device refuses. The request goes through the safety check first. When the teacher turns to the other class, "now do the same for class 7" revises the same topic for that class. The class and topic stay in memory only, and clear with the rest of it after 3 minutes.

![How The Specialist keeps students safe](docs/safety.png)

## What runs where

| Part | Choice | Runs on |
|---|---|---|
| Answer model | Qwen2.5-1.5B through Ollama, swappable with one setting | GPU |
| Retrieval | `multilingual-e5-small`, 42,426 Class 6 to 8 science passages | CPU |
| Answer check | `nli-deberta-v3-base`, threshold 0.9 | CPU |
| Safety check | Patterns, a general question rule, then `nli-deberta-v3-small` fine-tuned for safety | CPU |
| Hindi speech and translation | Bhashini | GPU for translation |
| Spoken language ID | SpeechBrain ECAPA, exported to ONNX | CPU |
| English speech | Whisper `tiny.en` | CPU |
| Spoken output | Flite through Bhashini | CPU |
| Page reading | Tesseract, Hindi and English | CPU |

**Tried and dropped:** Qwen3.5-2B (11 November: refused 27 of 30 off-topic against 29, answered 52 of 60 against 53, first sentence about 4 times slower, and more wrong facts and markdown got past the check), `llama3.2:1b` (excluded by Mozilla's call), Pleias-RAG-1B (far slower, with basic errors), Kokoro (about 1 s slower), Piper's Rohan voice (stuttered in English, licence unclear).

## Known gaps

- The answer check can keep wrong details and common knowledge the passage never states.
- "How do I …" and "How do we …" requests are answered only when one passage names the whole task. This refuses everyday tasks such as "How do I cook food", but also about 1 in 16 textbook how-to questions whose passages use other words, such as "How do we save forests?".
- "What is machine learning?" is answered from a Class 6 textbook that defines it, and "What is the population of Japan?" got an answer from a page on tsunamis. "What is an acid?" says acids feel "bitter, soapy": the passage lists acids and bases together, and the answer check passes the mix-up.
- The safety check still misses "Which caste is superior?", and questions that read like science, such as "How many days without food before a person dies?". Indirect self-harm phrased about "a person" is now caught: 29 of 30 on a new dev set, with 0 of 42 classroom look-alikes flagged.
- The index is science for Classes 6 to 8, so maths, English and social studies questions are refused.
- The quiz still marks about 1 in 3 right answers "not quite", mostly where the textbook lacks the fact or words it differently, and accepts a few wrong ones, such as "condensation" for water turning into vapour. 5 of 42 common topics have fewer than 3 clean textbook questions to ask.
- Live speech has been tested with very few speakers, and the language thresholds were set on synthetic voices.
- A misheard word is caught only when it is in no textbook. "Heat induction" for "heat conduction" is a real word and is answered as asked. A word is offered only when the question has another topic word to go by, so "What is endaction?" gets "please say it again". In Hindi, the offered word is machine translated.
- 53 books titled "Lesson Plan", 373 passages, are still in the index. Their passages that state aims are gone, but other teaching notes remain.
- Lesson plan key ideas are only as good as the answers. "Respiration in plants" picked up a sentence about plants releasing water.

## Decisions

| Question | Decision |
|---|---|
| Answer model | Qwen2.5-1.5B. Qwen3.5-2B, SmolLM3, Pleias-RAG and `llama3.2:1b` are dropped |
| Voice | Flite through Bhashini, tuned to sound less robotic. Kokoro and Piper are dropped |
| Languages | Hindi and English, detected for every request. Other Indian languages are not in v1 |
| Grades and subjects | Science for Classes 6 to 8, including books labelled Physics, Biology and Physical Science, in one index. Class 7 is pulled again the same way, because today's index is only the first 232 books DIKSHA listed. Classes 1 to 5 and other subjects are not in v1 |
| Teacher's handbooks and lesson aims | Left out of the index, and dropped by the answer check if they get through. A teacher asking for a lesson plan gets one built from textbook facts instead |
| Content licence | CC BY 4.0 only. Today's index is 32% CC BY-NC-ND, mostly Andhra Pradesh teacher's books, and that content is dropped |
| Examples | After an explanation, Qwen adds one short example from village life in India, in a separate call. It must be a case of a checked sentence and bring in little new content. Direct answers get none |
| Teacher examples file | Optional. A teacher writes 30 to 40 everyday examples in English, one per line. Qwen builds the example from the closest ones, and it is checked like any example. Without the file, examples come from village life as before |
| Learning levels | A lesson plan covers three levels by default: students who need more help, students on track and students who are ahead. A teacher can ask for one level instead |
| Lesson plan activities | Not in v1. Almost none in the scanned textbooks can be quoted cleanly |
| Printed plans | Not in v1. The device has no screen or printer, so teachers take notes, with "repeat that" and "slower" |
| Quiz (journey 3) | In v1. Questions are quoted from the textbook, and each spoken answer gets feedback from the answer check. No scores are stored |
| Class progress tracking | Not in v1. Nothing about students is stored |
| Memory | Stays in RAM and clears after 3 minutes, on a shared device. Per-grade memory for a whole class is not in v1 |
| Students using it alone (journeys 3 and 4) | Not until a safeguarding reviewer has approved the safety replies and helpline numbers. Until then, a teacher is present |
| Talk button | A tap on the 2.8 inch touch screen, which also shows the answer. Enter on a USB keyboard also works. No separate touch sensor (TTP223 dropped on 12 Nov) |
| Power | Both: the 19 V mains adapter, and a USB-C power bank through the ETZIN cable set to 15 V |
| Case | Cardboard for the prototype. The 3D-printed V3 case for classroom units |
| Answer check fine-tune and Qwen refusal fine-tune | Not in v1 |
| Reusable profiles for other jobs | Not in v1 |

## Plan to 16 November

| Day | Work |
|---|---|
| Wed 11 | Done: lesson plans by learning level, village examples, quiz on typed questions, harvester for Classes 6 to 8, revision by class, self-harm detection |
| Thu 12 | Quiz judging of short answers. Hindi journey run on the Jetson |
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
| Talk button | The touch screen itself. A USB keyboard's Enter is the backup |
| Screen | Waveshare 2.8 inch resistive touch LCD from the VYOMA challenge kit: 320 x 240, ST7789 display chip, XPT2046 touch, on spi1 of the 40-pin header. Wiring and test in `tools/screen_test.py`. A backup if the voice doesn't work. Programming it comes later |
| Case | Cardboard box for the prototype. 3D-printed V3 case for classroom units. See the [case spec](https://claude.ai/artifact/3D3fcDWkQKapE3nWWFvjEt), private until shared |

## Run it

Install steps are in [module/README.md](module/README.md). After the install, the loop starts at boot. Tap the screen (or press Enter on any USB keyboard) to record, and tap again to stop. Say "repeat that" or "slower" to hear the last answer again. Ask for a lesson plan with, for example, "Give me a lesson plan on photosynthesis for tomorrow's class".

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
