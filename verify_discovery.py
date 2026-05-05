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
    def __init__(self):
        self.generator = initialize_generator()
        self.received_messages: Dict[str, Dict[str, Any]] = {}
        self.devices: List[Dict[str, Any]] = []
        self.expected_topics: Dict[str, Dict[str, Any]] = {}
        self.results = {
            "matched": [],
            "mismatched": {}, # device_id -> {name, entities: []}
            "missing_in_mqtt": {}, # device_id -> {name, entities: []}
            "missing_in_json": [],
            "errors": []
        }

    def load_devices(self):
        try:
            with open(DEVICES_JSON, "r", encoding="utf-8") as f:
                self.devices = json.load(f)
            logger.info(f"Loaded {len(self.devices)} devices from {DEVICES_JSON}")
        except Exception as e:
            logger.error(f"Failed to load {DEVICES_JSON}: {e}")
            self.results["errors"].append(f"Load JSON failed: {e}")

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("Connected to MQTT broker")
            client.subscribe(f"{HA_PREFIX}/#")
        else:
            logger.error(f"Failed to connect to MQTT, return code {rc}")

    def on_message(self, client, userdata, msg):
        if not msg.topic.endswith("/config"):
            return

        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            self.received_messages[msg.topic] = {
                "payload": payload,
                "retain": msg.retain
            }
        except Exception as e:
            logger.warning(f"Failed to parse payload for {msg.topic}: {e}")

    def run(self):
        self.load_devices()
        if not self.devices:
            return

        client = mqtt.Client()
        client.on_connect = self.on_connect
        client.on_message = self.on_message

        try:
            client.connect(BROKER_HOST, BROKER_PORT, 60)
            client.loop_start()
            
            logger.info(f"Collecting retained messages for {WAIT_TIME} seconds...")
            time.sleep(WAIT_TIME)
            
            client.loop_stop()
            client.disconnect()
        except Exception as e:
            logger.error(f"MQTT Error: {e}")
            self.results["errors"].append(f"MQTT connection failed: {e}")
            # Continue to show what we have in JSON even if MQTT failed?
            # No, we need MQTT to verify.

        self.verify()
        self.print_summary()

    def verify(self):
        # 1. Initialize device results map
        device_results = {} # dev_id -> {name, matched: [], mismatched: [], missing: [], unexpected: []}
        known_device_ids = set()
        
        for device in self.devices:
            dev_id = device.get('id')
            if not dev_id: continue
            known_device_ids.add(dev_id)
            dev_name = device.get('name', dev_id)
            device_results[dev_id] = {
                "name": dev_name,
                "matched": [], "mismatched": [], "missing": [], "unexpected": []
            }
            
            try:
                expected_payloads, _ = self.generator.generate(device)
                for topic, payload in expected_payloads.items():
                    self.expected_topics[topic] = {
                        "payload": payload,
                        "device_id": dev_id
                    }
            except Exception as e:
                self.results["errors"].append(f"Generation error ({dev_id}): {e}")

        # 2. Process all received MQTT topics
        received_topics = set(self.received_messages.keys())
        for topic in list(received_topics):
            payload = self.received_messages[topic]["payload"]
            dev_info = payload.get("device", {})
            ids = dev_info.get("identifiers", [])
            dev_id = ids[0] if ids else "unknown"
            
            if topic in self.expected_topics:
                expected_info = self.expected_topics[topic]
                expected_payload = expected_info["payload"]
                target_dev_id = expected_info["device_id"]
                
                if self.compare_payloads(payload, expected_payload):
                    device_results[target_dev_id]["matched"].append(topic)
                else:
                    device_results[target_dev_id]["mismatched"].append({
                        "topic": topic, "actual": payload, "expected": expected_payload
                    })
                received_topics.remove(topic)
            else:
                # Unexpected topic
                if dev_id in device_results:
                    device_results[dev_id]["unexpected"].append(topic)
                    received_topics.remove(topic)
                elif dev_id == "unknown":
                    # Keep in received_topics to handle as true orphans later
                    pass
                else:
                    # Known device but not in our results map (shouldn't happen if loaded correctly)
                    if dev_id not in device_results:
                        device_results[dev_id] = {"name": dev_id, "matched": [], "mismatched": [], "missing": [], "unexpected": [topic]}
                    else:
                        device_results[dev_id]["unexpected"].append(topic)
                    received_topics.remove(topic)

        # 3. Identify missing topics
        for topic, info in self.expected_topics.items():
            dev_id = info["device_id"]
            if topic not in self.received_messages:
                device_results[dev_id]["missing"].append(topic)

        # 4. Final Categorization
        self.categorized = {
            "perfect": [],
            "mismatched_payload": [],
            "partially_missing": [],
            "pure_missing": [],
            "unexpected_topics": [],
            "orphans": [] # Devices not in JSON
        }

        for dev_id, data in device_results.items():
            has_matched = len(data["matched"]) > 0
            has_mismatched = len(data["mismatched"]) > 0
            has_missing = len(data["missing"]) > 0
            has_unexpected = len(data["unexpected"]) > 0
            
            if dev_id not in known_device_ids:
                self.categorized["orphans"].append({**data, "id": dev_id})
                continue

            if not has_matched and not has_mismatched and not has_unexpected:
                self.categorized["pure_missing"].append({**data, "id": dev_id})
            elif has_missing and not has_matched and not has_mismatched:
                # Has unexpected but no expected matched -> Misconfigured
                self.categorized["unexpected_topics"].append({**data, "id": dev_id})
            elif has_missing:
                self.categorized["partially_missing"].append({**data, "id": dev_id})
            elif has_mismatched:
                self.categorized["mismatched_payload"].append({**data, "id": dev_id})
            elif has_unexpected:
                self.categorized["unexpected_topics"].append({**data, "id": dev_id})
            else:
                self.categorized["perfect"].append({**data, "id": dev_id})

        # True orphans (topics with no device ID or unknown device ID)
        for topic in received_topics:
            self.categorized["orphans"].append({"id": "unknown", "name": "Unknown", "topics": [topic]})

    def compare_payloads(self, actual: Dict[str, Any], expected: Dict[str, Any]) -> bool:
        return actual == expected

    def print_summary(self):
        print("\n" + "="*60)
        print("         MQTT DISCOVERY VERIFICATION SUMMARY")
        print("="*60)
        
        print(f"\n📊 Overall Stats:")
        print(f"  - Devices in JSON: {len(self.devices)}")
        print(f"  - Perfect Matches: {len(self.categorized['perfect'])}")
        
        if self.categorized["mismatched_payload"]:
            print(f"\n❌ Payload Mismatch (Content differs): {len(self.categorized['mismatched_payload'])}")
            for d in self.categorized["mismatched_payload"]:
                print(f"  - 📱 {d['name']} ({d['id']}): {len(d['mismatched'])} entities differ")

        if self.categorized["partially_missing"]:
            print(f"\n⚠️ Partially Missing (Some entities missing): {len(self.categorized['partially_missing'])}")
            for d in self.categorized["partially_missing"]:
                print(f"  - 📱 {d['name']} ({d['id']}): {len(d['missing'])} missing / {len(d['matched'])} matched")

        if self.categorized["unexpected_topics"]:
            print(f"\n♻️ Unexpected Topics / Misconfigured: {len(self.categorized['unexpected_topics'])}")
            for d in self.categorized["unexpected_topics"]:
                status = "Legacy topics only" if not d['matched'] and not d['mismatched'] else "Extra topics found"
                print(f"  - 📱 {d['name']} ({d['id']}): {len(d['unexpected'])} unexpected topics ({status})")

        if self.categorized["pure_missing"]:
            print(f"\n🚫 Pure Missing (No discovery found at all): {len(self.categorized['pure_missing'])}")
            for d in self.categorized["pure_missing"]:
                print(f"  - 📱 {d['name']} ({d['id']})")

        if self.categorized["orphans"]:
            print(f"\n👻 Orphaned (Topic found but Device ID not in JSON): {len(self.categorized['orphans'])}")
            for d in self.categorized["orphans"]:
                topics_count = len(d.get('topics', d.get('unexpected', [])))
                print(f"  - ❓ {d['id']}: {topics_count} topics")

        if self.results["errors"]:
            print(f"\n❗ Errors encountered: {len(self.results['errors'])}")
            for err in self.results["errors"]:
                print(f"  - {err}")

        print("\n" + "="*60)
        total_issues = len(self.categorized["mismatched_payload"]) + len(self.categorized["partially_missing"]) + \
                       len(self.categorized["unexpected_topics"]) + len(self.categorized["pure_missing"])
        
        if total_issues == 0:
            print("✨ Everything is consistent!")
        else:
            print(f"🚨 Found {total_issues} devices with issues.")
        print("="*60 + "\n")

if __name__ == "__main__":
    verifier = DiscoveryVerifier()
    verifier.run()
