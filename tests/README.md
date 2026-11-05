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

On the Jetson with the full stack, each test in its own process: 47 passed, 3 skipped, 2 expected failures, 1 failed. The failure was `test_full_hindi_voice_loop_meets_the_target_on_the_device`, at 12.6 s against the 3 s target. It passed after translation moved to the GPU, along with the other live Hindi tests, but the full suite has not been rerun since. The skips are Whisper, which is left out, and two harvest tests that need PyMuPDF, which only runs off the device.

The 2 expected failures are known gaps in the answer check. It accepts some common knowledge the passage never states, and it checks that a sentence is supported, not that it answers the question.

On a Mac on 5 November 2026: 88 passed, 19 skipped, 2 expected failures. The skips are the Hindi tests, including the glossary tests, which need Bhashini, and one test that only means something on the Jetson.

## Test files

`fixtures/` holds a spoken English question, a spoken Hindi question and an image of a printed question. These are clean and synthetic: the WAV files use a macOS voice and the image is computer-rendered, so they do not show how well the device handles real classroom speech or camera photos.
