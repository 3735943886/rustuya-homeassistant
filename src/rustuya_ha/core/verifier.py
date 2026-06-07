"""Compare generator output against retained MQTT discovery state and
categorize each device. Category-filter helpers live here too."""
from typing import Dict, List

CATEGORY_ALIASES = {
    "mismatched": "mismatched_payload",
    "partial": "partially_missing",
    "pure": "pure_missing",
    "no-dp": "no_dp_config",
    "unexpected": "unexpected_topics",
    "perfect": "perfect",
}


def filter_matches_by_categories(matches, results, categories):
    """Narrow `matches` (list of device dicts) to those in any requested category."""
    if not categories:
        return matches
    cat_keys = [CATEGORY_ALIASES[c] for c in categories]
    allowed_ids = {d['id'] for k in cat_keys for d in results.get(k, [])}
    return [m for m in matches if m['id'] in allowed_ids]


def filter_status_results(results, categories):
    """Blank out result lists not in the requested categories. `errors` is preserved."""
    if not categories:
        return results
    cat_keys = {CATEGORY_ALIASES[c] for c in categories}
    return {
        k: (v if (not isinstance(v, list) or k in cat_keys or k == "errors") else [])
        for k, v in results.items()
    }


class DiscoveryVerifier:
    """Categorize devices by comparing JSON config against MQTT discovery state."""

    def __init__(self, generator):
        self.generator = generator

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
