// Injects the shared sidebar and top bar. Pages declare data-page and data-crumbs on <body>.
const ICONS = {
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
  table: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/><path d="M9 3v18"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M8 9h8"/><path d="M8 13h5"/>',
  braces: '<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1"/><path d="M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  play: '<polygon points="7 4 19 12 7 20 7 4"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  right: '<path d="m9 18 6-6-6-6"/>',
  left: '<path d="m15 18-6-6 6-6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  pencil: '<path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  alert: '<circle cx="12" cy="12" r="9"/><line x1="12" x2="12" y1="8" y2="12"/><line x1="12" x2="12.01" y1="16" y2="16"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  arrow: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  zoomin: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/><path d="M11 8v6M8 11h6"/>',
  zoomout: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/><path d="M8 11h6"/>',
  layers: '<path d="m12 2 10 5-10 5L2 7Z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
};
const icon = (name, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24">${ICONS[name]}</svg>`;
window.icon = icon;

function renderShell() {
  const theme = new URLSearchParams(location.search).get("theme");
  if (theme) document.documentElement.dataset.theme = theme;
  const body = document.body;
  const page = body.dataset.page;
  const crumbs = (body.dataset.crumbs || "").split("/").map((c) => c.trim());
  const nav = [
    ["documents", "file", "Documents", '<span class="count alert">5</span>'],
    ["results", "table", "Results", '<span class="count">79</span>'],
    ["genie", "chat", "Ask Genie", ""],
    ["schemas", "braces", "Schemas", '<span class="count">5</span>'],
  ];
  const side = `
    <aside class="side">
      <div class="brand"><div class="logo">i</div><b>IDP</b></div>
      <div class="project"><div>Invoices 2026<small>Production</small></div>${icon("down", "sm")}</div>
      <nav class="nav">
        ${nav.map(([id, ic, label, extra]) => `<a class="${id === page ? "active" : ""}">${icon(ic)}${label}${extra}</a>`).join("")}
      </nav>
      <div class="side-foot">
        <div class="queue-card">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px"><span class="pill processing" style="height:20px">Processing</span></div>
          <div class="row"><span>Preparing</span><b>3</b></div>
          <div class="row" style="margin-top:4px"><span>Extracting</span><b>0</b></div>
          <div class="activity" style="margin-top:10px"></div>
        </div>
        <div class="user"><div class="avatar">AM</div><div style="line-height:1.2">Alex Morgan<br><small class="faint">Finance ops</small></div></div>
      </div>
    </aside>`;
  const top = `
    <header class="top">
      <div class="crumbs">${crumbs.map((c, i) => (i === crumbs.length - 1 ? `<b>${c}</b>` : `<span>${c}</span>${icon("right", "sm")}`)).join("")}</div>
      <div class="top-right">
        <div class="search">${icon("search", "sm")}<span>Search documents, results…</span><kbd>⌘K</kbd></div>
        <button class="btn ghost" style="padding:0 8px">${icon("help")}</button>
        <button class="btn ghost" style="padding:0 8px">${icon("bell")}</button>
      </div>
    </header>`;
  const content = body.innerHTML;
  body.innerHTML = `<div class="app">${side}<div class="main">${top}<main class="content" style="position:relative">${content}</main></div></div>`;
  document.querySelectorAll("[data-i]").forEach((el) => { el.outerHTML = icon(el.dataset.i, el.className || ""); });
}
renderShell();
