#!/usr/bin/env python3
"""Fetch the Specialist's large binary assets from upstream Git LFS.

The upstream Suno Sutra repo stores its models and CUDA builds in Git LFS, and a
plain checkout only holds small pointer files. This fetches the real files listed
in module/assets.json. Standard library only, so it runs on a bare device.

Downloads resume after a dropped connection. A fresh download link is requested on
every attempt because the links expire. Every file is checked against its SHA-256
before it is moved into place, and a file that is already correct is left alone.

    python3 tools/fetch_assets.py --manifest module/assets.json --dest /opt/specialist/assets
    python3 tools/fetch_assets.py --manifest module/assets.json --dest DIR --only bhashini_models.zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

LFS_MEDIA = "application/vnd.git-lfs+json"
CHUNK = 1 << 20


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def download_link(repo: str, oid: str, size: int) -> str:
    body = json.dumps({
        "operation": "download", "transfers": ["basic"],
        "objects": [{"oid": oid, "size": size}],
    }).encode()
    req = urllib.request.Request(
        f"https://github.com/{repo}.git/info/lfs/objects/batch", data=body,
        headers={"Accept": LFS_MEDIA, "Content-Type": LFS_MEDIA},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        obj = json.load(r)["objects"][0]
    if "actions" not in obj:
        raise RuntimeError(f"upstream has no object {oid[:12]}: {obj.get('error')}")
    return obj["actions"]["download"]["href"]


def fetch_once(repo: str, asset: dict, part: str) -> None:
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if have >= asset["size"]:
        return
    href = download_link(repo, asset["sha256"], asset["size"])
    req = urllib.request.Request(href, headers={"Range": f"bytes={have}-"})
    with urllib.request.urlopen(req, timeout=60) as r:
        # A server that ignores Range sends the whole file, so start again.
        mode = "ab" if r.status == 206 else "wb"
        got = have if r.status == 206 else 0
        last = time.time()
        with open(part, mode) as f:
            for block in iter(lambda: r.read(CHUNK), b""):
                f.write(block)
                got += len(block)
                if time.time() - last > 15:
                    print(f"  {asset['name']}: {got * 100 // asset['size']}% "
                          f"of {asset['size'] / 1e6:.0f} MB", flush=True)
                    last = time.time()


def fetch(repo: str, asset: dict, dest: str, retries: int) -> None:
    final = os.path.join(dest, asset["name"])
    if os.path.exists(final) and os.path.getsize(final) == asset["size"] \
            and sha256_of(final) == asset["sha256"]:
        print(f"ok (already present): {asset['name']}", flush=True)
        return
    part = final + ".part"
    for attempt in range(1, retries + 1):
        try:
            fetch_once(repo, asset, part)
            break
        except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:
            print(f"  attempt {attempt}/{retries} stopped ({e}); resuming", flush=True)
            time.sleep(min(60, 5 * attempt))
    else:
        raise SystemExit(f"could not download {asset['name']} after {retries} attempts")
    if sha256_of(part) != asset["sha256"]:
        os.unlink(part)
        raise SystemExit(f"checksum mismatch for {asset['name']}; partial file removed, re-run")
    os.replace(part, final)
    print(f"ok (verified): {asset['name']}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch Specialist binary assets from Git LFS.")
    ap.add_argument("--manifest", required=True, help="path to assets.json")
    ap.add_argument("--dest", required=True, help="directory to place the files in")
    ap.add_argument("--only", action="append", help="fetch only this file (repeatable)")
    ap.add_argument("--retries", type=int, default=200)
    args = ap.parse_args()
    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    os.makedirs(args.dest, exist_ok=True)
    for asset in manifest["assets"]:
        if not args.only or asset["name"] in args.only:
            fetch(manifest["lfs_repo"], asset, args.dest, args.retries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
