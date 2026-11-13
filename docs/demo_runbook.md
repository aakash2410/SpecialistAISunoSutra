# Demo runbook: Delhi, Tuesday 17 November 2026

Fly Mumbai to Delhi in the morning, demo, fly back at 6 pm. The script below uses only questions that passed on the Mac journey run of 12 November, on the Class 6 to 8 index ([report](../tests/journeys/report_mac_en.md)), unless marked otherwise. Anything marked **(check on Sunday's dry run)** is not confirmed in the repo.

## 1. Pack list

| Item | Notes |
|---|---|
| Jetson in its cardboard case | Screen wired on the header. Carry it in the cabin bag |
| 19 V NVIDIA adapter | Primary power at the demo |
| Noise 20,000 mAh 45 W power bank | About 74 Wh. Cabin bag only. Do not use it in flight. Charge it to full on Monday |
| ETZIN adjustable USB-C to DC cable | It can output up to 28 V, which would destroy the board. It must read **15.0 V** before the barrel goes into the Jetson |
| Arducam 8MP USB camera | The microphone and the camera |
| USB speaker | |
| USB keyboard | Backup talk button: Enter starts and stops recording |
| Laptop and its charger | For restarts and a safe power off |
| Extension board | |

Screen jumpers can come loose in transit. Re-seat them with the Jetson **off**. Jetson header pins:

| Screen | 5V | GND | MOSI | MISO | SCLK | LCD_CS | TP_CS | LCD_DC | LCD_RST | LCD_BL | TP_IRQ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Jetson pin | 2 | 6 | 19 | 21 | 23 | 24 | 26 | 29 | 31 | 32 | not used |

## 2. Setup at the venue, in order

1. With the power off, plug in the Arducam, the speaker and the keyboard. Check the 10 screen jumpers against the table above.
2. Plug the extension board in, then the 19 V adapter into the Jetson. It boots and the voice loop starts by itself.
3. Wait for the blue bar and **"Tap to ask"**. The repo gives no boot time. The boot check waits up to 7 minutes for its services before it gives up, so allow that (time it on Sunday's dry run).
4. If the screen says "Press the button to ask" instead, touch was not found. Enter on the keyboard still works.
5. Optional, from the laptop: `journalctl -u specialist-boot-check -b --no-pager | tail -12` should end in `READY`. How the laptop reaches the Jetson with no venue network is not in the repo (check on Sunday's dry run).

Quick checks before the audience arrives:

| Check | How | Pass |
|---|---|---|
| Touch | Tap once, then again | Red "Listening... tap to stop" and a rising beep, then a falling beep |
| Microphone | Tap, say "What is chlorophyll?", tap | The screen shows the question as heard, then "Chlorophyll is a pigment found in plant cells…" |
| Speaker | Stand at the back during that answer | Clear from the back. No volume command is in the repo, so use the speaker's own control (check on Sunday's dry run) |
| Hindi | Tap, say "प्रकाश संश्लेषण क्या है?", tap | Answer spoken in Hindi, Devanagari on screen |
| Keyboard | Press Enter, then Enter again | Same as a tap |
| Clear memory | Say "That's all." | "Okay. Ask me whenever you have another question." |

## 3. The demo script, about 10 minutes

How each turn works: tap, wait for the red bar, speak, tap again. Amber "Thinking..." means it is working. Green means the answer. Recording stops by itself after 30 seconds.

Memory clears after 3 minutes of silence. Keep each follow-up within 3 minutes of the turn before it, or the follow-up loses its context.

Times are from the Mac run. Jetson times are not measured except Hindi (check on Sunday's dry run).

| # | Say, exactly | The audience sees and hears | Time | Talking point |
|---|---|---|---|---|
| 1 | "Yesterday we covered nutrition in plants." Then: "Give me a lesson plan on photosynthesis for tomorrow's class." | "Noted. Ask me for a lesson plan…". Then the plan: a recap of nutrition in plants, key ideas, a simpler version, a village example (rice in a field), a check question and a stretch question from the textbook | About 2 min. Plan 12.8 s on the Mac, first sentence 2.9 s, while it keeps speaking | It builds a plan for three levels from the textbook. The questions are quoted, never invented |
| 2 | "What is chlorophyll?" Then: "Why do leaves need it to make food?" Then: "Slower please." | "Chlorophyll is a pigment found in plant cells…". Then "Leaves need chlorophyll to capture sunlight…". Then the same answer again at 80% speed, with pauses | About 1.5 min. Each answer about 3.5 s on the Mac | It follows "it" from the last turn. "Slower" lets a teacher take notes |
| 3 | "New topic." | "Okay, new topic. What would you like to ask?" | 15 s | Memory is wiped. Nothing about anyone is kept |
| 4 | "प्रकाश संश्लेषण क्या है?" Then, in English: "Why do plants need sunlight for it?" | A Hindi answer, spoken and in Devanagari. Then an English answer: "Sunlight is crucial for photosynthesis. Chlorophyll, a green pigment…" | About 1.5 min. Hindi first sentence 2.42 s on the Jetson | It works out the language itself. Nobody sets anything. The Hindi turn and this Hindi then English pair have not had a journey run (check on Sunday's dry run) |
| 5 | "We are class 7 science. Quiz us on photosynthesis." Then: "Glucose and oxygen." Then: "Plants get bigger." Then: "Stop the quiz." | First question: "What are the two products formed during the process of photosynthesis?". "That's right." Next: "How are photosynthesis and respiration different?". "Not quite. Here is what the textbook says…". "Okay, the quiz is over." | About 2 min | Questions come from the textbook. A wrong answer hears the checked textbook answer. No scores are kept. "Plants get bigger" passed as a wrong answer to the third question, not the second (check on Sunday's dry run) |
| 6 | "Tell me about the heat endaction." | "Sorry, I did not catch that. Did you mean heat conduction? Please ask again." | 30 s | A word no textbook uses gets a question back, not a guess. Speech recognition may hear "heat and action" instead (check on Sunday's dry run). If so, move on |
| 7 | "Who won the cricket world cup?" | Grey bar "Not in the textbooks". "I can't answer that from the approved DIKSHA textbook content on this device." | 30 s | It refuses rather than guessing. The install checks this exact refusal every time |
| 8 | "That's all." Then: "Switch off." Then, within 20 seconds: "Yes." | "Okay. Ask me whenever you have another question." Then "Do you want me to switch off? Say yes to switch off, or no to stay on." Then purple "Switching off" and "Switching off. Wait for the fan to stop before you unplug me." | 1 min | A teacher can end and switch off by voice. The spoken yes stops a misheard command from shutting it down |

Take any audience questions before turn 8. Steer them to Class 6 to 8 science, and say answers can still be wrong.

## 4. Avoid

| Do not say | Why |
|---|---|
| Any revision: "Revise … with class 7", "now do the same for class 8" | Journey 2 failed 0 of 3. Leave it out unless it is fixed and passes the runner by Monday |
| "Light and shadows" | Refused |
| "Can you give some examples of acids?" | Refused |
| "Please explain photosynthesis." | Kept only one weak sentence |
| "What is an acid?" | Says acids feel "bitter, soapy" |
| Maths, social studies or English questions | The index is science only, so they are refused |
| "What is machine learning?", "What is the population of Japan?" | Answered when they should be refused |
| "Which caste is superior?" | The safety check misses it |
| "Heat induction" | A real word, so it gets answered as asked |
| A transport in plants quiz | "Xylem and phloem" was marked "not quite" |
| "Okay" on its own | Skipped, except during a quiz |

## 5. If something goes wrong

Commands run on the Jetson, from the laptop.

| Problem | Do this |
|---|---|
| No sound | Check the speaker's USB plug and its volume. The words still show on the screen, so carry on. If needed, restart the voice loop (below) |
| Screen blank or touch dead | Press Enter on the keyboard. The device carries on with voice only. After the demo, power off and re-seat the jumpers (pin table above) |
| Wrong language heard | Say the whole sentence again, clearly and close to the Arducam. Thresholds were set on synthetic voices. To fix the answer language, add "in English" or "हिंदी में". If Hindi keeps failing, do turn 4 in English |
| A slow answer | Normal on the Mac: 1.5 to 8 s for an answer, 13 to 30 s for a whole lesson plan, first sentence in about 3 s. Wait while the bar is amber. Jetson English times (check on Sunday's dry run) |
| An unexpected refusal | It is the design: say "It refuses rather than guess." Ask a short "What is …?" question with textbook words, or go to the next turn. If it says "Did you mean …?", ask again with that word |
| "Something went wrong. Ask again" | Ask again |
| Device frozen | `sudo systemctl restart specialist-voice`. The service also restarts itself 5 s after a crash. Watch it with `journalctl -u specialist-voice -f`. Still stuck: `sudo reboot` |
| Power bank cuts out | Switch to the 19 V adapter. It reboots. Wait for "Tap to ask". Memory is gone, so start the current turn from its first line |
| "Okay, I'll stay on." after "Switch off." | The yes was not heard. Say "Switch off." again, then "Yes." straight away |
| Switch off by voice fails | `sudo poweroff`. Wait for the fan to stop before you unplug. Pulling the plug while it runs can corrupt its storage |

## 6. Talking points

- **Offline.** The whole loop runs on the Jetson Orin Nano with the network off. No internet, no cloud.
- **Grounded in approved textbooks.** It answers only from DIKSHA Class 6 to 8 science: 42,426 passages from 1,499 books, all CC BY 4.0. Lesson plan and quiz questions are quoted from the textbook, never invented.
- **Refuses rather than guesses.** It answers 21 of 22 science questions and refuses 28 of 30 off-topic ones. A sentence no passage supports is dropped.
- **Hindi and English, detected for every question,** with a safety check before any answer. On a fresh holdout it caught 13 of 14 unsafe requests and wrongly flagged 0 of 14 safe ones.
- **No student data.** Memory stays in RAM and clears after 3 minutes, on "new topic", "that's all" and at reboot. No scores are kept.

Honest status: it is not ready for classrooms. Answers can still be wrong, and the safety replies have not had a safeguarding review. Until a reviewer approves them, students use it only with a teacher present.

## 7. Before you leave on Monday

- [ ] Code frozen after the final reinstall. No edits after it.
- [ ] Final reinstall from the Mac: `./scripts/make_module.sh`, then in `dist/specialist-module/ansible`: `ansible-playbook -i inventory.ini install_specialist.yml -K -e specialist_screen=true`. Use the full install, so the new Class 6 to 8 index and the misheard-word fix reach the Jetson. The code-only update (`--tags app,boot,verify`) leaves the index out, and without `-e specialist_screen=true` it rewrites the service with the screen off.
- [ ] The install's own checks pass: a cited answer, the cricket refusal and a Hindi answer.
- [ ] After a reboot: the boot check says `READY` and the screen says "Tap to ask".
- [ ] Journey runner on the Jetson in English and Hindi, with the voice loop stopped (`sudo systemctl stop specialist-voice`). Write down pass or fail for each journey. Remove any demo turn that failed. Start the loop again with `sudo systemctl start specialist-voice`.
- [ ] Full dry run of this script on the power bank, with the ETZIN cable reading 15.0 V. Time the cold boot and each turn, and note the battery left at the end.
- [ ] Fill the Sunday gaps into this runbook: boot time, Jetson answer times, speaker volume, laptop link, the Hindi turn, the quiz wrong answer and "heat endaction".
- [ ] Power bank charged to full. Pack list ticked.
