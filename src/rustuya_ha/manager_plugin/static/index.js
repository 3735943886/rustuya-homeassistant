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

// ── i18n (self-contained, file-based; follows the shell's language) ───────
// Translations live in sibling JSON catalogs under ./locales/: en.json is the
// source of truth + fallback, index.json lists which languages the plugin ships.
// They're fetched relative to this module's URL, so the plugin owns its
// languages outright — adding one is a dropped xx.json plus a line in index.json.
// There is no in-tab picker: the language is the manager shell's global choice
// (ctx.getLang at mount, ctx.onLangChange on switch). If the shell's language is
// one we ship we use it, otherwise we fall back to English.
const localeUrl = (name) => new URL(`./locales/${name}.json`, import.meta.url).href;

// Loaded dictionaries by code (en present once boot completes) + the manifest's
// list of languages we ship. `lang` is the active code.
const DICTS = {};
let LOCALES = [{ code: "en", name: "English" }];
let lang = "en";

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// Load a language's catalog once (cached). A failure resolves to {} so t() falls
// back to en / the raw key rather than throwing mid-render.
async function loadDict(code) {
  if (DICTS[code]) return DICTS[code];
  try {
    DICTS[code] = await fetchJson(localeUrl(code));
  } catch {
    DICTS[code] = {};
  }
  return DICTS[code];
}

// Boot i18n: read the manifest (best-effort) and always load en as the fallback
// layer. Returns the list of languages we ship.
async function initLocales() {
  try {
    const m = await fetchJson(localeUrl("index"));
    if (Array.isArray(m.available) && m.available.length) LOCALES = m.available;
  } catch {
    /* offline / no manifest — keep the en-only default list */
  }
  await loadDict("en");
  return LOCALES;
}

// Translate `key`, filling {name} placeholders from `vars`. Active locale →
// English fallback → the key itself (so a missing key is visible, not blank).
function t(key, vars) {
  const dict = DICTS[lang] || DICTS.en || {};
  let s = dict[key] ?? (DICTS.en || {})[key] ?? key;
  if (vars) {
    s = s.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
  }
  return s;
}

// Adopt the shell's language `code`: use it if we ship it, else fall back to
// English. Loads the target dictionary, then sets the active language. Callers
// re-render afterward.
async function applyLang(code) {
  const target = LOCALES.some((l) => l.code === code) ? code : "en";
  await loadDict(target);
  lang = target;
}
// [key, color] — color drives the filter-tab pill and must match the card's
// left-stripe color (CAT_STYLE) so a category reads as one hue. Labels come from
// t("cat.<key>"). Filter-pill order mirrors the manager's default (problems
// first, healthy last): missing → orphan → mismatch → partial → unexpected →
// no-DP → perfect. Only affects pill display order; status sort ranking lives in
// CAT_ORDER.
const CATEGORIES = [
  ["pure_missing", "sky"],
  ["orphans", "rose"],
  ["mismatched_payload", "amber"],
  ["partially_missing", "yellow"],
  ["unexpected_topics", "purple"],
  ["no_dp_config", "slate"],
  ["perfect", "emerald"],
];
// Category label, resolved live so a language switch re-localizes it.
const catLabel = (key) => t("cat." + key);

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

// Themed <select>. `dark:[color-scheme:dark]` makes the browser paint the
// native dropdown popup (option list) with the dark palette in dark mode —
// without it the popup stays light and is unreadable on a dark page.
const SELECT_CLS =
  "text-xs px-2 py-1 rounded border border-slate-300 dark:border-slate-600 " +
  "bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 dark:[color-scheme:dark]";

function describePlan(action, plan) {
  if (action === "publish") {
    const pub = (plan.per_device || []).reduce((n, p) => n + (p.publish || 0), 0);
    const clr = (plan.per_device || []).reduce((n, p) => n + (p.clear || 0), 0);
    let m = t("plan.publish", { devices: plan.per_device?.length || 0, configs: pub, stale: clr, messages: plan.msg_count });
    if (plan.errors?.length) m += t("plan.genErrors", { count: plan.errors.length });
    return m;
  }
  if (action === "clear") {
    return t("plan.clear", { messages: plan.msg_count, devices: plan.per_device?.length || 0 });
  }
  if (action === "restore") {
    return t("plan.restore", { from: plan.from || t("plan.lastBackup"), set: plan.set, clear: plan.clear });
  }
  return t("plan.messages", { count: plan.msg_count || 0 });
}

