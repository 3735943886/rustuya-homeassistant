# Web UI Roadmap — HA Discovery as a rustuya-manager plugin

## Final goal

Deliver a Home Assistant MQTT-discovery management UI **as an optional plugin that
rustuya-ha contributes into [rustuya-manager](https://github.com/3735943886/rustuya-manager)** —
not as a standalone web app, and not by making the manager depend on Home Assistant.

When both packages are installed, the manager UI shows an **"HA Discovery"** tab: a
manager-style status grid of each device's discovery state (matched / mismatched /
missing / unexpected), drill-in field-level diffs, per-device and bulk
publish / clear / restore (with the existing undo), and — later — inline editing of
the per-DP entity overrides (`custom_converters.json`).

The discovery **engine is `rustuya_ha.core`** (the single source of truth, already
used by the CLI). The plugin talks to its UI host only through a **small,
host-agnostic contract**, so a neutral shell could replace "manager-as-host" later
without changing the plugin.

### Dependency direction (the key principle)
```
rustuya-ha  ──(optional extra [manager])──▶  rustuya-manager
```
The manager never depends on rustuya-ha. It gains only a **generic, HA-agnostic
plugin host**; rustuya-ha plugs itself in when present (entry-point auto-discovery).

### Non-goals (explicit)
- **No neutral UI shell now.** Manager is the host. A neutral shell is a possible
  *end state* if a 3rd UI consumer ever appears — deferred until N≥3 and it actually
  hurts. The clean plugin contract is what keeps that door open for free.
- **No frontend framework / build step.** Stay vanilla JS + Tailwind CDN, matching
  the manager.
- **Manager stays HA-agnostic.** Manager alone = device sync only; no HA traces.
- **CLI remains the headless path** (`rustuya-ha`, paho-only) for scripting / dev /
  HA-less use. The plugin and the CLI are two thin shells over the same `core`.

### What already exists (reduces scope)
`core` generator, verifier categories, **field-level diffs** (`status --detail`),
pure `restore_plan`, backup/restore. The discovery dashboard's backend logic is
largely done; the remaining work is the plumbing into the manager + the UI.

---

## Milestones

Each milestone is an independently shippable slice. **Decision gate after M2** —
validate the whole pipeline before investing in write-actions and polish.

> **Status — 2026-06-07.** M0 ✅ and M1 ✅ are done and verified; the manager
> already ships the generic plugin host. The remaining critical path is on the
> rustuya-ha side: **M1.5 (core purification) → M2 (read-only discovery plugin,
> the decision gate)**, then M3+. The only outstanding manager-side work is the
> small **M-host+** publish primitive (needed for M3) and the **M-host++**
> frontend e2e hardening (optional). Milestone numbers/anchors below are pinned
> to verified current code.

### M0 — Publish `rustuya-ha` to PyPI  *(prereq, ~0.5d)*  ✅ DONE
The plugin ships inside the rustuya-ha package, so it must be pip-installable.
- Polish `pyproject.toml` (authors, urls, classifiers, SPDX license), add `LICENSE`.
- Declare a `[manager]` optional extra (depends on `rustuya-manager`).
- Build (sdist+wheel), rehearse on TestPyPI, then PyPI; tag the release.
- **Done:** `0.0.1rc1` published to PyPI as a *pre-release* (so default
  `pip install` skips it — `--pre` required — keeping the first stable slot for
  `0.1.0`). `core` API in use by the CLI. The `[manager]` extra + the
  `rustuya_manager.plugins` entry point land with M2 (the plugin module).

### M1 — Manager plugin host  *(generic, HA-agnostic)*  ✅ DONE & VERIFIED
A one-time, reusable extension surface in rustuya-manager — shipped in `0.1.0rc28`.
- **Backend** (`src/rustuya_manager/plugins.py`): entry-point loader
  (`rustuya_manager.plugins`, stdlib `importlib.metadata`), `PLUGIN_API_VERSION = 1`,
  per-plugin failure isolation. `ctx` exposes `add_api_router`,
  `add_mqtt_subscription(topic, handler)` (own MQTT wildcard matcher, no paho;
  routed before the bridge-template guard so retained topics flow; replayed on
  reconnect), `state_namespace(name)` (bumps `State` → rides the existing WS),
  `add_page`, and `bridge_client`. `GET /api/plugins` manifest; per-plugin static
  mounted at `/plugins/{id}/`.
- **Frontend** (`static/plugins.js`): page-host — boots from `/api/plugins`,
  builds the tab bar only when ≥1 plugin exists, dynamic `import()` + `mount(rootEl,
  ctx)` with `ctx = { getState, onState, api, toast, confirm }`. Devices view is
  the default page.
- **Done / verified:** `tests/test_plugins.py` (21 tests) covers all four surfaces
  + failure isolation + the **zero-plugin no-regression** guarantee (no `plugins`
  key in the WS snapshot, no tab bar in the served HTML, byte-identical wire). Full
  suite **213 passed**. With no plugins installed the manager behaves exactly as
  before, and it never imports any HA code.

