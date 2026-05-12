"""Publish HA MQTT discovery payloads for all (or matching) devices.

Use after changing generator behavior (e.g., editing
tuya_mapping.UNAVAILABLE_ERROR_CODES) to push the new retained payloads to HA.
Retained messages overwrite the prior ones, so HA picks up the new config
on its next discovery scan.

Usage:
    python3 register_device.py                      # publish all devices
    python3 register_device.py living_lightswitch   # exact name or id match
    python3 register_device.py 'eb*'                # fnmatch pattern (id or name)
"""
import json
import sys
import logging
import fnmatch
import paho.mqtt.publish as publish

from tuya_discovery_generator import initialize_generator

BROKER_HOST = "localhost"
BROKER_PORT = 1883
DEVICES_JSON = "tuyadevices.json"

logging.basicConfig(level=logging.WARNING)


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else "*"

    with open(DEVICES_JSON) as f:
        devices = json.load(f)

    matches = [
        d for d in devices
        if fnmatch.fnmatch(str(d.get("id", "")), pattern)
        or fnmatch.fnmatch(str(d.get("name", "")), pattern)
    ]

    if not matches:
        print(f"No devices matched pattern: {pattern!r}")
        return

    gen = initialize_generator()
    msgs = []
    for d in matches:
        payloads, source = gen.generate(d)
        for topic, payload in payloads.items():
            msgs.append({
                "topic": topic,
                "payload": json.dumps(payload),
                "retain": True,
            })
        print(f"  {d['id']} ({d.get('name')}) -> {len(payloads)} topics [{source}]")

    if not msgs:
        print("\nNo topics to publish (matched devices generated no payloads).")
        return

    publish.multiple(msgs, hostname=BROKER_HOST, port=BROKER_PORT)
    print(f"\nPublished {len(msgs)} retained topics for {len(matches)} device(s).")


if __name__ == "__main__":
    main()
