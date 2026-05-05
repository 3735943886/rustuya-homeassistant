import json
import time
import logging
import paho.mqtt.client as mqtt
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

class DiscoveryVerifierRunner:
    """CLI Runner for DiscoveryVerifier"""
    def __init__(self):
        self.verifier = DiscoveryVerifier()
        self.received_messages = {}
        self.devices = []

    def load_devices(self):
        try:
            with open(DEVICES_JSON, "r", encoding="utf-8") as f:
                self.devices = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load devices: {e}")

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

    def run(self):
        self.load_devices()
        if not self.devices: return

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
            return

        categorized = self.verifier.verify(self.devices, self.received_messages)
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

if __name__ == "__main__":
    verifier = DiscoveryVerifier()
    verifier.run()
