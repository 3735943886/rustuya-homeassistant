"""MQTT I/O and command dispatch. paho is imported lazily so read-only
commands (preview) work on machines without an MQTT stack installed."""
import json
import time
import logging
import fnmatch
from collections import defaultdict
from typing import Dict, List

from ..core.generator import initialize_generator
from ..core.bridge import BridgeConfig
from ..core.scheme import scheme_for
from ..core import plan
from . import render
from . import backup
from ..core.verifier import (
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
                 devices_path="tuyadevices.json", converters_path=None,
                 bridge_config_path=None, backup_dir=backup.DEFAULT_DIR,
                 no_backup=False):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.devices_path = devices_path
        self.bridge_config_path = bridge_config_path
        self.backup_dir = backup_dir
        self.no_backup = no_backup
        self.generator = initialize_generator(converters_path)
        self.verifier = DiscoveryVerifier(self.generator)
        self.devices: List[Dict] = []
        self.mqtt_data: Dict[str, Dict] = {}
        self.bridge_config_payload = None  # retained {root}/bridge/config, if seen
        self.config_source = "legacy"

    def apply_bridge_config(self, allow_mqtt=True):
        """Resolve the bridge config (file > retained MQTT > legacy) and inject the
        derived scheme/codec into the generator. Returns the source label."""
        cfg = None
        if self.bridge_config_path:
            cfg = BridgeConfig.from_json_file(self.bridge_config_path)
            self.config_source = "file"
        elif allow_mqtt and self.bridge_config_payload:
            cfg = BridgeConfig.from_bridge_config_topic(self.bridge_config_payload)
            self.config_source = "mqtt"
        else:
            self.config_source = "legacy"
        if cfg is not None:
            self.generator.scheme, self.generator.codec = scheme_for(cfg)
        render.print_bridge_source(self.config_source)
        return self.config_source

    # --- IO ---

    def load_config(self):
        with open(self.devices_path, "r", encoding="utf-8") as f:
            self.devices = json.load(f)

    def collect_mqtt(self):
        """Subscribe to homeassistant/# and collect retained /config payloads."""
        import paho.mqtt.client as mqtt
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        def on_msg(c, u, m):
            if m.topic.endswith("/bridge/config"):
                # rustuya-bridge publishes its resolved config here (retained).
                payload = m.payload.decode() if isinstance(m.payload, bytes) else m.payload
                if payload:
                    self.bridge_config_payload = payload
                return
            if m.topic.endswith("/config"):
                try:
                    self.mqtt_data[m.topic] = {"payload": json.loads(m.payload), "retain": m.retain}
                except Exception:
                    pass

        def on_connect(c, u, f, rc, p):
            if rc == 0:
                c.subscribe(f"{HA_PREFIX}/#")
                # {root}/bridge/config — root is unknown here, cover 1- and 2-level roots.
                c.subscribe("+/bridge/config")
                c.subscribe("+/+/bridge/config")

        client.on_connect = on_connect
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

    def fetch_bridge_config(self):
        """Best-effort grab of the retained ``{root}/bridge/config`` from the broker.

        Lets an otherwise-offline ``preview`` mirror the live topic/payload layout
        (the documented file > retained-MQTT > legacy order). Silently no-ops if
        paho isn't installed or the broker is unreachable, so preview stays usable
        on a box without an MQTT stack."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            return
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        def on_msg(c, u, m):
            payload = m.payload.decode() if isinstance(m.payload, bytes) else m.payload
            if payload:
                self.bridge_config_payload = payload

        def on_connect(c, u, f, rc, p):
            if rc == 0:
                # root is unknown here, cover 1- and 2-level roots.
                c.subscribe("+/bridge/config")
                c.subscribe("+/+/bridge/config")

        client.on_connect = on_connect
        client.on_message = on_msg
        try:
            client.connect(self.broker_host, self.broker_port)
        except (ConnectionRefusedError, OSError):
            return
        client.loop_start()
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

    def _auto_backup(self):
        """Snapshot the current retained discovery (already in self.mqtt_data from
        collect_mqtt) before a mutating write, so publish/clear/restore are undoable."""
        if self.no_backup:
            return None
        path = backup.save(self.backup_dir, self.mqtt_data, prefix="auto")
        print(f"💾 backup: {path} ({len(self.mqtt_data)} topic(s)) — undo with: {render.PROG} restore --last")
        return path

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
        self.apply_bridge_config()
        results = self.verifier.verify(self.devices, self.mqtt_data)
        results = filter_status_results(results, categories)
        render.print_summary(results)
        if detail:
            render.print_mismatch_details(results, pattern)

    def cmd_preview(self, pattern: str):
        """Show generator output for matching devices.

        Mirrors the live topic/payload layout: explicit --bridge-config wins, else
        a best-effort read of the retained bridge config from the broker, else the
        legacy default (preview still works with no broker / no MQTT stack)."""
        self.load_config()
        matches = self._match(pattern)
        if not matches:
            print(f"❌ No matching devices for {pattern!r}")
            return
        if not self.bridge_config_path:
            self.fetch_bridge_config()  # best-effort; no-op offline
        self.apply_bridge_config(allow_mqtt=True)  # file > retained MQTT > legacy
        render.print_preview(matches, self.generator)

    def cmd_publish(self, pattern: str, yes: bool, dry_run: bool, categories=None):
        self.load_config()
        matches = self._match(pattern)
        if not matches:
            print(f"❌ No matching devices for {pattern!r}")
            return

        # Collect existing retained topics up front (needed for category filter and stale calc)
        self.collect_mqtt()
        self.apply_bridge_config()

        if categories:
            results = self.verifier.verify(self.devices, self.mqtt_data)
            matches = filter_matches_by_categories(matches, results, categories)
            if not matches:
                print(f"❌ No devices match {pattern!r} with categories {categories}")
                return

        # Build the publish plan (pure, shared with the manager plugin).
        msgs, per_device, gen_errors = plan.publish_plan(matches, self.generator, self.mqtt_data)
        for err in gen_errors:
            logger.error(f"Generator failed for {err['id']}: {err['error']}")
        n_publish = sum(p["publish"] for p in per_device)
        n_clear = sum(p["clear"] for p in per_device)

        print(f"\n📋 publish plan for {len(matches)} device(s):")
        for p in per_device:
            print(f"  {p['name']} ({p['id']}): +{p['publish']} publish, -{p['clear']} clear")
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

        self._auto_backup()
        self._publish(msgs)
        print(f"✅ done.")

    def cmd_clear(self, pattern: str, stale_only: bool, yes: bool, dry_run: bool, categories=None):
        self.load_config()
        matches = self._match(pattern)
        if not matches:
            print(f"❌ No matching devices for {pattern!r}")
            return

        self.collect_mqtt()
        self.apply_bridge_config()
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
            # All retained topics whose payload device-id matches.
            topics_by_dev = {
                k: list(v) for k, v in plan.owned_topics(self.mqtt_data, matched_ids).items()
            }

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

        msgs, _ = plan.clear_plan(self.mqtt_data, matched_ids, topics_by_dev=topics_by_dev)
        self._auto_backup()
        self._publish(msgs)
        print(f"✅ cleared {total} topic(s).")

    def cmd_restore(self, target=None, yes=False, dry_run=False, show_list=False):
        """Revert retained discovery to a backup/snapshot (full-scope undo)."""
        if show_list:
            files = backup.listing(self.backup_dir)
            if not files:
                print(f"no backups in {self.backup_dir}")
                return
            print(f"backups in {self.backup_dir} (newest first):")
            for p in files:
                print(f"  {p.name}")
            return

        path = target or backup.latest(self.backup_dir)
        if not path:
            print(f"❌ no backup found in {self.backup_dir} (pass a file or run publish/clear first)")
            return
        snapshot = backup.load(path)

        self.collect_mqtt()  # current retained discovery
        current = set(self.mqtt_data)
        set_msgs, clear_msgs = backup.restore_plan(snapshot, current)

        print(f"\n📋 restore from {path}")
        print(f"  set {len(set_msgs)} topic(s) to snapshot, clear {len(clear_msgs)} added since")
        if dry_run:
            for m in clear_msgs:
                print(f"    - clear {m['topic']}")
            print("\n🔎 dry-run: no MQTT writes.")
            return
        if not set_msgs and not clear_msgs:
            print("ℹ️ already matches snapshot.")
            return
        if not yes and not self._confirm("\nProceed? [Y/n]: "):
            print("aborted.")
            return

        self._auto_backup()  # back up pre-restore state too (undo the undo)
        self._publish(clear_msgs + set_msgs)
        print(f"✅ restored to {path}")
