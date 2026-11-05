# Installing The Specialist

This role installs the full software pipeline on a Jetson running JetPack 6. It does not need the upstream Suno Sutra base install, and it leaves the desktop, hostname, kernel and existing packages as they are. Physical hardware comes later and is not part of this role yet.

## What it installs

| Part | Where |
|---|---|
| Answer model, Qwen2.5-1.5B | Ollama |
| Hindi speech input, translation to and from English, speech output | Bhashini service on `127.0.0.1:11400`, in `/opt/specialist/bhashini_models` |
| English speech input, Whisper `tiny.en` | `/opt/specialist/models/whisper` |
| Retrieval, `multilingual-e5-small` | `/opt/specialist/models` |
| Answer verifier, `nli-deberta-v3-small` | `/opt/specialist/models` |
| Safety model, `nli-safety-onnx` | `/opt/specialist/models` |
| Reading pages, Tesseract | System package |
| DIKSHA index | `/opt/specialist/corpus` |
| Pipeline code and Python environment | `/opt/specialist/app`, `/opt/specialist/venv` |
| Command to run it | `/usr/local/bin/specialist-voice` |

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

## Settings

Change these in the inventory or with `-e`. The full list is in `ansible/roles/specialist/defaults/main.yml`.

| Setting | Use it to |
|---|---|
| `specialist_llm_model` | Swap the answer model, for example to the fine-tuned model once it exists |
| `specialist_ocr_packs` | Choose the languages for reading pages |
| `specialist_mic_device`, `specialist_speaker_device` | Choose the microphone and speaker |
| `specialist_assets_dir` | Reuse files you have already downloaded |

To update only the syllabus:

```bash
ansible-playbook -i inventory.ini install_specialist.yml -K --tags content
```

## Run it

```bash
specialist-voice --no-speak --text "What is photosynthesis?"
```

```bash
specialist-voice --no-speak --out-lang hi --text "What is photosynthesis?"
```

For the full voice loop, give it a WAV file, a page image or a USB microphone:

```bash
specialist-voice --in-lang hi --out-lang hi --from-wav question.wav
```

```bash
specialist-voice --in-lang hi --out-lang hi --from-image page.jpg
```

```bash
specialist-voice --in-lang hi --out-lang hi
```

Set `SPECIALIST_ALSA_DEVICE` to choose the speaker, or pass `--no-speak` to print only.

## Every boot

The install sets these up to apply automatically. USB power saving stays off for the camera and continuous autofocus stays on, including after a replug. The microphone and speaker are chosen in `/etc/default/specialist`, using `specialist_mic_device` and `specialist_speaker_device`.

A boot check then waits for the services, loads the answer model and looks for the microphone, speaker and camera. To see its result:

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
sudo systemctl disable --now bhashini_models
```

```bash
sudo rm -rf /opt/specialist /usr/local/bin/specialist-voice /etc/systemd/system/bhashini_models.service
```

Ollama and the system packages stay installed.
