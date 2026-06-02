"""MQTT I/O and command dispatch. paho is imported lazily so read-only
commands (preview) work on machines without an MQTT stack installed."""
import json
import time
import logging
import fnmatch
from collections import defaultdict
from typing import Dict, List, Set

from ..core.generator import initialize_generator
from . import render
from .verifier import (
    DiscoveryVerifier,
    filter_matches_by_categories,
    filter_status_results,
)

HA_PREFIX = "homeassistant"
COLLECT_WAIT = 1.0

logger = logging.getLogger("tuya_discovery_admin")


class BrokerUnavailable(RuntimeError):
    """Raised when the MQTT broker cannot be reached, with a user-facing hint."""


class DiscoveryManager:
    """Orchestrates load -> verify -> publish/clear. Holds runtime config so the
    broker host and file paths are no longer module-level constants."""

    def __init__(self, broker_host="localhost", broker_port=1883,
                 devices_path="tuyadevices.json", converters_path=None):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.devices_path = devices_path
        self.generator = initialize_generator(converters_path)
        self.verifier = DiscoveryVerifier(self.generator)
        self.devices: List[Dict] = []
        self.mqtt_data: Dict[str, Dict] = {}

    # --- IO ---

    def load_config(self):
        with open(self.devices_path, "r", encoding="utf-8") as f:
            self.devices = json.load(f)

    def collect_mqtt(self):
        """Subscribe to homeassistant/# and collect retained /config payloads."""
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        def on_msg(c, u, m):
            if m.topic.endswith("/config"):
                try:
                    self.mqtt_data[m.topic] = {"payload": json.loads(m.payload), "retain": m.retain}
                except Exception:
                    pass

        client.on_connect = lambda c, u, f, rc, p: c.subscribe(f"{HA_PREFIX}/#") if rc == 0 else None
        client.on_message = on_msg
        try:
            client.connect(self.broker_host, self.broker_port)
        except (ConnectionRefusedError, OSError) as e:
            raise BrokerUnavailable(
                f"cannot reach MQTT broker at {self.broker_host}:{self.broker_port} ({e}).\n"
                f"   set it with --broker host:port or $RUSTUYA_MQTT, "
                f"and check the broker is running."
            ) from e
        client.loop_start()
        logger.info(f"Scanning MQTT discovery for {COLLECT_WAIT}s...")
        time.sleep(COLLECT_WAIT)
        client.loop_stop()
        client.disconnect()

    def _publish(self, msgs):
        import paho.mqtt.publish as publish
        try:
            publish.multiple(msgs, hostname=self.broker_host, port=self.broker_port)
        except (ConnectionRefusedError, OSError) as e:
            raise BrokerUnavailable(
                f"cannot reach MQTT broker at {self.broker_host}:{self.broker_port} ({e})."
            ) from e

    def _match(self, pattern: str) -> List[Dict]:
        return [
            d for d in self.devices
            if any(fnmatch.fnmatch(str(d.get(k, '')), pattern) for k in ['id', 'name'])
        ]

    @staticmethod
    def _confirm(prompt: str) -> bool:
        return input(prompt).strip().lower() in ('', 'y', 'yes')

    # --- Commands ---

    def cmd_status(self, detail: bool = False, pattern: str = "*", categories=None):
        self.load_config()
        self.collect_mqtt()
        results = self.verifier.verify(self.devices, self.mqtt_data)
        results = filter_status_results(results, categories)
        render.print_summary(results)
        if detail:
            render.print_mismatch_details(results, pattern)

    def cmd_preview(self, pattern: str):
        """Show generator output for matching devices without touching MQTT."""
        self.load_config()
        matches = self._match(pattern)
        if not matches:
            print(f"❌ No matching devices for {pattern!r}")
            return
        render.print_preview(matches, self.generator)

    def cmd_publish(self, pattern: str, yes: bool, dry_run: bool, categories=None):
        self.load_config()
        matches = self._match(pattern)
        if not matches:
            print(f"❌ No matching devices for {pattern!r}")
            return

        # Collect existing retained topics up front (needed for category filter and stale calc)
        self.collect_mqtt()

        if categories:
            results = self.verifier.verify(self.devices, self.mqtt_data)
            matches = filter_matches_by_categories(matches, results, categories)
            if not matches:
                print(f"❌ No devices match {pattern!r} with categories {categories}")
                return

        # Build new payloads for matching devices
        new_topics: Dict[str, Dict[str, dict]] = {}  # dev_id -> {topic -> payload}
        for d in matches:
            try:
                payloads, _ = self.generator.generate(d)
                new_topics[d['id']] = payloads
            except Exception as e:
                logger.error(f"Generator failed for {d['id']}: {e}")

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

        self._publish(msgs)
        print(f"✅ done.")

    def cmd_clear(self, pattern: str, stale_only: bool, yes: bool, dry_run: bool, categories=None):
        self.load_config()
        matches = self._match(pattern)
        if not matches:
            print(f"❌ No matching devices for {pattern!r}")
            return

        self.collect_mqtt()
        results = (self.verifier.verify(self.devices, self.mqtt_data)
                   if (stale_only or categories) else None)

        if categories:
            matches = filter_matches_by_categories(matches, results, categories)
            if not matches:
                print(f"❌ No devices match {pattern!r} with categories {categories}")
                return

        matched_ids = {m['id'] for m in matches}

        if stale_only:
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
        self._publish(msgs)
        print(f"✅ cleared {total} topic(s).")
