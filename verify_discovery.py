import json
import time
import logging
import argparse
import fnmatch
import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
from typing import Dict, Any, List, Set, Optional, Callable
from tuya_discovery_generator import initialize_generator

# --- Configuration ---
BROKER_HOST = "localhost"
BROKER_PORT = 1883
HA_PREFIX = "homeassistant"
WAIT_TIME = 2.5
DEVICES_JSON = "tuyadevices.json"

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("verify_discovery")

class DiscoveryVerifier:
    """Core logic for comparing JSON config vs MQTT discovery state"""
    def __init__(self):
        self.generator = initialize_generator()

    def verify(self, devices: List[Dict], mqtt_messages: Dict) -> Dict:
        """Categorize devices based on their MQTT discovery state"""
        results = {cat: [] for cat in [
            "perfect", "mismatched_payload", "partially_missing", "pure_missing", 
            "unexpected_topics", "orphans", "no_dp_config"
        ]}
        results["errors"] = []

        device_map = {d['id']: self._init_device_result(d) for d in devices if 'id' in d}
        expected_topics = self._generate_expected_map(devices, device_map, results["errors"])

        # Process MQTT messages
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

        # Identify missing
        for topic, info in expected_topics.items():
            if topic not in mqtt_messages:
                device_map[info["device_id"]]["missing"].append(topic)

        # Final categorization logic
        for dev_id, data in device_map.items():
            cat = self._determine_category(data)
            results[cat].append(data)

        # Orphans (remaining unknown topics)
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
    """Handles MQTT I/O and Interactive CLI flow"""
    def __init__(self):
        self.verifier = DiscoveryVerifier()
        self.devices = []
        self.mqtt_data = {}

    def load_config(self):
        with open(DEVICES_JSON, "r", encoding="utf-8") as f:
            self.devices = json.load(f)

    def collect_mqtt(self):
        """Collect discovery messages from broker"""
        client = mqtt.Client()
        def on_msg(c, u, m):
            if m.topic.endswith("/config"):
                try: self.mqtt_data[m.topic] = {"payload": json.loads(m.payload), "retain": m.retain}
                except: pass
        
        client.on_connect = lambda c, u, f, rc: c.subscribe(f"{HA_PREFIX}/#") if rc==0 else None
        client.on_message = on_msg
        client.connect(BROKER_HOST, BROKER_PORT)
        client.loop_start()
        logger.info(f"Scanning MQTT discovery for {WAIT_TIME}s...")
        time.sleep(WAIT_TIME)
        client.loop_stop()
        client.disconnect()

    def publish_discovery(self, device: Dict):
        """Register discovery topics for a device"""
        payloads, _ = self.verifier.generator.generate(device)
        logger.info(f"Publishing {len(payloads)} topics for {device['name']}...")
        for topic, payload in payloads.items():
            publish.single(topic, json.dumps(payload), retain=True, hostname=BROKER_HOST)

    def clear_topics(self, device_id: str, topics: List[str]):
        """Remove discovery topics from MQTT"""
        logger.info(f"Clearing {len(topics)} topics for {device_id}...")
        for topic in topics:
            publish.single(topic, retain=True, hostname=BROKER_HOST)

    def run_interactive(self, pattern: str, action_type: str):
        """Unified interactive handler for bulk actions"""
        self.load_config()
        matches = [d for d in self.devices if any(fnmatch.fnmatch(str(d.get(k, '')), pattern) for k in ['id', 'name'])]
        
        if not matches:
            print(f"❌ No matching devices for '{pattern}'")
            return

        # Verification needed for removal/missing-all
        if action_type in ['remove', 'remove-legacy', 'add-all']:
            self.collect_mqtt()
            results = self.verifier.verify(self.devices, self.mqtt_data)

        # Bulk logic
        is_bulk = len(matches) > 1 or any(c in pattern for c in '*?') or action_type == 'add-all'
        
        target_list = results['pure_missing'] if action_type == 'add-all' else matches
        
        for d in target_list:
            dev_id = d['id']
            if is_bulk:
                prompt = f"❓ {action_type.title()} for {d['name']} ({dev_id})? [Y/n]: "
                if input(prompt).lower() not in ['', 'y', 'yes']: continue

            if action_type in ['add', 'add-all']:
                self.publish_discovery(d)
            else:
                topics = self._get_topics_for_removal(dev_id, results, only_legacy=(action_type=='remove-legacy'))
                if topics: self.clear_topics(dev_id, topics)
                else: print(f"ℹ️ Nothing to remove for {d['name']}")

    def _get_topics_for_removal(self, dev_id: str, results: Dict, only_legacy: bool) -> List[str]:
        topics = []
        for cat, list_data in results.items():
            if not isinstance(list_data, list): continue
            for d in list_data:
                if d.get('id') == dev_id:
                    topics.extend(d.get('unexpected', []))
                    if not only_legacy:
                        topics.extend(d.get('matched', []))
                        if 'mismatched' in d:
                            topics.extend([m['topic'] for m in d['mismatched']])
        return list(set(topics))

    def print_summary(self, results: Dict):
        print("\n" + "="*60 + "\nDISCOVERY SUMMARY\n" + "="*60)
        counts = {cat: len(items) for cat, items in results.items() if isinstance(items, list)}
        
        for cat, count in counts.items():
            if count == 0: continue
            icon = {"perfect": "✅", "pure_missing": "🚫", "no_dp_config": "⚪"}.get(cat, "⚠️")
            print(f"\n{icon} {cat.replace('_', ' ').title()}: {count}")
            for d in results[cat][:15]: # Show first 15
                name = d.get('name', 'Unknown')
                info = f"({len(d.get('missing',[]))} missing / {len(d.get('matched',[]))} matched)" if 'missing' in d else ""
                print(f"  - {name} ({d.get('id', 'unknown')}) {info}")
            if count > 15: print(f"  ... and {count-15} more")

        total_err = sum(counts[c] for c in counts if c not in ['perfect', 'no_dp_config', 'orphans', 'errors'])
        print(f"\n{'✨ Consistent!' if not total_err else f'🚨 Issues found: {total_err}'}\n" + "="*60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tuya Discovery Manager")
    parser.add_argument("--add-missing", help="Add discovery for device (pattern supported)")
    parser.add_argument("--add-missing-all", action="store_true", help="Interactive fix for all missing")
    parser.add_argument("--remove-legacy", help="Clear unexpected topics (pattern supported)")
    parser.add_argument("--remove", help="Clear ALL topics for device (pattern supported)")
    args = parser.parse_args()

    mgr = DiscoveryManager()
    if args.add_missing_all: mgr.run_interactive("*", "add-all")
    elif args.add_missing:   mgr.run_interactive(args.add_missing, "add")
    elif args.remove_legacy: mgr.run_interactive(args.remove_legacy, "remove-legacy")
    elif args.remove:        mgr.run_interactive(args.remove, "remove")
    else:
        mgr.load_config()
        mgr.collect_mqtt()
        mgr.print_summary(mgr.verifier.verify(mgr.devices, mgr.mqtt_data))
