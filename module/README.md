# Installing The Specialist

This role installs the full software pipeline on a Jetson running JetPack 6, and sets up the microphone, speaker and camera. It does not need the upstream Suno Sutra base install, and it leaves the desktop, hostname, kernel and existing packages as they are, unless you turn on the desktop switch below.

It installs only what the device runs. Building indexes, training and exporting models happen on a computer, so PyTorch, Transformers, PyMuPDF, SpeechBrain and the other heavy packages never reach the Specialist's environment.

## What it installs

Sizes are on disk. Measured from the files the module ships, except where marked as an estimate.

| Part | Where | Size |
|---|---|---|
| Answer model, Qwen2.5-1.5B, and no other | Ollama | 986 MB |
| Hindi speech input, translation to and from English, speech output | Bhashini service on `127.0.0.1:11400`, in `/opt/specialist/bhashini_models` | 1.6 GB download, kept in `/opt/specialist/assets` |
| English speech input, Whisper `tiny.en` only | `/opt/specialist/models/whisper` | about 75 MB |
| Retrieval, `multilingual-e5-small`, quantised | `/opt/specialist/models` | 135 MB |
| Answer verifier, `nli-deberta-v3-base` | `/opt/specialist/models` | 253 MB |
| Safety model, `nli-safety-onnx` | `/opt/specialist/models` | 181 MB |
| Spoken language ID, `lang-id-ecapa-onnx` | `/opt/specialist/models` | 86 MB |
| Reading pages, Tesseract with Hindi and English | System package | |
| DIKSHA index, the one the voice loop uses | `/opt/specialist/corpus` | 21 MB for `diksha_g7_all` |
| Teacher examples, blank until a teacher fills it in | `/opt/specialist/teacher_examples.txt` | |
| Checked teacher examples, made at the first start after a new file | `/opt/specialist/cache` | under 1 MB |
| Pipeline code and Python environment | `/opt/specialist/app`, `/opt/specialist/venv` | about 300 MB, an estimate |
| Command to run it | `/usr/local/bin/specialist-voice` | |
| Hardware settings | `/etc/default/specialist` | |
| Ollama memory settings | `/etc/systemd/system/ollama.service.d/specialist.conf` | |
| Camera rules | `/etc/udev/rules.d/99-specialist-devices.rules` | |
| Permission to switch off by voice | `/etc/sudoers.d/specialist-poweroff` | |
| Boot check | `specialist-boot-check` service | |
| Voice loop at boot | `specialist-voice` service, started by the talk button | |

The Specialist's Python environment holds six packages and what they bring with them: `numpy`, `requests`, `ollama`, `onnxruntime` (the CPU build), `tokenizers` and `faster-whisper`. The list is in `requirements-specialist.txt`. System packages go in without recommended extras, and pip keeps no download cache.

Each install also removes what earlier installs left behind:

- Any model folder in `/opt/specialist/models` that the code does not load, and any file in a model folder that the module does not ship. The folders in `specialist_models_in_use` are never removed.
- Whisper models other than `tiny.en`, once `tiny.en` is in place.
- Any index in `/opt/specialist/corpus` other than the one shipped (`--tags content`).

## Install

From the Mac, build the module:

```bash
cd "/Users/aakashsangani/Desktop/Suno-Sutra-SW/Specialist v1" && ./scripts/make_module.sh
```

The first time only, make the inventory:

```bash
cd "/Users/aakashsangani/Desktop/Suno-Sutra-SW/Specialist v1/dist/specialist-module/ansible" && cp inventory.ini.sample inventory.ini
```

Set your device name and user in `inventory.ini`, then run:

```bash
ansible-playbook -i inventory.ini install_specialist.yml -K
```

`-K` asks for the device's sudo password. The playbook is safe to re-run, because finished steps get skipped and downloads resume. It ends by asking test questions in English and Hindi, and it fails if the device does not cite a source, refuse an off-topic question or answer in Hindi.

To update only the code after a change, which is the usual case, run this from the Mac:

```bash
cd "/Users/aakashsangani/Desktop/Suno-Sutra-SW/Specialist v1" && ./scripts/make_module.sh && cd dist/specialist-module/ansible && ansible-playbook -i inventory.ini install_specialist.yml -K --tags app,boot,verify
```

The first run after the memory settings below came in restarts Ollama and Bhashini once, so it takes a few minutes longer.

## Settings

Change these in the inventory or with `-e`. The full list is in `ansible/roles/specialist/defaults/main.yml`.

