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

// ── i18n (self-contained) ─────────────────────────────────────────────────
// The plugin ships its own dictionaries and re-renders on a language switch via
// ctx.onLangChange, so it never depends on the manager merging plugin keys. On
// an older manager that lacks the hooks it still localizes to the saved language
// at mount (read from the same localStorage key the shell persists); only live
// switching is unavailable there. Keys mirror the manager's {placeholder} fill.
const STR = {
  en: {
    "title": "Home Assistant Discovery",
    "cat.pure_missing": "Missing",
    "cat.orphans": "Orphans",
    "cat.mismatched_payload": "Mismatched",
    "cat.partially_missing": "Partial",
    "cat.unexpected_topics": "Unexpected",
    "cat.no_dp_config": "No DP config",
    "cat.perfect": "Perfect",
    "action.publish": "publish",
    "action.clear": "clear",
    "action.restore": "restore",
    "action.apply": "apply",
    "action.save": "save",
    "action.saveAll": "save all",
    "action.delete": "delete",
    "action.preview": "preview",
    "header.publish": "Publish",
    "header.publishTitle": "Review by category, then publish / clear orphans",
    "header.restore": "Restore",
    "header.restoreTitle": "Restore from a backup",
    "plan.publish": "Publish {devices} device(s): {configs} config(s), clear {stale} stale.\n{messages} retained MQTT message(s).",
    "plan.genErrors": "\n⚠ {count} generator error(s).",
    "plan.clear": "Clear {messages} retained topic(s) across {devices} device(s).\nThis removes them from Home Assistant.",
    "plan.restore": "Restore from {from}:\nre-publish {set} topic(s), clear {clear} added since.",
    "plan.lastBackup": "last backup",
    "plan.messages": "{count} message(s).",
    "confirm.title": "{action} — confirm",
    "toast.nothingToDo": "nothing to do",
    "toast.done": "done",
    "toast.backupSuffix": " · backup saved",
    "controls.searchPlaceholder": "search name / id…",
    "controls.clearSearch": "Clear",
    "controls.sortTitle": "Sort devices",
    "controls.sortBy": "sort by",
    "sort.category": "category",
    "sort.name": "name",
    "sort.id": "id",
    "filter.all": "all",
    "filter.hide": "hide {label}",
    "filter.show": "show {label}",
    "modal.titleBoth": "Publish & clear orphans",
    "modal.titleClear": "Clear orphans",
    "modal.titlePublish": "Publish needing sync",
    "modal.apply": "Apply",
    "modal.applyN": "Apply {count}",
    "modal.applying": "Applying…",
    "modal.selectAll": "select all",
    "modal.applied": "Applied.",
    "common.close": "Close",
    "common.cancel": "Cancel",
    "common.done": "Done",
    "common.save": "Save",
    "common.loading": "loading…",
    "common.error": "Error: {error}",
    "status.pending": "pending",
    "restore.noBackups": "no backups found",
    "restore.title": "Restore backup",
    "restore.restore": "Restore",
    "restore.showing": "Showing newest {shown} of {total} backups.",
    "restore.latest": "latest",
    "restore.preview": "Re-publish {set}, clear {clear} — Confirm to apply.",
    "restore.confirm": "Confirm restore",
    "restore.restored": "Restored.",
    "detail.missing": "missing",
    "detail.unexpected": "unexpected",
    "card.metricTitle": "matched / expected entities",
    "card.clearTopics": "Clear retained topic(s)",
    "card.publish": "Publish",
    "card.clear": "Clear",
    "card.collapse": "Collapse",
    "card.expand": "Expand",
    "card.orphan": "(orphan)",
    "grid.empty": "No devices to show yet.",
    "grid.noMatch": "No devices match the current filter.",
    "grid.waiting": "Waiting for discovery state…",
    "errors.title": "Generator errors ({count})",
    "conv.section": "Custom converters",
    "conv.label": "converters",
    "conv.all": "All (full JSON)",
    "conv.product": "{pid} · {count} dev",
    "conv.saveAllTitle": "converters — save all",
    "conv.saveAllMsg": "Replace the entire converters file with this JSON?\nThis changes what Publish emits for every overridden product.",
    "conv.savedAll": "converters saved ({count})",
    "conv.deleteMsg": "Delete the override for {pid}?",
    "conv.saveMsg": "Save the override for {pid}?\nThis changes what Publish emits for its device(s).",
    "conv.actionTitle": "converter {action}",
    "conv.deleted": "converter deleted",
    "conv.saved": "converter saved",
    "conv.previewTitle": "Preview — {pid} ({count} device(s))",
    "conv.previewDevice": "{name} ({id}) — {count} topic(s) · {source}",
    "conv.previewError": "{name} ({id}): {error}",
    "conv.phAll": '{"<product_id>": {"model": "...", "dp_meta": { ... }}, ...}  — the whole converters file',
    "conv.phOne": '{"model": "...", "dp_meta": { "1": { ... } }}  — empty or "null" deletes',
    "conv.preview": "Preview",
    "conv.deleteOverride": "Delete override",
    "conv.savesTo": "saves to {path}",
    "conv.invalidJson": "invalid JSON",
  },
  ko: {
    "title": "Home Assistant 디스커버리",
    "cat.pure_missing": "누락",
    "cat.orphans": "고아",
    "cat.mismatched_payload": "불일치",
    "cat.partially_missing": "부분누락",
    "cat.unexpected_topics": "예상밖",
    "cat.no_dp_config": "DP 설정 없음",
    "cat.perfect": "완벽",
    "action.publish": "발행",
    "action.clear": "제거",
    "action.restore": "복원",
    "action.apply": "적용",
    "action.save": "저장",
    "action.saveAll": "전체 저장",
    "action.delete": "삭제",
    "action.preview": "미리보기",
    "header.publish": "발행",
    "header.publishTitle": "카테고리별로 검토 후 발행 / 고아 제거",
    "header.restore": "복원",
    "header.restoreTitle": "백업에서 복원",
    "plan.publish": "기기 {devices}개 발행: 설정 {configs}개, 오래된 항목 {stale}개 제거.\n유지(retained) MQTT 메시지 {messages}개.",
    "plan.genErrors": "\n⚠ 제너레이터 오류 {count}개.",
    "plan.clear": "기기 {devices}개에서 유지 토픽 {messages}개 제거.\nHome Assistant에서 사라집니다.",
    "plan.restore": "{from}에서 복원:\n토픽 {set}개 재발행, 이후 추가된 {clear}개 제거.",
    "plan.lastBackup": "마지막 백업",
    "plan.messages": "메시지 {count}개.",
    "confirm.title": "{action} — 확인",
    "toast.nothingToDo": "할 작업 없음",
    "toast.done": "완료",
    "toast.backupSuffix": " · 백업됨",
    "controls.searchPlaceholder": "이름 / ID 검색…",
    "controls.clearSearch": "지우기",
    "controls.sortTitle": "기기 정렬",
    "controls.sortBy": "정렬 기준",
    "sort.category": "카테고리",
    "sort.name": "이름",
    "sort.id": "ID",
    "filter.all": "전체",
    "filter.hide": "{label} 숨기기",
    "filter.show": "{label} 보기",
    "modal.titleBoth": "발행 및 고아 제거",
    "modal.titleClear": "고아 제거",
    "modal.titlePublish": "동기화 필요 발행",
    "modal.apply": "적용",
    "modal.applyN": "{count}개 적용",
    "modal.applying": "적용 중…",
    "modal.selectAll": "전체 선택",
    "modal.applied": "적용됨.",
    "common.close": "닫기",
    "common.cancel": "취소",
    "common.done": "완료",
    "common.save": "저장",
    "common.loading": "불러오는 중…",
    "common.error": "오류: {error}",
    "status.pending": "대기",
    "restore.noBackups": "백업 없음",
    "restore.title": "백업 복원",
    "restore.restore": "복원",
    "restore.showing": "최근 {shown}개 표시 (전체 {total}개).",
    "restore.latest": "최신",
    "restore.preview": "{set}개 재발행, {clear}개 제거 — 확인 시 적용.",
    "restore.confirm": "복원 확인",
    "restore.restored": "복원됨.",
    "detail.missing": "누락",
    "detail.unexpected": "예상밖",
    "card.metricTitle": "일치 / 예상 엔티티",
    "card.clearTopics": "유지 토픽 제거",
    "card.publish": "발행",
    "card.clear": "제거",
    "card.collapse": "접기",
    "card.expand": "펼치기",
    "card.orphan": "(고아)",
    "grid.empty": "표시할 기기가 아직 없습니다.",
    "grid.noMatch": "현재 필터에 맞는 기기가 없습니다.",
    "grid.waiting": "디스커버리 상태 대기 중…",
    "errors.title": "제너레이터 오류 ({count})",
    "conv.section": "커스텀 컨버터",
    "conv.label": "컨버터",
    "conv.all": "전체 (전체 JSON)",
    "conv.product": "{pid} · 기기 {count}개",
    "conv.saveAllTitle": "컨버터 — 전체 저장",
    "conv.saveAllMsg": "컨버터 파일 전체를 이 JSON으로 교체할까요?\n오버라이드된 모든 제품의 발행 결과가 바뀝니다.",
    "conv.savedAll": "컨버터 저장됨 ({count})",
    "conv.deleteMsg": "{pid} 오버라이드를 삭제할까요?",
    "conv.saveMsg": "{pid} 오버라이드를 저장할까요?\n해당 기기의 발행 결과가 바뀝니다.",
    "conv.actionTitle": "컨버터 {action}",
    "conv.deleted": "컨버터 삭제됨",
    "conv.saved": "컨버터 저장됨",
    "conv.previewTitle": "미리보기 — {pid} (기기 {count}개)",
    "conv.previewDevice": "{name} ({id}) — 토픽 {count}개 · {source}",
    "conv.previewError": "{name} ({id}): {error}",
    "conv.phAll": '{"<product_id>": {"model": "...", "dp_meta": { ... }}, ...}  — 전체 컨버터 파일',
    "conv.phOne": '{"model": "...", "dp_meta": { "1": { ... } }}  — 비우거나 "null"이면 삭제',
    "conv.preview": "미리보기",
    "conv.deleteOverride": "오버라이드 삭제",
    "conv.savesTo": "저장 위치: {path}",
    "conv.invalidJson": "잘못된 JSON",
  },
};

