/* Brand design system components. Pages call these with data; layout and styling are owned here, not by the page. */
const EM = (() => {
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const $ = sel => typeof sel === "string" ? document.querySelector(sel) : sel;
  const statusClass = s => { s = String(s || "").toLowerCase(); return s.startsWith("couv") || s === "ok" ? "ok" : s.startsWith("part") ? "part" : s.startsWith("non") || s === "no" ? "no" : "tbc"; };
  const LABEL = {ok: "Couverte", part: "Partielle", no: "Non couverte", tbc: "À confirmer"};
  const badge = s => { const k = statusClass(s); return `<span class="em-badge ${k}">${esc(typeof s === "string" && s ? s : LABEL[k])}</span>`; };
  const legend = () => `<div class="em-legend">${Object.entries(LABEL).map(([k, l]) => `<span class="${k}"><i></i>${l}</span>`).join("")}</div>`;
  const tally = items => { const t = {ok: 0, part: 0, no: 0, tbc: 0}; items.forEach(i => t[statusClass(i.status)]++); return t; };
  const mini = t => { const n = t.ok + t.part + t.no + t.tbc || 1; return `<div class="em-mini">${["ok", "part", "no", "tbc"].map(k => `<span class="${k}" style="width:${t[k] / n * 100}%"></span>`).join("")}</div>`; };
  const reveal = root => { const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), {threshold: .08});
    root.querySelectorAll(".em-reveal:not(.in)").forEach(el => io.observe(el)); };
  const section = (el, o) => { el = $(el); el.classList.add("em-section", "em-reveal");
    el.innerHTML = `<div class="head"><div class="t">${o.eyebrow ? `<span class="em-eyebrow dark">${esc(o.eyebrow)}</span>` : ""}<h2>${esc(o.title)}</h2>${o.lede ? `<p class="lede">${esc(o.lede)}</p>` : ""}</div>${o.aside || ""}</div><div class="body"></div>`;
    reveal(document); return el.querySelector(".body"); };

  function drawer(title, html, eyebrow) {
    const d = $("#em-drawer"); d.querySelector(".ey").textContent = eyebrow || ""; d.querySelector("h3").textContent = title; d.querySelector(".c").innerHTML = html;
    d.classList.add("on"); $("#em-scrim").classList.add("on"); }
  function close() { $("#em-drawer").classList.remove("on"); $("#em-scrim").classList.remove("on"); }
  const detail = fields => `<dl>${Object.entries(fields).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;

  /* Executive summary: {eyebrow, title, text, points: ["<b>…</b> …"], source} */
  function summary(el, o) { el = $(el); el.classList.add("em-reveal");
    el.innerHTML = `<div class="em-summary"><div class="lead"><span class="em-eyebrow">${esc(o.eyebrow || "Synthèse")}</span><h3>${esc(o.title)}</h3>${o.text ? `<p>${esc(o.text)}</p>` : ""}${o.source ? `<div class="em-source">${esc(o.source)}</div>` : ""}</div>
      <ol>${(o.points || []).map(p => `<li><span>${p}</span></li>`).join("")}</ol></div>`; reveal(document); }

  /* KPI strip: [{label, value, unit, hint, status}] */
  function kpis(el, items) { el = $(el); el.classList.add("em-reveal");
    el.innerHTML = `<div class="em-kpis">${items.map(k => `<div class="em-kpi ${k.status ? statusClass(k.status) : ""}"><div class="l">${k.status ? "<i></i>" : ""}${esc(k.label)}</div>
      <div class="v">${esc(k.value)}${k.unit ? `<small>${esc(k.unit)}</small>` : ""}</div>${k.hint ? `<div class="h">${esc(k.hint)}</div>` : ""}</div>`).join("")}</div>`; reveal(document); }

  /* Capability map (L1 rows × L2 cells heatmap) with Coverage/Applications views, application filter and detail drawer.
     o = {capabilities: [{id, l1, l2, status, apps: [..], finding, detail: {Champ: valeur}}], onOpen?(cap)} */
  function capabilityMap(el, o) {
    el = $(el); const caps = o.capabilities || [];
    const apps = [...new Set(caps.flatMap(c => c.apps || []))].sort((a, b) => a.localeCompare(b, "fr"));
    const color = Object.fromEntries(apps.map((a, i) => [a, `var(--app${i % 12 + 1})`]));
    let view = "cov", app = "";
    el.innerHTML = `<div class="em-toolbar"><div class="em-seg"><button class="on" data-v="cov">Couverture</button><button data-v="app">Applications</button></div>
      <select class="em-select"><option value="">Toutes les applications</option>${apps.map(a => `<option>${esc(a)}</option>`).join("")}</select>
      <span class="lg"></span><span class="em-count"></span></div><div class="em-map"></div>`;
    const map = el.querySelector(".em-map"), lg = el.querySelector(".lg"), count = el.querySelector(".em-count");
    function draw() {
      const l1s = [...new Set(caps.map(c => c.l1))];
      lg.innerHTML = view === "cov" ? legend() : `<div class="em-legend">${apps.map(a => `<span style="--c:${color[a]}"><i></i>${esc(a)}</span>`).join("")}</div>`;
      map.innerHTML = l1s.map(l1 => { const items = caps.filter(c => c.l1 === l1), t = tally(items);
        return `<div class="row"><div class="l1"><b>${esc(l1)}</b><div class="m">${mini(t)}<span>${t.ok}/${items.length}</span></div></div><div class="cells">${items.map(c => {
          const k = statusClass(c.status), a0 = (c.apps || [])[0];
          const dim = app && !(c.apps || []).includes(app) ? " dim" : "";
          return view === "cov"
            ? `<button class="em-cell ${k}${dim}" data-id="${esc(c.id)}"><b>${esc(c.l2)}</b><small>${esc((c.apps || []).join(", ") || "Aucune application")}</small></button>`
            : `<button class="em-cell app${dim}" style="--ac:${a0 ? color[a0] : "var(--faint)"}" data-id="${esc(c.id)}"><b>${esc(c.l2)}</b><small>${esc((c.apps || []).join(", ") || "Aucune application")} · ${LABEL[k]}</small></button>`; }).join("")}</div></div>`; }).join("");
      const shown = app ? caps.filter(c => (c.apps || []).includes(app)).length : caps.length;
      count.textContent = `${shown} capacité${shown > 1 ? "s" : ""} sur ${caps.length}`;
    }
    el.querySelector(".em-seg").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; view = b.dataset.v;
      el.querySelectorAll(".em-seg button").forEach(x => x.classList.toggle("on", x === b)); draw(); });
    el.querySelector(".em-select").addEventListener("change", e => { app = e.target.value; draw(); });
    map.addEventListener("click", e => { const b = e.target.closest(".em-cell"); if (!b) return; open(b.dataset.id); });
    function open(id) { const c = caps.find(x => String(x.id) === String(id)); if (!c) return;
      if (o.onOpen) return o.onOpen(c);
      drawer(c.l2, detail({"Statut": badge(c.status), "Domaine": esc(c.l1), "Applications": esc((c.apps || []).join(", ") || "Aucune"), "Constat": esc(c.finding || "À confirmer"),
        ...Object.fromEntries(Object.entries(c.detail || {}).map(([k, v]) => [k, esc(v)]))}), c.l1); }
    draw(); el.classList.add("em-reveal"); reveal(document);
    return {open};
  }

  /* Filterable table: {columns: [{key, label, type: "text"|"key"|"status"|"links"}], rows, filters: [colKey], search: true, onLink(value)} */
  function table(el, o) {
    el = $(el); const cols = o.columns, rows = o.rows || []; const state = {q: ""};
    const fkeys = o.filters || [];
    el.innerHTML = `<div class="em-toolbar">${fkeys.map(k => { const vals = [...new Set(rows.map(r => r[k]).filter(v => v !== undefined && v !== ""))];
        const lab = (cols.find(c => c.key === k) || {}).label || k;
        return `<select class="em-select" data-k="${esc(k)}"><option value="">${esc(lab)} : tous</option>${vals.map(v => `<option>${esc(v)}</option>`).join("")}</select>`; }).join("")}
      ${o.search === false ? "" : `<input class="em-search" placeholder="Rechercher…">`}<span class="em-count"></span></div>
      <div class="em-table-wrap"><table class="em-table"><thead><tr>${cols.map(c => `<th>${esc(c.label)}</th>`).join("")}</tr></thead><tbody></tbody></table></div>`;
    const tb = el.querySelector("tbody"), count = el.querySelector(".em-count");
    const cell = (c, v) => c.type === "status" ? badge(v) : c.type === "links" ? `<div class="em-links">${(v || []).map(x => `<button class="em-link" data-l="${esc(x.id ?? x)}">${esc(x.label ?? x)}</button>`).join("")}</div>`
      : esc(Array.isArray(v) ? v.join(", ") : v);
    function draw() {
      const r = rows.filter(row => fkeys.every(k => !state[k] || String(row[k]) === state[k]) && (!state.q || JSON.stringify(row).toLowerCase().includes(state.q)));
      tb.innerHTML = r.length ? r.map(row => `<tr>${cols.map(c => `<td class="${c.type === "key" ? "key" : ""}">${cell(c, row[c.key])}</td>`).join("")}</tr>`).join("")
        : `<tr><td colspan="${cols.length}" class="em-empty">Aucun résultat</td></tr>`;
      count.textContent = `${r.length} ligne${r.length > 1 ? "s" : ""}`;
    }
    el.querySelectorAll("select").forEach(s => s.addEventListener("change", () => { state[s.dataset.k] = s.value; draw(); }));
    el.querySelector(".em-search")?.addEventListener("input", e => { state.q = e.target.value.toLowerCase(); draw(); });
    tb.addEventListener("click", e => { const b = e.target.closest(".em-link"); if (b && o.onLink) o.onLink(b.dataset.l); });
    draw(); el.classList.add("em-reveal"); reveal(document);
  }

  /* Coverage bars by group: [{label, note, ok, part, no, tbc}] (counts) */
  function coverage(el, groups) { el = $(el);
    el.innerHTML = `<div class="em-card em-cov">${legend()}<div style="height:8px"></div>${groups.map(g => { const n = (g.ok + g.part + g.no + g.tbc) || 1;
      return `<div class="r"><div class="n"><b>${esc(g.label)}</b>${g.note ? `<small>${esc(g.note)}</small>` : ""}</div><div class="em-bar">${["ok", "part", "no", "tbc"].map(k => `<span class="${k}" style="width:${(g[k] || 0) / n * 100}%"></span>`).join("")}</div>
      <div class="p">${Math.round((g.ok || 0) / n * 100)} %<small>${g.ok || 0} couverte${(g.ok || 0) > 1 ? "s" : ""} sur ${n}</small></div></div>`; }).join("")}</div>`;
    el.classList.add("em-reveal"); reveal(document); }

  /* Cards: [{eyebrow, title, status, text, items: [{label, status}], foot, id}] ; onItem(label) */
  function cards(el, items, onItem) { el = $(el);
    el.innerHTML = `<div class="em-grid">${items.map(c => `<article class="em-xcard"><div class="top">${c.eyebrow ? `<span class="em-eyebrow dark">${esc(c.eyebrow)}</span>` : ""}${c.status ? badge(c.status) : ""}</div>
      <h3>${esc(c.title)}</h3>${c.text ? `<p>${esc(c.text)}</p>` : ""}${(c.items || []).length ? `<ul>${c.items.map(i => `<li class="${statusClass(i.status)}"><i></i>${onItem ? `<button class="em-link" data-l="${esc(i.id ?? i.label)}">${esc(i.label)}</button>` : esc(i.label)}</li>`).join("")}</ul>` : ""}
      ${c.foot ? `<div class="foot">${esc(c.foot)}</div>` : ""}</article>`).join("")}</div>`;
    if (onItem) el.addEventListener("click", e => { const b = e.target.closest(".em-link"); if (b) onItem(b.dataset.l); });
    el.classList.add("em-reveal"); reveal(document); }

  /* EM.count(list) → total ; EM.count(list, "Couverte") → items with that status */
  const count = (list, status) => (list || []).filter(x => !status || statusClass(x.status ?? x.statut) === statusClass(status)).length;
  const callout = (el, text, status = "info") => { el = $(el); el.innerHTML = `<div class="em-callout ${statusClass(status) === "tbc" && status === "info" ? "info" : status}"><div>${text}</div></div>`; };

  function tab(id) {
    document.querySelectorAll(".em-tab").forEach(t => t.classList.toggle("on", t.dataset.tab === id));
    document.querySelectorAll(".em-panel").forEach(p => p.classList.toggle("on", p.id === id));
    try { history.replaceState(null, "", "#t-" + id); } catch (e) { /* opaque (sandboxed) origin: some browsers refuse */ }  // prefixed: a bare #id would scroll
    reveal(document);
  }
  document.addEventListener("DOMContentLoaded", () => {
    const tabs = [...document.querySelectorAll(".em-tab")];
    tabs.forEach(t => t.addEventListener("click", () => EM.tab(t.dataset.tab)));
    const want = location.hash.replace(/^#(t-)?/, "");
    if (tabs.length) tab(tabs.some(t => t.dataset.tab === want) ? want : tabs[0].dataset.tab);
    scrollTo(0, 0);
    document.querySelector(".em-theme")?.addEventListener("click", () => {
      const r = document.documentElement, dark = r.dataset.theme ? r.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      r.dataset.theme = dark ? "light" : "dark"; });
    document.getElementById("em-scrim")?.addEventListener("click", close);
    document.addEventListener("keydown", e => e.key === "Escape" && close());
  });
  return {count, esc, badge, statusClass, legend, drawer, close, detail, section, summary, kpis, capabilityMap, table, coverage, cards, callout, tab, reveal};
})();