| Setting | Use it to |
|---|---|
| `specialist_llm_model` | Swap the answer model, for example to the fine-tuned model once it exists |
| `specialist_ocr_packs` | Choose the languages for reading pages |
| `specialist_mic_device`, `specialist_speaker_device` | Choose the microphone and speaker, by ALSA card name from `arecord -L` and `aplay -L` |
| `specialist_camera_vendor`, `specialist_camera_product` | Point the camera settings at a different USB camera, using its IDs from `lsusb` |
| `specialist_assets_dir` | Reuse files you have already downloaded |
| `specialist_voice_autostart` | Start the voice loop at boot. Set to `false` to run it only by hand |
| `specialist_voice_in_lang`, `specialist_voice_out_lang` | The languages of the loop started at boot. `auto`, the default, works out each question's language and answers in it |
| `specialist_trigger` | The talk button: `key` for any USB keyboard or keypad (Enter), or `gpio` for a touch sensor |
| `specialist_gpio_chip`, `specialist_gpio_line` | With `gpio`, the chip and line the sensor's signal is wired to |
| `specialist_ollama_env` | Ollama's memory settings. See [Memory](#memory) |
| `specialist_malloc_arena_max` | glibc memory arenas for the voice loop and Bhashini, `2` by default. `""` leaves glibc's default |
| `specialist_headless` | Boot to the text console instead of the desktop. Off by default |
| `specialist_prune_ollama_models` | Delete every Ollama model except `specialist_llm_model`. Off by default |

To update only the syllabus:

```bash
ansible-playbook -i inventory.ini install_specialist.yml -K --tags content
```

## Memory

The Jetson Orin Nano shares its 7.4 GB between the CPU and the GPU, and the 6 November tests went as low as 2,766 MB free. The install keeps memory free in these ways, none of which changes an answer:

- **Ollama** gets a settings file, `/etc/systemd/system/ollama.service.d/specialist.conf`. It loads one model at a time and handles one request at a time, which is all the voice loop sends, and it gives the answer model 2048 tokens of context. The longest prompt the pipeline builds is about 1,100 tokens plus 128 to answer, so it fits with room to spare. Ollama's usual 4096 holds about 56 MB more for Qwen2.5-1.5B, and more again for each extra request it runs side by side. If a code change ever makes a prompt too long, Ollama cuts it and the answer changes, so `--tags verify` fails and tells you to raise `OLLAMA_CONTEXT_LENGTH` in `specialist_ollama_env`.
- **The voice loop and Bhashini** run with `MALLOC_ARENA_MAX=2`, so these two multi-threaded Python processes hold less freed memory. How much this saves varies, so measure it with the commands below.
- **Models load only when a mode needs them.** Typed questions and page photos never load Whisper or the language ID model.

`--tags verify` ends by printing the memory available with Ollama and Bhashini loaded and the voice loop paused, the boot target, what Ollama has loaded, and which Ollama models are on disk.

### The desktop

A desktop the device boots to holds memory the whole time, about 800 MB on JetPack 6 by NVIDIA's own estimate. The Suno Sutra base install already turns it off. This role leaves it alone unless you ask. On the Jetson, see which one boots:

```bash
systemctl get-default
```

`graphical.target` means the desktop starts. To turn it off from the next boot, run this from the Mac:

```bash
ansible-playbook -i inventory.ini install_specialist.yml -K --tags boot -e specialist_headless=true
```

Then reboot the Jetson:

```bash
sudo reboot
```

Compare the memory free before and after with this command on the Jetson:

```bash
free -m
```

To bring the desktop back, run this on the Jetson and then reboot:

```bash
sudo systemctl set-default graphical.target
```

### Measure it on the Jetson

Ollama's settings and what it has loaded:

```bash
systemctl show ollama -p Environment
```

```bash
ollama ps
```

The processes holding the most memory, in MB:

```bash
ps -eo rss,comm --sort=-rss | head -12 | awk 'NR==1 {print "MB   COMMAND"; next} {printf "%-5d %s\n", $1/1024, $2}'
```

Memory over time, including the GPU's share:

```bash
tegrastats --interval 2000
```

### Free disk space

Other Ollama models, such as the two the Suno Sutra base install pulls, take disk but no memory unless something asks for them. To list them on the Jetson:

```bash
ollama list
```

To delete all but the answer model, run this from the Mac. Check first that nothing else on the device uses them:

```bash
ansible-playbook -i inventory.ini install_specialist.yml -K --tags ollama -e specialist_prune_ollama_models=true
```

pip's download cache from earlier installs, on the Jetson:

