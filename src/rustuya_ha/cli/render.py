"""Terminal output for the CLI. Pure formatting — no MQTT, no generation."""
import json
import fnmatch
from typing import Dict, List

PROG = "rustuya-ha"

# One-line meaning per -c/--category alias (single source of truth, also used in --help).
CATEGORY_HELP = {
    "mismatched": "retained payload differs from current generator output",
    "partial":    "some expected topics present, others missing",
    "pure":       "none of the expected topics are retained",
    "unexpected": "retained topics with no expected counterpart",
    "no-dp":      "device produces no generator output (e.g. repeater stub)",
    "perfect":    "all expected topics present and matching",
}

CAT_ICON = {"perfect": "✅", "pure_missing": "🚫", "no_dp_config": "⚪"}

EPILOG = f"""\
Categories (-c/--category, repeatable, narrows matches AND-wise with PATTERN):
""" + "".join(f"  {a:<11} {d}\n" for a, d in CATEGORY_HELP.items()) + f"""
Config (flags override env, env overrides defaults):
  --broker / RUSTUYA_MQTT        MQTT broker host[:port]   (default localhost:1883)
  --devices / RUSTUYA_DEVICES    device list JSON          (default tuyadevices.json)
  --converters / RUSTUYA_CONVERTERS  custom converter JSON (default ./custom_converters.json)

Examples:
  {PROG} status                              # default summary
  {PROG} status -c mismatched --detail       # mismatched-only, field diffs
  {PROG} preview 'guest_*'                    # dump generator output, no MQTT
  {PROG} publish '*' -c mismatched --dry-run  # preview fix for mismatched
  {PROG} clear '*' --stale-only               # drop only orphan topics
"""


def print_preview(matches: List[Dict], generator):
    """Dump generator output for matching devices (read-only)."""
    for d in matches:
        print("\n" + "=" * 80)
        print(f"DEVICE: {d.get('name', 'N/A')}")
        print(f"  ID:       {d.get('id')}")
        print(f"  PID:      {d.get('product_id')}")
        print(f"  Category: {d.get('category')}")
        try:
            payloads, source = generator.generate(d)
        except Exception as e:
            print(f"  ❌ generator error: {e}")
            continue
        print(f"  Source:   {source}")
        print(f"  Topics:   {len(payloads)}")
        print("=" * 80)
        print(json.dumps(payloads, indent=2, ensure_ascii=False))


def print_summary(results: Dict):
    print("\n" + "=" * 60 + "\nDISCOVERY SUMMARY\n" + "=" * 60)
    counts = {cat: len(items) for cat, items in results.items() if isinstance(items, list)}

    for cat, count in counts.items():
        if count == 0:
            continue
        icon = CAT_ICON.get(cat, "⚠️")
        print(f"\n{icon} {cat.replace('_', ' ').title()}: {count}")
        for d in results[cat][:15]:
            name = d.get('name', 'Unknown')
            parts = []
            if d.get('missing'): parts.append(f"{len(d['missing'])} missing")
            if d.get('mismatched'): parts.append(f"{len(d['mismatched'])} mismatched")
            if d.get('matched'): parts.append(f"{len(d['matched'])} matched")
            if d.get('unexpected'): parts.append(f"{len(d['unexpected'])} unexpected")
            info = f"({', '.join(parts)})" if parts else ""
            print(f"  - {name} ({d.get('id', 'unknown')}) {info}")
        if count > 15:
            print(f"  ... and {count - 15} more")

    total_err = sum(counts[c] for c in counts
                    if c not in ['perfect', 'no_dp_config', 'orphans', 'errors'])
    print(f"\n{'✨ Consistent!' if not total_err else f'🚨 Issues found: {total_err}'}\n" + "=" * 60)
    _print_next_steps(results, total_err)


def _print_next_steps(results: Dict, total_err: int):
    """Nudge the user toward the natural next command instead of leaving them
    to guess the status -> preview -> publish workflow."""
    tips = []
    if results.get("mismatched_payload") or results.get("partially_missing") or results.get("pure_missing"):
        tips.append(f"{PROG} publish '*' --dry-run        # preview the fix, then re-run with -y")
    if results.get("unexpected_topics") or results.get("orphans"):
        tips.append(f"{PROG} clear '*' --stale-only        # drop orphan/stale topics (dry-run first)")
    if results.get("mismatched_payload"):
        tips.append(f"{PROG} status -c mismatched --detail # see which fields differ")
    if not tips:
        return
    print("\nNext steps:")
    for t in tips:
        print(f"  $ {t}")


def print_mismatch_details(results: Dict, pattern: str):
    mismatched = results.get("mismatched_payload") or []
    matched_devices = [
        d for d in mismatched
        if fnmatch.fnmatch(str(d.get('id', '')), pattern)
        or fnmatch.fnmatch(str(d.get('name', '')), pattern)
    ]
    if not matched_devices:
        print("\n(no mismatched devices for pattern)")
        return

    print("\n" + "=" * 60 + f"\nMISMATCH DETAIL  pattern={pattern!r}, devices={len(matched_devices)}\n" + "=" * 60)
    for d in matched_devices:
        print(f"\n📍 {d['name']} ({d['id']})")
        for m in d['mismatched']:
            a, e = m['actual'], m['expected']
            diff_keys = sorted(k for k in set(a) | set(e) if a.get(k) != e.get(k))
            print(f"  {m['topic']}  ({len(diff_keys)} field(s) differ)")
            for k in diff_keys:
                print(f"    [{k}]")
                print(f"      actual:   {a.get(k)}")
                print(f"      expected: {e.get(k)}")
