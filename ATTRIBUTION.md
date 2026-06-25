# Attribution & vendored assets

## Upstream platform

This repository builds on the **Suno Sutra** open-source edge-AI platform by
**CurrentAI**:

- Upstream: https://github.com/currentai-org/suno-sutra-sw
- License: MIT (per the upstream `setup.py`)

The directories `python/` (the `pocketinfer` platform code, minus the Specialist
additions noted below), `ioexpander/`, `rootfs/`, and `assets/` originate from
that project and remain under its MIT license. This repository is an independent
derivative and is **not** affiliated with or endorsed by CurrentAI.

## The Specialist additions (new in this repository)

Added on top of the platform, © 2026 Aakash Sangani, MIT:

- `python/pocketinfer/models/embed.py`, `python/pocketinfer/models/ocr.py`
- `python/pocketinfer/specialist/` (vector search, grounding, pipeline, LLM adapter, corpus)
- `python/pocketinfer/applications/specialist_app.py`
- `rootfs/roles/specialist/`
- `ingest/`, `demo.py`

## Vendored binary assets are NOT included (git-LFS)

The upstream project stores large binaries with **git-LFS**: the BHASHINI model
bundles, prebuilt wheels/debs, and font files —

```
*.zip  *.deb  *.whl  *.pcf
```

These are **excluded** from this repository (see `.gitignore`) to keep it light
and because they are CurrentAI's redistributable assets, not ours. The code and
the baked DIKSHA index are all here; only those vendored binaries are missing.

To obtain them for a full device provision, fetch them from upstream:

```bash
git clone https://github.com/currentai-org/suno-sutra-sw.git
cd suno-sutra-sw
git lfs install && git lfs pull            # downloads the LFS binaries
# then copy the *.zip/*.deb/*.whl/*.pcf files into the matching paths here,
# e.g. rootfs/roles/indic/files/ and python/pocketinfer/ui/
```

(Confirm bulk-download / redistribution licensing for those specific assets
before re-hosting them, per the product spec's open item.)
