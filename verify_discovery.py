"""Tuya MQTT discovery manager.

Three subcommands:

    python3 verify_discovery.py                       # status (default)
    python3 verify_discovery.py status

    python3 verify_discovery.py publish [PATTERN]     # clear stale + publish current
    python3 verify_discovery.py publish 'Door*' -y
    python3 verify_discovery.py publish '*' --dry-run

    python3 verify_discovery.py clear [PATTERN]       # clear ALL topics for matches
    python3 verify_discovery.py clear 'Door*' --stale-only
    python3 verify_discovery.py clear '*' -y

PATTERN is fnmatch on device id or name (default '*' = all).
`publish` always clears stale topics first (topics for matching device-id no
longer produced by generator), then publishes current generator output. Both
happen in a single MQTT connection via publish.multiple.
"""
import json
import time
import logging
import argparse
import fnmatch
from collections import defaultdict
from typing import Dict, List, Set
import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish

from tuya_discovery_generator import initialize_generator

# --- Configuration ---
BROKER_HOST = "localhost"
BROKER_PORT = 1883
HA_PREFIX = "homeassistant"
COLLECT_WAIT = 1.0
DEVICES_JSON = "tuyadevices.json"

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("verify_discovery")


class DiscoveryVerifier:
    """Categorize devices by comparing JSON config against MQTT discovery state."""

    def __init__(self):
        self.generator = initialize_generator()

    def verify(self, devices: List[Dict], mqtt_messages: Dict) -> Dict:
        results = {cat: [] for cat in [
            "perfect", "mismatched_payload", "partially_missing", "pure_missing",
            "unexpected_topics", "orphans", "no_dp_config"
        ]}
        results["errors"] = []

        device_map = {d['id']: self._init_device_result(d) for d in devices if 'id' in d}
        expected_topics = self._generate_expected_map(devices, device_map, results["errors"])

        working_topics = set(mqtt_messages.keys())
        for topic in list(working_topics):
            payload = mqtt_messages[topic]["payload"]
            dev_id = payload.get("device", {}).get("identifiers", ["unknown"])[0]

            if topic in expected_topics:
                expected = expected_topics[topic]
                if payload == expected["payload"]:
                    device_map[expected["device_id"]]["matched"].append(topic)
                else:
                    device_map[expected["device_id"]]["mismatched"].append({
                        "topic": topic, "actual": payload, "expected": expected["payload"]
                    })
                working_topics.remove(topic)
            elif dev_id in device_map:
                device_map[dev_id]["unexpected"].append(topic)
                working_topics.remove(topic)

        for topic, info in expected_topics.items():
            if topic not in mqtt_messages:
                device_map[info["device_id"]]["missing"].append(topic)

        for dev_id, data in device_map.items():
            cat = self._determine_category(data)
            results[cat].append(data)

        for topic in working_topics:
            results["orphans"].append({"id": "unknown", "topics": [topic]})

        return results

    def _init_device_result(self, d: Dict) -> Dict:
        return {
            "id": d['id'], "name": d.get('name', d['id']),
            "matched": [], "mismatched": [], "missing": [], "unexpected": [],
            "expected_count": 0
        }

    def _generate_expected_map(self, devices, device_map, errors_list) -> Dict:
        expected_map = {}
        for d in devices:
            try:
                payloads, _ = self.generator.generate(d)
                device_map[d['id']]["expected_count"] = len(payloads)
                for topic, payload in payloads.items():
                    expected_map[topic] = {"payload": payload, "device_id": d['id']}
            except Exception as e:
                errors_list.append(f"Gen error ({d['id']}): {e}")
        return expected_map

    def _determine_category(self, d: Dict) -> str:
        has_matched = bool(d["matched"])
        has_mismatched = bool(d["mismatched"])
        has_missing = bool(d["missing"])
        has_unexpected = bool(d["unexpected"])

        if d["expected_count"] == 0:
            return "unexpected_topics" if has_unexpected else "no_dp_config"
        if not (has_matched or has_mismatched or has_unexpected):
            return "pure_missing"
        if has_missing:
            return "partially_missing" if (has_matched or has_mismatched) else "unexpected_topics"
        if has_mismatched:
            return "mismatched_payload"
        if has_unexpected:
            return "unexpected_topics"
        return "perfect"


