# Attribution

This repository builds on the Suno Sutra open-source edge-AI platform by CurrentAI:

- Upstream: https://github.com/currentai-org/suno-sutra-sw
- Licence: MIT

The model wrappers `asr.py`, `nmt.py` and `tts.py` in `python/pocketinfer/models/` come from that project and stay under its MIT licence. This repository is an independent derivative and is not affiliated with or endorsed by CurrentAI.

Everything else is © 2026 Aakash Sangani, MIT.

## Fetched at install time

These are not stored here. `tools/fetch_assets.py` downloads the first group from upstream's Git LFS, and the Ansible role downloads the rest:

- Bhashini speech and translation models, and the CUDA build of CTranslate2 (CurrentAI)
- Flite speech engine and voices (festvox)
- Qwen2.5-1.5B (Apache-2.0), Whisper `tiny.en` (MIT), `multilingual-e5-small`, `nli-deberta-v3-base` (Apache-2.0)

## Hindi speech vocabulary

The Ansible role restores the full 128-token list for Bhashini's Hindi speech model, because the bundled code lists only 88. The list comes from `hi-conformer.nemo` in [AI4Bharat/transactional-voice-ai](https://github.com/AI4Bharat/transactional-voice-ai), which shows no licence.

## Language ID model

`local/models/lang-id-ecapa-onnx` is an ONNX export, made with `tools/export_langid.py`, of [speechbrain/lang-id-voxlingua107-ecapa](https://huggingface.co/speechbrain/lang-id-voxlingua107-ecapa) by SpeechBrain (Apache-2.0). It was trained on VoxLingua107 (Valk and Alumäe, 2021). The export ships in the install bundle.

## Safety model

The safety model is `cross-encoder/nli-deberta-v3-small` (Apache-2.0), fine-tuned on these sources:

- Aegis 2.0 content safety dataset (NVIDIA, CC BY 4.0)
- `vibhorag101` suicide dataset (MIT)
- DIKSHA textbook content
