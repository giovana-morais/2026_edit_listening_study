#!/usr/bin/env python3
"""
Validate and aggregate exported listening-study CSVs.

Two jobs, deliberately kept together: before trusting any aggregate number,
this script checks each participant's CSV is structurally sound (right
systems, right positions, right value ranges, nothing missing or duplicated)
against the *actual* CONFIG in index.html — not a hardcoded assumption of
what the study looks like, so it can't silently drift out of sync with the
page. Only after validation does it decode v1..v4 into real system names
(via system-mapping.secret.json) and compute per-system stats.

Usage:
    python3 scripts/compute_results.py path/to/one.csv path/to/two.csv ...
    python3 scripts/compute_results.py                # defaults to scripts/*.csv

Requires: pandas (pip install pandas)
"""
import glob
import json
import re
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = REPO_ROOT / "index.html"
MAPPING_PATH = REPO_ROOT / "system-mapping.secret.json"

ANCHOR_LABEL = "hidden anchor (unedited input)"


def load_config():
    html = HTML_PATH.read_text()
    m_items = re.search(r"\n  items:\s*(\[.*?\])\s*\n\};", html, re.S)
    m_systems = re.search(r"systems:\s*(\[.*?\])\s*,\s*\n\s*\n?\s*//", html, re.S) \
        or re.search(r"systems:\s*(\[.*?\])\s*,", html, re.S)
    m_scales = re.search(r"scales:\s*(\[.*?\])\s*,\s*\n\s*\n?\s*//", html, re.S) \
        or re.search(r"scales:\s*(\[.*?\])\s*,", html, re.S)
    if not (m_items and m_systems and m_scales):
        sys.exit("Could not find items/systems/scales in index.html — did its structure change?")

    def as_js_object_array(text):
        # systems/scales entries use bare (unquoted) keys, e.g. { id: "v1", label: "..." }.
        # Quote the keys so json.loads can parse them.
        quoted = re.sub(r"(\w+):", r'"\1":', text)
        return json.loads(quoted)

    items = json.loads(m_items.group(1))
    systems = as_js_object_array(m_systems.group(1))
    scales = as_js_object_array(m_scales.group(1))
    return items, systems, scales


def load_mapping():
    if not MAPPING_PATH.exists():
        sys.exit(f"{MAPPING_PATH.name} not found — can't decode system ids into real names.")
    return json.loads(MAPPING_PATH.read_text())


def validate(df, csv_path, item_ids, system_ids, scale_ids):
    letters = "ABCDEFGH"[: len(system_ids)]
    problems = []

    seen_items = set(df["item"])
    missing = item_ids - seen_items
    extra = seen_items - item_ids
    if missing:
        problems.append(f"missing {len(missing)} item(s) entirely: {sorted(missing)}")
    if extra:
        problems.append(f"{len(extra)} unexpected item id(s) not in CONFIG.items: {sorted(extra)}")

    zero_plays = 0
    for item_id, group in df.groupby("item"):
        if len(group) != len(system_ids):
            problems.append(f"{item_id}: expected {len(system_ids)} rows, found {len(group)}")
            continue
        got_systems = set(group["system"])
        if got_systems != system_ids:
            problems.append(f"{item_id}: system set {sorted(got_systems)} != expected {sorted(system_ids)}")
        got_positions = set(group["position"])
        if got_positions != set(letters):
            problems.append(f"{item_id}: position set {sorted(got_positions)} != expected {sorted(letters)}")
        for scale in scale_ids:
            bad = group[~group[scale].between(1, 5)]
            if len(bad):
                problems.append(f"{item_id}: {len(bad)} out-of-range value(s) in '{scale}'")
        zero_plays += (group["plays"] == 0).sum()

    if zero_plays:
        problems.append(f"NOTE (not an error): {zero_plays} row(s) rated with 0 recorded plays")

    hard_errors = [p for p in problems if not p.startswith("NOTE")]
    status = "FAIL" if hard_errors else ("OK" if not problems else "OK (with notes)")
    print(f"[{status}] {csv_path.name} — {len(df)} rows, {df['item'].nunique()} items")
    for p in problems:
        print(f"    {p}")
    return not hard_errors


def main():
    args = sys.argv[1:]
    paths = [Path(p) for p in args] if args else [Path(p) for p in glob.glob(str(REPO_ROOT / "scripts" / "*.csv"))]
    if not paths:
        sys.exit("No CSVs given and none found under scripts/*.csv")

    items, systems, scales = load_config()
    item_ids = {it["id"] for it in items}
    system_ids = {s["id"] for s in systems}
    scale_ids = [s["id"] for s in scales]
    mapping = load_mapping()

    def real_label(token):
        real = mapping.get(token, token)
        return ANCHOR_LABEL if real not in ("our_tf_nit09_rg05", "sao_instruct", "sdedit", "echoedit_plus") else real

    frames = []
    all_valid = True
    for path in paths:
        df = pd.read_csv(path)
        required_cols = {"item", "system", "position", "plays", "secondsOnTrial", *scale_ids}
        missing_cols = required_cols - set(df.columns)
        if missing_cols:
            print(f"[FAIL] {path.name} — missing column(s): {sorted(missing_cols)}")
            all_valid = False
            continue
        ok = validate(df, path, item_ids, system_ids, scale_ids)
        all_valid = all_valid and ok
        df["participant"] = path.stem
        df["real_system"] = df["system"].map(real_label)
        frames.append(df)

    if not frames:
        sys.exit("\nNo valid CSVs to aggregate.")

    combined = pd.concat(frames, ignore_index=True)

    print(f"\n{'='*60}")
    print(f"Aggregating {len(frames)} participant(s), {len(combined)} ratings total")
    if not all_valid:
        print("NOTE: one or more CSVs had validation problems — see above. "
              "Still aggregating everything that parsed, but check the flagged rows.")
    print(f"{'='*60}\n")

    summary = (
        combined.groupby("real_system")[scale_ids]
        .agg(["count", "mean", "std"])
        .round(2)
    )
    print(summary.to_string())

    out_path = REPO_ROOT / "scripts" / "results-summary.csv"
    summary.to_csv(out_path)
    print(f"\nWrote decoded per-system summary to {out_path} (gitignored).")

    if ANCHOR_LABEL in combined["real_system"].values:
        anchor = combined[combined["real_system"] == ANCHOR_LABEL]
        q, a = anchor["quality"].mean(), anchor["adherence"].mean()
        print(f"\nHidden anchor check — quality {q:.2f}, adherence {a:.2f} "
              f"(expect quality high, adherence low; it's unedited input rated blind).")
        if q < 3.5 or a > 2.5:
            print("  ⚠ anchor doesn't look like expected — check for inattentive raters.")


if __name__ == "__main__":
    main()