### M1.5 — Core purification  *(rustuya-ha, ~0.5d; recommended before M2)*
Keep the plugin importing only the pure engine, not the paho CLI shell.
- Promote `DiscoveryVerifier` (`cli/verifier.py`) and `restore_plan`
  (`cli/backup.py`) into `core/` (or re-export from `core`). Both are already pure.
- **Done:** the plugin depends only on `rustuya_ha.core`; `cli/` remains the
  paho-only headless shell. Golden + existing tests pass unchanged.

### M2 — Discovery plugin, read-only MVP  *(rustuya-ha, ~1.5–2d)*  ← decision gate
The smallest end-to-end proof that the pipeline works. New module
`rustuya_ha/manager_plugin/` + the `[manager]` extra + the `rustuya_manager.plugins`
entry point.
- `register(ctx)`: `add_mqtt_subscription("homeassistant/#", handler)` (retained
  discovery), `add_api_router` (`GET /api/discovery/status`),
  `state_namespace("discovery")`, `add_page("discovery", "HA Discovery", static_dir=…)`.
- **Adapter (linchpin, verified):** the manager keeps the full original device dict
  in `State.cloud[id].raw_data` (`models.py` `Device.from_dict`), which is exactly
  the shape `DiscoveryGenerator.generate(device)` consumes (`local_strategy` /
  `function` / `category` / `product_id`) — near-identity. Bridge config: build
  `BridgeConfig.from_bridge_config_topic(payload)` from the retained
  `{root}/bridge/config`. Run the verifier; push results into the namespace.
- **⚠️ Open contract point:** the plugin backend needs read access to devices
  (`raw_data` is server-side only — it is deliberately omitted from the WS
  snapshot). Today `ctx` exposes only `bridge_client` + the namespace. Resolve at
  M2 start: add a small read-only device accessor to `ctx` (manager-side change,
  HA-agnostic) — pairs naturally with M-host+.
- **Frontend:** `static/index.js` `mount()` subscribes via `ctx.onState` to
  `snapshot.plugins.discovery`; renders the status grid (view-only, no buttons).
- **Done:** with manager + rustuya-ha installed, the HA tab shows a live per-device
  discovery status grid. **← decision gate: confirm the contract feels right.**

### M-host+ — Generic retained publish primitive  *(rustuya-manager, ~0.5d; before M3)*
M3 prerequisite. `ctx.bridge_client` today only exposes `publish_command` (the
bridge command topic). The discovery plugin must write/clear `homeassistant/.../config`
retained topics.
- Add a public `BridgeClient.publish_raw(topic, payload, *, retain, qos=1)` (or
  expose `ctx.publish(...)`) so plugins don't reach into privates. Bundle the M2
  device-read accessor here.
- **Done:** behaviour unchanged for the manager; one unit test; HA-agnostic.

### M3 — Write actions: publish / clear / restore  *(rustuya-ha, ~1.5–2d)*
- `/api/discovery/{publish,clear,restore}`; publish via the M-host+ primitive
  (aiomqtt); reuse the pure `restore_plan`; auto-backup before writes.
- **Frontend:** per-device and bulk actions, dry-run/confirm UX (`ctx.confirm`),
  restore + undo.
- **Done:** discovery can be reconciled entirely from the UI; undo works.

### M4 — Drill-in diffs + parity polish  *(~1–1.5d)*
- Expand a device → field-level diffs (reuse `status --detail` logic).
- Filters / search / sort like the manager grid; a badge for the bridge-config
  source (file / MQTT / legacy).
- **Done:** UI reaches feature parity with the CLI's `status --detail`.

### M5 — Inline `custom_converters` editing  *(optional, later, ~1–2d)*
- Edit per-DP entity overrides in the UI, persist, live-preview regeneration.
- **Done:** an entity can be remapped without hand-editing JSON. (The biggest
  UI-only win over the CLI; intentionally last.)

### M-host++ — Frontend page-host e2e  *(rustuya-manager, optional hardening)*
The host backend has 21 tests; the `plugins.js` tab-bar/mount path has none.
- Add a `tests/e2e_ui` scenario with a fake plugin: tab bar appears → page mounts →
  `onState` receives a frame. Closes the one coverage gap in the host.

---

## Rough total
- M0 ✅ + M1 ✅ done. Remaining core (M1.5–M4): **~5–7 focused days.**
- MVP / decision gate (through **M2**): **~2–3 days** from here, then re-evaluate.
- M5 + M-host++ deferred / optional.

## Sizing risk
M1 (the page-host, the original highest-risk piece) is **done and verified**. What
remains estimate-wobbly is **M3/M4 grid UI**; the M2 backend and the adapters lean
heavily on existing pure logic. The one design decision left is the **M2 ⚠️ device
accessor on `ctx`** (do it with M-host+).
