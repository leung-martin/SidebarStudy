"""Builds mastery.html: a page for exploring your flashcard mastery as YoYo records it.

The page is self-contained (data embedded as JSON) and regenerated each time
you open it from the app, so it always shows the latest synced levels.
"""
import json
import time

LEVEL_KEYS = ["new", "0", "1", "2", "3", "4", "5"]
ACTIVITY_SHOWN = 3000  # newest receipts put on the page


def read_log(path):
    """The YoYo activity log (one JSON object per line), oldest first."""
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    pass  # a line cut short by a crash
    except OSError:
        pass
    return entries


def build(decks, level_of, synced_at, path, history=None, activity=None):
    """decks: {course: [card]}; level_of(code) -> -1..5; synced_at: epoch of last YoYo download;
    history: {"YYYY-MM-DD": {mastered, learning, new, easy, good, hard}} for the progress charts;
    activity: the YoYo activity log entries (see YoyoSync), shown as timestamped receipts."""
    courses = []
    for name, cards in decks.items():
        rows = []
        for c in cards:
            rows.append([c["code"], c["simplified"], c["pinyin"], c["english"], level_of(c["code"]),
                         c.get("level", ""), c.get("unit", "").strip(), c.get("lesson", "").strip()])
        courses.append({"name": name, "cards": rows})
    days = [dict(date=d, **v) for d, v in sorted((history or {}).items())]
    log = [dict(e, when=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("t", 0))))
           for e in (activity or [])[-ACTIVITY_SHOWN:]]
    data = {"courses": courses, "history": days, "activity": log,
            "synced": time.strftime("%Y-%m-%d %H:%M", time.localtime(synced_at)) if synced_at else "",
            "generated": time.strftime("%Y-%m-%d %H:%M")}
    html = TEMPLATE.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flashcard Mastery</title>
