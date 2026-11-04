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
- Qwen2.5-1.5B (Apache-2.0), Whisper `small.en`, `multilingual-e5-small`
