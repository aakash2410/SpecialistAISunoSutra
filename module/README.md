# Installing The Specialist

This role installs the full software pipeline on a Jetson running JetPack 6, and sets up the microphone, speaker and camera. It does not need the upstream Suno Sutra base install, and it leaves the desktop, hostname, kernel and existing packages as they are.

## What it installs

| Part | Where |
|---|---|
| Answer model, Qwen2.5-1.5B | Ollama |
| Hindi speech input, translation to and from English, speech output | Bhashini service on `127.0.0.1:11400`, in `/opt/specialist/bhashini_models` |
| English speech input, Whisper `tiny.en` | `/opt/specialist/models/whisper` |
| Retrieval, `multilingual-e5-small` | `/opt/specialist/models` |
| Answer verifier, `nli-deberta-v3-small` | `/opt/specialist/models` |
| Safety model, `nli-safety-onnx` | `/opt/specialist/models` |
| Reading pages, Tesseract with Hindi and English | System package |
| DIKSHA index | `/opt/specialist/corpus` |
| Pipeline code and Python environment | `/opt/specialist/app`, `/opt/specialist/venv` |
| Command to run it | `/usr/local/bin/specialist-voice` |
| Hardware settings | `/etc/default/specialist` |
| Camera rules | `/etc/udev/rules.d/99-specialist-devices.rules` |
| Boot check | `specialist-boot-check` service |

## Install

From a computer that can reach the Jetson:

```bash
./scripts/make_module.sh
```

```bash
cd dist/specialist-module/ansible && cp inventory.ini.sample inventory.ini
```

Set your device name and user in `inventory.ini`, then run:

```bash
ansible-playbook -i inventory.ini install_specialist.yml -K
```

`-K` asks for the device's sudo password. The playbook is safe to re-run, because finished steps get skipped and downloads resume. It ends by asking test questions in English and Hindi, and it fails if the device does not cite a source, refuse an off-topic question or answer in Hindi.

To update only the code after a change, which is the usual case:

```bash
ansible-playbook -i inventory.ini install_specialist.yml -K --tags app,boot,verify
```

## Settings

Change these in the inventory or with `-e`. The full list is in `ansible/roles/specialist/defaults/main.yml`.

| Setting | Use it to |
|---|---|
| `specialist_llm_model` | Swap the answer model, for example to the fine-tuned model once it exists |
| `specialist_ocr_packs` | Choose the languages for reading pages |
| `specialist_mic_device`, `specialist_speaker_device` | Choose the microphone and speaker, by ALSA card name from `arecord -L` and `aplay -L` |
| `specialist_camera_vendor`, `specialist_camera_product` | Point the camera settings at a different USB camera, using its IDs from `lsusb` |
| `specialist_assets_dir` | Reuse files you have already downloaded |

To update only the syllabus:

```bash
ansible-playbook -i inventory.ini install_specialist.yml -K --tags content
```

## Run it

With the microphone, press Enter to start recording and Enter again to stop:

```bash
specialist-voice --in-lang hi --out-lang hi
```

Choose `--in-lang` and `--out-lang` from `hi` and `en`. English speech goes to Whisper and Hindi speech to Bhashini, because the device does not detect the language yet.

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

## Every boot

The install sets these up to apply automatically, because they do not survive a reboot on their own:

- Continuous autofocus stays on for the camera, including after a replug.
- USB power saving stays off for the camera, because it made the camera drop off USB. A udev rule alone did not hold through boot, so the boot check applies it again before and after it runs. This second fix has not yet been confirmed after a reboot.
- The microphone and speaker come from `/etc/default/specialist`.

The boot check then waits for the services, loads the answer model and looks for the microphone, speaker and camera. To see its result:

```bash
journalctl -u specialist-boot-check -b --no-pager | tail -12
```

It ends in `READY`, or `NOT READY` with the problems listed above it. The voice loop itself does not start at boot, because it still needs someone to press Enter.

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
sudo systemctl disable --now bhashini_models specialist-boot-check
```

```bash
sudo rm -rf /opt/specialist /usr/local/bin/specialist-voice /usr/local/bin/specialist-boot-check /usr/local/bin/specialist-usb-power /etc/default/specialist /etc/udev/rules.d/99-specialist-devices.rules /etc/systemd/system/bhashini_models.service /etc/systemd/system/specialist-boot-check.service
```

Ollama and the system packages stay installed.