<style>
  :root {
    --bg: #101219; --panel: #161925; --card: #1a1d29; --btn: #252937; --track: #2a2e40;
    --ink: #f5f5fa; --dim: #8b90a8; --violet: #8b6cff;
    --s1: #3987e5; --s2: #d95926; --s3: #199e70;
    --new: #3a3f55; --l0: #ff5470; --l1: #ff7a5c; --l2: #ffa53d; --l3: #ffcf4d; --l4: #8fe3a0; --l5: #2ee59d;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 14px/1.45 "Segoe UI", system-ui, sans-serif; }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 20px 16px 60px; }
  h1 { font-size: 22px; margin: 0 0 2px; }
  .sub { color: var(--dim); font-size: 12px; margin-bottom: 16px; }
  .summary { background: var(--panel); border-radius: 12px; padding: 16px; margin-bottom: 14px; }
  .stats { display: flex; flex-wrap: wrap; gap: 22px; margin-bottom: 12px; }
  .stat b { display: block; font-size: 24px; }
  .stat span { color: var(--dim); font-size: 12px; font-weight: 600; }
  .bar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: var(--track); }
  .bar i { display: block; height: 100%; }
  .bar.big { height: 16px; border-radius: 8px; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .chip { border: 0; border-radius: 999px; padding: 4px 10px; font: 600 12px "Segoe UI", sans-serif;
          background: var(--btn); color: var(--ink); cursor: pointer; display: inline-flex; gap: 6px; align-items: center; }
  .chip .dot { width: 9px; height: 9px; border-radius: 50%; }
  .chip.off { opacity: .35; }
  .chip .n { color: var(--dim); }
  .tools { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0; position: sticky; top: 0;
           background: var(--bg); padding: 8px 0; z-index: 2; }
  select, input { background: var(--btn); color: var(--ink); border: 0; border-radius: 8px;
                  padding: 8px 10px; font: 14px "Segoe UI", sans-serif; }
  input { flex: 1; min-width: 180px; }
  details { background: var(--card); border-radius: 10px; margin: 6px 0; }
  details details { background: transparent; margin: 2px 0 2px 14px; border-left: 2px solid var(--track); border-radius: 0; }
  summary { list-style: none; cursor: pointer; padding: 9px 12px; display: grid;
            grid-template-columns: 1fr minmax(90px, 260px) 92px; gap: 12px; align-items: center; }
  summary::-webkit-details-marker { display: none; }
  summary .t { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  summary .t::before { content: "▸ "; color: var(--dim); }
  details[open] > summary .t::before { content: "▾ "; }
  summary .pct { text-align: right; color: var(--dim); font-size: 12px; font-weight: 600; white-space: nowrap; }
  .course > summary .t { font-size: 16px; }
  table { width: 100%; border-collapse: collapse; margin: 2px 0 8px 14px; width: calc(100% - 14px); }
  td { padding: 6px 8px; border-top: 1px solid var(--track); vertical-align: middle; }
  td.hz { font: 600 20px "Microsoft YaHei UI", sans-serif; white-space: nowrap; }
  td.py { color: var(--violet); font-weight: 600; white-space: nowrap; }
  td.en { color: var(--dim); }
  td.code { color: var(--dim); font-size: 11px; white-space: nowrap; }
  .lvl { display: inline-flex; gap: 3px; align-items: center; }
  .lvl i { width: 14px; height: 7px; border-radius: 2px; background: var(--track); }
  .tag { font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 999px; }
  .results td.where { color: var(--dim); font-size: 11px; }
  .empty { color: var(--dim); padding: 20px; text-align: center; }
  /* progress charts: categorical slots 1-3 of the reference palette, dark steps */
  .progress { background: var(--panel); border-radius: 12px; padding: 16px; margin-bottom: 14px; }
  .progress h2 { font-size: 15px; margin: 0 0 10px; }
  .progress .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
  .chart h3 { font-size: 13px; margin: 0 0 6px; color: var(--ink); }
  .chart .note { color: var(--dim); font-size: 12px; margin: 4px 0 0; }
  .chart svg { width: 100%; height: 200px; display: block; overflow: visible; }
  .chart .grid { stroke: var(--track); stroke-width: 1; }
  .chart .axis { fill: var(--dim); font: 11px "Segoe UI", sans-serif; }
  .chart .line { fill: none; stroke: var(--s1); stroke-width: 2; stroke-linejoin: round; }
  .chart .dot { fill: var(--s1); stroke: var(--panel); stroke-width: 2; }
  .chart .hit { fill: transparent; cursor: crosshair; }
  .chart .xh { stroke: var(--dim); stroke-width: 1; stroke-dasharray: 3 3; visibility: hidden; }
  .chart .legend { display: flex; gap: 14px; font-size: 12px; color: var(--dim); margin-top: 6px; }
  .chart .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
  .tip { position: fixed; pointer-events: none; background: var(--btn); color: var(--ink); border-radius: 8px;
         padding: 6px 9px; font-size: 12px; box-shadow: 0 4px 14px rgba(0,0,0,.4); display: none; z-index: 5; white-space: nowrap; }
  .tip b { display: block; margin-bottom: 2px; }
  details.table { margin-top: 10px; }
  details.table summary { display: inline; padding: 0; color: var(--dim); font-size: 12px; cursor: pointer; }
  details.table table { margin: 6px 0 0; width: 100%; font-size: 12px; }
  details.table td, details.table th { padding: 3px 8px; text-align: right; border-top: 1px solid var(--track); }
  details.table th { color: var(--dim); font-weight: 600; }
  details.table td:first-child, details.table th:first-child { text-align: left; }
  /* YoYo activity receipts */
  .activity .filters { display: flex; flex-wrap: wrap; gap: 14px; color: var(--dim); font-size: 12px; margin-bottom: 8px; }
  .activity .filters label { cursor: pointer; display: inline-flex; gap: 6px; align-items: center; }
  .activity .filters input { flex: none; min-width: 0; margin: 0; }
  .activity .day { color: var(--dim); font-size: 12px; font-weight: 700; margin: 12px 0 4px; }
  .activity .log { max-height: 460px; overflow-y: auto; }
  .rcpt { border-top: 1px solid var(--track); padding: 6px 0; font-size: 13px; }
  .rcpt .when { color: var(--dim); font-variant-numeric: tabular-nums; margin-right: 10px; }
  .rcpt .kind { font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 999px; margin-right: 8px; background: var(--btn); }
  .rcpt .kind.upload { color: #4cd3a0; } .rcpt .kind.download { color: #6aa8ff; } .rcpt .kind.due { color: var(--violet); }
  .rcpt .kind.error, .rcpt .kind.blocked, .rcpt .kind.dropped { color: #ff6b8b; } .rcpt .kind.check { color: var(--dim); }
  .rcpt details { background: transparent; margin: 4px 0 0; }
  .rcpt details summary { display: inline; padding: 0; color: var(--dim); font-size: 12px; }
  .rcpt table { margin: 4px 0 0 4px; width: auto; font-size: 12px; }
  .rcpt td { padding: 3px 8px; }
  .rcpt td.hz { font-size: 16px; }
  .up { color: #4cd3a0; } .down { color: #ff6b8b; }
  @media (max-width: 760px) { .progress .charts { grid-template-columns: 1fr; } }
  @media (max-width: 640px) {
    summary { grid-template-columns: 1fr 70px; }
    summary .bar { display: none; }
    td.en, td.code { display: none; }
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>Flashcard Mastery</h1>
  <div class="sub" id="sub"></div>
  <section class="progress" id="progress"></section>
  <section class="progress activity" id="activity"></section>
  <div class="tip" id="tip"></div>
  <div class="summary">
    <div class="stats" id="stats"></div>
    <div class="bar big" id="bigbar"></div>
    <div class="legend" id="legend"></div>
  </div>
  <div class="tools">
    <select id="course"></select>
    <input id="q" type="search" placeholder="Search characters, pinyin, English or code…">
  </div>
  <div id="out"></div>
</div>
<script>
const DATA = /*DATA*/null;
const KEYS = ["new", "0", "1", "2", "3", "4", "5"];
const NAMES = { new: "New", 0: "Level 0", 1: "Level 1", 2: "Level 2", 3: "Level 3", 4: "Level 4", 5: "Mastered" };
const COLOR = k => `var(--${k === "new" ? "new" : "l" + k})`;
const keyOf = lvl => (lvl < 0 ? "new" : String(lvl));
const shown = new Set(KEYS);
let course = "", query = "";
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// card rows: [code, simplified, pinyin, english, level, levelName, unit, lesson]
const ALL = [];
for (const c of DATA.courses) for (const r of c.cards) ALL.push({ course: c.name, row: r });

function counts(rows) {
  const n = Object.fromEntries(KEYS.map(k => [k, 0]));
  for (const r of rows) n[keyOf(r[4])]++;
  return n;
}
function bar(n, total, cls = "bar") {
  return `<div class="${cls}">` + KEYS.map(k => n[k] ? `<i style="width:${n[k] / total * 100}%;background:${COLOR(k)}" title="${NAMES[k]}: ${n[k]}"></i>` : "").join("") + "</div>";
}
function levelBar(lvl) {
  if (lvl < 0) return `<span class="tag" style="background:var(--new)">NEW</span>`;
  const col = lvl >= 5 ? "var(--l5)" : lvl > 3 ? "var(--l4)" : lvl > 1 ? "var(--l2)" : "var(--l0)";
  return `<span class="lvl" title="Level ${lvl}">` + [0, 1, 2, 3, 4].map(i => `<i style="${i < lvl ? "background:" + col : ""}"></i>`).join("") + "</span>";
}
function cardRows(rows, where = false) {
  return `<table${where ? ' class="results"' : ""}>` + rows.map(r =>
    `<tr><td class="hz">${esc(r[1])}</td><td class="py">${esc(r[2])}</td><td class="en">${esc(r[3])}</td>` +
    (where ? `<td class="where">${esc(r.where)}</td>` : `<td class="code">${esc(r[0])}</td>`) +
    `<td>${levelBar(r[4])}</td></tr>`).join("") + "</table>";
}
const visible = r => shown.has(keyOf(r[4]));
function pct(n, total) { return total ? `${n["5"]} / ${total} · ${Math.round(n["5"] / total * 100)}%` : ""; }

function group(rows, idx) {
  const m = new Map();
  for (const r of rows) { const k = r[idx] || "—"; if (!m.has(k)) m.set(k, []); m.get(k).push(r); }
  return m;
}
function node(title, rows, cls, inner) {
  const n = counts(rows);
  const el = document.createElement("details");
  if (cls) el.className = cls;
  el.innerHTML = `<summary><span class="t">${esc(title)}</span>${bar(n, rows.length)}<span class="pct">${pct(n, rows.length)}</span></summary>`;
  let filled = false;
  el.addEventListener("toggle", () => {   // build children only when opened: 17k cards stay fast
    if (!el.open || filled) return;
    filled = true;
    inner(el);
  });
  return el;
}

function render() {
  const out = document.getElementById("out");
  out.innerHTML = "";
  const pool = ALL.filter(x => !course || x.course === course);
  const rows = pool.map(x => x.row);
  summary(rows);
  if (query) {
    const q = query.toLowerCase();
    const plain = s => s.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, "");
    const hits = pool.filter(({ row: r }) => visible(r) && (r[1].includes(query) || r[0].toLowerCase().includes(q) ||
      r[3].toLowerCase().includes(q) || plain(r[2]).includes(plain(q)))).slice(0, 400)
      .map(({ course, row }) => Object.assign([...row], { where: `${course} · ${row[5]} · ${row[7]}` }));
    out.innerHTML = hits.length ? cardRows(hits, true) + (hits.length === 400 ? '<div class="empty">Showing the first 400 matches.</div>' : "")
                                : '<div class="empty">No cards match.</div>';
    return;
  }
  for (const c of DATA.courses) {
    if (course && c.name !== course) continue;
    const crow = c.cards;
    out.appendChild(node(c.name, crow, "course", el => {
      for (const [lv, lvRows] of group(crow, 5)) el.appendChild(node(lv, lvRows, "", el2 => {
        for (const [unit, uRows] of group(lvRows, 6)) el2.appendChild(node(unit, uRows, "", el3 => {
          for (const [lesson, lRows] of group(uRows, 7)) el3.appendChild(node(lesson, lRows, "", el4 => {
            const vis = lRows.filter(visible);
            el4.insertAdjacentHTML("beforeend", vis.length ? cardRows(vis) : '<div class="empty">No cards at the selected levels.</div>');
          }));
        }));
      }));
    }));
  }
}

function summary(rows) {
  const n = counts(rows), total = rows.length;
  const learning = KEYS.slice(1, 6).reduce((s, k) => s + n[k], 0);
  document.getElementById("stats").innerHTML =
    `<div class="stat"><b>${n["5"].toLocaleString()}</b><span>MASTERED · ${Math.round(n["5"] / total * 100)}%</span></div>` +
    `<div class="stat"><b>${learning.toLocaleString()}</b><span>LEARNING (LEVEL 0–4)</span></div>` +
    `<div class="stat"><b>${n.new.toLocaleString()}</b><span>NEW</span></div>` +
    `<div class="stat"><b>${total.toLocaleString()}</b><span>CARDS</span></div>`;
  document.getElementById("bigbar").outerHTML = bar(n, total, "bar big").replace('class="bar big"', 'class="bar big" id="bigbar"');
  document.getElementById("legend").innerHTML = KEYS.map(k =>
    `<button class="chip ${shown.has(k) ? "" : "off"}" data-k="${k}"><span class="dot" style="background:${COLOR(k)}"></span>${NAMES[k]} <span class="n">${n[k].toLocaleString()}</span></button>`).join("");
  document.querySelectorAll(".chip").forEach(b => b.onclick = () => {
    shown.has(b.dataset.k) ? shown.delete(b.dataset.k) : shown.add(b.dataset.k);
    render();
  });
}

// ---------- progress over time (one entry per day the app was used)
const H = DATA.history || [];
const tip = document.getElementById("tip");
const fmtDate = d => new Date(d + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
const dayNum = d => Math.round(new Date(d + "T00:00:00") / 864e5);
function showTip(e, html) { tip.innerHTML = html; tip.style.display = "block";
  tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8) + "px"; tip.style.top = (e.clientY + 14) + "px"; }
function hideTip() { tip.style.display = "none"; }
function niceTicks(lo, hi, n = 4) {
  const span = Math.max(hi - lo, 1), raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map(m => m * mag).find(s => s >= raw);
  const t = [];  // from below lo up to the first tick at or above hi, so nothing pokes out the top
  for (let v = Math.floor(lo / step) * step; ; v += step) { t.push(v); if (v >= hi - 1e-9) break; }
  return t;
}
const W = 600, PH = 200, M = { l: 44, r: 10, t: 10, b: 24 };
function frame(ticks, y, xs, labels) {
  let s = ticks.map(v => `<line class="grid" x1="${M.l}" x2="${W - M.r}" y1="${y(v)}" y2="${y(v)}"/>` +
    `<text class="axis" x="${M.l - 6}" y="${y(v) + 4}" text-anchor="end">${v.toLocaleString()}</text>`).join("");
  const every = Math.ceil(labels.length / 6);
  s += labels.map((d, i) => i % every ? "" : `<text class="axis" x="${xs(i)}" y="${PH - 6}" text-anchor="middle">${fmtDate(d)}</text>`).join("");
  return s;
}
function progress() {
  const box = document.getElementById("progress");
  if (!H.length) { box.innerHTML = '<h2>Progress</h2><div class="sub">No history yet — it starts with your first session.</div>'; return; }
  const last = H[H.length - 1], first = H[0];
  const weekAgo = [...H].reverse().find(h => dayNum(last.date) - dayNum(h.date) >= 7) || first;
  const gained = last.mastered - weekAgo.mastered;
  const answered = (last.easy || 0) + (last.good || 0) + (last.hard || 0);
  const firstTry = answered ? Math.round((last.easy || 0) / answered * 100) : null;
  const since = weekAgo === first && dayNum(last.date) - dayNum(first.date) < 7 ? `since ${fmtDate(first.date)}` : "in 7 days";
  box.innerHTML = `<h2>Progress</h2>
    <div class="stats">
      <div class="stat"><b>${last.mastered.toLocaleString()}</b><span>MASTERED</span></div>
      <div class="stat"><b>${gained >= 0 ? "+" : ""}${gained.toLocaleString()}</b><span>MASTERED ${since.toUpperCase()}</span></div>
      <div class="stat"><b>${answered}</b><span>ANSWERED TODAY</span></div>
      <div class="stat"><b>${firstTry === null ? "–" : firstTry + "%"}</b><span>RIGHT FIRST TRY TODAY</span></div>
    </div>
    <div class="charts">
      <div class="chart"><h3>Mastered cards</h3><svg id="trend" viewBox="0 0 ${W} ${PH}" role="img" aria-label="Mastered cards per day"></svg>
        ${H.length < 2 ? '<p class="note">Tracking started ' + fmtDate(first.date) + ' — the line grows one point per day you study.</p>' : ""}</div>
      <div class="chart"><h3>Answers per day</h3><svg id="bars" viewBox="0 0 ${W} ${PH}" role="img" aria-label="Answers per day by grade"></svg>
        <div class="legend"><span><i style="background:var(--s1)"></i>Easy (first try)</span><span><i style="background:var(--s2)"></i>Not Bad</span><span><i style="background:var(--s3)"></i>Forgot (passed)</span></div></div>
    </div>
    <details class="table"><summary>Show as table</summary><table><tr><th>Day</th><th>Mastered</th><th>Learning</th><th>New</th><th>Easy</th><th>Not Bad</th><th>Forgot</th></tr>
      ${[...H].reverse().map(h => `<tr><td>${fmtDate(h.date)}</td><td>${h.mastered.toLocaleString()}</td><td>${h.learning}</td><td>${h.new.toLocaleString()}</td><td>${h.easy || 0}</td><td>${h.good || 0}</td><td>${h.hard || 0}</td></tr>`).join("")}
    </table></details>`;

  // trend line: mastered per day, x by calendar day so skipped days show as gaps in time
  const d0 = dayNum(first.date), span = Math.max(dayNum(last.date) - d0, 1);
  const xs = i => H.length < 2 ? (M.l + W - M.r) / 2 : M.l + (dayNum(H[i].date) - d0) / span * (W - M.l - M.r);
  const vals = H.map(h => h.mastered), lo = Math.min(...vals), hi = Math.max(...vals);
  const ticks = niceTicks(lo - Math.max(5, (hi - lo) * 0.1), hi + Math.max(5, (hi - lo) * 0.1));
  const y = v => M.t + (1 - (v - ticks[0]) / (ticks[ticks.length - 1] - ticks[0])) * (PH - M.t - M.b);
  const pts = H.map((h, i) => `${xs(i)},${y(h.mastered)}`).join(" ");
  const trend = document.getElementById("trend");
  trend.innerHTML = frame(ticks, y, xs, H.map(h => h.date)) +
    `<polyline class="line" points="${pts}"/>` +
    (H.length <= 60 ? H.map((h, i) => `<circle class="dot" cx="${xs(i)}" cy="${y(h.mastered)}" r="4.5"/>`).join("") : "") +
    `<line class="xh" id="xh" y1="${M.t}" y2="${PH - M.b}"/>` +
    `<rect class="hit" x="${M.l}" y="0" width="${W - M.l - M.r}" height="${PH}"/>`;
  const hit = trend.querySelector(".hit"), xh = trend.querySelector("#xh");
  hit.addEventListener("mousemove", e => {  // crosshair: nearest day
    const p = trend.createSVGPoint(); p.x = e.clientX; p.y = e.clientY;
    const sx = p.matrixTransform(trend.getScreenCTM().inverse()).x;
    let i = 0; H.forEach((_, k) => { if (Math.abs(xs(k) - sx) < Math.abs(xs(i) - sx)) i = k; });
    const h = H[i], prev = H[i - 1], diff = prev ? h.mastered - prev.mastered : null;
    xh.setAttribute("x1", xs(i)); xh.setAttribute("x2", xs(i)); xh.style.visibility = "visible";
    showTip(e, `<b>${fmtDate(h.date)}</b>Mastered ${h.mastered.toLocaleString()}` +
      (diff !== null ? ` (${diff >= 0 ? "+" : ""}${diff})` : "") + `<br>Learning ${h.learning} · New ${h.new.toLocaleString()}`);
  });
  hit.addEventListener("mouseleave", () => { xh.style.visibility = "hidden"; hideTip(); });

  // stacked bars: answers per day by grade, 2px gaps between segments
  const totals = H.map(h => (h.easy || 0) + (h.good || 0) + (h.hard || 0));
  const bt = niceTicks(0, Math.max(10, ...totals));
  const by = v => M.t + (1 - v / bt[bt.length - 1]) * (PH - M.t - M.b);
  const bw = Math.max(4, Math.min(28, (W - M.l - M.r) / Math.max(span + 1, H.length) * 0.6));
  const bx = i => H.length < 2 ? (M.l + W - M.r) / 2 : M.l + bw / 2 + (dayNum(H[i].date) - d0) / span * (W - M.l - M.r - bw);
  let bars = frame(bt, by, bx, H.map(h => h.date));
  H.forEach((h, i) => {
    let base = 0;
    [["easy", "s1"], ["good", "s2"], ["hard", "s3"]].forEach(([k, s]) => {
      const v = h[k] || 0; if (!v) return;
      const top = by(base + v), bottom = by(base);
      bars += `<rect x="${bx(i) - bw / 2}" y="${top}" width="${bw}" height="${Math.max(1, bottom - top - (base ? 2 : 0))}" rx="2" fill="var(--${s})"/>`;
      base += v;
    });
    bars += `<rect class="hit" data-i="${i}" x="${bx(i) - Math.max(bw, 14) / 2}" y="${M.t}" width="${Math.max(bw, 14)}" height="${PH - M.t - M.b}"/>`;
  });
  const barsEl = document.getElementById("bars");
  barsEl.innerHTML = bars;
  barsEl.querySelectorAll(".hit").forEach(r => {
    r.addEventListener("mousemove", e => { const h = H[+r.dataset.i];
      showTip(e, `<b>${fmtDate(h.date)}</b>Easy ${h.easy || 0} · Not Bad ${h.good || 0} · Forgot ${h.hard || 0}`); });
    r.addEventListener("mouseleave", hideTip);
  });
}
progress();

// ---------- YoYo activity: a timestamped receipt of every exchange with YoYo
function activity() {
  const box = document.getElementById("activity");
  const A = DATA.activity || [];
  const card = {};
  for (const c of DATA.courses) for (const r of c.cards) card[r[0]] = r;
  const ANSWER = { easy: "Easy", good: "Not Bad", hard: "Forgot" };
  const LV = v => v < 0 ? "new" : v >= 5 ? "5 ✓" : String(v);
  const KINDS = { upload: "Sent", download: "Downloaded", due: "Due list", check: "Check", "sign-in": "Sign-in",
                  blocked: "Blocked", dropped: "Dropped", error: "Problem" };
  const show = { check: false };
  function draw() {
    const rows = A.filter(e => show[e.kind] !== false).reverse();
    let day = "", html = "";
    for (const e of rows) {
      const d = e.when.slice(0, 10);
      if (d !== day) { day = d; html += `<div class="day">${fmtDate(d)}</div>`; }
      let detail = "";
      if (e.cards && e.cards.length) {
        detail = `<details><summary>${e.cards.length} card${e.cards.length > 1 ? "s" : ""}</summary><table>` +
          e.cards.map(c => {
            const r = card[c.code] || [];
            const cls = c.after > c.before ? "up" : c.after < c.before ? "down" : "";
            return `<tr><td class="hz">${esc(r[1] || "")}</td><td class="py">${esc(r[2] || "")}</td>` +
              `<td>${c.answer ? ANSWER[c.answer] : ""}</td><td class="${cls}">${LV(c.before)} → ${LV(c.after)}</td>` +
              `<td class="code">${esc(c.code)}</td></tr>`;
          }).join("") + `</table></details>`;
      }
      html += `<div class="rcpt"><span class="when">${e.when.slice(11)}</span>` +
        `<span class="kind ${e.kind}">${KINDS[e.kind] || esc(e.kind)}</span>${esc(e.text)}${detail}</div>`;
    }
    box.querySelector(".log").innerHTML = html || '<div class="sub">Nothing yet.</div>';
  }
  box.innerHTML = `<h2>YoYo activity</h2>
    <div class="filters"><span>Every exchange with YoYo, newest first. Click "cards" to see each card's level before → after.</span>
      <label><input type="checkbox" id="showChecks"> show routine 5-minute checks</label></div>
    <div class="log"></div>`;
  if (!A.length) { box.querySelector(".log").innerHTML = '<div class="sub">Nothing yet — receipts start with the next sync.</div>'; return; }
  document.getElementById("showChecks").onchange = e => { show.check = e.target.checked; draw(); };
  draw();
}
activity();

document.getElementById("sub").textContent =
  (DATA.synced ? `Levels as of your last YoYo download (${DATA.synced})` : "Not synced with YoYo yet") +
  ` · generated ${DATA.generated} · click a course, level, unit or lesson to open it; the level chips filter cards`;
const sel = document.getElementById("course");
sel.innerHTML = `<option value="">All courses</option>` + DATA.courses.map(c => `<option>${esc(c.name)}</option>`).join("");
sel.onchange = () => { course = sel.value; render(); };
let t;
document.getElementById("q").oninput = e => { clearTimeout(t); t = setTimeout(() => { query = e.target.value.trim(); render(); }, 150); };
render();
</script>
</body>
</html>
"""
