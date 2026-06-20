"""Example code converter — derive a Home Assistant cover state from a Tuya
curtain motor's raw DPs, for a whole device *model* (by product_id).

Copy this into your `custom_converters/` directory (next to the JSON converters)
and set `PRODUCT` to your curtain model's product_id — the same key you used in
the JSON converter that carries the cover override. Every device of that model
is served by this one converter; state is kept per device id. It folds the
control / set-position / position / work-state DPs into one cover state
(opening / closing / open / closed / stopped) and publishes it as a derived DP.
The manager renders the topic; here we only send `(dp, value)`.

Why product_id and not a device id: same model → same DP layout, so one
converter covers all units (guest curtain, bedroom curtain, …) without editing
a device list. To target a single device instead, swap
`@api.on_product(PRODUCT)` for `@api.on_device("<device id>")` and drop the
per-device state dict (a single bag is then enough).

Why the logic isn't a one-liner: the work-state DP only ever reports
opening/closing, so "stopped" and the resting open/closed states have to be
inferred from the other DPs plus the hard-limit clamp.

Needs manager api_version >= 2 (the plugin runtime). Loaded once at startup;
edit + restart the manager to apply changes.
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
        st = by_device.setdefault(device_id, {"latest": {}, "emitted": None})
        latest = st["latest"]
        latest.update(dps)

        # Current position as a percent (0 = closed, 100 = open).
        raw_pos = latest.get(POSITION_DP)
        if raw_pos is None:
            return
        try:
            pos = float(raw_pos)
        except (TypeError, ValueError):
            return

        # Which DP changed this event decides what we can infer.
        state = None
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
                state = "opening" if float(dps[SET_DP]) > pos else "closing"
            except (TypeError, ValueError):
                pass
        elif WORK_DP in dps:
            w = dps[WORK_DP]
            if w in ("opening", "closing"):
                state = w
        elif POSITION_DP in dps:
            state = "closed" if pos <= 0 else "open"

        # At a hard limit the motor can't move further → it has stopped.
        if (pos <= 0 and state == "closing") or (pos >= 100 and state == "opening"):
            state = "stopped"

        # The bridge republishes each DP on several event streams (active /
        # passive / state), so this handler fires more than once for one real
        # change. Publish only when the derived state actually changes — the
        # output is idempotent regardless of how many streams echo the input.
        if state is not None and state != st["emitted"]:
            st["emitted"] = state
            await api.derive(device_id, OUT_DP, state)