// Two-phase action: dry-run to preview the plan, confirm, then execute. The
// grid refreshes itself afterwards via the namespace push (broker echo).
async function runAction(ctx, action, body, danger) {
  const al = t("action." + action); // localized action label for toasts/confirm
  let plan;
  try {
    plan = await ctx.api(`/api/discovery/${action}`, {
      method: "POST",
      body: { ...body, dry_run: true },
    });
  } catch (e) {
    ctx.toast && ctx.toast(`${al}: ${e.message}`, "error");
    return;
  }
  if (action !== "restore" && !plan.msg_count) {
    ctx.toast && ctx.toast(`${al}: ${t("toast.nothingToDo")}`, "ok");
    return;
  }
  const ok = ctx.confirm
    ? await ctx.confirm({
        title: t("confirm.title", { action: al }),
        message: describePlan(action, plan),
        okLabel: al,
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
      ? t("toast.done") + (res.backup ? t("toast.backupSuffix") : "")
      : res.error || t("toast.nothingToDo");
    ctx.toast && ctx.toast(`${al}: ${note}`, "ok");
  } catch (e) {
    ctx.toast && ctx.toast(`${al}: ${e.message}`, "error");
  }
}

const ALL_CAT_KEYS = CATEGORIES.map(([k]) => k);
const SORT_KEYS = ["category", "name", "id"];

// View prefs (category filter + sort) persist in the browser like the manager's,
// so a reload doesn't reset them. Search stays transient on purpose.
const LS_FILTERS = "rustuya-ha.discovery.filters";
const LS_SORT = "rustuya-ha.discovery.sort";

function loadFilters() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_FILTERS) || "null");
    if (Array.isArray(raw)) {
      const valid = raw.filter((k) => ALL_CAT_KEYS.includes(k));
      if (valid.length) return new Set(valid); // empty stored -> fall back to all
    }
  } catch {}
  return new Set(ALL_CAT_KEYS);
}
function loadSort() {
  try {
    const s = localStorage.getItem(LS_SORT);
    if (SORT_KEYS.includes(s)) return s;
  } catch {}
  return "category";
}
function persistView(view) {
  try {
    localStorage.setItem(LS_FILTERS, JSON.stringify([...view.filters]));
    localStorage.setItem(LS_SORT, view.sort);
  } catch {}
}

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
// actionable ones. `view.filters` is the set of enabled category keys. The bulk
// actions (Publish / Restore) ride the right end of this same row.
// Title row with the bulk actions (Publish / Restore) right-aligned next to the
// heading. Lives in `body` (rebuilt each render) so Publish's disabled state
// tracks the current data. `data` may be null while waiting — show title only.
function renderHeader(ctx, data) {
  const head = el("div", "flex items-center gap-2 mb-3");
  head.appendChild(el("h2", "text-base font-semibold", t("title")));
  if (!data) return head;

  const devices = data.devices || [];
  const syncIds = devices.filter((d) => NEEDS_SYNC.has(d.category)).map((d) => d.id);
  const orphanTopics = devices.filter((d) => d.category === "orphans").flatMap((d) => d.topics || []);
  const actionable = syncIds.length + orphanTopics.length;
  const actions = el("span", "ml-auto flex items-center gap-1.5");
  const pub = barBtn(
    t("header.publish"),
    "slate", // neutral like Restore — a colored button read as the "Missing" (sky) category
    t("header.publishTitle"),
    () => openApplyModal(ctx, data, { publish: true, clear: true }),
  );
  pub.disabled = actionable === 0;
  actions.appendChild(pub);
  actions.appendChild(barBtn(t("header.restore"), "slate", t("header.restoreTitle"), () => openRestoreModal(ctx)));
  head.appendChild(actions);
  return head;
}

function renderFilterTabs(ctx, data, view, rerender) {
  const wrap = el("div", "flex flex-wrap gap-1 mb-3 text-xs items-center");
  const counts = data.counts || {};
  const allOn = ALL_CAT_KEYS.every((k) => view.filters.has(k));
  const total = ALL_CAT_KEYS.reduce((n, k) => n + (counts[k] || 0), 0);
  wrap.appendChild(
    filterPill(t("filter.all"), total, allOn, "all", () => {
      if (allOn) view.filters.clear();
      else ALL_CAT_KEYS.forEach((k) => view.filters.add(k));
      persistView(view);
      rerender();
    }),
  );
  for (const [key, color] of CATEGORIES) {
    const n = counts[key] || 0;
    const on = view.filters.has(key);
    const label = catLabel(key);
    const pill = filterPill(label, n, on, color, () => {
      if (on) view.filters.delete(key);
      else view.filters.add(key);
      persistView(view);
      rerender();
    });
    if (n === 0 && !on) pill.classList.add("opacity-50");
    pill.title = on ? t("filter.hide", { label }) : t("filter.show", { label });
    wrap.appendChild(pill);
  }
  return wrap;
}

