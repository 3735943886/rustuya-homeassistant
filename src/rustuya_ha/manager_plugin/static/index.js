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

// [key, label, color] — color drives the filter-tab pill and must match the
// card's left-stripe color (CAT_STYLE) so a category reads as one hue.
const CATEGORIES = [
  ["perfect", "Perfect", "emerald"],
  ["mismatched_payload", "Mismatched", "amber"],
  ["partially_missing", "Partial", "yellow"],
  ["pure_missing", "Missing", "sky"],
  ["unexpected_topics", "Unexpected", "purple"],
  ["no_dp_config", "No DP config", "slate"],
  ["orphans", "Orphans", "rose"],
];
const LABEL = Object.fromEntries(CATEGORIES.map(([k, l]) => [k, l]));

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

// Categories with retained topics of ours that a per-device clear can remove.
// pure_missing has nothing published yet; no_dp_config we never generate for.
const CAN_CLEAR = new Set([
  "perfect",
  "mismatched_payload",
  "partially_missing",
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

const ALL_CAT_KEYS = CATEGORIES.map(([k]) => k);

// Filter-tab styling mirroring the manager's: a colored pill per category that
// doubles as count + toggle. Active = filled saturated; idle = faint tint.
const FILTER_STYLES = {
  all:     { active: "bg-slate-700 text-white border-slate-700 dark:bg-slate-200 dark:text-slate-900 dark:border-slate-200", idle: "bg-white text-slate-700 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-600" },
  emerald: { active: "bg-emerald-600 text-white border-emerald-600", idle: "bg-emerald-50 text-emerald-700 border-emerald-300 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-700" },
  amber:   { active: "bg-amber-600 text-white border-amber-600",     idle: "bg-amber-50 text-amber-700 border-amber-300 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-700" },
  yellow:  { active: "bg-yellow-500 text-white border-yellow-500",   idle: "bg-yellow-50 text-yellow-700 border-yellow-300 dark:bg-yellow-900/30 dark:text-yellow-300 dark:border-yellow-700" },
  rose:    { active: "bg-rose-600 text-white border-rose-600",       idle: "bg-rose-50 text-rose-700 border-rose-300 dark:bg-rose-900/30 dark:text-rose-300 dark:border-rose-700" },
  sky:     { active: "bg-sky-600 text-white border-sky-600",         idle: "bg-sky-50 text-sky-700 border-sky-300 dark:bg-sky-900/30 dark:text-sky-300 dark:border-sky-700" },
  purple:  { active: "bg-purple-600 text-white border-purple-600",   idle: "bg-purple-50 text-purple-700 border-purple-300 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-700" },
  slate:   { active: "bg-slate-600 text-white border-slate-600",     idle: "bg-slate-50 text-slate-600 border-slate-300 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-600" },
};

function filterPill(label, count, on, color, onClick) {
  const st = FILTER_STYLES[color] || FILTER_STYLES.slate;
  return btn(`${label}${count ? ` ${count}` : ""}`, `px-2 py-1 rounded border ${on ? st.active : st.idle}`, onClick);
}

// Manager-style category filter: multi-select colored tabs + an "all" pill that
// toggles every category at once. Tabs with 0 fade so the eye lands on the
// actionable ones. `view.filters` is the set of enabled category keys.
function renderFilterTabs(data, view, rerender) {
  const wrap = el("div", "flex flex-wrap gap-1 mb-3 text-xs");
  const counts = data.counts || {};
  const allOn = ALL_CAT_KEYS.every((k) => view.filters.has(k));
  const total = ALL_CAT_KEYS.reduce((n, k) => n + (counts[k] || 0), 0);
  wrap.appendChild(
    filterPill("all", total, allOn, "all", () => {
      if (allOn) view.filters.clear();
      else ALL_CAT_KEYS.forEach((k) => view.filters.add(k));
      rerender();
    }),
  );
  for (const [key, label, color] of CATEGORIES) {
    const n = counts[key] || 0;
    const on = view.filters.has(key);
    const pill = filterPill(label, n, on, color, () => {
      if (on) view.filters.delete(key);
      else view.filters.add(key);
      rerender();
    });
    if (n === 0 && !on) pill.classList.add("opacity-50");
    pill.title = on ? `hide ${label}` : `show ${label}`;
    wrap.appendChild(pill);
  }
  return wrap;
}

function renderControls(data, view, rerender) {
  const bar = el("div", "flex flex-wrap gap-2 mb-3 items-center");
  const search = el(
    "input",
    "px-2 py-1 rounded border border-slate-300 dark:border-slate-600 bg-transparent text-sm w-56",
  );
  search.type = "search";
  search.placeholder = "search name / id…";
  search.value = view.search;
  search.dataset.keepFocus = "search";
  search.addEventListener("input", () => {
    view.search = search.value;
    rerender();
  });
  bar.appendChild(search);

  // Manager-style: the "sort by" label is folded into the select via an
  // <optgroup> header (shown when open), so no separate label row is needed.
  const sort = el(
    "select",
    "text-xs px-2 py-1 rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100",
  );
  sort.title = "Sort devices";
  const og = el("optgroup");
  og.label = "sort by";
  for (const [val, lbl] of [["category", "category"], ["name", "name"], ["id", "id"]]) {
    const o = el("option", null, lbl);
    o.value = val;
    if (view.sort === val) o.selected = true;
    og.appendChild(o);
  }
  sort.appendChild(og);
  sort.addEventListener("change", () => {
    view.sort = sort.value;
    rerender();
  });
  bar.appendChild(sort);

  const allOn = ALL_CAT_KEYS.every((k) => view.filters.has(k));
  if (!allOn || view.search) {
    bar.appendChild(
      btn("clear filters", BTN_GHOST, () => {
        view.filters = new Set(ALL_CAT_KEYS);
        view.search = "";
        rerender();
      }),
    );
  }
  return bar;
}

// Order used when sorting by status (worst first, so problems float up).
const CAT_ORDER = [
  "mismatched_payload", "partially_missing", "pure_missing",
  "unexpected_topics", "orphans", "no_dp_config", "perfect",
];

function applyView(devices, view) {
  let out = devices.filter((d) => view.filters.has(d.category));
  if (view.search) {
    const q = view.search.toLowerCase();
    out = out.filter(
      (d) =>
        String(d.name || "").toLowerCase().includes(q) ||
        String(d.id || "").toLowerCase().includes(q),
    );
  }
  const by = view.sort;
  const catRank = (c) => {
    const i = CAT_ORDER.indexOf(c);
    return i < 0 ? CAT_ORDER.length : i;
  };
  return [...out].sort((a, b) => {
    if (by === "name") return String(a.name).localeCompare(String(b.name));
    if (by === "id") return String(a.id).localeCompare(String(b.id));
    return catRank(a.category) - catRank(b.category) || String(a.id).localeCompare(String(b.id));
  });
}

// One small colored button in the sync bar, styled like the manager's.
function barBtn(label, variant, title, onClick) {
  const styles = {
    sky:   "border-sky-300 dark:border-sky-700 bg-sky-50 dark:bg-sky-900/40 hover:bg-sky-100 dark:hover:bg-sky-900/60 text-sky-800 dark:text-sky-200",
    rose:  "border-rose-300 dark:border-rose-700 bg-rose-50 dark:bg-rose-900/40 hover:bg-rose-100 dark:hover:bg-rose-900/60 text-rose-800 dark:text-rose-200",
    slate: "border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 hover:bg-slate-100 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-200",
    dark:  "border-transparent bg-slate-900 hover:bg-slate-800 dark:bg-slate-200 dark:text-slate-900 dark:hover:bg-white text-white font-medium",
  }[variant];
  const b = btn(label, `text-xs px-2 py-1 rounded border whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed ${styles}`, onClick);
  b.title = title;
  return b;
}

// Section tints for the apply modal — border + faint wash per scope.
const SECTION_TINT = {
  sky:  "border-sky-200 dark:border-sky-700 bg-sky-50 dark:bg-sky-900/20",
  rose: "border-rose-200 dark:border-rose-700 bg-rose-50 dark:bg-rose-900/20",
};
const ROW_STATUS = {
  pending: ["pending", "text-slate-400 dark:text-slate-500"],
  ok:      ["✓", "text-emerald-600 dark:text-emerald-400"],
  error:   ["✘", "text-rose-600 dark:text-rose-400"],
};

// Manager-style bulk modal: lists the devices to publish and/or the orphan
// topics to clear with per-row checkboxes (+ select-all per section) so the
// user reviews and trims the selection before applying. Self-contained (the
// plugin can't reach the host's modal), appended to <body>.
function openApplyModal(ctx, data, opts) {
  const devices = data.devices || [];
  const pub = opts.publish
    ? devices.filter((d) => NEEDS_SYNC.has(d.category)).map((d) => ({ id: d.id, name: d.name, category: d.category, checked: true, status: "pending" }))
    : [];
  const clr = opts.clear
    ? devices.filter((d) => d.category === "orphans").flatMap((d) => (d.topics || []).map((t) => ({ topic: t, checked: true, status: "pending" })))
    : [];
  if (!pub.length && !clr.length) {
    ctx.toast && ctx.toast("nothing to do", "ok");
    return;
  }

  let applying = false;
  const overlay = el("div", "fixed inset-0 z-50 bg-black/40 flex items-start justify-center p-4 overflow-y-auto");
  const panel = el("div", "bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 rounded-lg shadow-xl w-full max-w-lg my-8 max-h-[85vh] flex flex-col");
  overlay.appendChild(panel);

  const head = el("div", "px-4 py-3 border-b border-slate-200 dark:border-slate-700 flex items-center gap-2");
  head.appendChild(el("h3", "text-sm font-semibold", opts.publish && opts.clear ? "Apply all" : opts.publish ? "Publish needing sync" : "Clear orphans"));
  const closeX = iconBtn("✕", "Close", () => close());
  closeX.classList.add("ml-auto");
  head.appendChild(closeX);
  panel.appendChild(head);

  const bodyEl = el("div", "p-3 space-y-3 overflow-y-auto");
  panel.appendChild(bodyEl);

  const foot = el("div", "px-4 py-3 border-t border-slate-200 dark:border-slate-700 flex items-center gap-2");
  const progress = el("span", "text-xs text-slate-500 dark:text-slate-400 min-w-0 truncate");
  const cancelBtn = btn("Cancel", `ml-auto ${BTN_GHOST}`, () => close());
  let applyHandler = apply;
  const applyBtn = el("button", BTN_PRIMARY, "Apply");
  applyBtn.type = "button";
  applyBtn.addEventListener("click", () => applyHandler());
  foot.appendChild(progress);
  foot.appendChild(cancelBtn);
  foot.appendChild(applyBtn);
  panel.appendChild(foot);

  function close() {
    if (applying) return;
    document.removeEventListener("keydown", onKey);
    overlay.remove();
  }
  function onKey(e) { if (e.key === "Escape") close(); }
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

  function section(label, color, items, fillRow) {
    if (!items.length) return;
    const sec = el("div", `border rounded ${SECTION_TINT[color] || ""}`);
    const h = el("div", "px-3 py-2 flex items-center gap-2 border-b border-black/5 dark:border-white/10");
    h.appendChild(el("strong", "text-sm", label));
    h.appendChild(el("span", "text-xs", String(items.length)));
    const allLbl = el("label", "ml-auto text-xs flex items-center gap-1 cursor-pointer");
    const allCb = el("input", "rounded");
    allCb.type = "checkbox";
    allCb.checked = items.every((i) => i.checked);
    allCb.indeterminate = items.some((i) => i.checked) && !allCb.checked;
    allCb.disabled = applying;
    allCb.addEventListener("change", () => { items.forEach((i) => (i.checked = allCb.checked)); rerenderBody(); });
    allLbl.appendChild(allCb);
    allLbl.appendChild(el("span", null, "select all"));
    h.appendChild(allLbl);
    sec.appendChild(h);

    const ul = el("div", "divide-y divide-black/5 dark:divide-white/10 bg-white/60 dark:bg-slate-800/40");
    for (const it of items) {
      const row = el("label", "px-3 py-2 flex items-center gap-2 text-sm cursor-pointer");
      const cb = el("input", "rounded shrink-0");
      cb.type = "checkbox";
      cb.checked = it.checked;
      cb.disabled = applying;
      cb.addEventListener("change", () => { it.checked = cb.checked; allCb.checked = items.every((i) => i.checked); allCb.indeterminate = items.some((i) => i.checked) && !allCb.checked; updateApply(); });
      row.appendChild(cb);
      const txt = el("span", "flex-1 min-w-0 break-all");
      fillRow(txt, it);
      row.appendChild(txt);
      const [glyph, gcls] = ROW_STATUS[it.status];
      row.appendChild(el("span", `text-xs shrink-0 ${gcls}`, glyph));
      ul.appendChild(row);
    }
    sec.appendChild(ul);
    bodyEl.appendChild(sec);
  }

  function rerenderBody() {
    bodyEl.innerHTML = "";
    section("Publish", "sky", pub, (txt, it) => {
      txt.appendChild(el("span", "font-medium", it.name || it.id));
      txt.appendChild(el("span", "ml-2 font-mono text-[11px] text-slate-400 dark:text-slate-500", it.id));
    });
    section("Clear orphans", "rose", clr, (txt, it) => {
      txt.appendChild(el("span", "font-mono text-xs", it.topic));
    });
    updateApply();
  }

  function selectedCount() { return pub.filter((i) => i.checked).length + clr.filter((i) => i.checked).length; }
  function updateApply() {
    const n = selectedCount();
    applyBtn.textContent = applying ? "Applying…" : n ? `Apply ${n}` : "Apply";
    applyBtn.disabled = applying || n === 0;
  }

  async function apply() {
    if (applying) return;
    const ids = pub.filter((i) => i.checked).map((i) => i.id);
    const topics = clr.filter((i) => i.checked).map((i) => i.topic);
    if (!ids.length && !topics.length) return;
    applying = true;
    cancelBtn.disabled = true;
    closeX.disabled = true;
    rerenderBody();
    let pubOk = true, clrOk = true, errMsg = "";
    try { if (ids.length) await ctx.api("/api/discovery/publish", { method: "POST", body: { ids, dry_run: false } }); }
    catch (e) { pubOk = false; errMsg = e.message; }
    try { if (topics.length) await ctx.api("/api/discovery/clear", { method: "POST", body: { topics, dry_run: false } }); }
    catch (e) { clrOk = false; errMsg = e.message; }
    for (const i of pub) if (i.checked) i.status = pubOk ? "ok" : "error";
    for (const i of clr) if (i.checked) i.status = clrOk ? "ok" : "error";
    applying = false;
    cancelBtn.disabled = false;
    closeX.disabled = false;
    rerenderBody();
    const okAll = pubOk && clrOk;
    progress.textContent = okAll ? "Applied." : `Error: ${errMsg}`;
    ctx.toast && ctx.toast(okAll ? "apply: done" : `apply: ${errMsg}`, okAll ? "ok" : "error");
    applyHandler = close;
    applyBtn.textContent = "Done";
    applyBtn.disabled = false;
  }

  rerenderBody();
  document.body.appendChild(overlay);
}

// Manager-style bulk-action bar: scoped buttons (hidden when their scope is
// empty) + an "Apply all" primary on the right. Also carries the scheme badge.
function renderSyncBar(ctx, data, view) {
  const devices = data.devices || [];
  const syncIds = devices.filter((d) => NEEDS_SYNC.has(d.category)).map((d) => d.id);
  const orphanTopics = devices.filter((d) => d.category === "orphans").flatMap((d) => d.topics || []);

  const bar = el(
    "div",
    "flex flex-wrap items-center gap-1.5 bg-white dark:bg-slate-800 " +
      "border border-slate-200 dark:border-slate-700 rounded-lg px-2 py-1.5 mb-3",
  );
  bar.appendChild(
    el(
      "span",
      "text-[11px] text-slate-400 dark:text-slate-500 whitespace-nowrap mr-1",
      `scheme: ${data.config_source || "?"} · retained: ${data.retained_topics ?? 0}`,
    ),
  );
  if (syncIds.length) {
    bar.appendChild(barBtn(`Publish ${syncIds.length}`, "sky", "Review + publish devices needing sync", () => openApplyModal(ctx, data, { publish: true })));
  }
  if (orphanTopics.length) {
    bar.appendChild(barBtn(`Clear orphans ${orphanTopics.length}`, "rose", "Review + clear orphan retained topics", () => openApplyModal(ctx, data, { clear: true })));
  }
  bar.appendChild(barBtn("Restore", "slate", "Restore last backup", () => runAction(ctx, "restore", {}, true)));

  const apply = barBtn("Apply all", "dark", "Review publish + clear orphans, then apply", () => openApplyModal(ctx, data, { publish: true, clear: true }));
  apply.classList.add("ml-auto");
  apply.disabled = !syncIds.length && !orphanTopics.length;
  bar.appendChild(apply);
  return bar;
}

function renderDetailPanel(d) {
  const wrap = el(
    "div",
    "mt-2 pt-2 border-t border-slate-200/70 dark:border-slate-700/70 text-xs space-y-2",
  );
  const det = d.detail || {};
  if (det.mismatched?.length) {
    for (const m of det.mismatched) {
      const block = el("div");
      block.appendChild(
        el("div", "font-mono text-slate-500 dark:text-slate-400 mb-1 break-all", m.topic),
      );
      for (const f of m.fields || []) {
        const row = el("div", "pl-3 mb-1 break-all");
        row.appendChild(el("span", "font-medium", `${f.key}: `));
        row.appendChild(
          el("span", "text-rose-600 dark:text-rose-400 line-through", JSON.stringify(f.actual)),
        );
        row.appendChild(el("span", "text-slate-400", "  →  "));
        row.appendChild(
          el("span", "text-emerald-600 dark:text-emerald-400", JSON.stringify(f.expected)),
        );
        block.appendChild(row);
      }
      wrap.appendChild(block);
    }
  }
  const topicList = (label, topics, cls) => {
    if (!topics?.length) return;
    const b = el("div");
    b.appendChild(el("div", `font-medium ${cls}`, `${label} (${topics.length})`));
    for (const t of topics) b.appendChild(el("div", "font-mono pl-3 text-slate-500 break-all", t));
    wrap.appendChild(b);
  };
  topicList("missing", det.missing, "text-rose-600 dark:text-rose-400");
  topicList("unexpected", det.unexpected, "text-purple-600 dark:text-purple-400");
  return wrap;
}

// Per-category card colors, mirroring rustuya-manager's device-card grammar:
// a left-edge stripe carries the category (so it never needs its own column)
// and a faint body wash echoes it. The top counts chips act as the legend.
const CAT_STYLE = {
  perfect:            { edge: "border-l-emerald-400 dark:border-l-emerald-500", wash: "bg-white dark:bg-slate-800" },
  mismatched_payload: { edge: "border-l-amber-400 dark:border-l-amber-500",     wash: "bg-amber-50 dark:bg-amber-900/30" },
  partially_missing:  { edge: "border-l-yellow-400 dark:border-l-yellow-500",   wash: "bg-yellow-50 dark:bg-yellow-900/30" },
  pure_missing:       { edge: "border-l-sky-400 dark:border-l-sky-500",         wash: "bg-sky-50 dark:bg-sky-900/30" },
  unexpected_topics:  { edge: "border-l-purple-400 dark:border-l-purple-500",   wash: "bg-purple-50 dark:bg-purple-900/30" },
  no_dp_config:       { edge: "border-l-slate-300 dark:border-l-slate-500",     wash: "bg-white dark:bg-slate-800" },
  orphans:            { edge: "border-l-rose-400 dark:border-l-rose-500",       wash: "bg-rose-50 dark:bg-rose-900/30" },
};
const CAT_FALLBACK = { edge: "border-l-slate-200 dark:border-l-slate-600", wash: "bg-white dark:bg-slate-800" };

// Small square icon button matching the manager's iconButton so the HA tab's
// actions read the same as the host's device cards. stopPropagation keeps a
// click from also toggling the card's expand.
function iconBtn(glyph, title, onClick, variant) {
  const styles = {
    default:       "border-slate-200 dark:border-slate-600 bg-white dark:bg-slate-700 hover:bg-slate-100 dark:hover:bg-slate-600 text-slate-500 dark:text-slate-300",
    danger:        "border-rose-200 dark:border-rose-800 bg-white dark:bg-slate-700 hover:bg-rose-50 dark:hover:bg-rose-900/40 text-rose-600 dark:text-rose-400",
    "danger-fill": "border-rose-300 dark:border-rose-700 bg-rose-100 dark:bg-rose-900/70 hover:bg-rose-200 dark:hover:bg-rose-800 text-rose-700 dark:text-rose-200",
  }[variant || "default"];
  const b = el(
    "button",
    `w-5 h-5 inline-flex items-center justify-center rounded border text-xs leading-none ` +
      `disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-white dark:disabled:hover:bg-slate-700 ${styles}`,
    glyph,
  );
  b.type = "button";
  b.title = title;
  b.addEventListener("click", (ev) => { ev.stopPropagation(); onClick(); });
  return b;
}

// One device, as a manager-style card: name on top, id beneath it, the
// category as the left stripe + wash, a compact match metric and +/− actions
// in the right cluster, and the diff detail in the expanded body.
function deviceCard(ctx, d, view, rerender) {
  const cat = CAT_STYLE[d.category] || CAT_FALLBACK;
  const expandable = !!d.detail;
  const isOrphan = d.category === "orphans";
  const toggle = () => {
    if (view.expanded.has(d.id)) view.expanded.delete(d.id);
    else view.expanded.add(d.id);
    rerender();
  };
  const isOpen = view.expanded.has(d.id);

  const card = el(
    "div",
    `${cat.wash} rounded-lg border border-slate-200 dark:border-slate-700 ` +
      `border-l-4 dark:border-l-[6px] ${cat.edge} p-3 mb-2` +
      (expandable ? " cursor-pointer" : ""),
  );
  card.title = LABEL[d.category] || d.category; // category lives on the stripe; hover to read it
  if (expandable) {
    card.addEventListener("click", (ev) => {
      if (ev.target.closest("button, input, a")) return;
      toggle();
    });
  }

  // ── header row 1: name (or orphan topic) + right cluster ──
  const top = el("div", "flex items-center gap-2 min-w-0");
  top.appendChild(
    el(
      "span",
      isOrphan
        ? "font-mono text-xs text-slate-700 dark:text-slate-300 break-all min-w-0"
        : "font-medium text-sm text-slate-900 dark:text-slate-100 truncate min-w-0",
      isOrphan ? ((d.topics || []).join(", ") || "(orphan)") : (d.name || d.id || ""),
    ),
  );
  const right = el("span", "ml-auto flex items-center gap-1.5 shrink-0");
  if (isOrphan) {
    // Orphans have no device to publish; the only action is clearing the stray
    // retained topic(s) — cleared by explicit topic (their owner id is unknown).
    right.appendChild(
      iconBtn("🗑", "Clear retained topic(s)", () => runAction(ctx, "clear", { topics: d.topics || [] }, true), "danger-fill"),
    );
  } else {
    const ok = (d.matched ?? 0) === (d.expected ?? 0);
    const metric = el(
      "span",
      `text-[11px] font-mono ${ok ? "text-emerald-600 dark:text-emerald-400" : "text-slate-500 dark:text-slate-400"}`,
      `${d.matched ?? 0}/${d.expected ?? 0}`,
    );
    metric.title = "matched / expected entities";
    right.appendChild(metric);
    // + disabled when nothing needs publishing (perfect / no_dp_config);
    // − disabled when nothing of ours is retained (pure_missing / no_dp_config).
    const pub = iconBtn("+", "Publish", () => runAction(ctx, "publish", { ids: [d.id] }, false));
    pub.disabled = !NEEDS_SYNC.has(d.category);
    right.appendChild(pub);
    const clr = iconBtn("−", "Clear", () => runAction(ctx, "clear", { ids: [d.id] }, true), "danger");
    clr.disabled = !CAN_CLEAR.has(d.category);
    right.appendChild(clr);
  }
  if (expandable) {
    right.appendChild(iconBtn(isOpen ? "▾" : "▸", isOpen ? "Collapse" : "Expand", toggle));
  }
  top.appendChild(right);
  card.appendChild(top);

  // ── header row 2: id (under name) + diff summary; skipped for orphans whose
  // identity is the topic already shown above. ──
  if (!isOrphan) {
    const bottom = el("div", "flex items-center gap-2 mt-0.5 min-w-0");
    bottom.appendChild(
      d.id && d.id !== d.name
        ? el("span", "font-mono text-[11px] text-slate-400 dark:text-slate-500 truncate min-w-0", d.id)
        : el("span", "min-w-0"),
    );
    const parts = [];
    if (d.mismatched) parts.push(["~" + d.mismatched, "text-amber-600 dark:text-amber-400"]);
    if (d.missing) parts.push(["-" + d.missing, "text-rose-600 dark:text-rose-400"]);
    if (d.unexpected) parts.push(["+" + d.unexpected, "text-purple-600 dark:text-purple-400"]);
    if (parts.length) {
      const diff = el("span", "ml-auto flex items-center gap-1.5 shrink-0 text-[10px] font-mono");
      for (const [t, c] of parts) diff.appendChild(el("span", c, t));
      bottom.appendChild(diff);
    }
    card.appendChild(bottom);
  }

  if (expandable && isOpen) card.appendChild(renderDetailPanel(d));
  return card;
}

function renderGrid(ctx, data, view, rerender) {
  const all = data.devices || [];
  const devices = applyView(all, view);
  if (!all.length) {
    return el(
      "div",
      "text-sm text-slate-500 dark:text-slate-400 py-8 text-center",
      "No devices to show yet.",
    );
  }
  if (!devices.length) {
    return el(
      "div",
      "text-sm text-slate-500 dark:text-slate-400 py-8 text-center",
      "No devices match the current filter.",
    );
  }
  const list = el("div"); // cards carry their own mb-2
  for (const d of devices) list.appendChild(deviceCard(ctx, d, view, rerender));
  return list;
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

// ── custom converters editor (M5) ────────────────────────────────────────
function jsonPretty(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return "";
  }
}

function selectProduct(view, pid) {
  const c = view.conv;
  c.selected = pid;
  const existing = c.info && c.info.converters ? c.info.converters[pid] : null;
  c.text = existing ? jsonPretty(existing) : jsonPretty({ model: "", dp_meta: {} });
  c.preview = null;
}

async function loadConverters(ctx, view, rerender) {
  view.conv.busy = true;
  rerender();
  try {
    const info = await ctx.api("/api/discovery/converters");
    view.conv.info = info;
    view.conv.loaded = true;
    const pids = (info.products || []).map((p) => p.product_id);
    const keep = view.conv.selected && pids.includes(view.conv.selected)
      ? view.conv.selected
      : pids[0];
    if (keep) selectProduct(view, keep);
  } catch (e) {
    ctx.toast && ctx.toast(`converters: ${e.message}`, "error");
  } finally {
    view.conv.busy = false;
    rerender();
  }
}

function parseOverride(text) {
  const t = text.trim();
  if (t === "" || t === "null") return null; // null = delete the override
  return JSON.parse(t); // throws on malformed JSON (caller toasts)
}

async function convAction(ctx, view, rerender, kind) {
  const c = view.conv;
  if (!c.selected) return;
  let override;
  try {
    override = kind === "delete" ? null : parseOverride(c.text);
  } catch (e) {
    ctx.toast && ctx.toast(`invalid JSON: ${e.message}`, "error");
    return;
  }
  if (kind === "preview") {
    try {
      c.preview = await ctx.api("/api/discovery/converters/preview", {
        method: "POST",
        body: { product_id: c.selected, override },
      });
      rerender();
    } catch (e) {
      ctx.toast && ctx.toast(`preview: ${e.message}`, "error");
    }
    return;
  }
  const msg = kind === "delete"
    ? `Delete the override for ${c.selected}?`
    : `Save the override for ${c.selected}?\nThis changes what Publish emits for its device(s).`;
  const ok = ctx.confirm
    ? await ctx.confirm({ title: `converter ${kind}`, message: msg, okLabel: kind, danger: kind === "delete" })
    : true;
  if (!ok) return;
  try {
    const res = await ctx.api("/api/discovery/converters/save", {
      method: "POST",
      body: { product_id: c.selected, override },
    });
    ctx.toast &&
      ctx.toast(`converter ${res.deleted ? "deleted" : "saved"}${res.backup ? " · backup" : ""}`, "ok");
    await loadConverters(ctx, view, rerender); // refresh has-override + canonical text
  } catch (e) {
    ctx.toast && ctx.toast(`${kind}: ${e.message}`, "error");
  }
}

function renderConvPreview(preview) {
  const box = el("div", "mt-2 p-2 rounded bg-slate-50 dark:bg-slate-800/40 text-xs space-y-2");
  box.appendChild(
    el("div", "font-medium", `Preview — ${preview.product_id} (${(preview.devices || []).length} device(s))`),
  );
  for (const d of preview.devices || []) {
    const b = el("div");
    if (d.error) {
      b.appendChild(el("div", "text-rose-600 dark:text-rose-400", `${d.name} (${d.id}): ${d.error}`));
    } else {
      const topics = Object.keys(d.topics || {});
      b.appendChild(
        el("div", "font-medium", `${d.name} (${d.id}) — ${topics.length} topic(s) · ${d.source || ""}`),
      );
      const pre = el("pre", "font-mono whitespace-pre-wrap break-all text-slate-500 dark:text-slate-400");
      pre.textContent = jsonPretty(d.topics);
      b.appendChild(pre);
    }
    box.appendChild(b);
  }
  return box;
}

function renderConverters(ctx, view, rerender) {
  const c = view.conv;
  const wrap = el("div", "mt-4 border-t border-slate-200 dark:border-slate-700 pt-3");
  wrap.appendChild(
    btn(`${c.open ? "▾" : "▸"} Custom converters`, "text-sm font-semibold mb-2", () => {
      c.open = !c.open;
      if (c.open && !c.loaded) loadConverters(ctx, view, rerender);
      else rerender();
    }),
  );
  if (!c.open) return wrap;
  if (!c.loaded) {
    wrap.appendChild(el("div", "text-xs text-slate-500", c.busy ? "loading…" : ""));
    return wrap;
  }
  const info = c.info || { products: [], converters: {}, save_path: "" };

  const row = el("div", "flex flex-wrap gap-2 items-center mb-2");
  const sel = el(
    "select",
    "px-2 py-1 rounded border border-slate-300 dark:border-slate-600 bg-transparent text-sm",
  );
  if (!info.products.length) {
    sel.appendChild(el("option", null, "(no product_ids in fleet)"));
    sel.disabled = true;
  }
  for (const pr of info.products) {
    const o = el(
      "option",
      null,
      `${pr.product_id} · ${pr.device_ids.length} dev${pr.has_override ? " ✏" : ""}`,
    );
    o.value = pr.product_id;
    if (pr.product_id === c.selected) o.selected = true;
    sel.appendChild(o);
  }
  sel.addEventListener("change", () => {
    selectProduct(view, sel.value);
    rerender();
  });
  row.appendChild(sel);
  wrap.appendChild(row);

  const ta = el(
    "textarea",
    "w-full h-48 font-mono text-xs p-2 rounded border border-slate-300 dark:border-slate-600 bg-transparent",
  );
  ta.value = c.text;
  ta.dataset.keepFocus = "conv-text";
  ta.spellcheck = false;
  ta.placeholder = '{"model": "...", "dp_meta": { "1": { ... } }}  — empty or "null" deletes';
  ta.addEventListener("input", () => {
    c.text = ta.value;
  });
  if (sel.disabled) ta.disabled = true;
  wrap.appendChild(ta);

  const acts = el("div", "flex flex-wrap gap-2 mt-2 items-center");
  const previewBtn = btn("Preview", BTN_GHOST, () => convAction(ctx, view, rerender, "preview"));
  const saveBtn = btn("Save", BTN_PRIMARY, () => convAction(ctx, view, rerender, "save"));
  previewBtn.disabled = saveBtn.disabled = !c.selected;
  acts.appendChild(previewBtn);
  acts.appendChild(saveBtn);
  if (c.selected && info.converters && c.selected in info.converters) {
    acts.appendChild(btn("Delete override", BTN_DANGER, () => convAction(ctx, view, rerender, "delete")));
  }
  acts.appendChild(el("span", "text-xs text-slate-400 ml-auto", `saves to ${info.save_path || "?"}`));
  wrap.appendChild(acts);

  if (c.preview) wrap.appendChild(renderConvPreview(c.preview));
  return wrap;
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

  // View state persists across re-renders (live pushes + filter/sort/expand).
  // `filters` is the set of enabled category keys (manager-style multi-select).
  const view = {
    search: "", filters: new Set(ALL_CAT_KEYS), sort: "category", expanded: new Set(),
    conv: { open: false, loaded: false, info: null, selected: "", text: "", preview: null, busy: false },
  };
  let lastData = null;

  function render() {
    const data = lastData;
    // Preserve focus + caret of the active editable across the full rebuild
    // (search box / converters textarea), keyed by its data-keep-focus id.
    const active = document.activeElement;
    const keep = active && body.contains(active) ? active.dataset.keepFocus : null;
    const selS = keep ? active.selectionStart : null;
    const selE = keep ? active.selectionEnd : null;

    body.innerHTML = "";
    if (!data) {
      body.appendChild(
        el("div", "text-sm text-slate-500 py-8 text-center", "Waiting for discovery state…"),
      );
      return;
    }
    body.appendChild(renderSyncBar(ctx, data, view));
    body.appendChild(renderFilterTabs(data, view, render));
    body.appendChild(renderControls(data, view, render));
    body.appendChild(renderGrid(ctx, data, view, render));
    const errs = renderErrors(data);
    if (errs) body.appendChild(errs);
    body.appendChild(renderConverters(ctx, view, render));

    if (keep) {
      const next = body.querySelector(`[data-keep-focus="${keep}"]`);
      if (next) {
        next.focus();
        try {
          if (selS != null) next.setSelectionRange(selS, selE);
        } catch {
          /* element type may not support selection range */
        }
      }
    }
  }

  function paint(data) {
    if (data) {
      lastData = data;
      // Drop expansion state for ids no longer present.
      const ids = new Set((data.devices || []).map((d) => d.id));
      for (const id of [...view.expanded]) if (!ids.has(id)) view.expanded.delete(id);
    }
    render();
  }

  // Seed from the WS snapshot if it already carries our namespace, else fetch.
  const snap = ctx.getState && ctx.getState();
  const initial = snap && snap.plugins && snap.plugins.discovery;
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
