// HA Discovery plugin page (read-only MVP).
//
// Mounted by rustuya-manager's plugin host via mount(rootEl, ctx). `ctx` is the
// host-agnostic surface: getState(), onState(cb), api(path), toast, confirm.
//
// We render a per-device status grid from the discovery plugin's state
// namespace (snapshot.plugins.discovery), seeded by an initial fetch of
// /api/discovery/status so the grid shows even before the first WS push. No
// mutating controls here — publish/clear is a later milestone.

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

function renderGrid(data) {
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
  for (const h of ["Status", "Device", "ID", "matched / expected", "diff"]) {
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
  heading.appendChild(
    el("span", "text-xs text-slate-400", "read-only"),
  );
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
    body.appendChild(renderGrid(data));
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
