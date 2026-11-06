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
| Jetson, full stack, each test in its own process | 5 November 2026 | 104 passed, 1 failed, 2 expected failures, 2 skipped. Lowest free memory 2,531 MB |
| Mac | 5 November 2026, after removing unused code | 91 passed, 19 skipped, 2 expected failures |

The Jetson failure was "चालन क्या है?" not translating as conduction. It is fixed, and other changes followed, so the suite needs another run on the device once the current code is installed. The Jetson skips are two harvest tests that need PyMuPDF, which only runs off the device. The Mac skips are tests that need Bhashini or the Jetson.

The 2 expected failures are known gaps in the answer check. It accepts some common knowledge the passage never states, and it checks that a sentence is supported, not that it answers the question.

To run every test in its own process on the Jetson, with pytest installed outside the app's environment in `~/pt`:

```bash
cd ~/specialist && export PYTHONPATH=~/pt; P="/opt/specialist/venv/bin/python -W ignore -m pytest"; $P tests --collect-only -q | grep "::" > /tmp/tests.txt; : > ~/test_results.txt; while IFS= read -r t; do r=$($P "$t" -q -rsx --no-header 2>&1 | tail -1); printf '%s => %s | free %s MB\n' "$t" "$r" "$(free -m | awk 'NR==2{print $7}')" | tee -a ~/test_results.txt; done < /tmp/tests.txt
```

## Test files

`fixtures/` holds a spoken English question, a spoken Hindi question and an image of a printed question. These are clean and synthetic: the WAV files use a macOS voice and the image is computer-rendered, so they do not show how well the device handles real classroom speech or camera photos. Live tests with the Arducam microphone and camera are recorded in the main [README](../README.md).
