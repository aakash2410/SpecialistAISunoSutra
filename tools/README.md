# Device tools

## `fetch_onnx_embedder.py` — the neural embedding model (ONNX)

The production embedder runs `multilingual-e5-small` on **onnxruntime** (already
installed on the Jetson via the base image) with only a lightweight `tokenizers`
dependency — no torch/sklearn/scipy, so it avoids the Jetson NumPy/ABI conflicts.

```bash
# 1. deps (on the device use the base's onnxruntime-gpu; do NOT pip-install
#    plain onnxruntime over it). Keep numpy < 2.
pip3 install --user "numpy<2" tokenizers

# 2. fetch the model once (online): ~120 MB quantized (default) or --fp32
python3 tools/fetch_onnx_embedder.py --dest /opt/specialist/models/multilingual-e5-small-onnx

# 3. rebuild the index with the neural embedder, then deploy it
python3 ingest/build_index.py --prefer onnx
sudo cp -r corpus/diksha_g7_science/index /opt/specialist/corpus/diksha_g7_science/

# 4. confirm
python3 tests/device_io_check.py     # embedding model (neural) -> PASS
```

`Embed` finds the model automatically at `/opt/specialist/models/…`,
`local/models/…` (dev checkout), or `$SPECIALIST_ONNX_DIR`. If it's absent, the
embedder falls back to TF-IDF so the device keeps working.

---

## Whisper ASR (context-aware English speech-to-text)

Replaces the small Vosk model (which mangles science vocabulary — "photosynthesis"
→ "photo sensis") with `faster-whisper`, which attends over the whole utterance.
Backend is CTranslate2, **already on the device** (CUDA build, used by BHASHINI).

```bash
# 1. install faster-whisper WITHOUT clobbering the device's CUDA ctranslate2:
pip3 install --user faster-whisper
python3 -c "import ctranslate2; print(ctranslate2.__version__)"   # verify unchanged
#   if it got replaced by a CPU wheel, reinstall the CUDA one:
#   pip3 install --user --force-reinstall rootfs/roles/indic/files/ctranslate2-*.whl

# 2. pre-fetch the model once (online) into the device data dir:
mkdir -p /opt/specialist/models/whisper
python3 -c "from pocketinfer.models.whisper import Whisper; Whisper(model_size='small.en', model_dir='/opt/specialist/models/whisper')"

# 3. test it
python3 -m local.voice_loop --from-wav question.wav --asr whisper --embed-prefer <match-index>
```

The app uses Whisper for English automatically (`input_language=en`) and falls
back to Vosk if faster-whisper isn't installed. Set `SPECIALIST_WHISPER_DIR` to
override the model location.

---

# Remote-access tools

## `tailscale_access.py`

Get the device onto **Tailscale** (a mesh VPN) and report exactly how to SSH in
**over the internet** — through NAT/CGNAT, no port-forwarding, no changing IPs to
chase. Stdlib only; it just wraps the `tailscale` CLI.

### One-time setup

**On the Jetson** (SSH in locally once, e.g. over the USB tether):
```bash
# 1. install tailscale (official one-liner)
curl -fsSL https://tailscale.com/install.sh | sh

# 2. bring it up. Headless (no browser on the device) via an auth key from
#    https://login.tailscale.com/admin/settings/keys
export TS_AUTHKEY="tskey-auth-xxxx"
sudo -E python3 tools/tailscale_access.py --up --enable-ssh --hostname suno-jetson
```
`--enable-ssh` turns on **Tailscale SSH**: you log in by tailnet identity, so no
passwords or SSH keys to manage.

**On your Mac:**
```bash
brew install --cask tailscale      # or the Mac App Store "Tailscale"
```
Open it and log in to the **same** Tailscale account.

### Every day

On the Jetson, ask where it is:
```bash
python3 tools/tailscale_access.py
# state    : Running (online=True)
# ipv4     : 100.101.102.103
# magicdns : suno-jetson.tail1234.ts.net
# ssh      : ssh ubuntu@suno-jetson.tail1234.ts.net   # run this from your Mac, anywhere
```
Then from your **Mac terminal**, from any network:
```bash
ssh ubuntu@suno-jetson.tail1234.ts.net
```
That's the full remote-control path — a normal shell on the Jetson, over the
internet. (macOS already has `ssh`; nothing to install beyond the Tailscale app.)

### The address is stable

Unlike a LAN/DHCP IP, the Tailscale address doesn't change — so there's nothing
to keep re-publishing. Bookmark the `magicdns` name and you're done.

### Handy flags

```bash
--up                 bring Tailscale up (then report)
--enable-ssh         turn on Tailscale SSH during --up
--authkey / TS_AUTHKEY   headless auth (env is safer than the flag)
--hostname NAME      register a friendly name on the tailnet
--wait 60            block until connected (use on boot)
--json               machine-readable status
--ssh-user / --ssh-port   customise the printed ssh command (default ubuntu:22)
```

Exit code is **0 only when connected**, so it doubles as a health check
(`python3 tools/tailscale_access.py >/dev/null && echo up`).

### Bring it up automatically on boot (systemd, on the device)

`/etc/systemd/system/tailscale-access.service`
```ini
[Unit]
Description=Ensure Tailscale is up for remote access
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=TS_AUTHKEY=tskey-auth-xxxx
ExecStart=/usr/bin/python3 /home/ubuntu/pocket-infer-sw/tools/tailscale_access.py --up --enable-ssh --hostname suno-jetson --wait 60

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tailscale-access.service
```
(Once a device is authed, `tailscaled` reconnects on its own on boot — this unit
just guarantees state + SSH are enabled and is a no-op if already up.)

### Security

- The **auth key** is a secret — pass it via `TS_AUTHKEY` or a root-only unit
  file, never hardcode or commit it. Use an **ephemeral, pre-approved** key for
  devices so a lost key can't linger.
- Restrict who/what can reach the device with **tailnet ACLs** in the Tailscale
  admin console rather than opening anything to the public internet.
