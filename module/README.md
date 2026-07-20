# The Specialist — device module

A **purely additive overlay** for a Suno Sutra device that already has the base
platform installed. It adds new files only — **no base file is modified or
overwritten**, so the platform stays upstream-updatable.

## What it installs

| Component | On-device destination |
|---|---|
| `embed.py`, `ocr.py` (new model wrappers) | `{device_root}/python/pocketinfer/models/` |
| `specialist/` (engine, grounding, retrieval) | `{device_root}/python/pocketinfer/specialist/` |
| `specialist_app.py` (the application) | `{device_root}/python/pocketinfer/applications/` |
| DIKSHA corpus + index (content) | `/opt/specialist/corpus/` |
| Boot-into-Specialist systemd drop-in | `/etc/systemd/system/pocketinfer.service.d/10-specialist.conf` |

`device_root` defaults to `/home/ubuntu/pocket-infer-sw`.

Content is deliberately kept **outside** the code tree so the syllabus can be
refreshed without redeploying the module (`--tags content`).

## Install

```bash
cp ansible/inventory.ini.sample ansible/inventory.ini   # adjust for your device
cd ansible
ansible-playbook -i inventory.ini install_specialist.yml
```

The app is auto-discovered by the platform's application registry, so it shows
up in `pocketinfer-service --list-apps` with no extra wiring, and the drop-in
makes the device boot into it.

## Before you run it — confirm the Python environment

The base platform is inconsistent about where the service runs from:

- the base `pocketinfer.service` unit points at the **venv**
  (`{device_root}/python/venv/bin/pocketinfer-service`), while
- the base `app` Ansible role pip-installs **system-wide** and calls
  `/usr/local/bin/pocketinfer-service`.

The module's dependencies must land in whichever environment actually runs the
service, or the app will import-fail on boot. Defaults assume the **venv**;
switch with:

```bash
ansible-playbook -i inventory.ini install_specialist.yml \
  -e specialist_exec_path=/usr/local/bin/pocketinfer-service \
  -e specialist_pip_executable=pip3
```

## Useful overrides / tags

```bash
-e specialist_set_default_app=false      # don't change the boot app
-e specialist_corpus_dir=/opt/specialist/corpus
--tags content                            # refresh syllabus only (no code)
--tags code                               # push code only
```

## Rollback

Remove the drop-in and reload systemd to return the device to its previous
default application; the added files are inert unless the app is selected:

```bash
sudo rm /etc/systemd/system/pocketinfer.service.d/10-specialist.conf
sudo systemctl daemon-reload && sudo systemctl restart pocketinfer
```

## Regenerating this module

It is generated from the canonical source tree — don't edit it by hand:

```bash
./scripts/make_module.sh --tar
```

See `MANIFEST.txt` in the generated module for the exact file→destination map.
