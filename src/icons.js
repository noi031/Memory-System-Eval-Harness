function renderAttrs(attrs = {}) {
  return Object.entries(attrs)
    .map(([key, value]) => `${key}="${String(value)}"`)
    .join(" ");
}

function iconNode(tag, attrs) {
  return `<${tag} ${renderAttrs(attrs)}></${tag}>`;
}

const ICONS = {
  user: [
    ["path", { d: "M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" }],
    ["circle", { cx: "12", cy: "7", r: "4" }],
  ],
  database: [
    ["ellipse", { cx: "12", cy: "5", rx: "9", ry: "3" }],
    ["path", { d: "M3 5V19A9 3 0 0 0 21 19V5" }],
    ["path", { d: "M3 12A9 3 0 0 0 21 12" }],
  ],
  activity: [
    ["path", { d: "M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" }],
  ],
  refreshCw: [
    ["path", { d: "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" }],
    ["path", { d: "M21 3v5h-5" }],
    ["path", { d: "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" }],
    ["path", { d: "M8 16H3v5" }],
  ],
  bookOpen: [
    ["path", { d: "M12 7v14" }],
    ["path", { d: "M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z" }],
  ],
  clipboardList: [
    ["rect", { width: "8", height: "4", x: "8", y: "2", rx: "1", ry: "1" }],
    ["path", { d: "M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" }],
    ["path", { d: "M12 11h4" }],
    ["path", { d: "M12 16h4" }],
    ["path", { d: "M8 11h.01" }],
    ["path", { d: "M8 16h.01" }],
  ],
  uploadCloud: [
    ["path", { d: "M12 13v8" }],
    ["path", { d: "M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242" }],
    ["path", { d: "m8 17 4-4 4 4" }],
  ],
  play: [
    ["polygon", { points: "6 4 20 12 6 20 6 4" }],
  ],
  messagesSquare: [
    ["path", { d: "M16 10a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 14.286V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" }],
    ["path", { d: "M20 9a2 2 0 0 1 2 2v10.286a.71.71 0 0 1-1.212.502l-2.202-2.202A2 2 0 0 0 17.172 19H10a2 2 0 0 1-2-2v-1" }],
  ],
  barChart3: [
    ["path", { d: "M3 3v16a2 2 0 0 0 2 2h16" }],
    ["path", { d: "M18 17V9" }],
    ["path", { d: "M13 17V5" }],
    ["path", { d: "M8 17v-3" }],
  ],
  folderArchive: [
    ["circle", { cx: "15", cy: "19", r: "2" }],
    ["path", { d: "M20.9 19.8A2 2 0 0 0 22 18V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h5.1" }],
    ["path", { d: "M15 11v-1" }],
    ["path", { d: "M15 17v-2" }],
  ],
  clock3: [
    ["path", { d: "M12 6v6h4" }],
    ["circle", { cx: "12", cy: "12", r: "10" }],
  ],
  fileJson: [
    ["path", { d: "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" }],
    ["path", { d: "M14 2v4a2 2 0 0 0 2 2h4" }],
    ["path", { d: "M10 12a1 1 0 0 0-1 1v1a1 1 0 0 1-1 1 1 1 0 0 1 1 1v1a1 1 0 0 0 1 1" }],
    ["path", { d: "M14 18a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1 1 1 0 0 1-1-1v-1a1 1 0 0 0-1-1" }],
  ],
  circleDot: [
    ["circle", { cx: "12", cy: "12", r: "10" }],
    ["circle", { cx: "12", cy: "12", r: "1" }],
  ],
  settings2: [
    ["path", { d: "M14 17H5" }],
    ["path", { d: "M19 7h-9" }],
    ["circle", { cx: "17", cy: "17", r: "3" }],
    ["circle", { cx: "7", cy: "7", r: "3" }],
  ],
  shieldCheck: [
    ["path", { d: "M20 13c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V5l8-3 8 3z" }],
    ["path", { d: "m9 12 2 2 4-4" }],
  ],
  terminal: [
    ["path", { d: "M12 19h8" }],
    ["path", { d: "m4 17 6-6-6-6" }],
  ],
  folderCog: [
    ["path", { d: "M10.5 20H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5l2 2h9a2 2 0 0 1 2 2v2.5" }],
    ["circle", { cx: "18", cy: "18", r: "3" }],
    ["path", { d: "M18 13.5v1" }],
    ["path", { d: "M18 21.5v1" }],
    ["path", { d: "M14.88 15.88l.7.7" }],
    ["path", { d: "M20.42 21.42l.7.7" }],
    ["path", { d: "M13.5 18h1" }],
    ["path", { d: "M21.5 18h1" }],
    ["path", { d: "M14.88 20.12l.7-.7" }],
    ["path", { d: "M20.42 14.58l.7-.7" }],
  ],
  searchCheck: [
    ["circle", { cx: "11", cy: "11", r: "7" }],
    ["path", { d: "m21 21-4.3-4.3" }],
    ["path", { d: "m9.5 11 1.5 1.5 3-3" }],
  ],
  slidersHorizontal: [
    ["line", { x1: "21", x2: "14", y1: "4", y2: "4" }],
    ["line", { x1: "10", x2: "3", y1: "4", y2: "4" }],
    ["line", { x1: "21", x2: "12", y1: "12", y2: "12" }],
    ["line", { x1: "8", x2: "3", y1: "12", y2: "12" }],
    ["line", { x1: "21", x2: "16", y1: "20", y2: "20" }],
    ["line", { x1: "12", x2: "3", y1: "20", y2: "20" }],
    ["line", { x1: "14", x2: "14", y1: "2", y2: "6" }],
    ["line", { x1: "8", x2: "8", y1: "10", y2: "14" }],
    ["line", { x1: "16", x2: "16", y1: "18", y2: "22" }],
  ],
  badges: [
    ["path", { d: "M12 15.5 8.5 17l.8-3.8-2.8-2.5 3.9-.4L12 6.8l1.6 3.5 3.9.4-2.8 2.5.8 3.8z" }],
    ["path", { d: "M7 18v3l5-2 5 2v-3" }],
  ],
  idCard: [
    ["rect", { x: "3", y: "5", width: "18", height: "14", rx: "2" }],
    ["path", { d: "M7 9h4" }],
    ["path", { d: "M7 13h3" }],
    ["circle", { cx: "16.5", cy: "12", r: "2.5" }],
    ["path", { d: "M14.5 16c.6-1 1.6-1.5 3-1.5S20 15 20.5 16" }],
  ],
};

export function icon(name, { className = "", stroke = 1.75 } = {}) {
  const nodes = ICONS[name] || ICONS.circleDot;
  const content = nodes.map(([tag, attrs]) => iconNode(tag, attrs)).join("");
  const classes = ["wb-icon", className].filter(Boolean).join(" ");
  return `
    <svg class="${classes}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      ${content}
    </svg>
  `;
}