```bash
du -sh ~/.cache/pip
```

```bash
rm -rf ~/.cache/pip
```

## Teacher examples

After an explanation, the device gives one short example from village life. A teacher can make these examples about things their own children know, with a plain text file of 30 to 40 everyday examples, one per line, in English.

1. Send the teacher `data/teacher_examples.txt` from this repo, printed or on WhatsApp. The notes at the top say what to write.
2. When it comes back, type or paste the teacher's lines below the notes, one per line, and save it as plain text. Numbering such as "1." is fine. Lines with digits, letters other than English, or anything the check for teachers' lines flags are skipped. That check is looser than the one on children's questions, so that Holi, gilli danda or the weekly haat are not skipped. It still misses some harmful lines, so read the list before installing it.
3. Install it from the computer that runs the install:

```bash
ansible-playbook -i inventory.ini install_specialist.yml -K --tags examples -e specialist_teacher_examples=/path/to/teacher_examples.txt
```

This replaces `/opt/specialist/teacher_examples.txt`, keeps the old one as a backup next to it, and restarts the voice loop so it reads the new file. A normal install or update never overwrites this file. It only puts in the blank template when there is no file yet.

To check which lines the device kept, run `specialist-voice --no-speak --log-level info --text "Explain photosynthesis"`. The log says `Loaded N teacher examples`, and names each skipped line and why. The first start after a new file checks the lines and saves the result in `/opt/specialist/cache`, which takes about 15 seconds for 33 lines on a Mac. Later starts read it from there, and the log says `(cached)`. Changing the file makes the device check it again. A blank or missing file gives the village examples as before.

## Run it

With the microphone, press Enter to start recording and Enter again to stop:

```bash
specialist-voice --in-lang hi --out-lang hi
```

`--in-lang` and `--out-lang` default to `auto`. For speech, a language ID model and Whisper's confidence decide between Hindi and English, then Hindi goes to Bhashini and English to Whisper. Typed questions and pages go by their script. The answer comes in the question's language, unless the question names one, such as "in English" or "हिंदी में". Set `hi` or `en` to fix either. The routing is explained in `python/pocketinfer/specialist/language.py`.

To type a question, or use a recording or a page photo:

```bash
specialist-voice --no-speak --text "What is photosynthesis?"
```

```bash
specialist-voice --in-lang hi --out-lang hi --from-wav question.wav
```

```bash
specialist-voice --in-lang en --out-lang en --from-image page.jpg
```

`--no-speak` prints the answer without playing it. Each answer prints how long every step took, under `[steps]`.

Saying "repeat that" or "दोबारा बोलो" plays the last answer again. Saying "slower" or "धीरे बोलो" plays it at 80% speed, with a pause after each sentence for writing it down. Neither asks the question again. Saying "new topic", "change the topic" or "विषय बदलो" clears the conversation and asks for the next question; "New topic. What is friction?" clears it and answers. Saying "that's all", "thank you", "stop", "बस" or "धन्यवाद" clears it and says a short goodbye. Words like "okay", "now", "please", "अच्छा" and "अब" around a command are ignored, and "okay" on its own is skipped, except during a quiz.

Saying "switch off", "shut down", "बंद हो जाओ", "स्विच ऑफ़" or "switch off karo" asks "Do you want me to switch off?". Only the very next thing said counts: "yes", "हाँ" or "switch off" again within 20 seconds, and the device says "Switching off. Wait for the fan to stop before you unplug me.", shows it on the screen, then shuts down. Anything else gets "Okay, I'll stay on." and is not answered as a question. A question about switching something else off, such as "What happens when you switch off the light?", is answered as usual.

Only the `specialist-voice` service switches off: it sets `SPECIALIST_ALLOW_POWEROFF=1`, and the install adds `/etc/sudoers.d/specialist-poweroff`, which lets the service user run exactly `/usr/bin/systemctl poweroff` without a password. Run by hand, in tests or on a Mac, the voice loop prints "would switch off now" and stays on. The service has no login session, so logind's polkit rule would ask for a password, which is why this uses sudo. To turn it off, install with `-e specialist_voice_poweroff=false --tags app,screen`.

## The talk button

At boot the voice loop starts as the `specialist-voice` service. It doesn't use the terminal, so press the talk button to start recording and again to stop. A rising beep means it's listening, and a falling beep means it stopped. Recording stops by itself after 30 seconds.