// Active language; set in mount() from ctx.getLang()/localStorage. Mutable so a
// language switch (ctx.onLangChange → re-render) picks up the new dictionary.
let lang = "en";

// Translate `key`, filling {name} placeholders from `vars`. Active locale →
// English fallback → the key itself, mirroring the manager's resolver.
function t(key, vars) {
  let s = (STR[lang] && STR[lang][key]) ?? STR.en[key] ?? key;
  if (vars) {
    s = s.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
  }
  return s;
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
function jsonPretty(obj) {
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return "";
  }
}

const ALL_CONV = "__all__"; // pseudo-selection: edit the whole converters file

function selectProduct(view, pid) {
  const c = view.conv;
  c.selected = pid;
  if (pid === ALL_CONV) {
    c.text = jsonPretty((c.info && c.info.converters) || {});
  } else {
    const existing = c.info && c.info.converters ? c.info.converters[pid] : null;
    c.text = existing ? jsonPretty(existing) : jsonPretty({ model: "", dp_meta: {} });
  }
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
    const keep =
      view.conv.selected === ALL_CONV || (view.conv.selected && pids.includes(view.conv.selected))
        ? view.conv.selected
        : ALL_CONV; // default to the whole-file ("All") view; a stale per-product pick also falls back here
    selectProduct(view, keep);
  } catch (e) {
    ctx.toast && ctx.toast(`${t("conv.label")}: ${e.message}`, "error");
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
  // All-mode: the textarea is the whole converters file; Save replaces it.
  if (c.selected === ALL_CONV) {
    if (kind !== "save") return; // preview/delete are per-product only
    let mapping;
    try {
      mapping = JSON.parse(c.text.trim() || "{}");
    } catch (e) {
      ctx.toast && ctx.toast(`${t("conv.invalidJson")}: ${e.message}`, "error");
      return;
    }
    const ok = ctx.confirm
      ? await ctx.confirm({
          title: t("conv.saveAllTitle"),
          message: t("conv.saveAllMsg"),
          okLabel: t("action.saveAll"),
          danger: true,
        })
      : true;
    if (!ok) return;
    try {
      const res = await ctx.api("/api/discovery/converters/save_all", { method: "POST", body: { converters: mapping } });
      ctx.toast && ctx.toast(t("conv.savedAll", { count: res.count }) + (res.backup ? t("toast.backupSuffix") : ""), "ok");
      await loadConverters(ctx, view, rerender);
    } catch (e) {
      ctx.toast && ctx.toast(`${t("action.saveAll")}: ${e.message}`, "error");
    }
    return;
  }
  let override;
  try {
    override = kind === "delete" ? null : parseOverride(c.text);
  } catch (e) {
    ctx.toast && ctx.toast(`${t("conv.invalidJson")}: ${e.message}`, "error");
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
      ctx.toast && ctx.toast(`${t("action.preview")}: ${e.message}`, "error");
    }
    return;
  }
  const msg = kind === "delete"
    ? t("conv.deleteMsg", { pid: c.selected })
    : t("conv.saveMsg", { pid: c.selected });
  const ok = ctx.confirm
    ? await ctx.confirm({ title: t("conv.actionTitle", { action: t("action." + kind) }), message: msg, okLabel: t("action." + kind), danger: kind === "delete" })
    : true;
  if (!ok) return;
  try {
    const res = await ctx.api("/api/discovery/converters/save", {
      method: "POST",
      body: { product_id: c.selected, override },
    });
    ctx.toast &&
      ctx.toast((res.deleted ? t("conv.deleted") : t("conv.saved")) + (res.backup ? t("toast.backupSuffix") : ""), "ok");
    await loadConverters(ctx, view, rerender); // refresh has-override + canonical text
  } catch (e) {
    ctx.toast && ctx.toast(`${t("action." + kind)}: ${e.message}`, "error");
  }
}

