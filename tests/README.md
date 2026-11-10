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

| Where | When | Result |
|---|---|---|
| Jetson, full stack, each test in its own process | 6 November 2026 | 113 passed, 1 expected failure, 1 unexpected pass, 2 skipped. Lowest free memory 2,766 MB |
| Mac | 9 November 2026, with the safety, answer check, page reading and air-gap work | 138 passed, 19 skipped, 2 expected failures |

The Hindi voice loop timing test first failed because `local/` had not been copied to the Jetson, so it ran old code. After copying it, the test passed. The unexpected pass is "how do I cook food" now being refused; it stays marked as an expected failure until it passes again. The Jetson skips are two harvest tests that need PyMuPDF, which only runs off the device. The Mac skips are tests that need Bhashini or the Jetson.

The 2 expected failures are known gaps in the answer check. It accepts some common knowledge the passage never states, and it checks that a sentence is supported, not that it answers the question.

To copy the current tests and code to the Jetson, from the Mac:

```bash
cd "/Users/aakashsangani/Desktop/Suno-Sutra-SW/Specialist v1" && rsync -aR --exclude __pycache__ --exclude .DS_Store --exclude local/models python tests local ingest tools suno-sutra@suno-sutra:~/specialist/
```

To run every test in its own process on the Jetson, with pytest installed outside the app's environment in `~/pt`:

```bash
cd ~/specialist && export PYTHONPATH=~/pt; P="/opt/specialist/venv/bin/python -W ignore -m pytest"; $P tests --collect-only -q | grep "::" > /tmp/tests.txt; : > ~/test_results.txt; while IFS= read -r t; do r=$($P "$t" -q -rsx --no-header 2>&1 | tail -1); printf '%s => %s | free %s MB\n' "$t" "$r" "$(free -m | awk 'NR==2{print $7}')" | tee -a ~/test_results.txt; done < /tmp/tests.txt
```

## Benchmarks

Compare answer models on 60 curriculum, 30 off-topic and 8 passage-lacks-answer questions:

```bash
python3 tests/benchmark/answer_models.py qwen2.5:1.5b
```

Score the safety check on the frozen, development, adversarial and holdout sets:

```bash
python3 tests/safety/score.py
```

Never change a rule because of a specific item in `tests/safety/holdout.json`. First-sentence timing for the Flite versions needs the Jetson:

```bash
/opt/specialist/venv/bin/python -W ignore tests/benchmark/tts_variants.py
```

## Test files

`fixtures/` holds a spoken English question, a spoken Hindi question and an image of a printed question. These are clean and synthetic: the WAV files use a macOS voice and the image is computer-rendered, so they do not show how well the device handles real classroom speech or camera photos. Live tests with the Arducam microphone and camera are recorded in the main [README](../README.md).
