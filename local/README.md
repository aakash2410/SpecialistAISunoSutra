# Voice loop

Runs the full pipeline on the device: speech or a page image goes in, and a spoken, grounded answer comes out.

After the device install, use the `specialist-voice` command. See [module/README.md](../module/README.md) for examples.

From a checkout on the device, with Ollama and Bhashini running:

```bash
python -m local.voice_loop --in-lang hi --out-lang hi --from-wav question.wav
```

Each part is swappable by name:

| Option | Choices |
|---|---|
| `--asr` | `auto`, `bhashini`, `whisper` (`auto` uses Whisper `tiny.en` for English and Bhashini for Hindi) |
| `--mt` | `bhashini`, `none` |
| `--tts` | `kokoro`, `bhashini` |
| `--llm` | `ollama`, `extractive` |

The embedding model and answer verifier belong in `local/models/`. Fetch them with `python3 tools/fetch_onnx_embedder.py` and `python3 tools/fetch_onnx_verifier.py`. The safety model goes in `local/models/nli-safety-onnx`. It cannot be fetched, because it is our own fine-tune, so export it with `training/export_nli.py`.
