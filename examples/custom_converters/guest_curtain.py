"""Example code converter — derive a Home Assistant cover state from a Tuya
curtain motor's raw DPs.

Copy this into your `custom_converters/` directory (next to the JSON converters)
and set `DEVICE` to your curtain's device id. It reproduces the classic helper:
fold the control / set-position / position / work-state DPs into one cover state
(opening / closing / open / closed / stopped) — accounting for an *inverted*
motor — and publish it as a derived DP. The manager renders the topic; here we
only send `(dp, value)`.

Why the logic isn't a one-liner: the work-state DP only ever reports
opening/closing, so "stopped" and the resting open/closed states have to be
inferred from the other DPs plus the hard-limit clamp. The `inverted` flag is
read from the device's own JSON converter
(`discovery_overrides.cover.invert_position`) — the same flag that drives
discovery also drives this derivation, so there's one source of truth.

Needs manager api_version >= 2 (the plugin runtime). Loaded once at startup;
edit + restart the manager to apply changes.
"""

# ── configure for your device ────────────────────────────────────────────
DEVICE = "eba796ada03c1aeb00cfah"  # ← your curtain's device id
CONTROL_DP = "1"   # open / close / stop
SET_DP = "2"       # target position (raw %)
POSITION_DP = "3"  # current position (raw %)
WORK_DP = "7"      # work state: opening / closing only
OUT_DP = "99"      # derived cover-state output


def setup(api):
    cover = (api.converter(api.product_id(DEVICE)) or {}).get("discovery_overrides", {}).get("cover", {})
    inverted = bool(cover.get("invert_position"))

    latest = {}  # last value seen for each DP, held across events
    emitted = {"state": None}  # last derived state actually published

    @api.on_device(DEVICE)
    async def _(device_id, dps, origin):
        latest.update(dps)

        # Current position in HA "open%" terms (0 = closed, 100 = open).
        raw_pos = latest.get(POSITION_DP)
        if raw_pos is None:
            return
        try:
            pos = (100 - float(raw_pos)) if inverted else float(raw_pos)
        except (TypeError, ValueError):
            return

        # Which DP changed this event decides what we can infer.
        state = None
        if CONTROL_DP in dps:
            cmd = dps[CONTROL_DP]
            if cmd == "stop":
                state = "stopped"
            elif cmd == "open":
                state = "closing" if inverted else "opening"
            elif cmd == "close":
                state = "opening" if inverted else "closing"
        elif SET_DP in dps:
            try:
                target = float(dps[SET_DP])
                target = (100 - target) if inverted else target
                state = "opening" if target > pos else "closing"
            except (TypeError, ValueError):
                pass
        elif WORK_DP in dps:
            w = dps[WORK_DP]
            if w == "opening":
                state = "closing" if inverted else "opening"
            elif w == "closing":
                state = "opening" if inverted else "closing"
        elif POSITION_DP in dps:
            state = "closed" if pos <= 0 else "open"

        # At a hard limit the motor can't move further → it has stopped.
        if (pos <= 0 and state == "closing") or (pos >= 100 and state == "opening"):
            state = "stopped"

        # The bridge republishes each DP on several event streams (active /
        # passive / state), so this handler fires more than once for one real
        # change. Publish only when the derived state actually changes — the
        # output is idempotent regardless of how many streams echo the input.
        if state is not None and state != emitted["state"]:
            emitted["state"] = state
            await api.derive(device_id, OUT_DP, state)
