# Dev test set

The tuning partner to the blind set in `../blind/`. Tune the answer check against this set, then score the blind set once to see whether the change holds on data it never saw.

`answer_check.jsonl` holds 192 sentences, 6 for each of 32 passages in `passages.jsonl`, using the same six types as the blind set. Claude wrote them on 6 November 2026. The passages come from different books than the blind set's, chosen the same way. These are test data only, and must never be used to train a model.

```bash
python3 tests/blind/score.py --set dev
```

`retrieval.jsonl` holds 61 curriculum questions with a short reference answer each, written by Claude on 6 November 2026, to tune search. A question counts as found when a passage sent to the answer model supports its reference answer. Some questions have no supporting passage in the index at all, so the best possible score is below 61.
