"""Code converter — derive a Home Assistant cover state from a Tuya curtain
motor's raw DPs, for whole device *models* (by product_id).

It folds each model's control / set-position / position DPs into one cover state
(opening / closing / open / closed) and publishes it as a derived DP. The manager
renders the topic; here we only send `(dp, value)`.

When this is needed at all: a curtain whose firmware reports a *complete* cover
state on one DP needs no code — point the cover's `state_dp` at it and map the
values with `state_opening`/`state_closed`/… in the JSON converter. This converter
is for motors that *don't*, assembling the state from the control/set/position
DPs instead. A motor whose state DP is only partially usable is treated as not
usable: we never half-consume a work-state DP here (its vocabulary varies per
model and that would be a pile of edge cases). A genuinely odd one gets its own
dedicated `.py` targeting that product_id, leaving this generic converter clean.

Adding a curtain is JSON-only — this file is generic and never edited per model.
At startup it self-registers for every product whose JSON cover opts in with
`discovery_overrides.cover.state_stream == "derived"`. Everything model-specific
is read from that cover at runtime, so curtains differing only in wiring,
vocabulary, or inversion need no code change here:

  * DP roles — `command_dp` / `set_position_dp` / `position_dp` / `state_dp`: which
    DPs carry the command, the target, the current position, and the derived
    output. These are the same role keys the discovery payload uses (single
    source), each defaulting to the common Tuya curtain layout (1 / 2 / 3 / 99)
    when the JSON omits it.

  * Command words — `discovery_overrides.cover.payload_open/close/stop`: the very
    strings HA publishes to command the cover, so they are exactly what the device
    echoes back on its control DP. `payload_open` simply *means* "the word that
    opens this device" — be it `OPEN`, `open`, `START`, or even `close` on a
    command-mirrored motor. We match the control DP against these (no inversion
    applied here — the JSON value already reflects any mirror/case/vocab). When
    the JSON declares none, we fall back to Home Assistant's MQTT-cover defaults
    (`OPEN`/`CLOSE`/`STOP`), i.e. what HA itself would send.

  * Inverted end — `discovery_overrides.cover.invert_position`: which raw end is
    "closed". This is the *position* axis only; it is applied where we reason
    about position numbers (set-target vs current, resting open/closed), never to
    the command words above. Reading it from the same JSON
    the discovery payload uses keeps the derived state and the rendered position
    in lock-step. We emit the *canonical* HA strings, so the JSON needs no
    `state_opening`/`state_open`/… remapping.

Why product_id and not a device id: same model → same DP layout, so one
registration covers every unit of that model without editing a device list. To
target a single device instead, swap `@api.on_product(...)` for
`@api.on_device("<device id>")`.

Why we emit open/closed (not just motion): Home Assistant, given a separate state
topic, will *not* derive open/closed from the position — and on a retained replay
it can latch the wrong resting state if "stopped" is delivered before the
position. So we resolve open/closed here and HA sets it verbatim, race-free.

State that survives a (re)start: each device's last-seen DPs are seeded from the
manager's snapshot (`api.current_dps`) the first time it's seen, so position is
known immediately — the first command after a restart isn't dropped. The `origin`
argument tells a snapshot *replay* (`"retained"`) from a live change (`"device"`):
on replay we only assert the resting open/closed state, never motion — the motion
DPs in a snapshot describe the last command, not a move happening now, so
inferring opening/closing there would flicker the cover on every reconnect.

Needs manager api_version >= 2 (the plugin runtime); `current_dps` seeding needs
rc58+ but degrades gracefully on older hosts. Loaded once at startup; edit +
restart the manager to apply changes.
"""

# Opt-in marker: a cover whose state is produced by a code converter. Every
# product whose JSON declares discovery_overrides.cover.state_stream == "derived"
# is picked up automatically — adding a curtain is JSON-only, no edit here.
DERIVED_STREAM = "derived"

# Default DP layout for the common Tuya curtain motor; a product overrides any of
# these via its JSON cover roles (command_dp / set_position_dp / position_dp /
# state_dp) — the same keys the discovery payload uses, so it stays single-source.
DEFAULT_CONTROL_DP = "1"
DEFAULT_SET_DP = "2"
DEFAULT_POSITION_DP = "3"
DEFAULT_OUT_DP = "99"

# Home Assistant MQTT-cover command defaults — used only when the JSON converter's
# discovery_overrides.cover declares no payload_* (HA itself would send these).
HA_PAYLOAD_OPEN = "OPEN"
HA_PAYLOAD_CLOSE = "CLOSE"
HA_PAYLOAD_STOP = "STOP"

