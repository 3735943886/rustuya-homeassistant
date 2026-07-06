# Changelog

All notable changes to this project are documented in this file, curated by
hand. This file is the single source of truth: the GitHub Release notes for each
tag are the matching `## [version]` section extracted from here by the release
workflow.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project versions follow [PEP 440](https://peps.python.org/pep-0440/):
`0.0.1rcN` pre-releases lead up to the `0.1.0` final. Release channels:

- **Manual run** (Actions → publish → *Run workflow*) rehearses the build on
  **TestPyPI** — no tag involved.
- **Any `v*` tag** publishes to **PyPI** and attaches a GitHub Release (plus the
  drop-in plugin zip the rustuya-manager catalog consumes). An `rc`/`a`/`b`/`dev`
  tag ships as a pre-release (so plain `pip install` skips it — `--pre` required);
  a bare `vX.Y.Z` is the stable release.

## [Unreleased]

## [0.0.1rc23] — 2026-07-06

### Added

- **Status grid refreshes when the cloud device set changes.** The plugin now
  wires the manager's device-set bus (`ctx.watch_devices`, host api_version ≥ 4,
  in rustuya-manager `0.1.0rc74`): adding or removing a device in the manager
  (devices upload / login wizard) re-runs the verifier against the live device
  list and re-pushes the grid, instead of the device surfacing only on the next
  `homeassistant/#` message or write action. The host also replays the current
  set at startup, so the grid seeds at boot. Method-presence feature-detect — a
  no-op on an older host that lacks the bus (previous surfacing behavior kept).

## [0.0.1rc22] — 2026-06-29

### Changed

- **Replaced the read-only bridge-config advisory with declared topic/retain
  requirements.** rustuya-manager now offers a first-class mechanism
  (`ctx.require_topic` / `ctx.require_retain`, host api_version ≥ 3) for a plugin
  to declare the bridge topic scheme it depends on; the manager evaluates it
  against the live config and surfaces any gap *with a guided fix* in its Info
  panel — strictly better than a passive hint inside the plugin tab. So the
  rc21 in-plugin advisory banner is removed and `register()` now declares, on a
  v3+ host (no-op on older ones):
  - `require_retain("HA Discovery")` — needs `mqtt_retain=True` so stateful
    entities read the retained `state` snapshot and restore on HA restart.
  - `require_topic("HA Discovery", "event", must_have=("type",))` — needs
    `{type}` in the event topic so the retained snapshot is distinct from
    transient active/passive deltas (no flicker). `{dp}` is deliberately not
    required — the generator adapts to multi-DP and flat-command layouts.

  (Payload-template validity is covered separately by the bridge binding, so it
  isn't re-declared here.)

## [0.0.1rc21] — 2026-06-29

### Added

- **Read-only bridge-config advisory in the HA Discovery tab.** A new pure
  `advise(BridgeConfig)` helper inspects the live, resolved `{root}/bridge/config`
  and flags the few settings that produce a *working-but-degraded* integration,
  surfaced as a read-only hint banner (en/ko). It deliberately stays quiet for
  the many shapes the generator already adapts to transparently (flat command
  topics, multi-DP payloads, missing `{action}`/`{id}`), and shows nothing at all
  for an optimal config or when no live bridge config is visible. Three checks:
  - `retain_off` — `mqtt_retain` is off, so the bridge keeps no retained `state`
    snapshot: absolute-value entities stay unknown until the next device report
    and don't restore on HA restart. (HA discovery configs themselves are always
    published retained, independent of this setting.)
  - `payload_unparseable` — the payload template isn't JSON-shaped or carries no
    `{value}`/`{dps}`, so the codec can't locate the value and entities break.
  - `no_type` — retain is on but `{type}` rides in neither the event topic nor
    the payload, so the retained snapshot can't be told apart from transient
    active/passive deltas and absolute entities may briefly flicker.

## [0.0.1rc20] — 2026-06-24

### Changed

