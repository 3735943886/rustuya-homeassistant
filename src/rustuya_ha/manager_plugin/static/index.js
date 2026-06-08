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

function renderCounts(data, view, rerender) {
  const wrap = el("div", "flex flex-wrap gap-2 mb-3 items-center");
  const counts = data.counts || {};
  for (const [key, label] of CATEGORIES.map(([k, l]) => [k, l])) {
    const n = counts[key] || 0;
    if (!n && key !== "perfect") continue;
    const active = view.category === key;
    const chip = btn(
      `${label}: ${n}`,
      `px-2 py-1 rounded text-xs font-medium ${CHIP[key]} ${
        active ? "ring-2 ring-slate-500 dark:ring-slate-300" : ""
      }`,
      () => {
        view.category = active ? null : key; // toggle filter
        rerender();
      },
    );
    chip.title = active ? "click to clear filter" : `filter to ${label}`;
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

  const sort = el(
    "select",
    "px-2 py-1 rounded border border-slate-300 dark:border-slate-600 bg-transparent text-sm",
  );
  for (const [val, lbl] of [["category", "sort: status"], ["name", "sort: name"], ["id", "sort: id"]]) {
    const o = el("option", null, lbl);
    o.value = val;
    if (view.sort === val) o.selected = true;
    sort.appendChild(o);
  }
  sort.addEventListener("change", () => {
    view.sort = sort.value;
    rerender();
  });
  bar.appendChild(sort);

  if (view.category || view.search) {
    bar.appendChild(
      btn("clear filters", BTN_GHOST, () => {
        view.category = null;
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
  let out = devices;
  if (view.category) out = out.filter((d) => d.category === view.category);
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

function renderDetailPanel(d) {
  const wrap = el("div", "py-2 pl-2 text-xs space-y-2");
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

// A data cell that reflows for mobile: a normal <td> on `sm`+ screens, but a
// labelled flex row on narrow screens, where the table collapses into per-
// device cards and the <thead> is hidden (so each cell carries its own label).
// `content` may be a string or a pre-built Node.
function cell(label, content) {
  const td = el(
    "td",
    "block sm:table-cell py-1 sm:pr-3 max-sm:flex max-sm:items-baseline max-sm:justify-between max-sm:gap-3",
  );
  if (label) {
    td.appendChild(
      el("span", "sm:hidden shrink-0 text-slate-400 dark:text-slate-500 font-medium", label),
    );
  }
  if (content instanceof Node) td.appendChild(content);
  else td.appendChild(el("span", "max-sm:text-right max-sm:break-all", content == null ? "" : String(content)));
  return td;
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
  // Below `sm` the table reflows into per-device cards (each <tr> a card, each
  // <td> a labelled row); at `sm`+ it's a normal table that can scroll if the
  // 7 columns ever exceed the panel width.
  const table = el("table", "w-full text-sm max-sm:block");
  const thead = el("thead", "hidden sm:table-header-group text-left text-slate-500 dark:text-slate-400");
  const htr = el("tr");
  for (const h of ["", "Status", "Device", "ID", "matched / expected", "diff", ""]) {
    htr.appendChild(el("th", "py-1 pr-3 font-medium", h));
  }
  thead.appendChild(htr);
  table.appendChild(thead);

  const tbody = el("tbody", "max-sm:block");
  for (const d of devices) {
    const expandable = !!d.detail;
    const isOpen = view.expanded.has(d.id);
    const tr = el(
      "tr",
      "align-top block sm:table-row sm:border-t border-slate-100 dark:border-slate-800 " +
        "max-sm:rounded-lg max-sm:border max-sm:border-slate-200 max-sm:dark:border-slate-700 " +
        "max-sm:bg-white max-sm:dark:bg-slate-800/60 max-sm:p-3 max-sm:mb-2" +
        (expandable ? " cursor-pointer" : ""),
    );
    if (expandable) {
      tr.addEventListener("click", (ev) => {
        if (ev.target.tagName === "BUTTON") return; // don't toggle when hitting an action
        if (isOpen) view.expanded.delete(d.id);
        else view.expanded.add(d.id);
        rerender();
      });
    }

    // chevron / expand toggle — its own column on desktop; on mobile it rides
    // alongside the status chip (the caret column is hidden there).
    const caret = el(
      "td",
      "hidden sm:table-cell py-1 pr-2 text-slate-400 select-none w-4",
      expandable ? (isOpen ? "▾" : "▸") : "",
    );
    tr.appendChild(caret);

    const status = el(
      "td",
      "block sm:table-cell py-1 sm:pr-3 max-sm:flex max-sm:items-center max-sm:justify-between max-sm:mb-1",
    );
    status.appendChild(
      el(
        "span",
        `px-2 py-0.5 rounded text-xs font-medium ${CHIP[d.category] || COLOR.slate}`,
        LABEL[d.category] || d.category,
      ),
    );
    if (expandable) {
      status.appendChild(el("span", "sm:hidden text-slate-400 select-none", isOpen ? "▾" : "▸"));
    }
    tr.appendChild(status);
    tr.appendChild(cell("Device", d.name || ""));
    tr.appendChild(
      cell(
        "ID",
        el("span", "font-mono text-xs text-slate-500 dark:text-slate-400 max-sm:text-right max-sm:break-all", d.id || ""),
      ),
    );
    if (d.category === "orphans") {
      tr.appendChild(cell("matched / expected", "—"));
      tr.appendChild(
        cell("diff", el("span", "text-xs text-slate-500 max-sm:text-right max-sm:break-all", (d.topics || []).join(", "))),
      );
      tr.appendChild(el("td", "hidden sm:table-cell py-1 pr-3"));
    } else {
      tr.appendChild(cell("matched / expected", `${d.matched ?? 0} / ${d.expected ?? 0}`));
      const parts = [];
      if (d.mismatched) parts.push(`~${d.mismatched}`);
      if (d.missing) parts.push(`-${d.missing}`);
      if (d.unexpected) parts.push(`+${d.unexpected}`);
      tr.appendChild(
        cell("diff", el("span", "text-xs text-slate-500 max-sm:text-right max-sm:break-all", parts.join("  ") || "—")),
      );
      const actions = el("td", "block sm:table-cell py-1 sm:pr-3 max-sm:mt-2 whitespace-nowrap");
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

    if (expandable && isOpen) {
      const dtr = el("tr", "block sm:table-row bg-slate-50 dark:bg-slate-800/40 max-sm:rounded-lg max-sm:-mt-1 max-sm:mb-2");
      const dcell = el("td", "block sm:table-cell px-2 max-sm:px-3 max-sm:pb-2");
      dcell.colSpan = 7;
      dcell.appendChild(renderDetailPanel(d));
      dtr.appendChild(dcell);
      tbody.appendChild(dtr);
    }
  }
  table.appendChild(tbody);
  const scroller = el("div", "overflow-x-auto");
  scroller.appendChild(table);
  return scroller;
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
  const view = {
    search: "", category: null, sort: "category", expanded: new Set(),
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
    body.appendChild(renderCounts(data, view, render));
    body.appendChild(renderControls(data, view, render));
    body.appendChild(renderToolbar(ctx, data));
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
