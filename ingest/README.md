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
- **`build_index.py`** keeps one copy of each page of a book listed under several classes. It leaves out books tagged with a class outside the harvest, or with more than 3 classes. In books that bind science with other subjects, such as the Tamil Nadu term books, it keeps only the science pages, judged page by page from the words on them. It drops Wikipedia copies, garbled pages, teacher's books, books whose title marks them as a teacher's handbook (such as "THB" or "Handbook"), passages that state lesson aims ("students will be able to", "the teacher will", "learning outcomes") and front matter, splits pages into passages of up to 120 words, and embeds them with e5. `manifest.json` lists the passages per licence, class and subject, and what was left out. `words.json` lists every word in the index, so the device can tell when a request word is in none of its passages.
- **Which index the device uses** is decided in `local/corpus_index.py`: `SPECIALIST_INDEX_DIR` if set, otherwise `diksha_g6_8_science`, then the older `diksha_g7_all`. `make_module.sh` ships only that one index, and the install removes any other index from the device.

## What the 12 November index holds

The device uses `diksha_g6_8_science`, at the 0.80 search score. The 11 November build had 54,065 passages. Five filters then took rows out of it without embedding again, and `build_index.py` now applies the same filters, so a rebuild gives the same index:

| Left out | Passages | Why |
|---|---|---|
| 63 books tagged with a class outside 6 to 8 | 1,555 | Such as a Class 6 to 10 book on AI in science. It won class searches for every class, and reached the top 3 for 1 of 125 test questions |
| Pages of 29 mixed-subject books that are not science | 2,785 | Mostly the history, civics, economics and maths pages of the Tamil Nadu term books, judged page by page. One of those books is really a maths book. Pages on earthquakes, rocks and weather stay |
| 50 Wikipedia copies | 2,787 | Written for adults, and CC BY-SA rather than CC BY. They answered "How does a jet engine work?" and "What is DNA replication?" |
| 13 teacher's handbooks, judged by title | 2,556 | The Andhra Pradesh Science Handbooks for Classes 6, 7 and 8, and 9 chapters marked THB (Teacher's Handbook). Period-by-period teaching notes. On 12 November the device read out a THB lesson aim as an answer |
| Passages that state lesson aims | 1,956 | "Learners will be able to", "the teacher will", "learning outcomes", "by the end of this lesson", almost all from lesson plans. 24 of 25 sampled were teaching notes throughout, so the whole passage goes. "Aim:" and "Objective:" stay, since textbook activities use them |

That leaves 42,426 passages from 1,499 books, all CC BY 4.0.

On the 98-question benchmark it answers 21 of 22 science questions and refuses 28 of 30 off-topic ones, as before the last two filters, and 37 of 42 common quiz topics still have 3 or more clean textbook questions. Maths, social studies and English are mostly refused, since the index is science only. "What is machine learning?" is answered from a Class 6 textbook that defines it.

To check again after a rebuild:

```bash
cd "/Users/aakashsangani/Desktop/Suno-Sutra-SW/Specialist v1" && python3 -W ignore tests/benchmark/answer_models.py qwen2.5:1.5b
```
