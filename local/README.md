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
| `--asr` | `auto`, `whisper`, `bhashini` (`auto` uses Whisper for English and Bhashini otherwise) |
| `--mt` | `bhashini`, `none` |
| `--tts` | `bhashini` |
| `--llm` | `ollama`, `extractive` |

The `e5` embedding model belongs in `local/models/`. Fetch it with `python3 tools/fetch_onnx_embedder.py`.