- **Filter section now leads with ID then name, matching rustuya-manager.** The
  search placeholder and the sort dropdown are reordered ID-first (아이디 / 이름),
  and the Korean labels use 아이디 instead of `ID`.

## [0.0.1rc19] — 2026-06-24

### Fixed

- Terser read-only-converter error; cleaner save/delete toast copy.

## [0.0.1rc18] — 2026-06-24

### Fixed

- Clearer discard-changes prompt; dropped the now-obsolete managed-converter note.

## [0.0.1rc17] — 2026-06-24

### Added

- Read-only default converters with a full-screen modal editor: default-pack
  files are protected from in-place edits and copied to a new file to customize.

## [0.0.1rc16] — 2026-06-22

### Fixed

- The converters editor Save button now reacts on keystroke rather than waiting
  for the next state push.

## [0.0.1rc15] — 2026-06-22

### Fixed

- Defer the live re-paint while the user is mid-gesture (drag-select / editing),
  so a background state push no longer collapses cards or drops a selection.

## [0.0.1rc14] — 2026-06-22

### Added

- Generic JSON-driven cover converter (command/position axes split) and kids
  cover support; a restart-attention cue on the manager header for `.py`
  converters that need a restart to take effect.

## [0.0.1rc13] — 2026-06-21

### Changed

- Adopt the host's `ctx.data_dir` for a CWD-independent custom-converters
  directory, so the editor and the runtime agree regardless of working directory.

## [0.0.1rc12] — 2026-06-21

### Added

- Live-updatable default-converter pack: fetch the latest defaults from GitHub
  on demand without touching the user's own files.

### Fixed

- Curtain converter emits explicit open/closed and reads invert from the JSON,
  rather than deriving open/closed inside the converter.

## [0.0.1rc11] — 2026-06-21

### Added

- `state_stream: "derived"` so a cover's state can read a manager-rendered
  derived DP.

## [0.0.1rc10] — 2026-06-21

### Added

- Per-device `product_id` in the grid (the authoring key for product-scoped
  converters); converter editor gains an unsaved indicator + cancel/revert.

### Fixed

- Confine backup restore to the backup directory (path-traversal hardening) and
  avoid information exposure through an exception.

## [0.0.1rc9] — 2026-06-20

### Added

- Converter file picker rendered as a grouped dropdown.

## [0.0.1rc8] — 2026-06-20

### Added

- Product-based code converters via `api.on_product`.

## [0.0.1rc7] — 2026-06-20

### Added

- Drop-in converters directory + Python code converters, with a file-browser
  editor in the plugin tab.

## [0.0.1rc6] — 2026-06-20

### Changed

- Follow the manager's global language; dropped the in-tab language picker.

## [0.0.1rc5] — 2026-06-19

### Added

- File-based plugin i18n (en/ko JSON catalogs) and (transiently) an in-tab
  language picker.

## [0.0.1rc4] — 2026-06-16

### Added

- Manager-style HA Discovery UI: card list, filter tabs + sync bar, review modal
  for bulk publish/clear, restore-picker modal, persisted category filter + sort,
  and a top-level `register()` for manager drop-in discovery.

### Fixed

- Responsive grid that reflows to cards on mobile; `value_template` renders `''`
  (keep state) rather than `none` on a no-value read; read pass-through state via
  a `{type}` wildcard catching active|passive.

## [0.0.1rc3] — 2026-06-08

### Added

- Emit a `command_template` for flat command topics (the write path), so a
  command topic without `{dp}` still encodes id/dp/action in the payload.

## [0.0.1rc2] — 2026-06-08

### Added

- Webui milestones M2–M5: read-only HA Discovery plugin, write actions
  (publish/clear/restore), drill-in field diffs, and inline custom-converter
  editing.

## [0.0.1rc1] — 2026-06-07

### Added

- Initial pre-release: generate & sync Home Assistant MQTT discovery for
  rustuya-bridge Tuya devices. Bridge-config-driven topic/value-template derive,
  retained `state` snapshot reads for stateful entities, and passive companion
  sensors for delta DPs.
