# Tests

There are no unit tests here. These suites run the real system, with real retrieval and the real commands.

| Suite | Question it answers |
|---|---|
| `test_sit.py` | System integration: do the real parts work together, from ingest through to the commands? |
| `test_uat.py` | User acceptance: does the device meet the v1 spec, as a teacher sees it? |

```bash
pip install -r requirements.txt -r tests/requirements-test.txt
```

```bash
pytest tests/ -q -rsx
```

A test that needs something this machine lacks skips and names it. A full pass needs the device install, because that is where Bhashini runs. The timing tests only run on the Jetson.

## Current results

On a Mac with Ollama, Whisper and Tesseract, without Bhashini: 24 passed, 6 skipped, 3 expected failures. The suites have not been run on the Jetson yet.

The 3 expected failures are the known gap: the stock model answers from memory when an on-topic passage lacks the answer. They are in `test_device_does_not_answer_from_memory`, and the fine-tune should clear them.

## Test files

`fixtures/` holds a spoken English question, a spoken Hindi question and an image of a printed question. These are clean and synthetic: the WAV files use a macOS voice and the image is computer-rendered, so they do not show how well the device handles real classroom speech or camera photos.
