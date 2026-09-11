#!/usr/bin/env python3
"""
Prove — by checksum, not by ear — that the anonymized v1..v4 files the study
actually serves are byte-identical to the real, correctly-labeled files in
the original zip. This is the "are we 100% sure we're not mixing up which
audio is which system" check, run once against the source-of-truth backup
rather than trusting the rename script or the HTML by inspection alone.

For each item referenced in index.html's CONFIG:
  - v1/v2/v3 must match the zip's <real-system-name>.wav for that folder
    (real system per system-mapping.secret.json).
  - v4 must match the zip's input.wav for that folder (it's the hidden
    anchor now, not a distinct edited file — see system-mapping.secret.json).
  - source must also match the zip's input.wav for that folder.

Run from the repo root:
    python3 scripts/verify_audio_integrity.py

Exits non-zero (and prints every mismatch) if anything doesn't line up.
"""
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = REPO_ROOT / "index.html"
MAPPING_PATH = REPO_ROOT / "system-mapping.secret.json"
ZIP_CANDIDATES = list(REPO_ROOT.glob("*.zip"))


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_config_items():
    html = HTML_PATH.read_text()
    m1 = re.search(r"practiceItems:\s*(\[.*?\])\s*,\s*\n\s*screeningRounds", html, re.S)
    m2 = re.search(r"\n  items:\s*(\[.*?\])\s*\n\};", html, re.S)
    if not m1 or not m2:
        sys.exit("Could not find practiceItems/items in index.html — did its structure change?")
    return json.loads(m1.group(1)) + json.loads(m2.group(1))


def folder_name_for(item_id: str) -> str:
    # assets/<folder>/... paths already exist in CONFIG; just read one of them.
    return None  # unused — we read the actual path out of CONFIG instead.


def main():
    if not MAPPING_PATH.exists():
        sys.exit(f"{MAPPING_PATH.name} not found — can't decode which real system is which token.")
    if not ZIP_CANDIDATES:
        sys.exit("No .zip found in the repo root — need the original backup to verify against.")
    zip_path = ZIP_CANDIDATES[0]

    mapping = json.loads(MAPPING_PATH.read_text())  # token -> real system name (or anchor note)
    real_system_of = {
        tok: (name if name in ("our_tf_nit09_rg05", "sao_instruct", "sdedit", "echoedit_plus") else None)
        for tok, name in mapping.items()
    }
    anchor_tokens = {tok for tok, name in mapping.items() if real_system_of[tok] is None}

    items = load_config_items()
    print(f"Checking {len(items)} items against {zip_path.name} using {MAPPING_PATH.name} ...\n")

    zf = zipfile.ZipFile(zip_path)
    # zip entries look like listening-test-v4/<folder>/<file>.wav
    zip_root = None
    for n in zf.namelist():
        if n.endswith("/") and "/" not in n[:-1] and not n.startswith("__MACOSX"):
            zip_root = n
            break
    if zip_root is None:
        sys.exit("Could not find the top-level folder inside the zip.")

    failures = []
    checked = 0

    def zip_bytes(folder: str, filename: str) -> bytes:
        path = f"{zip_root}{folder}/{filename}"
        try:
            return zf.read(path)
        except KeyError:
            return None

    for item in items:
        # folder name is embedded in every asset path, e.g. assets/05_testB.../v1.wav
        any_path = item["source"]
        folder = any_path.split("/")[1]

        # source (and by extension v4, the hidden anchor) must match the zip's input.wav
        for label, rel_path in [("source", item["source"])] + [
            (tok, path) for tok, path in item["audio"].items() if tok in anchor_tokens
        ]:
            local = (REPO_ROOT / rel_path).read_bytes()
            zbytes = zip_bytes(folder, "input.wav")
            checked += 1
            if zbytes is None:
                failures.append(f"{item['id']} [{label}]: input.wav not found in zip for {folder}")
            elif sha256_of(local) != sha256_of(zbytes):
                failures.append(f"{item['id']} [{label}]: checksum mismatch vs zip's input.wav")

        for tok, rel_path in item["audio"].items():
            real = real_system_of.get(tok)
            if real is None:
                continue  # anchor token, already checked above via source/input.wav
            local_file = REPO_ROOT / rel_path
            if not local_file.exists():
                failures.append(f"{item['id']} [{tok}]: {rel_path} does not exist on disk")
                continue
            local = local_file.read_bytes()
            zbytes = zip_bytes(folder, f"{real}.wav")
            checked += 1
            if zbytes is None:
                failures.append(f"{item['id']} [{tok}={real}]: {real}.wav not found in zip for {folder}")
            elif sha256_of(local) != sha256_of(zbytes):
                failures.append(f"{item['id']} [{tok}={real}]: checksum MISMATCH vs zip's {real}.wav")

    print(f"Compared {checked} files.")
    if failures:
        print(f"\n{len(failures)} problem(s) found:\n")
        for f in failures:
            print(f"  ✗ {f}")
        sys.exit(1)

    print("All anonymized files are byte-identical to their labeled originals in the zip. ✓")


if __name__ == "__main__":
    main()