- **USB keyboard or keypad (`key`, the default):** press Enter. Any keyboard works, plugged in before or after boot.
- **Touch sensor (`gpio`):** wire the sensor's power to a 3.3 V pin, never 5 V, its ground to a ground pin and its signal to a GPIO pin. Find that pin's chip and line with `gpioinfo`, then install with `-e specialist_trigger=gpio -e specialist_gpio_chip=gpiochip0 -e specialist_gpio_line=<line> --tags boot`.

- **The touch screen (`screen`):** tap anywhere on it. It joins whatever `specialist_trigger` is set to, so a keypad or touch sensor still works too. See [The screen](#the-screen).

## The screen

The Waveshare 2.8 inch resistive touch LCD (ST7789 display, XPT2046 touch) is optional. It sits on spi1 of the 40-pin header. It shows a coloured bar with the state: tap to ask, listening, thinking, answer, not in the textbooks, or switching off. Below the bar it shows the question as heard and the answer, sentence by sentence as it is spoken. The model's own examples appear in blue. The answer stays up until the next question. If the screen is missing or fails, the voice loop carries on with voice only.

Wiring, with the Jetson off (the Jetson's header pin numbers):

| Screen | 5V | GND | MOSI | MISO | SCLK | LCD_CS | TP_CS | LCD_DC | LCD_RST | LCD_BL | TP_IRQ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Jetson pin | 2 | 6 | 19 | 21 | 23 | 24 | 26 | 29 | 31 | 32 | not used |

1. On the Jetson, turn on spi1 from jetson-io's menu: run `sudo /opt/nvidia/jetson-io/jetson-io.py`, choose Configure Jetson 40pin Header, then Configure header pins manually. Tick spi1, then go Back, Save pin changes, and Save and reboot. Its command-line tool saves the setting without adding it to the boot entry, so use the menu.
2. From the Mac, install with the screen on, then reboot the Jetson:

```bash
~/Library/Python/3.9/bin/ansible-playbook -i inventory.ini install_specialist.yml -K -e specialist_screen=true --tags app,screen
```

The install does five things:
- It stops if spi1 is off.
- It runs `tools/screen_pins.sh`. JetPack 6 leaves pins 29, 31 and 32 unable to drive, so the screen stays blank until this adds them as outputs to jetson-io's overlay.
- It installs the fonts and the Python packages.
- It gives the service user the SPI bus.
- It adds `screen` to the talk button.

Running jetson-io again rewrites the overlay, so run the install again afterwards.

To check it by hand, stop the service, then run `cd /opt/specialist/app && /opt/specialist/venv/bin/python -m local.screen demo`. `demo` draws each state, `touch` prints where presses land, and `calibrate` maps presses to pixels. Touch reads pressure, so TP_IRQ is not needed. If the text is upside down, install with `-e specialist_screen_rotation=270`.

To watch it, or stop it before running `specialist-voice` by hand or running the tests:

```bash
journalctl -u specialist-voice -f
```

```bash
sudo systemctl stop specialist-voice
```

It starts again at the next boot, or with `sudo systemctl start specialist-voice`.

## Every boot

The install sets these up to apply automatically, because they do not survive a reboot on their own:

- Continuous autofocus stays on for the camera, including after a replug.
- USB power saving stays off for the camera, because it made the camera drop off USB. A udev rule alone did not hold through boot, so the boot check applies it again before and after it runs. This second fix held after a reboot on 6 November.
- The microphone and speaker come from `/etc/default/specialist`.

The boot check then waits for the services, loads the answer model and looks for the microphone, speaker and camera. To see its result:

```bash
journalctl -u specialist-boot-check -b --no-pager | tail -12
```

It ends in `READY`, or `NOT READY` with the problems listed above it. The voice loop service starts after it.

## Power mode

The role does not change the power mode, because switching can require a reboot. On the Orin Nano Super, `MAXN_SUPER` is mode 2:

```bash
sudo nvpmodel -m 2
```

```bash
sudo jetson_clocks
```

## Undo it

Stop the services and remove the install folder:

```bash
sudo systemctl disable --now specialist-voice bhashini_models specialist-boot-check
```

```bash
sudo rm -rf /opt/specialist /usr/local/bin/specialist-voice /usr/local/bin/specialist-boot-check /usr/local/bin/specialist-usb-power /etc/default/specialist /etc/udev/rules.d/99-specialist-devices.rules /etc/systemd/system/bhashini_models.service /etc/systemd/system/specialist-boot-check.service /etc/systemd/system/specialist-voice.service /etc/systemd/system/ollama.service.d/specialist.conf /etc/sudoers.d/specialist-poweroff
```

Ollama and the system packages stay installed.
