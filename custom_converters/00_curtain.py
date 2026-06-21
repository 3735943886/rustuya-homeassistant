"""Code converter — derive a Home Assistant cover state from a Tuya curtain
motor's raw DPs, for a whole device *model* (by product_id).

This is a ready-to-use converter for the product_id pinned in `PRODUCT` below.
For a different curtain, copy this file, set `PRODUCT` to your model's product_id
(the key you used in the JSON converter), and check the DP numbers. It folds the
control / set-position / position / work-state DPs into one cover state
(opening / closing / open / closed) and publishes it as a derived DP. The manager
renders the topic; here we only send `(dp, value)`.

Why product_id and not a device id: same model → same DP layout, so one
converter covers all units (every curtain of this model) without editing a
device list. To target a single device instead, swap `@api.on_product(PRODUCT)`
for `@api.on_device("<device id>")`.

Why we emit open/closed (not just motion): Home Assistant, given a separate state
topic, will *not* derive open/closed from the position — and on a retained replay
it can latch the wrong resting state if "stopped" is delivered before the
position. So we resolve open/closed here and HA sets it verbatim, race-free.

Inverted motors: which raw end is "closed" is the one physical fact this logic
needs, and it's the very same `invert_position` flag the discovery payload reads
(`discovery_overrides.cover.invert_position` in the JSON converter). We read it
through `api.converter(...)` so the derived state and the rendered position can
never disagree, and emit the *canonical* HA strings — the JSON needs no
`state_opening`/`state_open`/… remapping, only the position/command inversion it
already does.

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

# ── configure for your device model ──────────────────────────────────────
PRODUCT = "h2wipnagcunsar5r"  # ← product_id (the JSON converter's key)
CONTROL_DP = "1"   # open / close / stop
SET_DP = "2"       # target position (raw %)
POSITION_DP = "3"  # current position (raw %)
WORK_DP = "7"      # work state: opening / closing only
OUT_DP = "99"      # derived cover-state output


def setup(api):
    # One handler serves every device of this model, so state is keyed by
    # device id: {device_id: {"latest": {dp: val}, "emitted": last_state}}.
    by_device = {}

    def _inverted(device_id):
        """Whether this device's position is inverted — read from the same JSON
        `invert_position` flag the discovery payload uses (so the derived state
        and the rendered position stay in lock-step). False on any device without
        the override."""
        cover = ((api.converter(api.product_id(device_id)) or {})
                 .get("discovery_overrides") or {}).get("cover") or {}
        return bool(cover.get("invert_position"))

    @api.on_product(PRODUCT)
    async def _(device_id, dps, origin):
        st = by_device.get(device_id)
        if st is None:
            # First sighting — seed the DP bag from the manager's live snapshot so
            # position is known before the first command. Empty on hosts without
            # current_dps; the position-aware branches below guard for that.
            st = by_device[device_id] = {"latest": dict(api.current_dps(device_id)), "emitted": None}
        latest = st["latest"]
        latest.update(dps)

        raw_pos = latest.get(POSITION_DP)
        try:
            pos = float(raw_pos) if raw_pos is not None else None
        except (TypeError, ValueError):
            pos = None
        # Position as Home Assistant sees it (0 = closed, 100 = open). On an
        # inverted-position device the motor's raw 0/100 are mirrored — the same
        # mirroring the position_template applies — so open/closed lands on the
        # correct physical end. May be None if no position is known yet.
        inv = _inverted(device_id)
        ha_pos = (100 - pos) if (inv and pos is not None) else pos

        state = None
        if origin == "retained":
            # Snapshot replay — assert the standing state only, never motion.
            if ha_pos is not None:
                state = "closed" if ha_pos <= 0 else "open"
        else:
            # Live change — infer motion from whichever DP changed this event.
            # Motor-native motion is mapped to the HA direction: on an inverted
            # device the motor "opening" is HA "closing" and vice versa.
            if CONTROL_DP in dps:
                cmd = dps[CONTROL_DP]
                if cmd == "stop":
                    # A stop settles wherever it is — closed only at the closed end.
                    if ha_pos is not None:
                        state = "closed" if ha_pos <= 0 else "open"
                elif cmd in ("open", "close"):
                    opening = (cmd == "open") != inv
                    state = "opening" if opening else "closing"
            elif SET_DP in dps and pos is not None:
                try:
                    rising = float(dps[SET_DP]) > pos  # raw motor position rising
                    state = "opening" if (rising != inv) else "closing"
                except (TypeError, ValueError):
                    pass
            elif WORK_DP in dps:
                w = dps[WORK_DP]
                if w in ("opening", "closing"):
                    state = w if not inv else ("closing" if w == "opening" else "opening")
            elif POSITION_DP in dps and ha_pos is not None:
                state = "closed" if ha_pos <= 0 else "open"

            # At a hard limit the motor can't move further → it has arrived; settle
            # to the resting open/closed rather than a stale "opening"/"closing".
            if ha_pos is not None:
                if ha_pos <= 0 and state == "closing":
                    state = "closed"
                elif ha_pos >= 100 and state == "opening":
                    state = "open"

        # The bridge republishes each live DP on several event streams, so this
        # handler fires more than once per real change. Publish only when the
        # derived state actually changes — idempotent regardless of echoes.
        if state is not None and state != st["emitted"]:
            st["emitted"] = state
            await api.derive(device_id, OUT_DP, state)