function renderControls(data, view, rerender) {
  const bar = el("div", "flex flex-wrap gap-2 mb-3 items-center");
  // Manager-style search: grows to fill the row, solid bg, a custom ✕ clear
  // button (the native WebKit search X is suppressed by the host's head CSS).
  const searchWrap = el("div", "relative flex-1 min-w-[180px]");
  const search = el(
    "input",
    "w-full text-sm pl-3 pr-8 py-1.5 rounded border border-slate-300 dark:border-slate-600 " +
      "bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 " +
      "placeholder:text-slate-400 dark:placeholder:text-slate-500 " +
      "focus:outline-none focus:ring-2 focus:ring-slate-400 dark:focus:ring-slate-500",
  );
  search.type = "search";
  search.placeholder = t("controls.searchPlaceholder");
  search.value = view.search;
  search.dataset.keepFocus = "search";
  search.addEventListener("input", () => {
    view.search = search.value;
    rerender();
  });
  const clearX = el(
    "button",
    "absolute right-1.5 top-1/2 -translate-y-1/2 w-5 h-5 inline-flex items-center justify-center " +
      "rounded-full text-slate-400 dark:text-slate-500 hover:text-slate-700 dark:hover:text-slate-200 " +
      "hover:bg-slate-100 dark:hover:bg-slate-700 text-xs leading-none" + (view.search ? "" : " hidden"),
    "✕",
  );
  clearX.type = "button";
  clearX.title = t("controls.clearSearch");
  clearX.addEventListener("click", () => { view.search = ""; rerender(); });
  searchWrap.appendChild(search);
  searchWrap.appendChild(clearX);
  bar.appendChild(searchWrap);

  // Manager-style: the "sort by" label is folded into the select via an
  // <optgroup> header (shown when open), so no separate label row is needed.
  const sort = el("select", SELECT_CLS);
  sort.title = t("controls.sortTitle");
  const og = el("optgroup");
  og.label = t("controls.sortBy");
  for (const val of ["category", "name", "id"]) {
    const o = el("option", null, t("sort." + val));
    o.value = val;
    if (view.sort === val) o.selected = true;
    og.appendChild(o);
  }
  sort.appendChild(og);
  sort.addEventListener("change", () => {
    view.sort = sort.value;
    persistView(view);
    rerender();
  });
  bar.appendChild(sort);
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

// Section tints for the apply modal — border + faint wash per category color.
const SECTION_TINT = {
  emerald: "border-emerald-200 dark:border-emerald-700 bg-emerald-50 dark:bg-emerald-900/20",
  amber:   "border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20",
  yellow:  "border-yellow-200 dark:border-yellow-700 bg-yellow-50 dark:bg-yellow-900/20",
  sky:     "border-sky-200 dark:border-sky-700 bg-sky-50 dark:bg-sky-900/20",
  purple:  "border-purple-200 dark:border-purple-700 bg-purple-50 dark:bg-purple-900/20",
  slate:   "border-slate-200 dark:border-slate-600 bg-slate-50 dark:bg-slate-800/40",
  rose:    "border-rose-200 dark:border-rose-700 bg-rose-50 dark:bg-rose-900/20",
};
const CAT_COLOR = Object.fromEntries(CATEGORIES.map(([k, c]) => [k, c]));
const ROW_STATUS = {
  pending: ["pending", "text-slate-400 dark:text-slate-500"],
  ok:      ["✓", "text-emerald-600 dark:text-emerald-400"],
  error:   ["✘", "text-rose-600 dark:text-rose-400"],
};

// Manager-style bulk modal: the publish scope is split into one collapsible
// group per category (+ orphans for the clear scope), each with a select-all,
// so publishing a single category is one click. Groups start collapsed (the
// header select-all works without expanding) to stay short with large fleets;
// empty categories are omitted. Self-contained, appended to <body>.
function openApplyModal(ctx, data, opts) {
  const devices = data.devices || [];
  const groups = [];
  if (opts.publish) {
    for (const cat of CAT_ORDER) {
      if (!NEEDS_SYNC.has(cat)) continue;
      const items = devices
        .filter((d) => d.category === cat)
        .map((d) => ({ id: d.id, name: d.name, checked: true, status: "pending" }));
      if (items.length) groups.push({ key: cat, label: catLabel(cat), color: CAT_COLOR[cat], kind: "publish", items });
    }
  }
  if (opts.clear) {
    const items = devices
      .filter((d) => d.category === "orphans")
      .flatMap((d) => (d.topics || []).map((t) => ({ topic: t, checked: true, status: "pending" })));
    if (items.length) groups.push({ key: "orphans", label: catLabel("orphans"), color: CAT_COLOR.orphans, kind: "clear", items });
  }
  if (!groups.length) {
    ctx.toast && ctx.toast(t("toast.nothingToDo"), "ok");
    return;
  }

  let applying = false;
  // Collapsed by default; a lone group auto-expands (nothing to hide).
  const expanded = new Set(groups.length === 1 ? [groups[0].key] : []);

  const overlay = el("div", "fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4");
  const panel = el("div", "bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 rounded-lg shadow-xl w-full max-w-lg max-h-[85vh] flex flex-col");
  overlay.appendChild(panel);

  const hasPub = groups.some((g) => g.kind === "publish");
  const hasClr = groups.some((g) => g.kind === "clear");
  const head = el("div", "px-4 py-3 border-b border-slate-200 dark:border-slate-700 flex items-center gap-2");
  head.appendChild(el("h3", "text-sm font-semibold", hasPub && hasClr ? t("modal.titleBoth") : hasClr ? t("modal.titleClear") : t("modal.titlePublish")));
  const closeX = iconBtn("✕", t("common.close"), () => close());
  closeX.classList.add("ml-auto");
  head.appendChild(closeX);
  panel.appendChild(head);

  const bodyEl = el("div", "p-3 space-y-2 overflow-y-auto");
  panel.appendChild(bodyEl);

  const foot = el("div", "px-4 py-3 border-t border-slate-200 dark:border-slate-700 flex items-center gap-2");
  const progress = el("span", "text-xs text-slate-500 dark:text-slate-400 min-w-0 truncate");
  const cancelBtn = btn(t("common.cancel"), `ml-auto ${BTN_GHOST}`, () => close());
  let applyHandler = apply;
  const applyBtn = el("button", BTN_PRIMARY, t("modal.apply"));
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

  function groupSection(g) {
    const isOpen = expanded.has(g.key);
    const checkedN = g.items.filter((i) => i.checked).length;
    const allChecked = checkedN === g.items.length;
    const sec = el("div", `border rounded ${SECTION_TINT[g.color] || ""}`);

    // Header: caret + label + count toggles expand; the select-all label does not.
    const h = el("div", "px-3 py-2 flex items-center gap-2 cursor-pointer select-none");
    h.appendChild(el("span", "text-slate-400 dark:text-slate-500 text-xs w-3 shrink-0", isOpen ? "▾" : "▸"));
    h.appendChild(el("strong", "text-sm", g.label));
    h.appendChild(el("span", "text-xs text-slate-500 dark:text-slate-400", `${checkedN}/${g.items.length}`));
    const allLbl = el("label", "ml-auto text-xs flex items-center gap-1 cursor-pointer");
    const allCb = el("input", "rounded");
    allCb.type = "checkbox";
    allCb.checked = allChecked;
    allCb.indeterminate = checkedN > 0 && !allChecked;
    allCb.disabled = applying;
    allCb.addEventListener("change", () => { g.items.forEach((i) => (i.checked = allCb.checked)); rerenderBody(); });
    allLbl.appendChild(allCb);
    allLbl.appendChild(el("span", null, t("modal.selectAll")));
    h.appendChild(allLbl);
    h.addEventListener("click", (ev) => {
      if (ev.target.closest("label, input")) return; // let the checkbox do its thing
      if (isOpen) expanded.delete(g.key); else expanded.add(g.key);
      rerenderBody();
    });
    sec.appendChild(h);

    if (!isOpen) return sec;

    const ul = el("div", "divide-y divide-black/5 dark:divide-white/10 bg-white/60 dark:bg-slate-800/40 border-t border-black/5 dark:border-white/10");
    for (const it of g.items) {
      const row = el("label", "px-3 py-2 flex items-center gap-2 text-sm cursor-pointer");
      const cb = el("input", "rounded shrink-0");
      cb.type = "checkbox";
      cb.checked = it.checked;
      cb.disabled = applying;
      cb.addEventListener("change", () => { it.checked = cb.checked; rerenderBody(); });
      row.appendChild(cb);
      const txt = el("span", "flex-1 min-w-0 break-all");
      if (g.kind === "publish") {
        txt.appendChild(el("span", "font-medium", it.name || it.id));
        txt.appendChild(el("span", "ml-2 font-mono text-[11px] text-slate-400 dark:text-slate-500", it.id));
      } else {
        txt.appendChild(el("span", "font-mono text-xs", it.topic));
      }
      row.appendChild(txt);
      const [glyph, gcls] = ROW_STATUS[it.status];
      row.appendChild(el("span", `text-xs shrink-0 ${gcls}`, it.status === "pending" ? t("status.pending") : glyph));
      ul.appendChild(row);
    }
    sec.appendChild(ul);
    return sec;
  }

  function rerenderBody() {
    bodyEl.innerHTML = "";
    for (const g of groups) bodyEl.appendChild(groupSection(g));
    updateApply();
  }

  function selectedCount() { return groups.reduce((n, g) => n + g.items.filter((i) => i.checked).length, 0); }
  function updateApply() {
    const n = selectedCount();
    applyBtn.textContent = applying ? t("modal.applying") : n ? t("modal.applyN", { count: n }) : t("modal.apply");
    applyBtn.disabled = applying || n === 0;
  }

  async function apply() {
    if (applying) return;
    const pubItems = groups.filter((g) => g.kind === "publish").flatMap((g) => g.items);
    const clrItems = groups.filter((g) => g.kind === "clear").flatMap((g) => g.items);
    const ids = pubItems.filter((i) => i.checked).map((i) => i.id);
    const topics = clrItems.filter((i) => i.checked).map((i) => i.topic);
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
    for (const i of pubItems) if (i.checked) i.status = pubOk ? "ok" : "error";
    for (const i of clrItems) if (i.checked) i.status = clrOk ? "ok" : "error";
    // Expand groups that were applied so their per-row ✓/✘ is visible.
    for (const g of groups) if (g.items.some((i) => i.checked)) expanded.add(g.key);
    applying = false;
    cancelBtn.disabled = false;
    closeX.disabled = false;
    rerenderBody();
    const okAll = pubOk && clrOk;
    const al = t("action.apply");
    progress.textContent = okAll ? t("modal.applied") : t("common.error", { error: errMsg });
    ctx.toast && ctx.toast(okAll ? `${al}: ${t("toast.done")}` : `${al}: ${errMsg}`, okAll ? "ok" : "error");
    applyHandler = close;
    applyBtn.textContent = t("common.done");
    applyBtn.disabled = false;
  }

  rerenderBody();
  document.body.appendChild(overlay);
}

// Restore modal: pick a server-side backup (newest pre-selected), dry-run to
// preview the plan, then confirm. No OS file upload — backups already live in
// `.rustuya-ha-backups/`; drop a file there to have it appear in the list.
async function openRestoreModal(ctx) {
  let list = [];
  let total = 0;
  try {
    const r = await ctx.api("/api/discovery/backups");
    list = r.backups || [];
    total = r.total ?? list.length;
  } catch (e) {
    ctx.toast && ctx.toast(`${t("action.restore")}: ${e.message}`, "error");
    return;
  }
  if (!list.length) {
    ctx.toast && ctx.toast(t("restore.noBackups"), "ok");
    return;
  }
  // API returns newest-first (mtime desc); list[0] == what a default restore
  // would pick.
  let selected = list[0].path;
  let phase = "select"; // select -> confirm -> done
  let busy = false;

  const overlay = el("div", "fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4");
  const panel = el("div", "bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 rounded-lg shadow-xl w-full max-w-lg max-h-[85vh] flex flex-col");
  overlay.appendChild(panel);

  const head = el("div", "px-4 py-3 border-b border-slate-200 dark:border-slate-700 flex items-center gap-2");
  head.appendChild(el("h3", "text-sm font-semibold", t("restore.title")));
  const closeX = iconBtn("✕", t("common.close"), () => close());
  closeX.classList.add("ml-auto");
  head.appendChild(closeX);
  panel.appendChild(head);

  const bodyEl = el("div", "p-3 overflow-y-auto");
  panel.appendChild(bodyEl);

  const foot = el("div", "px-4 py-3 border-t border-slate-200 dark:border-slate-700 flex items-center gap-2");
  const progress = el("span", "text-xs text-slate-500 dark:text-slate-400 min-w-0 break-all");
  const cancelBtn = btn(t("common.cancel"), `ml-auto ${BTN_GHOST}`, () => close());
  const okBtn = el("button", BTN_PRIMARY, t("restore.restore"));
  okBtn.type = "button";
  okBtn.addEventListener("click", () => onOk());
  foot.appendChild(progress);
  foot.appendChild(cancelBtn);
  foot.appendChild(okBtn);
  panel.appendChild(foot);

  function close() {
    if (busy) return;
    document.removeEventListener("keydown", onKey);
    overlay.remove();
  }
  function onKey(e) { if (e.key === "Escape") close(); }
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

  function renderList() {
    bodyEl.innerHTML = "";
    if (total > list.length) {
      bodyEl.appendChild(
        el("div", "text-[11px] text-slate-400 dark:text-slate-500 mb-2", t("restore.showing", { shown: list.length, total })),
      );
    }
    const ul = el("div", "divide-y divide-slate-100 dark:divide-slate-700 border border-slate-200 dark:border-slate-700 rounded");
    list.forEach((bk, i) => {
      const row = el("label", "px-3 py-2 flex items-center gap-2 text-sm cursor-pointer");
      const rb = el("input", "shrink-0");
      rb.type = "radio";
      rb.name = "rha-restore";
      rb.checked = bk.path === selected;
      rb.disabled = busy || phase !== "select";
      rb.addEventListener("change", () => { selected = bk.path; });
      row.appendChild(rb);
      row.appendChild(el("span", "flex-1 min-w-0 break-all font-mono text-xs", bk.name));
      if (i === 0) row.appendChild(el("span", "text-[10px] uppercase tracking-wide text-emerald-600 dark:text-emerald-400 shrink-0", t("restore.latest")));
      ul.appendChild(row);
    });
    bodyEl.appendChild(ul);
  }

  async function onOk() {
    if (busy) return;
    if (phase === "done") { close(); return; }
    busy = true;
    cancelBtn.disabled = true;
    closeX.disabled = true;
    okBtn.disabled = true;
    if (phase === "select") {
      try {
        const plan = await ctx.api("/api/discovery/restore", { method: "POST", body: { file: selected, dry_run: true } });
        progress.textContent = t("restore.preview", { set: plan.set ?? 0, clear: plan.clear ?? 0 });
        phase = "confirm";
        okBtn.textContent = t("restore.confirm");
      } catch (e) {
        progress.textContent = t("common.error", { error: e.message });
        ctx.toast && ctx.toast(`${t("action.restore")}: ${e.message}`, "error");
      }
      busy = false;
      cancelBtn.disabled = false;
      closeX.disabled = false;
      okBtn.disabled = false;
      renderList();
      return;
    }
    // confirm -> execute
    try {
      const res = await ctx.api("/api/discovery/restore", { method: "POST", body: { file: selected, dry_run: false } });
      const ok = !!res.executed;
      const rl = t("action.restore");
      progress.textContent = ok ? t("restore.restored") : (res.error || t("toast.nothingToDo"));
      ctx.toast && ctx.toast(ok ? `${rl}: ${t("toast.done")}` : `${rl}: ${res.error || t("toast.nothingToDo")}`, ok ? "ok" : "error");
    } catch (e) {
      progress.textContent = t("common.error", { error: e.message });
      ctx.toast && ctx.toast(`${t("action.restore")}: ${e.message}`, "error");
    }
    busy = false;
    cancelBtn.disabled = false;
    closeX.disabled = false;
    phase = "done";
    okBtn.textContent = t("common.done");
    okBtn.disabled = false;
    renderList();
  }

  renderList();
  document.body.appendChild(overlay);
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
  topicList(t("detail.missing"), det.missing, "text-rose-600 dark:text-rose-400");
  topicList(t("detail.unexpected"), det.unexpected, "text-purple-600 dark:text-purple-400");
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
  card.title = catLabel(d.category) || d.category; // category lives on the stripe; hover to read it
  if (expandable) {
    card.addEventListener("click", (ev) => {
      if (ev.target.closest("button, input, a")) return;
      // Skip the toggle when the user is finishing a drag-to-select inside the
      // card: dragging across the topic/diff text fires a click on mouseup,
      // which would collapse the card and drop the selection before they can
      // copy it. The browser sets the selection on mouseup *before* the click,
      // so a non-collapsed selection anchored in this card means "selecting,
      // not tapping".
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed && card.contains(sel.anchorNode)) return;
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
      isOrphan ? ((d.topics || []).join(", ") || t("card.orphan")) : (d.name || d.id || ""),
    ),
  );
  const right = el("span", "ml-auto flex items-center gap-1.5 shrink-0");
  if (isOrphan) {
    // Orphans have no device to publish; the only action is clearing the stray
    // retained topic(s) — cleared by explicit topic (their owner id is unknown).
    right.appendChild(
      iconBtn("🗑", t("card.clearTopics"), () => runAction(ctx, "clear", { topics: d.topics || [] }, true), "danger-fill"),
    );
  } else {
    const ok = (d.matched ?? 0) === (d.expected ?? 0);
    const metric = el(
      "span",
      `text-[11px] font-mono ${ok ? "text-emerald-600 dark:text-emerald-400" : "text-slate-500 dark:text-slate-400"}`,
      `${d.matched ?? 0}/${d.expected ?? 0}`,
    );
    metric.title = t("card.metricTitle");
    right.appendChild(metric);
    // + disabled when nothing needs publishing (perfect / no_dp_config);
    // − disabled when nothing of ours is retained (pure_missing / no_dp_config).
    const pub = iconBtn("+", t("card.publish"), () => runAction(ctx, "publish", { ids: [d.id] }, false));
    pub.disabled = !NEEDS_SYNC.has(d.category);
    right.appendChild(pub);
    const clr = iconBtn("−", t("card.clear"), () => runAction(ctx, "clear", { ids: [d.id] }, true), "danger");
    clr.disabled = !CAN_CLEAR.has(d.category);
    right.appendChild(clr);
  }
  if (expandable) {
    right.appendChild(iconBtn(isOpen ? "▾" : "▸", isOpen ? t("card.collapse") : t("card.expand"), toggle));
  }
  top.appendChild(right);
  card.appendChild(top);

  // ── header row 2: id (under name) + diff summary; skipped for orphans whose
  // identity is the topic already shown above. ──
  if (!isOrphan) {
    const bottom = el("div", "flex items-center gap-2 mt-0.5 min-w-0");
    // id + product_id (the authoring key for product-scoped converters). Both
    // are mono+faint; the 🏷 prefix + tooltip disambiguates the product_id.
    const idGroup = el("div", "flex items-baseline gap-1.5 min-w-0 flex-1");
    if (d.id && d.id !== d.name) {
      idGroup.appendChild(
        el("span", "font-mono text-[11px] text-slate-400 dark:text-slate-500 truncate", d.id),
      );
    }
    if (d.product_id) {
      const pid = el(
        "span",
        "font-mono text-[10px] text-slate-400 dark:text-slate-500 shrink-0",
        `🏷 ${d.product_id}`,
      );
      pid.title = t("card.productId");
      idGroup.appendChild(pid);
    }
    bottom.appendChild(idGroup);
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
      t("grid.empty"),
    );
  }
  if (!devices.length) {
    return el(
      "div",
      "text-sm text-slate-500 dark:text-slate-400 py-8 text-center",
      t("grid.noMatch"),
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
  box.appendChild(el("div", "font-medium mb-1", t("errors.title", { count: errs.length })));
  for (const e of errs) box.appendChild(el("div", "font-mono", e));
  return box;
}

// ── custom converters editor (M5) ────────────────────────────────────────
// ── custom converters editor — a file browser over custom_converters/ ─────
// Lists the *.json (merged) + *.py (code) files on disk; pick one to edit its
// raw text, "＋" to add a new file, save/delete per file. JSON applies on save
// (hot reload); a .py change needs a manager restart (flagged by the API).
function jsonPretty(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return "";
  }
}

async function loadConvFiles(ctx, view, rerender) {
  const c = view.conv;
  c.busy = true;
  rerender();
  try {
    const info = await ctx.api("/api/discovery/converters");
    c.dir = info.dir || "";
    c.files = info.files || [];
    c.products = info.products || [];
    c.pack = info.pack || { synced: false, managed: 0 };
    c.loaded = true;
    // Drop a selection that no longer exists on disk (unless mid-create).
    if (c.selected && !c.creating && !c.files.some((f) => f.name === c.selected)) {
      c.selected = null;
      c.content = "";
    }
  } catch (e) {
    ctx.toast && ctx.toast(`${t("conv.label")}: ${e.message}`, "error");
  } finally {
    c.busy = false;
    rerender();
  }
}

async function updateDefaultConverters(ctx, view, rerender) {
  const c = view.conv;
  if (c.packBusy) return;
  c.packBusy = true;
  rerender();
  try {
    const res = await ctx.api("/api/discovery/converters/update", { method: "POST" });
    if (res.changed) {
      ctx.toast &&
        ctx.toast(
          t("conv.packDone", {
            added: res.added.length,
            updated: res.updated.length,
            removed: res.removed.length,
          }),
          "ok",
        );
    } else {
      ctx.toast && ctx.toast(t("conv.packUpToDate"), "ok");
    }
    if ((res.failed || []).length) {
      ctx.toast && ctx.toast(t("conv.packFailed", { count: res.failed.length }), "error");
    }
    if (res.restart_required) {
      ctx.toast && ctx.toast(t("conv.restartNote"), "ok");
      // Persist an amber attention dot on the header "Restart manager" item
      // (manager rc60+) so the cue survives leaving this tab and stays until an
      // actual restart reloads the page. Feature-detected for older hosts.
      ctx.setHeaderAttention && ctx.setHeaderAttention("restart-btn", true);
    }
    c.loaded = false; // re-fetch the file list + pack state
    await loadConvFiles(ctx, view, rerender);
  } catch (e) {
    ctx.toast && ctx.toast(`${t("conv.label")}: ${e.message}`, "error");
  } finally {
    c.packBusy = false;
    rerender();
  }
}

async function openConvFile(ctx, view, name, rerender) {
  const c = view.conv;
  try {
    const f = await ctx.api(`/api/discovery/converters/file?name=${encodeURIComponent(name)}`);
    c.selected = f.name;
    c.kind = f.kind;
    c.content = f.content;
    c.original = f.content; // baseline for dirty/revert
    c.creating = false;
    c.dirty = false;
    rerender();
  } catch (e) {
    ctx.toast && ctx.toast(`${t("conv.label")}: ${e.message}`, "error");
  }
}

function startNewConvFile(view, rerender) {
  const c = view.conv;
  c.creating = true;
  c.selected = null;
  c.newName = "";
  c.content = "";
  c.original = "";
  c.kind = "json";
  c.dirty = false;
  rerender();
}

// Revert unsaved edits: a new-file draft is discarded (back to the picker); an
// existing file is restored to its on-disk baseline. Keeps the section open.
function cancelConvEdit(view, rerender) {
  const c = view.conv;
  if (c.creating) {
    c.creating = false;
    c.newName = "";
    c.content = "";
    c.original = "";
  } else {
    c.content = c.original || "";
  }
  c.dirty = false;
  rerender();
}

async function saveConvFile(ctx, view, rerender) {
  const c = view.conv;
  const name = (c.creating ? c.newName : c.selected || "").trim();
  if (!name) {
    ctx.toast && ctx.toast(t("conv.needName"), "error");
    return;
  }
  if (!/\.(json|py)$/.test(name)) {
    ctx.toast && ctx.toast(t("conv.badName"), "error");
    return;
  }
  try {
    const res = await ctx.api("/api/discovery/converters/file", {
      method: "POST",
      body: { name, content: c.content },
    });
    ctx.toast &&
      ctx.toast(t("conv.savedFile", { name: res.name }) + (res.backup ? t("toast.backupSuffix") : ""), "ok");
    if (res.restart_required) {
      ctx.toast && ctx.toast(t("conv.restartNote"), "ok");
      // Persist an amber attention dot on the header "Restart manager" item
      // (manager rc60+) so the cue survives leaving this tab and stays until an
      // actual restart reloads the page. Feature-detected for older hosts.
      ctx.setHeaderAttention && ctx.setHeaderAttention("restart-btn", true);
    }
    c.creating = false;
    c.selected = res.name;
    c.kind = res.kind;
    c.original = c.content; // saved content is the new baseline
    c.dirty = false;
    await loadConvFiles(ctx, view, rerender);
  } catch (e) {
    ctx.toast && ctx.toast(`${t("common.save")}: ${e.message}`, "error");
  }
}

async function deleteConvFile(ctx, view, rerender) {
  const c = view.conv;
  if (!c.selected) return;
  const name = c.selected;
  const ok = ctx.confirm
    ? await ctx.confirm({
        title: t("conv.deleteFileTitle"),
        message: t("conv.deleteFileMsg", { name }),
        okLabel: t("action.delete"),
        danger: true,
      })
    : true;
  if (!ok) return;
  try {
    const res = await ctx.api("/api/discovery/converters/file/delete", { method: "POST", body: { name } });
    ctx.toast &&
      ctx.toast(t("conv.deletedFile", { name }) + (res.backup ? t("toast.backupSuffix") : ""), "ok");
    c.selected = null;
    c.content = "";
    c.creating = false;
    await loadConvFiles(ctx, view, rerender);
  } catch (e) {
    ctx.toast && ctx.toast(`${t("action.delete")}: ${e.message}`, "error");
  }
}

function renderConverters(ctx, view, rerender) {
  const c = view.conv;
  const wrap = el("div", "mt-4 border-t border-slate-200 dark:border-slate-700 pt-3");
  wrap.appendChild(
    btn(`${c.open ? "▾" : "▸"} ${t("conv.section")}`, "text-sm font-semibold mb-2", () => {
      c.open = !c.open;
      if (c.open && !c.loaded) loadConvFiles(ctx, view, rerender);
      else rerender();
    }),
  );
  if (!c.open) return wrap;
  if (!c.loaded) {
    wrap.appendChild(el("div", "text-xs text-slate-500", c.busy ? t("common.loading") : ""));
    return wrap;
  }

  // One dropdown (grouped by kind) + "＋ new file". A native <select> scales
  // past a wrapping chip row when there are many files, and the <optgroup>s keep
  // JSON / Python visually separated within the single control.
  const bar = el("div", "flex flex-wrap gap-2 mb-2 items-center");
  const sel = el(
    "select",
    "text-xs font-mono px-2 py-1 rounded border border-slate-300 dark:border-slate-600 " +
      "bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 max-w-full disabled:opacity-50",
  );
  const ph = el("option", null, t("conv.selectPh"));
  ph.value = "";
  sel.appendChild(ph);
  for (const [kind, glabel] of [["json", "JSON"], ["py", "Python"]]) {
    const files = c.files.filter((f) => f.kind === kind);
    if (!files.length) continue;
    const og = el("optgroup");
    og.label = glabel;
    for (const f of files) {
      const o = el("option", null, f.name);
      o.value = f.name;
      og.appendChild(o);
    }
    sel.appendChild(og);
  }
  sel.value = !c.creating && c.selected ? c.selected : "";
  sel.disabled = c.files.length === 0;
  sel.addEventListener("change", () => {
    if (sel.value) openConvFile(ctx, view, sel.value, rerender);
  });
  bar.appendChild(sel);
  const addCls = c.creating
    ? "bg-slate-700 text-white border-slate-700 dark:bg-slate-200 dark:text-slate-900"
    : "border-dashed border-slate-400 text-slate-500 dark:text-slate-400";
  bar.appendChild(
    btn(`＋ ${t("conv.newFile")}`, `px-2 py-1 rounded border text-xs ${addCls}`, () =>
      startNewConvFile(view, rerender),
    ),
  );
  // Pull the curated default-converter pack from GitHub (independent of the
  // plugin release). Labelled "get" on a fresh install, "update" once present.
  const packLabel = c.packBusy
    ? t("common.loading")
    : c.pack && c.pack.synced
      ? t("conv.packUpdate")
      : t("conv.packGet");
  const packBtn = btn(
    `⟳ ${packLabel}`,
    "px-2 py-1 rounded border text-xs border-slate-300 dark:border-slate-600 " +
      "text-slate-600 dark:text-slate-300 ml-auto disabled:opacity-50",
    () => updateDefaultConverters(ctx, view, rerender),
  );
  packBtn.disabled = !!c.packBusy;
  packBtn.title = t("conv.packHint");
  bar.appendChild(packBtn);
  wrap.appendChild(bar);

  if (c.creating || c.selected) {
    if (c.creating) {
      const nameInput = el(
        "input",
        "w-full text-xs font-mono px-2 py-1 mb-2 rounded border border-slate-300 dark:border-slate-600 " +
          "bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100",
      );
      nameInput.placeholder = t("conv.filenamePh");
      nameInput.value = c.newName || "";
      nameInput.dataset.keepFocus = "conv-name";
      nameInput.addEventListener("input", () => {
        c.newName = nameInput.value;
      });
      wrap.appendChild(nameInput);
    }
    // Dirty = a new draft, or edited away from the on-disk baseline. Drives the
    // Save/Cancel enablement and the unsaved marker.
    const isDirty = c.creating || c.content !== (c.original || "");
    const ta = el(
      "textarea",
      "w-full h-48 font-mono text-xs p-2 rounded border " +
        (isDirty
          ? "border-amber-400 dark:border-amber-500/70 "
          : "border-slate-300 dark:border-slate-600 ") +
        "bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100",
    );
    ta.value = c.content;
    ta.dataset.keepFocus = "conv-text";
    ta.spellcheck = false;
    ta.placeholder = t("conv.contentPh");
    ta.addEventListener("input", () => {
      c.content = ta.value;
      c.dirty = true;
    });
    wrap.appendChild(ta);

    const acts = el("div", "flex flex-wrap gap-2 mt-2 items-center");
    const saveBtn = btn(t("common.save"), BTN_PRIMARY, () => saveConvFile(ctx, view, rerender));
    saveBtn.disabled = !isDirty; // BTN_BASE styles disabled as muted
    acts.appendChild(saveBtn);
    if (isDirty) {
      acts.appendChild(btn(t("conv.cancel"), BTN_GHOST, () => cancelConvEdit(view, rerender)));
      acts.appendChild(
        el("span", "text-[11px] font-medium text-amber-600 dark:text-amber-400", `● ${t("conv.unsaved")}`),
      );
    }
    if (!c.creating && c.selected) {
      acts.appendChild(btn(t("conv.deleteFile"), BTN_DANGER, () => deleteConvFile(ctx, view, rerender)));
    }
    acts.appendChild(el("span", "text-xs text-slate-400 ml-auto break-all", t("conv.dir", { path: c.dir || "?" })));
    wrap.appendChild(acts);
  } else {
    wrap.appendChild(
      el(
        "div",
        "text-xs text-slate-500 dark:text-slate-400 py-2",
        c.files.length ? t("conv.pickFile") : t("conv.noFiles"),
      ),
    );
  }

  return wrap;
}
export async function mount(rootEl, ctx) {
  rootEl.innerHTML = "";
  // No horizontal padding: the manager's <main> already supplies px-4, and its
  // own device view sits flush to that edge. A px here would inset the tab's
  // content past the manager's screens (the reported extra left/right margin),
  // so keep only vertical padding.
  const container = el("div", "py-2");
  // Title + bulk actions are rendered inside `body` (see renderHeader) so the
  // Publish button's enabled state tracks live data.
  const body = el("div");
  container.appendChild(body);
  rootEl.appendChild(container);

  // Boot translations before the first paint so every label resolves, then adopt
  // the shell's current language (ctx.getLang) — applyLang falls back to English
  // if we don't ship it. getLang exists from manager rc48; a still-older host
  // just leaves us on English.
  await initLocales();
  const host = (() => {
    try {
      return ctx.getLang && ctx.getLang();
    } catch {
      return null;
    }
  })();
  await applyLang(host || "en");

  // View state persists across re-renders (live pushes + filter/sort/expand).
  // `filters` (category multi-select) and `sort` are seeded from localStorage so
  // they survive a reload, like the manager's filter/sort.
  const view = {
    search: "", filters: loadFilters(), sort: loadSort(), expanded: new Set(),
    conv: {
      open: false, loaded: false, busy: false, dir: "", files: [], products: [],
      selected: null, content: "", original: "", kind: "json", creating: false, newName: "", dirty: false,
    },
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
    body.appendChild(renderHeader(ctx, data));
    if (!data) {
      body.appendChild(
        el("div", "text-sm text-slate-500 py-8 text-center", t("grid.waiting")),
      );
      return;
    }
    // Search + sort first, then the category filter pills below them.
    body.appendChild(renderControls(data, view, render));
    body.appendChild(renderFilterTabs(ctx, data, view, render));
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

  // Defer a live re-paint while the user is mid-gesture — a drag, an open native
  // <select> (the converters file picker), or a live text selection — so an
  // incoming WS frame never rebuilds the DOM out from under them (the dropdown
  // snapping shut / a drag "letting go" after a second). The latest frame is
  // remembered and applied the moment the gesture ends, so nothing goes stale.
  let pendingData; // last skipped payload (undefined = none pending)
  let pointerDown = false;
  function interacting() {
    if (pointerDown) return true;
    const a = document.activeElement;
    if (a && a.tagName === "SELECT") return true; // open dropdown keeps focus
    const sel = window.getSelection && window.getSelection();
    return !!(sel && sel.type === "Range" && String(sel).length);
  }
  function paintFromPush(data) {
    if (interacting()) pendingData = data;
    else paint(data);
  }
  function flushPending() {
    if (pendingData !== undefined && !interacting()) {
      const d = pendingData;
      pendingData = undefined;
      paint(d);
    }
  }
  const onPointerDown = () => { pointerDown = true; };
  const onPointerUp = () => { pointerDown = false; flushPending(); };
  const onFocusOut = () => setTimeout(flushPending, 0);
  document.addEventListener("pointerdown", onPointerDown, true);
  document.addEventListener("pointerup", onPointerUp, true);
  document.addEventListener("selectionchange", flushPending);
  document.addEventListener("focusout", onFocusOut);

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

  // Live updates: re-paint whenever our namespace changes in the WS snapshot —
  // deferred while the user is mid-gesture (see paintFromPush).
  const unsub = ctx.onState((s) => {
    const d = s && s.plugins && s.plugins.discovery;
    if (d) paintFromPush(d);
  });

  // Follow the shell's language when it switches (manager rc49+). applyDom() only
  // reaches [data-i18n] nodes, so this imperatively-built UI re-renders itself.
  // applyLang adopts the new code or falls back to English if we don't ship it.
  // Optional — an older manager never fires it (language then only updates on a
  // tab re-enter, picking up the shell's language via getLang at mount).
  const unsubLang = ctx.onLangChange?.(async (code) => {
    await applyLang(code);
    render();
  });
  return () => {
    unsub && unsub();
    unsubLang && unsubLang();
    document.removeEventListener("pointerdown", onPointerDown, true);
    document.removeEventListener("pointerup", onPointerUp, true);
    document.removeEventListener("selectionchange", flushPending);
    document.removeEventListener("focusout", onFocusOut);
  };
}
