"""Regenerate golden snapshots from current generator output.

Run this ONLY when you intentionally change generator behavior and want the
new output to become the new baseline.

    python3 tests/generate_snapshots.py
"""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tuya_discovery_generator import initialize_generator

REAL_DEVICES_PATH = ROOT / "tuyadevices.json"
SYNTH_DEVICES_PATH = Path(__file__).parent / "fixtures" / "synthetic_devices.json"
REAL_SNAPSHOT_PATH = Path(__file__).parent / "golden" / "snapshots.json"
SYNTH_SNAPSHOT_PATH = Path(__file__).parent / "golden" / "synthetic_snapshots.json"


def build_snapshot(devices_path: Path, generator) -> dict:
    with open(devices_path) as f:
        devices = json.load(f)

    snapshot = {}
    for d in devices:
        dev_id = d.get("id")
        if not dev_id:
            continue
        payloads, source = generator.generate(d)
        snapshot[dev_id] = {
            "name": d.get("name", dev_id),
            "category": d.get("category"),
            "product_id": d.get("product_id"),
            "source": source,
            "payloads": payloads,
        }
    return snapshot


def write_snapshot(snapshot: dict, path: Path, label: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True, ensure_ascii=False)
    total_topics = sum(len(v["payloads"]) for v in snapshot.values())
    print(f"[{label}] wrote {path}")
    print(f"  devices: {len(snapshot)}")
    print(f"  total topics: {total_topics}")


def main():
    logging.basicConfig(level=logging.WARNING)
    gen = initialize_generator()

    if REAL_DEVICES_PATH.exists():
        write_snapshot(build_snapshot(REAL_DEVICES_PATH, gen), REAL_SNAPSHOT_PATH, "real")
    else:
        print(f"[real] skipped (missing {REAL_DEVICES_PATH})")

    if SYNTH_DEVICES_PATH.exists():
        write_snapshot(build_snapshot(SYNTH_DEVICES_PATH, gen), SYNTH_SNAPSHOT_PATH, "synth")
    else:
        print(f"[synth] skipped (missing {SYNTH_DEVICES_PATH})")


if __name__ == "__main__":
    main()
