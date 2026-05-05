import json
import time
import logging
import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
from typing import Dict, Any, List, Set
from tuya_discovery_generator import initialize_generator, Config as GenConfig

# --- Constants ---
BROKER_HOST = "localhost"
BROKER_PORT = 1883
HA_PREFIX = "homeassistant"
WAIT_TIME = 2.0  # Seconds to wait for retained messages
DEVICES_JSON = "tuyadevices.json"

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("verify_discovery")

class DiscoveryVerifier:
    def __init__(self, generator=None):
        self.generator = generator or initialize_generator()

    def verify(self, devices: List[Dict[str, Any]], received_messages: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Verify devices against received MQTT discovery messages.
        :param devices: List of device dictionaries from tuyadevices.json
        :param received_messages: Dict mapping topic -> {"payload": dict, "retain": bool}
        :return: Categorized results dictionary
        """
        device_results = {}
        known_device_ids = set()
        expected_topics = {}
        errors = []

        # 1. Initialize results and generate expected topics
        for device in devices:
            dev_id = device.get('id')
            if not dev_id: continue
            known_device_ids.add(dev_id)
            dev_name = device.get('name', dev_id)
            device_results[dev_id] = {
                "id": dev_id, "name": dev_name,
                "matched": [], "mismatched": [], "missing": [], "unexpected": []
            }
            
            try:
                expected_payloads, _ = self.generator.generate(device)
                for topic, payload in expected_payloads.items():
                    expected_topics[topic] = {"payload": payload, "device_id": dev_id}
            except Exception as e:
                errors.append(f"Generation error ({dev_id}): {e}")

        # 2. Process received MQTT topics
        working_received = set(received_messages.keys())
        for topic in list(working_received):
            actual_payload = received_messages[topic]["payload"]
            dev_info = actual_payload.get("device", {})
            ids = dev_info.get("identifiers", [])
            dev_id = ids[0] if ids else "unknown"
            
            if topic in expected_topics:
                expected_info = expected_topics[topic]
                expected_payload = expected_info["payload"]
                target_dev_id = expected_info["device_id"]
                
                if actual_payload == expected_payload:
                    device_results[target_dev_id]["matched"].append(topic)
                else:
                    device_results[target_dev_id]["mismatched"].append({
                        "topic": topic, "actual": actual_payload, "expected": expected_payload
                    })
                working_received.remove(topic)
            else:
                if dev_id in device_results:
                    device_results[dev_id]["unexpected"].append(topic)
                    working_received.remove(topic)
                elif dev_id != "unknown":
                    if dev_id not in device_results:
                        device_results[dev_id] = {"id": dev_id, "name": dev_id, "matched": [], "mismatched": [], "missing": [], "unexpected": [topic]}
                    else:
                        device_results[dev_id]["unexpected"].append(topic)
                    working_received.remove(topic)

        # 3. Identify missing topics
        for topic, info in expected_topics.items():
            if topic not in received_messages:
                device_results[info["device_id"]]["missing"].append(topic)

        # 4. Final Categorization
        categorized = {
            "perfect": [], "mismatched_payload": [], "partially_missing": [],
            "pure_missing": [], "unexpected_topics": [], "orphans": [], "errors": errors
        }

        for dev_id, data in device_results.items():
            has_matched = len(data["matched"]) > 0
            has_mismatched = len(data["mismatched"]) > 0
            has_missing = len(data["missing"]) > 0
            has_unexpected = len(data["unexpected"]) > 0
            
            if dev_id not in known_device_ids:
                categorized["orphans"].append(data)
                continue

            if not has_matched and not has_mismatched and not has_unexpected:
                categorized["pure_missing"].append(data)
            elif has_missing and not has_matched and not has_mismatched:
                categorized["unexpected_topics"].append(data)
            elif has_missing:
                categorized["partially_missing"].append(data)
            elif has_mismatched:
                categorized["mismatched_payload"].append(data)
            elif has_unexpected:
                categorized["unexpected_topics"].append(data)
            else:
                categorized["perfect"].append(data)

        # 5. Handle remaining topics with unknown device IDs
        for topic in working_received:
            categorized["orphans"].append({"id": "unknown", "name": "Unknown", "topics": [topic]})

        return categorized

import argparse

class DiscoveryFixer:
    def __init__(self, broker_host=BROKER_HOST, broker_port=BROKER_PORT):
        self.host = broker_host
        self.port = broker_port
        self.generator = initialize_generator()

    def fix_missing(self, device: Dict[str, Any]):
        """Publish expected discovery topics for a device"""
        client = mqtt.Client()
        try:
            client.connect(self.host, self.port)
            expected_payloads, _ = self.generator.generate(device)
            logger.info(f"Fixing missing discovery for {device.get('name')} ({device.get('id')})...")
            for topic, payload in expected_payloads.items():
                logger.info(f"  Publishing: {topic}")
                publish.single(topic=topic, payload=json.dumps(payload), retain=True)
            client.disconnect()
            print(f"✅ Successfully published {len(expected_payloads)} topics for {device.get('name')}")
        except Exception as e:
            logger.error(f"Failed to fix missing: {e}")

    def remove_legacy(self, device_id: str, unexpected_topics: List[str]):
        """Clear retained discovery topics that are unexpected"""
        client = mqtt.Client()
        try:
            client.connect(self.host, self.port)
            logger.info(f"Removing {len(unexpected_topics)} legacy topics for {device_id}...")
            for topic in unexpected_topics:
                logger.info(f"  Clearing: {topic}")
                client.publish(topic, "", retain=True)
            client.disconnect()
            print(f"✅ Successfully cleared {len(unexpected_topics)} legacy topics for {device_id}")
        except Exception as e:
            logger.error(f"Failed to remove legacy: {e}")

class DiscoveryVerifierRunner:
    """CLI Runner for DiscoveryVerifier with Fixing capabilities"""
    def __init__(self):
        self.verifier = DiscoveryVerifier()
        self.fixer = DiscoveryFixer()
        self.received_messages = {}
        self.devices = []

    def load_devices(self):
        try:
            with open(DEVICES_JSON, "r", encoding="utf-8") as f:
                self.devices = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load devices: {e}")

    def find_device(self, search_term: str) -> Dict[str, Any]:
        for d in self.devices:
            if d.get('id') == search_term or d.get('name') == search_term:
                return d
        return None

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(f"{HA_PREFIX}/#")
        else:
            logger.error(f"MQTT connection failed: {rc}")

    def on_message(self, client, userdata, msg):
        if msg.topic.endswith("/config"):
            try:
                self.received_messages[msg.topic] = {
                    "payload": json.loads(msg.payload.decode('utf-8')),
                    "retain": msg.retain
                }
            except: pass

    def collect_mqtt(self):
        client = mqtt.Client()
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        try:
            client.connect(BROKER_HOST, BROKER_PORT, 60)
            client.loop_start()
            logger.info(f"Collecting MQTT messages for {WAIT_TIME}s...")
            time.sleep(WAIT_TIME)
            client.loop_stop()
            client.disconnect()
        except Exception as e:
            logger.error(f"MQTT Error: {e}")

    def run(self):
        parser = argparse.ArgumentParser(description="Tuya Home Assistant Discovery Verifier & Fixer")
        parser.add_argument("--add-missing", help="Device ID or Name to fix missing discovery")
        parser.add_argument("--add-missing-all", action="store_true", help="Interactively fix all 'Pure Missing' devices")
        parser.add_argument("--remove-legacy", help="Device ID or Name to remove legacy discovery topics")
        args = parser.parse_args()

        self.load_devices()
        if not self.devices: return

        if args.add_missing_all:
            # For interactive mode, we need to verify first
            self.collect_mqtt()
            categorized = self.verifier.verify(self.devices, self.received_messages)
            pure_missing = categorized.get('pure_missing', [])
            
            if not pure_missing:
                print("✨ No 'Pure Missing' devices found to fix.")
                return
            
            print(f"\n🔍 Found {len(pure_missing)} Pure Missing devices.")
            for d in pure_missing:
                try:
                    ans = input(f"❓ Fix discovery for {d['name']} ({d['id']})? [Y/n]: ").strip().lower()
                    if ans in ['', 'y', 'yes']:
                        full_device = next((dev for dev in self.devices if dev['id'] == d['id']), None)
                        if full_device:
                            self.fixer.fix_missing(full_device)
                except KeyboardInterrupt:
                    print("\nStopping interactive fix.")
                    break
            return

        if args.add_missing:
            device = self.find_device(args.add_missing)
            if device:
                self.fixer.fix_missing(device)
            else:
                print(f"❌ Device '{args.add_missing}' not found in JSON.")
            return

        # For removal, we need to know which topics are unexpected, so we verify first
        self.collect_mqtt()
        categorized = self.verifier.verify(self.devices, self.received_messages)

        if args.remove_legacy:
            device = self.find_device(args.remove_legacy)
            if not device:
                print(f"❌ Device '{args.remove_legacy}' not found in JSON.")
                return
            
            dev_id = device['id']
            # Find unexpected topics for this device
            unexpected = []
            # Check categorized['unexpected_topics'] and categorized['partially_missing']
            for cat in ['unexpected_topics', 'partially_missing']:
                for d in categorized[cat]:
                    if d['id'] == dev_id:
                        unexpected.extend(d['unexpected'])
            
            if unexpected:
                self.fixer.remove_legacy(dev_id, unexpected)
            else:
                print(f"ℹ️ No unexpected topics found for {device['name']} ({dev_id}).")
            return

        # Default: Print summary
        self.print_summary(categorized)

    def print_summary(self, categorized):
        print("\n" + "="*60)
        print("         MQTT DISCOVERY VERIFICATION SUMMARY")
        print("="*60)
        
        print(f"\n📊 Overall Stats:")
        print(f"  - Devices in JSON: {len(self.devices)}")
        print(f"  - Perfect Matches: {len(categorized['perfect'])}")
        for d in categorized["perfect"]:
            print(f"  - 📱 {d['name']} ({d['id']})")
        
        if categorized["mismatched_payload"]:
            print(f"\n❌ Payload Mismatch: {len(categorized['mismatched_payload'])}")
            for d in categorized["mismatched_payload"]:
                print(f"  - 📱 {d['name']} ({d['id']}): {len(d['mismatched'])} entities differ")

        if categorized["partially_missing"]:
            print(f"\n⚠️ Partially Missing: {len(categorized['partially_missing'])}")
            for d in categorized["partially_missing"]:
                print(f"  - 📱 {d['name']} ({d['id']}): {len(d['missing'])} missing / {len(d['matched'])} matched")

        if categorized["unexpected_topics"]:
            print(f"\n♻️ Unexpected Topics / Misconfigured: {len(categorized['unexpected_topics'])}")
            for d in categorized["unexpected_topics"]:
                status = "Legacy topics only" if not d['matched'] and not d['mismatched'] else "Extra topics found"
                print(f"  - 📱 {d['name']} ({d['id']}): {len(d['unexpected'])} unexpected topics ({status})")

        if categorized["pure_missing"]:
            print(f"\n🚫 Pure Missing: {len(categorized['pure_missing'])}")
            for d in categorized["pure_missing"]:
                print(f"  - 📱 {d['name']} ({d['id']})")

        if categorized["orphans"]:
            print(f"\n👻 Orphaned or External: {len(categorized['orphans'])}")
            for d in categorized["orphans"]:
                topics_count = len(d.get('topics', d.get('unexpected', [])))
                print(f"  - ❓ {d['id']}: {topics_count} topics")

        if categorized["errors"]:
            print(f"\n❗ Errors: {len(categorized['errors'])}")
            for err in categorized["errors"]:
                print(f"  - {err}")

        print("\n" + "="*60)
        total_issues = len(categorized["mismatched_payload"]) + len(categorized["partially_missing"]) + \
                       len(categorized["unexpected_topics"]) + len(categorized["pure_missing"])
        if total_issues == 0:
            print("✨ Everything is consistent!")
        else:
            print(f"🚨 Found {total_issues} devices with issues.")
        print("="*60 + "\n")

if __name__ == "__main__":
    runner = DiscoveryVerifierRunner()
    runner.run()
