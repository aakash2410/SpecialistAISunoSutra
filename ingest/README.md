# Building the textbook index

The device answers only from an index of DIKSHA textbook pages. The index is built once, on the Mac, with the network on. The device never calls DIKSHA.

## What goes in

Science for Classes 6 to 8, in English, under the CC BY 4.0 licence only. That includes books DIKSHA labels Physics, Biology, Physical Science and Chemistry. These choices are recorded in the main [README](../README.md) under Decisions.

A dry run on 11 November found 1,980 PDFs, 7.39 GB:

| Class | Items | Size |
|---|---|---|
| 6 | 560 | 2.30 GB |
| 7 | 747 | 3.20 GB |
| 8 | 842 | 2.16 GB |

The classes add up to more than 1,980 because some books are tagged with several classes. Each is downloaded once.

## Build it

1. Check what will be downloaded, without downloading anything:

```bash
cd "/Users/aakashsangani/Desktop/Suno-Sutra-SW/Specialist v1" && python3 ingest/diksha_pull.py --mode online --dry-run --grade "Class 6" "Class 7" "Class 8" --subject Science Physics Biology "Physical Science" Chemistry --license "CC BY 4.0"
```

2. Harvest overnight. It takes about 2 to 4 hours. PDFs are read in memory and not saved, and pages with no usable text are read with Tesseract, 6 at a time. If it stops, run the same command again and it carries on from `progress.jsonl`:

```bash
cd "/Users/aakashsangani/Desktop/Suno-Sutra-SW/Specialist v1" && mkdir -p corpus/diksha_g6_8_science/raw && caffeinate -i python3 ingest/diksha_pull.py --mode online --grade "Class 6" "Class 7" "Class 8" --subject Science Physics Biology "Physical Science" Chemistry --license "CC BY 4.0" --ocr --workers 6 --out corpus/diksha_g6_8_science/raw/chapters.json --progress corpus/diksha_g6_8_science/raw/progress.jsonl 2>&1 | tee corpus/diksha_g6_8_science/raw/harvest.log
```

3. Build the index, about 20 to 40 minutes:

```bash
cd "/Users/aakashsangani/Desktop/Suno-Sutra-SW/Specialist v1" && python3 ingest/build_index.py --corpus corpus/diksha_g6_8_science
```

4. Build the module and install it as usual (see [module/README.md](../module/README.md)).

## What the steps do

- **`diksha_pull.py`** searches DIKSHA by class, subject and licence, checks each book's licence again before downloading, and keeps one document per readable page. Each page records its classes, subjects, board and licence. Without `--subject` it stops, unless you add `--all-subjects`, because every subject is about 20 GB a class.
- **`build_index.py`** removes books listed under several classes, drops garbled pages, teacher's books and front matter, splits pages into passages of up to 120 words, and embeds them with e5. `manifest.json` lists the passages per licence, class and subject.
- **Which index the device uses** is decided in `local/corpus_index.py`: `SPECIALIST_INDEX_DIR` if set, otherwise `diksha_g6_8_science`, then the older `diksha_g7_all`. `make_module.sh` ships only that one index, and the install removes any other index from the device.

## Before switching the device over

The answer thresholds, such as the 0.80 search score, were set on the Class 7 index. Run the tests and the 60-question benchmark on the new index before it replaces `diksha_g7_all`:

```bash
cd "/Users/aakashsangani/Desktop/Suno-Sutra-SW/Specialist v1" && python3 -W ignore tests/benchmark/answer_models.py qwen2.5:1.5b
```