# Canonical Home Assistant cover states we publish on the derived DP — HA sets
# these verbatim. We never emit "stopped": a stop is resolved to open/closed so
# HA shows the real resting state instead of an ambiguous "stopped".
OPENING = "opening"
CLOSING = "closing"
OPEN = "open"
CLOSED = "closed"


def setup(api):
    # Self-register for every product whose JSON cover opts into derived state.
    # No per-model list lives here — a curtain is added entirely in JSON; restart
    # the manager to pick up a newly-added one.
    for product_id, conv in (api.converters or {}).items():
        cover = ((conv or {}).get("discovery_overrides") or {}).get("cover") or {}
        if cover.get("state_stream") == DERIVED_STREAM:
            _register(api, product_id, cover)


def _register(api, product_id, cover):
    # DP roles come from the JSON cover (single source with the discovery payload),
    # each falling back to the common curtain layout when the JSON omits it.
    CONTROL_DP = cover.get("command_dp", DEFAULT_CONTROL_DP)
    SET_DP = cover.get("set_position_dp", DEFAULT_SET_DP)
    POSITION_DP = cover.get("position_dp", DEFAULT_POSITION_DP)
    OUT_DP = cover.get("state_dp", DEFAULT_OUT_DP)

    # One handler serves every device of this model, so state is keyed by
    # device id: {device_id: {"latest": {dp: val}, "emitted": last_state}}.
    by_device = {}

    def _cover(device_id):
        """This device's discovery_overrides.cover (or {}), read from the merged
        JSON converter — the single source of truth for both the command words
        and the inverted end, shared with the rendered discovery payload."""
        return (((api.converter(api.product_id(device_id)) or {})
                 .get("discovery_overrides") or {}).get("cover") or {})

    @api.on_product(product_id)
    async def _(device_id, dps, origin):
        st = by_device.get(device_id)
        if st is None:
            # First sighting — seed the DP bag from the manager's live snapshot so
            # position is known before the first command. Empty on hosts without
            # current_dps; the position-aware branches below guard for that.
            st = by_device[device_id] = {"latest": dict(api.current_dps(device_id)), "emitted": None}
        latest = st["latest"]
        latest.update(dps)

        cover = _cover(device_id)
        inv = bool(cover.get("invert_position"))      # position axis only
        p_open = cover.get("payload_open", HA_PAYLOAD_OPEN)   # command axis
        p_close = cover.get("payload_close", HA_PAYLOAD_CLOSE)
        p_stop = cover.get("payload_stop", HA_PAYLOAD_STOP)

        raw_pos = latest.get(POSITION_DP)
        try:
            pos = float(raw_pos) if raw_pos is not None else None
        except (TypeError, ValueError):
            pos = None
        # Position as Home Assistant sees it (0 = closed, 100 = open). On an
        # inverted-position device the motor's raw 0/100 are mirrored — the same
        # mirroring the position_template applies — so open/closed lands on the
        # correct physical end. May be None if no position is known yet.
        ha_pos = (100 - pos) if (inv and pos is not None) else pos

        state = None
        if origin == "retained":
            # Snapshot replay — assert the standing state only, never motion.
            if ha_pos is not None:
                state = CLOSED if ha_pos <= 0 else OPEN
        else:
            # Live change — infer motion from whichever DP changed this event.
            if CONTROL_DP in dps:
                # Command axis: match the raw word against the JSON's payload_*
                # (which already carries any command mirror / custom vocab / case)
                # — so NO position-invert is applied here.
                cmd = dps[CONTROL_DP]
                if cmd == p_stop:
                    # A stop settles wherever it is — closed only at the closed end.
                    if ha_pos is not None:
                        state = CLOSED if ha_pos <= 0 else OPEN
                elif cmd == p_open:
                    state = OPENING
                elif cmd == p_close:
                    state = CLOSING
            elif SET_DP and SET_DP in dps and pos is not None:
                # Position axis: raw target vs raw current → raw direction, mapped
                # to the HA direction through invert_position.
                try:
                    rising = float(dps[SET_DP]) > pos  # raw motor position rising
                    state = OPENING if (rising != inv) else CLOSING
                except (TypeError, ValueError):
                    pass
            elif POSITION_DP in dps and ha_pos is not None:
                state = CLOSED if ha_pos <= 0 else OPEN

            # At a hard limit the motor can't move further → it has arrived; settle
            # to the resting open/closed rather than a stale "opening"/"closing".
            if ha_pos is not None:
                if ha_pos <= 0 and state == CLOSING:
                    state = CLOSED
                elif ha_pos >= 100 and state == OPENING:
                    state = OPEN

        # The bridge republishes each live DP on several event streams, so this
        # handler fires more than once per real change. Publish only when the
        # derived state actually changes — idempotent regardless of echoes.
        if state is not None and state != st["emitted"]:
            st["emitted"] = state
            await api.derive(device_id, OUT_DP, state)
