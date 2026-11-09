# Blind test set

A test set that no model or setting was tuned on. Use it to measure, never to tune.

`answer_check.jsonl` holds 240 candidate answer sentences, 6 for each of 40 real passages from the live DIKSHA index in `passages.jsonl`. Claude wrote the questions and sentences on 6 November 2026, before any model was run on them. They are test data only, and must never be used to train a model.

| Type | Count | What the answer check should do |
|---|---|---|
| `paraphrase` | 40 | Keep: it restates the passage |
| `simplified` | 40 | Keep: it restates the passage in a child's words |
| `outside_knowledge` | 40 | Drop: it is true, or plausible, but the passage does not say it |
| `contradiction` | 40 | Drop: the passage says the opposite |
| `wrong_detail` | 40 | Drop: one name, number or direction is wrong |
| `off_question` | 40 | Keep, because the passage supports it. It does not answer the question, which the check is not designed to catch |

The passages were drawn at random, one per book, then hand-picked for stating facts. About half of the first random draw was teacher instructions, exercises or garbled scans that state no facts, which is a finding about the index in its own right. A few chosen passages still have scan damage, because the device sees those too.

On 9 November the pipeline gained checks for flipped facts and question relevance. `score.py` now reports three columns, and `off_question` sentences are expected to be dropped by the relevance column.

## Rules

- Score with `score.py`, and report the result alongside the change that produced it.
- Never change a threshold, rule, prompt or model because of a specific item here. Once that happens, this set is no longer blind, and a new one is needed.
- Do not add these items to any training data.

```bash
python3 tests/blind/score.py
```
