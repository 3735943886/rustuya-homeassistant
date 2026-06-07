# rustuya-homeassistant

Generate and sync **Home Assistant MQTT Discovery** payloads for Tuya devices
bridged by [rustuya-bridge](https://github.com/3735943886/rustuya-bridge).

It reads your device list, maps each Tuya datapoint (DP) to the right Home
Assistant entity (sensor / switch / light / climate / cover / fan / …), and
publishes the corresponding retained `homeassistant/.../config` topics.

## Install

```bash
pip install --pre rustuya-homeassistant   # from PyPI (pre-release only for now)
pip install -e .                           # or from a checkout
rustuya-ha --help
```

> Only a `0.0.1rc1` pre-release is published so far, so `--pre` is required
> (a plain `pip install rustuya-homeassistant` finds nothing until the first
> stable `0.1.0` release).

`python -m rustuya_ha` works as well.

## Usage

```bash
rustuya-ha status                       # compare retained discovery vs current output
rustuya-ha status -c mismatched --detail # show field-level diffs
rustuya-ha preview 'guest_*'            # dump generator output, no MQTT
rustuya-ha publish '*' --dry-run        # preview the publish/clear plan
rustuya-ha publish '*' -y               # apply
rustuya-ha clear '*' --stale-only       # drop orphan/stale topics
rustuya-ha restore --last               # undo the last publish/clear
```

### Undo (backup / restore)

`publish` and `clear` already read the full retained discovery state before they
write, so each one drops a timestamped backup of it first (under `--backup-dir`,
default `.rustuya-ha-backups/`; `--no-backup` to skip). Since the discovery state
lives entirely in retained `homeassistant/.../config` topics, that backup is a
complete restore point.

```bash
rustuya-ha restore --last        # revert to the most recent backup
rustuya-ha restore --list        # list available backups
rustuya-ha restore <file>        # revert to a specific backup
rustuya-ha restore --last --dry-run
```

Restore re-publishes the saved topics (retained) and clears any added since, so
the live state matches the snapshot exactly. Restore also backs up the
pre-restore state first, so an undo is itself undoable. Backups hold device
names/ids (treat as private; the dir is gitignored).

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
- **active vs passive**: three classes —
  - `event` entities and **incremental/delta DPs** (e.g. `add_ele`; see
    `mapping.ACTIVE_ONLY_CODES`, or per-product `custom_converters` `"active": true`)
    read the momentary `active` push and ignore the retained snapshot;
  - all other (absolute-state) entities read only the retained `passive` snapshot.

  This holds for any config — if the event topic separates `{type}` the topics do
  it; if active and passive share a topic, the `value_template` filters by type
  instead. Filtering applies only when `mqtt_retain` is on (a passive snapshot is
  guaranteed) and the payload carries `{type}` (so the two can be told apart);
  otherwise entities accept whatever arrives.

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
