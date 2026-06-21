"""Code converter — derive a Home Assistant cover state from a Tuya curtain
motor's raw DPs, for a whole device *model* (by product_id).

This is a ready-to-use converter for the product_id pinned in `PRODUCT` below.
For a different curtain, copy this file, set `PRODUCT` to your model's product_id
(the key you used in the JSON converter), and check the DP numbers. It folds the
control / set-position / position / work-state DPs into one cover *motion* state
(opening / closing / stopped) and publishes it as a derived DP. It never emits
open/closed — Home Assistant resolves the resting open/closed state from the
cover's position itself (CLOSED at position 0, else OPEN), so an inverted-position
device stays correct without this code knowing which physical end is "closed".
The manager renders the topic; here we only send `(dp, value)`.

Why product_id and not a device id: same model → same DP layout, so one
converter covers all units (every curtain of this model) without editing a
device list. To target a single device instead, swap `@api.on_product(PRODUCT)`
for `@api.on_device("<device id>")`.

State that survives a (re)start: each device's last-seen DPs are seeded from the
manager's snapshot (`api.current_dps`) the first time it's seen, so position is
known immediately — the first command after a restart isn't dropped, and the
hard-limit clamp works from the start. The `origin` argument tells a snapshot
*replay* (`"retained"`) from a live change (`"device"`): on replay we settle to
"stopped" (HA resolves it to open/closed from the retained position), never
motion — the motion DPs in a snapshot describe the last command, not a move
happening now, so inferring opening/closing there would flicker the cover on
every reconnect.

Why the logic isn't a one-liner: the work-state DP only ever reports
opening/closing, so "stopped" has to be inferred from the other DPs plus the
hard-limit clamp (a bare position report, a stop command, or hitting an end).

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

    @api.on_product(PRODUCT)
    async def _(device_id, dps, origin):
        st = by_device.get(device_id)
        if st is None:
            # First sighting — seed the DP bag from the manager's live snapshot so
            # position is known before the first command (no dropped first event
            # after a restart). Empty on hosts without current_dps; that's fine.
            st = by_device[device_id] = {"latest": dict(api.current_dps(device_id)), "emitted": None}
        latest = st["latest"]
        latest.update(dps)

        # Current position as a percent (0 = closed, 100 = open). May still be
        # unknown if the snapshot was empty and no position has arrived yet; the
        # position-aware branches below guard for that.
        raw_pos = latest.get(POSITION_DP)
        try:
            pos = float(raw_pos) if raw_pos is not None else None
        except (TypeError, ValueError):
            pos = None

        state = None
        if origin == "retained":
            # Snapshot replay — settle to a resting state, never motion. Emit
            # "stopped" rather than open/closed: HA resolves stopped→open/closed
            # from its own (possibly inverted) position, so this stays correct
            # regardless of which physical end is "closed".
            state = "stopped"
        else:
            # Live change — infer motion from whichever DP changed this event.
            if CONTROL_DP in dps:
                cmd = dps[CONTROL_DP]
                if cmd == "stop":
                    state = "stopped"
                elif cmd == "open":
                    state = "opening"
                elif cmd == "close":
                    state = "closing"
            elif SET_DP in dps:
                try:
                    if pos is not None:
                        state = "opening" if float(dps[SET_DP]) > pos else "closing"
                except (TypeError, ValueError):
                    pass
            elif WORK_DP in dps:
                w = dps[WORK_DP]
                if w in ("opening", "closing"):
                    state = w
            elif POSITION_DP in dps and pos is not None:
                # A bare position report means motion has settled → stopped; HA
                # derives open/closed from the position.
                state = "stopped"

            # At a hard limit the motor can't move further → it has stopped.
            if pos is not None and (
                (pos <= 0 and state == "closing") or (pos >= 100 and state == "opening")
            ):
                state = "stopped"

        # The bridge republishes each live DP on several event streams, so this
        # handler fires more than once per real change. Publish only when the
        # derived state actually changes — idempotent regardless of echoes.
        if state is not None and state != st["emitted"]:
            st["emitted"] = state
            await api.derive(device_id, OUT_DP, state)