class DiscoveryManager:
    """MQTT I/O and command dispatch."""

    def __init__(self):
        self.verifier = DiscoveryVerifier()
        self.devices: List[Dict] = []
        self.mqtt_data: Dict[str, Dict] = {}

    def load_config(self):
        with open(DEVICES_JSON, "r", encoding="utf-8") as f:
            self.devices = json.load(f)

    def collect_mqtt(self):
        """Subscribe to homeassistant/# and collect retained /config payloads."""
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        def on_msg(c, u, m):
            if m.topic.endswith("/config"):
                try:
                    self.mqtt_data[m.topic] = {"payload": json.loads(m.payload), "retain": m.retain}
                except Exception:
                    pass

        client.on_connect = lambda c, u, f, rc, p: c.subscribe(f"{HA_PREFIX}/#") if rc == 0 else None
        client.on_message = on_msg
        client.connect(BROKER_HOST, BROKER_PORT)
        client.loop_start()
        logger.info(f"Scanning MQTT discovery for {COLLECT_WAIT}s...")
        time.sleep(COLLECT_WAIT)
        client.loop_stop()
        client.disconnect()

    def _match(self, pattern: str) -> List[Dict]:
        return [
            d for d in self.devices
            if any(fnmatch.fnmatch(str(d.get(k, '')), pattern) for k in ['id', 'name'])
        ]

    @staticmethod
    def _confirm(prompt: str) -> bool:
        return input(prompt).strip().lower() in ('', 'y', 'yes')

    # --- Commands ---

    def cmd_status(self):
        self.load_config()
        self.collect_mqtt()
        results = self.verifier.verify(self.devices, self.mqtt_data)
        self._print_summary(results)

    def cmd_publish(self, pattern: str, yes: bool, dry_run: bool):
        self.load_config()
        matches = self._match(pattern)
        if not matches:
            print(f"❌ No matching devices for {pattern!r}")
            return

        # Build new payloads for matching devices
        new_topics: Dict[str, Dict[str, dict]] = {}  # dev_id -> {topic -> payload}
        for d in matches:
            try:
                payloads, _ = self.verifier.generator.generate(d)
                new_topics[d['id']] = payloads
            except Exception as e:
                logger.error(f"Generator failed for {d['id']}: {e}")

        # Collect existing retained topics for the same device-ids
        self.collect_mqtt()
        existing: Dict[str, Set[str]] = defaultdict(set)
        for topic, msg in self.mqtt_data.items():
            ids = msg["payload"].get("device", {}).get("identifiers", [])
            if ids and ids[0] in new_topics:
                existing[ids[0]].add(topic)

        # Compute stale + new per device
        msgs = []
        n_clear = n_publish = 0
        per_device_lines = []
        for d in matches:
            dev_id = d['id']
            new = new_topics.get(dev_id, {})
            old = existing.get(dev_id, set())
            stale = old - set(new)
            for t in sorted(stale):
                msgs.append({"topic": t, "payload": "", "retain": True})
                n_clear += 1
            for t in sorted(new):
                msgs.append({"topic": t, "payload": json.dumps(new[t]), "retain": True})
                n_publish += 1
            per_device_lines.append(
                f"  {d.get('name', dev_id)} ({dev_id}): "
                f"+{len(new)} publish, -{len(stale)} clear"
            )

        print(f"\n📋 publish plan for {len(matches)} device(s):")
        for line in per_device_lines:
            print(line)
        print(f"\nTotal: {n_publish} publish, {n_clear} clear stale, {len(msgs)} MQTT msgs")

        if dry_run:
            print("\n🔎 dry-run: no MQTT writes.")
            return

        if not msgs:
            print("ℹ️ Nothing to do.")
            return

        if not yes and not self._confirm("\nProceed? [Y/n]: "):
            print("aborted.")
            return

        publish.multiple(msgs, hostname=BROKER_HOST, port=BROKER_PORT)
        print(f"✅ done.")

    def cmd_clear(self, pattern: str, stale_only: bool, yes: bool, dry_run: bool):
        self.load_config()
        matches = self._match(pattern)
        if not matches:
            print(f"❌ No matching devices for {pattern!r}")
            return

        matched_ids = {m['id'] for m in matches}
        self.collect_mqtt()

        if stale_only:
            # Need verify to determine which existing topics are unexpected
            results = self.verifier.verify(self.devices, self.mqtt_data)
            topics_by_dev: Dict[str, List[str]] = defaultdict(list)
            for items in results.values():
                if not isinstance(items, list):
                    continue
                for entry in items:
                    if entry.get('id') in matched_ids:
                        topics_by_dev[entry['id']].extend(entry.get('unexpected', []))
        else:
            # All retained topics whose payload device-id matches
            topics_by_dev = defaultdict(list)
            for topic, msg in self.mqtt_data.items():
                ids = msg["payload"].get("device", {}).get("identifiers", [])
                if ids and ids[0] in matched_ids:
                    topics_by_dev[ids[0]].append(topic)

        total = sum(len(t) for t in topics_by_dev.values())
        label = "stale" if stale_only else "ALL"
        print(f"\n📋 clear plan ({label}) for {len(matches)} device(s):")
        for d in matches:
            topics = sorted(set(topics_by_dev.get(d['id'], [])))
            print(f"  {d.get('name', d['id'])} ({d['id']}): {len(topics)} topic(s)")
            if dry_run:
                for t in topics:
                    print(f"    - {t}")
        print(f"\nTotal: {total} topic(s) to clear")

        if total == 0:
            print("ℹ️ Nothing to clear.")
            return

        if dry_run:
            print("\n🔎 dry-run: no MQTT writes.")
            return

        if not yes and not self._confirm("\nProceed? [Y/n]: "):
            print("aborted.")
            return

        msgs = [
            {"topic": t, "payload": "", "retain": True}
            for topics in topics_by_dev.values()
            for t in set(topics)
        ]
        publish.multiple(msgs, hostname=BROKER_HOST, port=BROKER_PORT)
        print(f"✅ cleared {total} topic(s).")

    # --- Status output ---

    def _print_summary(self, results: Dict):
        print("\n" + "=" * 60 + "\nDISCOVERY SUMMARY\n" + "=" * 60)
        counts = {cat: len(items) for cat, items in results.items() if isinstance(items, list)}

        for cat, count in counts.items():
            if count == 0:
                continue
            icon = {"perfect": "✅", "pure_missing": "🚫", "no_dp_config": "⚪"}.get(cat, "⚠️")
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

        total_err = sum(counts[c] for c in counts if c not in ['perfect', 'no_dp_config', 'orphans', 'errors'])
        print(f"\n{'✨ Consistent!' if not total_err else f'🚨 Issues found: {total_err}'}\n" + "=" * 60)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tuya MQTT discovery manager")
    sub = parser.add_subparsers(dest='cmd', metavar='COMMAND')

    sub.add_parser('status', help='Show discovery state summary (default)')

    p_pub = sub.add_parser('publish', help='Clear stale + publish current discovery for matching devices')
    p_pub.add_argument('pattern', nargs='?', default='*', help='fnmatch on id/name (default: all)')
    p_pub.add_argument('-y', '--yes', action='store_true', help='Skip confirmation prompt')
    p_pub.add_argument('--dry-run', action='store_true', help='Show plan without publishing')

    p_clr = sub.add_parser('clear', help='Clear discovery topics for matching devices')
    p_clr.add_argument('pattern', nargs='?', default='*', help='fnmatch on id/name (default: all)')
    p_clr.add_argument('--stale-only', action='store_true', help='Only clear topics not produced by current generator')
    p_clr.add_argument('-y', '--yes', action='store_true')
    p_clr.add_argument('--dry-run', action='store_true')

    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    mgr = DiscoveryManager()

    if args.cmd in (None, 'status'):
        mgr.cmd_status()
    elif args.cmd == 'publish':
        mgr.cmd_publish(args.pattern, yes=args.yes, dry_run=args.dry_run)
    elif args.cmd == 'clear':
        mgr.cmd_clear(args.pattern, stale_only=args.stale_only, yes=args.yes, dry_run=args.dry_run)
