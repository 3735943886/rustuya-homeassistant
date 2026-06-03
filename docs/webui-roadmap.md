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

### M0 — Publish `rustuya-ha` to PyPI  *(prereq, ~0.5d)*
The plugin ships inside the rustuya-ha package, so it must be pip-installable.
- Polish `pyproject.toml` (authors, urls, classifiers, SPDX license), add `LICENSE`.
- Declare a `[manager]` optional extra (depends on `rustuya-manager`).
- Build (sdist+wheel), rehearse on TestPyPI, then PyPI; tag the release.
- **Done:** `pip install rustuya-homeassistant` works; `core` API stable.

### M1 — Manager plugin host  *(generic, HA-agnostic, ~2–3d; highest-risk piece)*
A one-time, reusable extension surface in rustuya-manager.
- **Backend:** entry-point loader (`rustuya_manager.plugins`); a plugin `ctx` with
  `add_api_router`, `add_mqtt_subscription(topic, handler)`,
  `state_namespace(name)` (+ notify, serialized over the existing WS), and
  `bridge_client`; a `/api/plugins` manifest endpoint; a `PLUGIN_API_VERSION` the
  plugin can check.
- **Frontend (the risky bit):** a page-host — tab nav built from `/api/plugins`,
  `currentPage` state, dynamic `import()` of each plugin JS module, and a stable
  `mount(rootEl, ctx)` contract where `ctx = { getState, onState, api, toast,
  confirm }`. Make `render()` page-aware without regressing the device-sync view
  (it becomes the default page).
- **Done:** a throwaway "hello" plugin loads its tab end-to-end; the manager with
  no plugins behaves byte-identically to today.

### M2 — Discovery plugin, read-only MVP  *(vertical slice, ~1.5–2d)*
The smallest end-to-end proof that the pipeline works.
- `rustuya_ha.manager_plugin` registers via entry point; subscribes
  `homeassistant/#`; exposes `/api/discovery/status`.
- Adapter: manager `State` devices + parsed bridge config → `core` generator input;
  run the verifier; publish results into the plugin's state namespace.
- **Frontend:** `discovery.js` renders the status grid (view-only, no buttons).
- **Done:** with manager + rustuya-ha installed, the HA tab shows a live per-device
  discovery status grid. **← decision gate: confirm the contract feels right.**

### M3 — Write actions: publish / clear / restore  *(~1.5–2d)*
- `/api/discovery/{publish,clear,restore}`; publish via `ctx.bridge_client`
  (aiomqtt); reuse the pure `restore_plan`; auto-backup before writes.
- **Frontend:** per-device and bulk actions, dry-run/confirm UX, restore + undo.
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

---

## Rough total
- Core (M0–M4): **~6–9 focused days.**
- MVP confidence point (M0–M2): **~4–5 days**, then re-evaluate.
- M5 deferred.

## Sizing risk
Only **M1 frontend (page-host)** and **M3/M4 grid UI** are genuinely new and
estimate-wobbly; M0/M2-backend lean heavily on existing patterns and logic.
