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
hardcoded. `DefaultTopicScheme` / `DefaultPayloadCodec` reproduce the current
layout. The next track will derive these from a rustuya-bridge config
(`mqtt_event_topic` / `mqtt_command_topic` / `mqtt_payload_template`) using the
`pyrustuyabridge` template helpers, so discovery automatically follows whatever
topic/payload templates the bridge is configured with.

## Tests

```bash
python3 -m pytest
```

Golden snapshot tests lock generator output so refactors stay regression-free.
Regenerate the baseline only on intentional behavior changes:

```bash
python3 tests/generate_snapshots.py
```
