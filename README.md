# rustuya-homeassistant

Generate and sync **Home Assistant MQTT Discovery** payloads for Tuya devices
bridged by [rustuya-bridge](https://github.com/3735943886/rustuya-bridge).

It reads your device list, maps each Tuya datapoint (DP) to the right Home
Assistant entity (sensor / switch / light / climate / cover / fan / …), and
publishes the corresponding retained `homeassistant/.../config` topics.

## Install

```bash
pip install -e .          # from a checkout
rustuya-ha --help
```

`python -m rustuya_ha` works as well.

## Usage

```bash
rustuya-ha status                       # compare retained discovery vs current output
rustuya-ha status -c mismatched --detail # show field-level diffs
rustuya-ha preview 'guest_*'            # dump generator output, no MQTT
rustuya-ha publish '*' --dry-run        # preview the publish/clear plan
rustuya-ha publish '*' -y               # apply
rustuya-ha clear '*' --stale-only       # drop orphan/stale topics
```

`PATTERN` is an fnmatch on device id or name (default `*`). `-c/--category`
narrows by verifier category (see `rustuya-ha -h`).

### Configuration

| Setting | Flag | Env | Default |
|---|---|---|---|
| MQTT broker | `--broker HOST[:PORT]` | `RUSTUYA_MQTT` | `localhost:1883` |
| Device list | `--devices PATH` | `RUSTUYA_DEVICES` | `tuyadevices.json` |
| Custom converters | `--converters PATH` | `RUSTUYA_CONVERTERS` | `./custom_converters.json` |

## Architecture

```
rustuya_ha/
  core/    pure generation logic (no MQTT, no argparse) — usable as a library
    generator.py   DiscoveryGenerator: device -> {topic: payload}
    mapping.py     DP/category -> HA entity tables
    converter.py   user DP overrides (custom_converters.json)
    scheme.py      TopicScheme / PayloadCodec seams (topic layout + payload shape)
  cli/     thin argparse wrapper (manager = MQTT I/O, verifier, render)
```

Use the core directly from other front-ends:

```python
from rustuya_ha import initialize_generator
payloads, source = initialize_generator().generate(device)
```

### Topic/payload schemes (`TopicScheme` / `PayloadCodec`)

Topics and the MQTT payload shape are injected via `scheme.py` rather than
hardcoded, so **discovery follows whatever templates the bridge is configured
with**. `DefaultTopicScheme` / `DefaultPayloadCodec` reproduce the historical
layout; `BridgeTopicScheme` / `BridgePayloadCodec` (`core/bridge.py`) derive the
layout from a rustuya-bridge config (`mqtt_event_topic` / `mqtt_command_topic` /
`mqtt_message_topic` / `mqtt_payload_template`).

The config is resolved per run: `--bridge-config <file>` > the retained
`{root}/bridge/config` topic (read over the same MQTT connection, like
rustuya-manager) > the legacy default. Derivation handles:

- **per-DP** (`{dp}` in the event topic) and **multi-DP** (full `dps` dict on one
  topic; `value_template` indexes by DP).
- **value path**: `value_template` points at wherever `{value}`/`{dps}` sits in
  the payload template (e.g. `{"value":{value}}` → `value_json.value`).
- **active vs passive**: `event` entities consume the momentary `active` push;
  stateful entities read only the retained `passive` snapshot. This holds for any
  config — if the event topic separates `{type}` the topics do it; if active and
  passive share a topic, the `value_template` drops `active` messages instead.
  The drop is applied only when `mqtt_retain` is on (a passive snapshot is
  guaranteed to follow) and the payload carries `{type}` (so the two can be told
  apart); otherwise stateful entities accept whatever arrives.

The `LEGACY` profile in `core/bridge.py` is the bridge config that reproduces the
historical output; `tests/test_bridge_scheme_legacy.py` asserts it stays
byte-identical to the golden snapshots.

## Tests

```bash
python3 -m pytest
```

Golden snapshot tests lock generator output so refactors stay regression-free.
Regenerate the baseline only on intentional behavior changes:

```bash
python3 tests/generate_snapshots.py
```
