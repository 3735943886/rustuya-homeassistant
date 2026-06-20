# Custom converters

This directory holds the drop-in converters the generator and manager load:

- `*.json` — DP / discovery overrides keyed by product_id (deep-merged across
  files). Hot-reloaded; edit and save, no restart.
- `*.py` — *code converters*. Each defines `setup(api)` and uses the manager's
  plugin runtime (api_version >= 2) to react to decoded device DPs and publish
  *derived* DPs — per-device logic the JSON converters can't express. The result
  topic is rendered by the manager; a converter only supplies `(dp, value)`.
  Loaded at startup — edit + **restart the manager** to apply (unlike JSON, they
  don't hot-reload).

Resolution order is `$RUSTUYA_CONVERTERS` (a dir or a single `.json`) else
`./custom_converters/`. To adapt a `.py` here for your own device, copy it, set
the product_id / DP numbers at the top, and restart.

## `api` surface (passed to `setup(api)`)

- `@api.on_device(id)` — `handler(device_id, dps, origin)` for one device's events
- `@api.on_product(product_id)` — same, for every device of one model (product_id)
- `@api.on_dp(id, dp)` — `handler(device_id, value)` when that DP changes
- `api.on_any(handler)` — every device's events
- `origin` (3rd handler arg) — `"retained"` for a snapshot replay on (re)connect,
  `"device"` for a live change (tell an initial-state seed from a real move)
- `await api.derive(id, dp, value)` — publish a derived DP (manager renders the topic)
- `await api.clear(id, dp)` / `await api.set_dp(id, dp, value)`
- `api.service(coro_factory)` — a long-lived supervised async daemon
- `api.current_dps(id?)` — snapshot of current DP values to seed from at setup
  (rc58+; `{}` on older hosts)
- `api.converter(product_id)` / `api.product_id(device_id)` / `api.devices()` /
  `api.bridge_config()` — read your converters + the fleet

## Files

- [`00_curtain.py`](00_curtain.py) — derive a Home Assistant cover state
  (opening/closing/open/closed/stopped) from a Tuya curtain motor's raw DPs, for
  a whole device model (by product_id), with per-device state seeded from the
  manager snapshot.
- [`00_default.json`](00_default.json) — base JSON overrides (a window opener + two curtain models).
  Files deep-merge by sorted filename, so a higher-sorted gitignored `99_*.json`
  can layer local tweaks on top of the base.
