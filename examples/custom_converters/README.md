# Example code converters

Drop-in `*.py` files for the `custom_converters/` directory. Each defines
`setup(api)` and uses the manager's plugin runtime (api_version >= 2) to react to
decoded device DPs and publish *derived* DPs — per-device logic that the JSON
converters can't express. The result topic is rendered by the manager; a
converter only supplies `(dp, value)`.

To use one: copy it into your converters directory (the same place as your
`*.json` converters — `$RUSTUYA_CONVERTERS` or `./custom_converters/`), edit the
device id / DP numbers at the top, and **restart the manager** (Python converters
load at startup; unlike JSON they don't hot-reload).

## `api` surface (passed to `setup(api)`)

- `@api.on_device(id)` — `handler(device_id, dps, origin)` for one device's events
- `@api.on_dp(id, dp)` — `handler(device_id, value)` when that DP changes
- `api.on_any(handler)` — every device's events
- `await api.derive(id, dp, value)` — publish a derived DP (manager renders the topic)
- `await api.clear(id, dp)` / `await api.set_dp(id, dp, value)`
- `api.service(coro_factory)` — a long-lived supervised async daemon
- `api.converter(product_id)` / `api.product_id(device_id)` / `api.devices()` /
  `api.bridge_config()` — read your converters + the fleet

## Files

- [`guest_curtain.py`](guest_curtain.py) — derive a Home Assistant cover state
  (opening/closing/open/closed/stopped) from an inverted Tuya curtain motor's raw
  DPs, reading the `invert_position` flag from the device's own JSON converter.
