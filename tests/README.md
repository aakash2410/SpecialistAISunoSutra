# Tests

See [TEST_PLAN.md](TEST_PLAN.md) for the full plan (software → device I/O →
acceptance criteria). This folder holds the runnable parts.

## Layer A — automated software suite (`pytest`)

Runs anywhere (laptop or device), no hardware, no network — it builds a
temporary TF-IDF index from the corpus so results are deterministic regardless
of whether the neural embedder is installed.

```bash
pip install -r tests/requirements-test.txt
pytest tests/ -q
```

| File | Covers |
|------|--------|
| `test_embed.py` | embedding determinism, normalization, build==run state round-trip |
| `test_index_integrity.py` | the shipped index is consistent + every chunk is citable |
| `test_vector_index.py` | ranked retrieval, right-chapter hits, off-topic ≈ 0, dim guard |
| `test_grounding.py` | intent classification, both refusal gates, grounded prompt |
| `test_pipeline.py` | full engine loop, citations, refusal, MT/TTS hooks, timings |
| `test_providers.py` | AudioClip round-trip, provider contracts, factory errors |

Tests that need `sentence-transformers`, `vosk`, or macOS `say` **skip** cleanly
when those aren't present, so a green run on a fresh laptop is expected to show a
couple of skips.

## Layer B — on-device health + I/O

Run on the Jetson after installing the module:

```bash
python3 tests/device_io_check.py                 # services, index, resources
python3 tests/device_io_check.py --interactive   # + mic / speaker / camera / screen / button
python3 tests/device_io_check.py --json          # machine-readable
```

Exits non-zero if any non-skipped check FAILs, so it can gate a smoke run in a
provisioning script. On a laptop it will (correctly) FAIL the BHASHINI/ollama
service checks and SKIP the board — that's expected; it's meant for the device.

## Layer C+ — manual

Follow the procedures in [TEST_PLAN.md](TEST_PLAN.md) (integration, acceptance
criteria, robustness, provisioning) on real hardware and record results in the
sign-off table.
