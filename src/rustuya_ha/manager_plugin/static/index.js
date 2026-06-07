// HA Discovery plugin page.
//
// Mounted by rustuya-manager's plugin host via mount(rootEl, ctx). `ctx` is the
// host-agnostic surface: getState(), onState(cb), api(path), toast, confirm.
//
// Renders a per-device status grid from the discovery plugin's state namespace
// (snapshot.plugins.discovery), seeded by an initial fetch of
// /api/discovery/status so the grid shows even before the first WS push. Write
// actions (publish / clear / restore) POST to /api/discovery/*; each previews
// its plan via a dry-run, confirms, then executes — the grid refreshes itself
// from the namespace push (broker echo) afterwards.

const CATEGORIES = [
  ["perfect", "Perfect", "emerald"],
  ["mismatched_payload", "Mismatched", "amber"],
  ["partially_missing", "Partial", "yellow"],
  ["pure_missing", "Missing", "rose"],
  ["unexpected_topics", "Unexpected", "purple"],
  ["no_dp_config", "No DP config", "slate"],
  ["orphans", "Orphans", "red"],
];

const COLOR = {
  emerald: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  yellow: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-200",
  rose: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-200",
  purple: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-200",
  slate: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  red: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
};
const LABEL = Object.fromEntries(CATEGORIES.map(([k, l]) => [k, l]));
const CHIP = Object.fromEntries(CATEGORIES.map(([k, , c]) => [k, COLOR[c]]));

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function btn(label, cls, onClick) {
  const b = el("button", cls, label);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

const BTN_BASE =
  "px-2 py-0.5 rounded text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed";
const BTN_PRIMARY =
  `${BTN_BASE} bg-slate-700 text-white hover:bg-slate-600 dark:bg-slate-200 dark:text-slate-900 dark:hover:bg-white`;
const BTN_DANGER =
  `${BTN_BASE} bg-rose-600 text-white hover:bg-rose-500`;
const BTN_GHOST =
  `${BTN_BASE} border border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800`;

// Categories that have something to publish (missing / drifted / partial).
const NEEDS_SYNC = new Set([
  "mismatched_payload",
  "partially_missing",
  "pure_missing",
  "unexpected_topics",
]);

function describePlan(action, plan) {
  if (action === "publish") {
    const pub = (plan.per_device || []).reduce((n, p) => n + (p.publish || 0), 0);
    const clr = (plan.per_device || []).reduce((n, p) => n + (p.clear || 0), 0);
    let m = `Publish ${plan.per_device?.length || 0} device(s): ${pub} config(s), clear ${clr} stale.\n${plan.msg_count} retained MQTT message(s).`;
    if (plan.errors?.length) m += `\n⚠ ${plan.errors.length} generator error(s).`;
    return m;
  }
  if (action === "clear") {
    return `Clear ${plan.msg_count} retained topic(s) across ${plan.per_device?.length || 0} device(s).\nThis removes them from Home Assistant.`;
  }
  if (action === "restore") {
    return `Restore from ${plan.from || "last backup"}:\nre-publish ${plan.set} topic(s), clear ${plan.clear} added since.`;
  }
  return `${plan.msg_count || 0} message(s).`;
}

// Two-phase action: dry-run to preview the plan, confirm, then execute. The
// grid refreshes itself afterwards via the namespace push (broker echo).
async function runAction(ctx, action, body, danger) {
  let plan;
  try {
    plan = await ctx.api(`/api/discovery/${action}`, {
      method: "POST",
      body: { ...body, dry_run: true },
    });
  } catch (e) {
    ctx.toast && ctx.toast(`${action}: ${e.message}`, "error");
    return;
  }
  if (action !== "restore" && !plan.msg_count) {
    ctx.toast && ctx.toast(`${action}: nothing to do`, "ok");
    return;
  }
  const ok = ctx.confirm
    ? await ctx.confirm({
        title: `${action} — confirm`,
        message: describePlan(action, plan),
        okLabel: action,
        danger: !!danger,
      })
    : true;
  if (!ok) return;
  try {
    const res = await ctx.api(`/api/discovery/${action}`, {
      method: "POST",
      body: { ...body, dry_run: false },
    });
    const note = res.executed
      ? `done${res.backup ? " · backup saved" : ""}`
      : res.error || "nothing to do";
    ctx.toast && ctx.toast(`${action}: ${note}`, "ok");
  } catch (e) {
    ctx.toast && ctx.toast(`${action}: ${e.message}`, "error");
  }
}

function renderCounts(data) {
  const wrap = el("div", "flex flex-wrap gap-2 mb-3");
  const counts = data.counts || {};
  for (const [key, label] of CATEGORIES.map(([k, l]) => [k, l])) {
    const n = counts[key] || 0;
    if (!n && key !== "perfect") continue;
    const chip = el(
      "span",
      `px-2 py-1 rounded text-xs font-medium ${CHIP[key]}`,
      `${label}: ${n}`,
    );
    wrap.appendChild(chip);
  }
  const src = el(
    "span",
    "px-2 py-1 rounded text-xs bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400 ml-auto",
    `scheme: ${data.config_source || "?"} · retained: ${data.retained_topics ?? 0}`,
  );
  wrap.appendChild(src);
  return wrap;
}

function renderToolbar(ctx, data) {
  const bar = el("div", "flex flex-wrap gap-2 mb-3 items-center");
  const devices = data.devices || [];
  const syncIds = devices.filter((d) => NEEDS_SYNC.has(d.category)).map((d) => d.id);
  const allIds = devices.filter((d) => d.category !== "orphans").map((d) => d.id);

  const publishSync = btn(
    `Publish needing sync (${syncIds.length})`,
    BTN_PRIMARY,
    () => runAction(ctx, "publish", { ids: syncIds }, false),
  );
  publishSync.disabled = syncIds.length === 0;
  bar.appendChild(publishSync);

  const clearAll = btn("Clear all", BTN_DANGER, () =>
    runAction(ctx, "clear", { ids: allIds }, true),
  );
  clearAll.disabled = allIds.length === 0;
  bar.appendChild(clearAll);

  bar.appendChild(
    btn("Restore last", BTN_GHOST, () => runAction(ctx, "restore", {}, true)),
  );
  return bar;
}

function renderGrid(ctx, data) {
  const devices = data.devices || [];
  if (!devices.length) {
    return el(
      "div",
      "text-sm text-slate-500 dark:text-slate-400 py-8 text-center",
      "No devices to show yet.",
    );
  }
  const table = el("table", "w-full text-sm");
  const thead = el("thead", "text-left text-slate-500 dark:text-slate-400");
  const htr = el("tr");
  for (const h of ["Status", "Device", "ID", "matched / expected", "diff", ""]) {
    htr.appendChild(el("th", "py-1 pr-3 font-medium", h));
  }
  thead.appendChild(htr);
  table.appendChild(thead);

  const tbody = el("tbody");
  for (const d of devices) {
    const tr = el(
      "tr",
      "border-t border-slate-100 dark:border-slate-800 align-top",
    );
    const status = el("td", "py-1 pr-3");
    status.appendChild(
      el(
        "span",
        `px-2 py-0.5 rounded text-xs font-medium ${CHIP[d.category] || COLOR.slate}`,
        LABEL[d.category] || d.category,
      ),
    );
    tr.appendChild(status);
    tr.appendChild(el("td", "py-1 pr-3", d.name || ""));
    tr.appendChild(
      el("td", "py-1 pr-3 font-mono text-xs text-slate-500 dark:text-slate-400", d.id || ""),
    );
    if (d.category === "orphans") {
      tr.appendChild(el("td", "py-1 pr-3 text-slate-400", "—"));
      tr.appendChild(
        el("td", "py-1 pr-3 text-xs text-slate-500", (d.topics || []).join(", ")),
      );
      tr.appendChild(el("td", "py-1 pr-3"));
    } else {
      tr.appendChild(
        el("td", "py-1 pr-3", `${d.matched ?? 0} / ${d.expected ?? 0}`),
      );
      const parts = [];
      if (d.mismatched) parts.push(`~${d.mismatched}`);
      if (d.missing) parts.push(`-${d.missing}`);
      if (d.unexpected) parts.push(`+${d.unexpected}`);
      tr.appendChild(
        el("td", "py-1 pr-3 text-xs text-slate-500", parts.join("  ") || "—"),
      );
      const actions = el("td", "py-1 pr-3 whitespace-nowrap");
      const sp = el("span", "flex gap-1");
      sp.appendChild(
        btn("Publish", BTN_GHOST, () => runAction(ctx, "publish", { ids: [d.id] }, false)),
      );
      sp.appendChild(
        btn("Clear", BTN_GHOST, () => runAction(ctx, "clear", { ids: [d.id] }, true)),
      );
      actions.appendChild(sp);
      tr.appendChild(actions);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  return table;
}

function renderErrors(data) {
  const errs = data.errors || [];
  if (!errs.length) return null;
  const box = el(
    "div",
    "mt-3 p-2 rounded bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-200 text-xs",
  );
  box.appendChild(el("div", "font-medium mb-1", `Generator errors (${errs.length})`));
  for (const e of errs) box.appendChild(el("div", "font-mono", e));
  return box;
}

export async function mount(rootEl, ctx) {
  rootEl.innerHTML = "";
  const container = el("div", "p-2");
  const heading = el("div", "flex items-center gap-2 mb-3");
  heading.appendChild(el("h2", "text-base font-semibold", "Home Assistant Discovery"));
  container.appendChild(heading);
  const body = el("div");
  container.appendChild(body);
  rootEl.appendChild(container);

  function paint(data) {
    body.innerHTML = "";
    if (!data) {
      body.appendChild(
        el("div", "text-sm text-slate-500 py-8 text-center", "Waiting for discovery state…"),
      );
      return;
    }
    body.appendChild(renderCounts(data));
    body.appendChild(renderToolbar(ctx, data));
    body.appendChild(renderGrid(ctx, data));
    const errs = renderErrors(data);
    if (errs) body.appendChild(errs);
  }

  // Seed from the WS snapshot if it already carries our namespace, else fetch.
  const snap = ctx.getState && ctx.getState();
  let initial = snap && snap.plugins && snap.plugins.discovery;
  paint(initial || null);
  if (!initial) {
    try {
      paint(await ctx.api("/api/discovery/status"));
    } catch (e) {
      ctx.toast && ctx.toast(`discovery: ${e.message}`, "error");
    }
  }

  // Live updates: re-paint whenever our namespace changes in the WS snapshot.
  const unsub = ctx.onState((s) => {
    const d = s && s.plugins && s.plugins.discovery;
    if (d) paint(d);
  });
  return unsub; // host may call this on unmount (future-proofing)
}
