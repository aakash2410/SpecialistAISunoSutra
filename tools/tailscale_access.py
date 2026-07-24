#!/usr/bin/env python3
"""Get this device onto Tailscale and report how to reach it over the internet.

Runs ON the device (e.g. the Suno Sutra Jetson). Tailscale is a mesh VPN that
works through NAT/CGNAT with no port-forwarding, and gives the device a *stable*
address, so — unlike a changing LAN IP — you don't need to keep re-publishing it.

Stdlib only — no pip installs — so it drops onto a device as-is. It just wraps
the `tailscale` CLI to do three useful things:

  status (default)  Print the device's Tailscale IP / MagicDNS name / a ready
                    ssh command. Exits 0 only if it's actually connected, so it
                    doubles as a health check.
  --up              Bring Tailscale up. Headless-friendly with an auth key
                    (no browser needed on the device), and can turn on
                    Tailscale SSH so you log in by tailnet identity — no
                    passwords or key management.
  --wait N          Block until connected (or N seconds pass). Useful on boot.

--------------------------------------------------------------------------------
First-time bring-up on the Jetson (headless):
  1. Create a reusable auth key at https://login.tailscale.com/admin/settings/keys
  2. On the device:
       export TS_AUTHKEY="tskey-auth-xxxx"
       sudo -E python3 tailscale_access.py --up --enable-ssh --hostname suno-jetson
  3. Install Tailscale on your Mac (brew install --cask tailscale) and log in to
     the SAME account. Then from anywhere:
       python3 tailscale_access.py            # on the Jetson: shows the ssh line
       ssh ubuntu@<that-address>              # from your Mac, over the internet

If `tailscale` isn't installed yet, install it (official one-liner):
       curl -fsSL https://tailscale.com/install.sh | sh
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

INSTALL_HINT = "Install it:  curl -fsSL https://tailscale.com/install.sh | sh"


def _have_tailscale() -> bool:
    return shutil.which("tailscale") is not None


def _run(cmd: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", "tailscale not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


# ------------------------------------------------------------------- status
def get_status() -> dict:
    """Parse `tailscale status --json` into the few fields we care about."""
    rc, out, err = _run(["tailscale", "status", "--json"])
    if rc == 127:
        return {"installed": False}
    info: dict = {"installed": True, "raw_error": err if rc != 0 else ""}
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        data = {}
    info["backend_state"] = data.get("BackendState", "Unknown")  # Running / NeedsLogin / Stopped
    self_node = data.get("Self") or {}
    ips = self_node.get("TailscaleIPs") or []
    ipv4 = next((ip for ip in ips if ":" not in ip), ips[0] if ips else "")
    dns = (self_node.get("DNSName") or "").rstrip(".")
    info["ipv4"] = ipv4
    info["hostname"] = self_node.get("HostName", "")
    info["magicdns"] = dns
    info["online"] = bool(self_node.get("Online", False))
    tailnet = data.get("CurrentTailnet") or {}
    info["tailnet"] = tailnet.get("Name", "")
    info["connected"] = info["backend_state"] == "Running" and bool(ipv4)
    return info


def ssh_command(info: dict, user: str, port: str) -> str:
    host = info.get("magicdns") or info.get("ipv4")
    if not host:
        return ""
    portpart = "" if str(port) == "22" else f" -p {port}"
    return f"ssh {user}@{host}{portpart}"


# ------------------------------------------------------------------- bring up
def bring_up(args) -> int:
    if not _have_tailscale():
        print(f"tailscale is not installed. {INSTALL_HINT}", file=sys.stderr)
        return 3
    cmd = ["tailscale", "up"]
    authkey = args.authkey or os.environ.get("TS_AUTHKEY")
    if authkey:
        cmd.append(f"--authkey={authkey}")
    if args.hostname:
        cmd.append(f"--hostname={args.hostname}")
    if args.enable_ssh:
        cmd.append("--ssh")
    if args.accept_routes:
        cmd.append("--accept-routes")
    if os.geteuid() != 0:
        cmd = ["sudo"] + cmd  # `tailscale up` needs root

    # Don't echo the auth key.
    shown = " ".join("--authkey=***" if c.startswith("--authkey=") else c for c in cmd)
    print(f"running: {shown}")
    if args.authkey and "--authkey" in " ".join(sys.argv):
        print("note: an auth key on the command line is visible in `ps`; prefer TS_AUTHKEY.",
              file=sys.stderr)
    rc, out, err = _run(cmd, timeout=90)
    if out:
        print(out)
    if rc != 0:
        print(err or "tailscale up failed", file=sys.stderr)
        return rc or 1
    return 0


# ------------------------------------------------------------------- render
def render(info: dict, args) -> int:
    if not info.get("installed", True):
        if args.json:
            print(json.dumps({"installed": False}))
        else:
            print(f"tailscale is not installed. {INSTALL_HINT}", file=sys.stderr)
        return 3

    info = dict(info)
    info["ssh"] = ssh_command(info, args.ssh_user, args.ssh_port)

    if args.json:
        print(json.dumps(info, indent=2))
    elif info["connected"]:
        print(f"state    : {info['backend_state']} (online={info['online']})")
        print(f"tailnet  : {info['tailnet']}")
        print(f"ipv4     : {info['ipv4']}")
        if info["magicdns"]:
            print(f"magicdns : {info['magicdns']}")
        print(f"ssh      : {info['ssh']}   # run this from your Mac, anywhere")
    else:
        state = info["backend_state"]
        print(f"NOT connected (state={state}).", file=sys.stderr)
        if state == "NeedsLogin":
            print("Bring it up:  sudo -E python3 tailscale_access.py --up --enable-ssh "
                  "   (set TS_AUTHKEY for headless).", file=sys.stderr)
        elif state == "Stopped":
            print("Start it:     sudo tailscale up", file=sys.stderr)
    return 0 if info["connected"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Get this device onto Tailscale and report how to reach it.")
    ap.add_argument("--up", action="store_true", help="bring Tailscale up (then report status)")
    ap.add_argument("--authkey", default=None, help="Tailscale auth key for headless bring-up (or set TS_AUTHKEY)")
    ap.add_argument("--hostname", default=None, help="hostname to register on the tailnet (e.g. suno-jetson)")
    ap.add_argument("--enable-ssh", action="store_true", help="enable Tailscale SSH (log in by tailnet identity)")
    ap.add_argument("--accept-routes", action="store_true", help="accept subnet routes advertised on the tailnet")
    ap.add_argument("--wait", type=float, default=0.0, help="seconds to wait for connection before reporting (0=don't)")
    ap.add_argument("--ssh-user", default=os.environ.get("SSH_USER", "ubuntu"))
    ap.add_argument("--ssh-port", default=os.environ.get("SSH_PORT", "22"))
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.up:
        rc = bring_up(args)
        if rc != 0:
            return rc

    if args.wait > 0:
        deadline = time.time() + args.wait
        while time.time() < deadline:
            if get_status().get("connected"):
                break
            time.sleep(2)

    return render(get_status(), args)


if __name__ == "__main__":
    sys.exit(main())