function renderConvPreview(preview) {
  const box = el("div", "mt-2 p-2 rounded bg-slate-50 dark:bg-slate-800/40 text-xs space-y-2");
  box.appendChild(
    el("div", "font-medium", t("conv.previewTitle", { pid: preview.product_id, count: (preview.devices || []).length })),
  );
  for (const d of preview.devices || []) {
    const b = el("div");
    if (d.error) {
      b.appendChild(el("div", "text-rose-600 dark:text-rose-400", t("conv.previewError", { name: d.name, id: d.id, error: d.error })));
    } else {
      const topics = Object.keys(d.topics || {});
      b.appendChild(
        el("div", "font-medium", t("conv.previewDevice", { name: d.name, id: d.id, count: topics.length, source: d.source || "" })),
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
    btn(`${c.open ? "▾" : "▸"} ${t("conv.section")}`, "text-sm font-semibold mb-2", () => {
      c.open = !c.open;
      if (c.open && !c.loaded) loadConverters(ctx, view, rerender);
      else rerender();
    }),
  );
  if (!c.open) return wrap;
  if (!c.loaded) {
    wrap.appendChild(el("div", "text-xs text-slate-500", c.busy ? t("common.loading") : ""));
    return wrap;
  }
  const info = c.info || { products: [], converters: {}, save_path: "" };

  const isAll = c.selected === ALL_CONV;

  const row = el("div", "flex flex-wrap gap-2 items-center mb-2");
  const sel = el("select", SELECT_CLS);
  // "All" lets you see/edit the whole converters file in one JSON blob.
  const allOpt = el("option", null, t("conv.all"));
  allOpt.value = ALL_CONV;
  if (isAll) allOpt.selected = true;
  sel.appendChild(allOpt);
  for (const pr of info.products) {
    const o = el(
      "option",
      null,
      t("conv.product", { pid: pr.product_id, count: pr.device_ids.length }) + (pr.has_override ? " ✏" : ""),
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
    "w-full h-48 font-mono text-xs p-2 rounded border border-slate-300 dark:border-slate-600 " +
      "bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100",
  );
  ta.value = c.text;
  ta.dataset.keepFocus = "conv-text";
  ta.spellcheck = false;
  ta.placeholder = isAll ? t("conv.phAll") : t("conv.phOne");
  ta.addEventListener("input", () => {
    c.text = ta.value;
  });
  wrap.appendChild(ta);

  const acts = el("div", "flex flex-wrap gap-2 mt-2 items-center");
  // Preview is per-product (regenerates that product's devices); not meaningful
  // for the whole-file edit.
  const previewBtn = btn(t("conv.preview"), BTN_GHOST, () => convAction(ctx, view, rerender, "preview"));
  const saveBtn = btn(t("common.save"), BTN_PRIMARY, () => convAction(ctx, view, rerender, "save"));
  previewBtn.disabled = isAll;
  saveBtn.disabled = false;
  acts.appendChild(previewBtn);
  acts.appendChild(saveBtn);
  if (!isAll && c.selected && info.converters && c.selected in info.converters) {
    acts.appendChild(btn(t("conv.deleteOverride"), BTN_DANGER, () => convAction(ctx, view, rerender, "delete")));
  }
  acts.appendChild(el("span", "text-xs text-slate-400 ml-auto", t("conv.savesTo", { path: info.save_path || "?" })));
  wrap.appendChild(acts);

  if (c.preview) wrap.appendChild(renderConvPreview(c.preview));
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

  // Pick the initial language from the host (ctx.getLang, rc49+) or, on an older
  // manager without it, the same localStorage key the shell persists — so the
  // plugin still localizes even where live switching isn't wired.
  const readLang = () => {
    try {
      return (ctx.getLang && ctx.getLang()) || localStorage.getItem("lang") || "en";
    } catch {
      return "en";
    }
  };
  lang = readLang();

  // View state persists across re-renders (live pushes + filter/sort/expand).
  // `filters` (category multi-select) and `sort` are seeded from localStorage so
  // they survive a reload, like the manager's filter/sort.
  const view = {
    search: "", filters: loadFilters(), sort: loadSort(), expanded: new Set(),
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

  // Re-render in the new language when the shell's language switches (rc49+).
  // applyDom() only reaches [data-i18n] nodes, so this imperatively-built UI
  // needs its own re-render. Optional: an older manager simply never fires it
  // (the language then changes only on a tab re-enter). The host passes the new
  // code, but we re-read to also cover a switch to a code the host doesn't pass.
  const unsubLang = ctx.onLangChange?.((code) => {
    lang = code || readLang();
    render();
  });
  return () => {
    unsub && unsub();
    unsubLang && unsubLang();
  };
}
