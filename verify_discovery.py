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
        # 1. Generate expected topics from JSON
        for device in self.devices:
            device_id = device.get('id')
            if not device_id:
                continue
            
            device_name = device.get('name', device_id)
            try:
                expected_payloads, source = self.generator.generate(device)
                for topic, payload in expected_payloads.items():
                    self.expected_topics[topic] = {
                        "payload": payload,
                        "device_id": device_id,
                        "device_name": device_name
                    }
            except Exception as e:
                logger.error(f"Error generating discovery for device {device_id}: {e}")
                self.results["errors"].append(f"Generation error ({device_id}): {e}")

        # 2. Compare Expected vs Received
        received_topics = set(self.received_messages.keys())
        expected_topics_set = set(self.expected_topics.keys())

        for topic in expected_topics_set:
            info = self.expected_topics[topic]
            dev_id = info["device_id"]
            dev_name = info["device_name"]

            if topic in received_topics:
                actual = self.received_messages[topic]["payload"]
                expected = info["payload"]
                
                if self.compare_payloads(actual, expected):
                    self.results["matched"].append({
                        "topic": topic,
                        "device": dev_name,
                        "retain": self.received_messages[topic]["retain"]
                    })
                else:
                    if dev_id not in self.results["mismatched"]:
                        self.results["mismatched"][dev_id] = {"name": dev_name, "entities": []}
                    self.results["mismatched"][dev_id]["entities"].append({
                        "topic": topic,
                        "actual": actual,
                        "expected": expected
                    })
                received_topics.remove(topic)
            else:
                if dev_id not in self.results["missing_in_mqtt"]:
                    self.results["missing_in_mqtt"][dev_id] = {"name": dev_name, "entities": []}
                self.results["missing_in_mqtt"][dev_id]["entities"].append(topic)

        # 3. Check for topics in MQTT that are not in JSON (orphans)
        for topic in received_topics:
            self.results["missing_in_json"].append({
                "topic": topic,
                "payload": self.received_messages[topic]["payload"]
            })

    def compare_payloads(self, actual: Dict[str, Any], expected: Dict[str, Any]) -> bool:
        return actual == expected

    def print_summary(self):
        print("\n" + "="*60)
        print("         MQTT DISCOVERY VERIFICATION SUMMARY")
        print("="*60)
        
        print(f"\n📊 Statistics:")
        print(f"  - Devices in JSON: {len(self.devices)}")
        print(f"  - Total Expected Topics: {len(self.expected_topics)}")
        print(f"  - Total Received Topics: {len(self.received_messages)}")
        
        print(f"\n✅ Matches: {len(self.results['matched'])} entities")
        
        if self.results["mismatched"]:
            print(f"\n❌ Mismatched Devices: {len(self.results['mismatched'])}")
            for dev_id, data in self.results["mismatched"].items():
                print(f"  - 📱 {data['name']} ({dev_id})")
                for ent in data["entities"]:
                    print(f"    - Mismatch in: {ent['topic']}")

        if self.results["missing_in_mqtt"]:
            print(f"\n⚠️ Missing Discovery (Devices with missing entities): {len(self.results['missing_in_mqtt'])}")
            for dev_id, data in self.results["missing_in_mqtt"].items():
                count = len(data["entities"])
                print(f"  - 📱 {data['name']} ({dev_id}): {count} entities missing")
                # Optional: list them if count is small?
                if count <= 3:
                    for t in data["entities"]:
                        print(f"    - {t}")
                else:
                    print(f"    - ... and {count-3} more")

        if self.results["missing_in_json"]:
            print(f"\n👻 Orphaned Discovery (MQTT topics not in JSON): {len(self.results['missing_in_json'])}")
            # Group orphans by device ID if possible
            orphans_by_dev = {}
            for item in self.results["missing_in_json"]:
                topic = item["topic"]
                # Try to extract device ID from topic or payload
                payload = item["payload"]
                dev_info = payload.get("device", {})
                ids = dev_info.get("identifiers", [])
                dev_id = ids[0] if ids else "unknown"
                if dev_id not in orphans_by_dev: orphans_by_dev[dev_id] = []
                orphans_by_dev[dev_id].append(topic)
            
            for dev_id, topics in orphans_by_dev.items():
                print(f"  - ❓ {dev_id}: {len(topics)} orphaned topics")

        if self.results["errors"]:
            print(f"\n❗ Errors encountered: {len(self.results['errors'])}")
            for err in self.results["errors"]:
                print(f"  - {err}")

        print("\n" + "="*60)
        
        total_issues = len(self.results["mismatched"]) + len(self.results["missing_in_mqtt"]) + len(self.results["missing_in_json"])
        if total_issues == 0:
            print("✨ Everything is consistent!")
        else:
            print(f"🚨 Found {total_issues} devices/groups with issues.")
        print("="*60 + "\n")

if __name__ == "__main__":
    verifier = DiscoveryVerifier()
    verifier.run()
