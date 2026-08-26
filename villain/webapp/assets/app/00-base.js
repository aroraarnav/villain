const $ = (s, r) => (r || document).querySelector(s);
const fmtPct = v => (100 * v).toFixed(0) + "%";
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const SVG = "http://www.w3.org/2000/svg";
const state = {tab: "players", session: null, player: null, roster: null, glossary: null, game: null, lastEvent: null, stepTimer: null, descOn: true, revealed: false, checkFold: false, checkFoldHand: null, heroPoll: null,
               sessionId: null, paused: false, muted: false, analysis: null, simGen: 0,
               dealHand: null, dealUntil: null, stepUntil: null, clockHold: null};

/* Hosted demo, signed out: the sample is readable, not writable. Set by the
   boot page. Absent under `villain test`, which has no accounts and must stay
   writable. */
const isGuest = () => !!window.villainGuest;
const GUEST_WHY = `<span class="hl">demo is read-only</span><br>
  Sign in to import your own hands and keep them across devices.`;

/** Grey a control out and explain why on hover. Disabled buttons do not
    receive pointer events, so the tip is bound to a wrapper. */
function guestLock(el) {
  if (!el) return null;
  el.disabled = true;
  const wrap = h("span", "guest-lock");
  if (el.parentNode) el.replaceWith(wrap);
  wrap.appendChild(el);
  bindTip(wrap, GUEST_WHY);
  return wrap;
}
function guestLockDrop(el) {
  if (!el) return;
  el.classList.add("locked");
  bindTip(el, GUEST_WHY);
}

/* An "i" that explains a term on hover. Everything the tool says in shorthand
   gets one, because a number nobody can interpret is worse than no number. */
function info(html) {
  const span = document.createElement("button");
  span.type = "button";
  span.className = "info"; span.textContent = "i";
  span.setAttribute("aria-label", "what this means");
  bindTip(span, html);
  return span;
}
function termTip(term) {
  const g = state.glossary;
  const text = g && g.terms[term];
  return text ? `<span class="hl">${esc(term)}</span><br>${esc(text)}` : esc(term);
}
/* Full explanation of a statistic: what it counts, and whether *this* player
   is over or under the field -- with the matching play implication. */
let _heroVoice = false;   // set by profileCard while rendering the hero
const _YOU = {"They're":"You're","they're":"you're","They've":"You've","they've":"you've",
  "Themselves":"Yourself","themselves":"yourself","Theirs":"Yours","theirs":"yours",
  "Their":"Your","their":"your","Them":"You","them":"you","They":"You","they":"you"};
function toYou(s) {
  return (s || "").replace(
    /\b(They're|they're|They've|they've|Themselves|themselves|Theirs|theirs|Their|their|Them|them|They|they)\b/g,
    m => _YOU[m]);
}

function statTip(stat, label, row) {
  const V = t => esc(_heroVoice ? toYou(t) : t);
  const g = state.glossary;
  const h = g && (g.stats[stat] || g.stats[stat.split(":")[0]]);
  if (!h) return esc(label || stat);
  let direction = "";
  if (row && row.population != null && row.value != null) {
    const delta = row.value - row.population;
    if (delta > 0.03) {
      direction = `<div class="dir"><b>High</b> vs field \u2014 ${V(h.high)}</div>`;
    } else if (delta < -0.03) {
      direction = `<div class="dir"><b>Low</b> vs field \u2014 ${V(h.low)}</div>`;
    } else {
      direction = `<div class="dir"><b>Near</b> the field \u2014 neither direction is clear yet.</div>`;
    }
  } else {
    direction = `<div class="dir"><b>High</b> ${V(h.high)}</div>
      <div class="dir"><b>Low</b> ${V(h.low)}</div>`;
  }
  return `<span class="hl">${esc(label || stat)}</span><br>${V(h.what)}
    ${direction}`;
}

function fieldRead(row) {
  const g = state.glossary;
  const h = g && (g.stats[row.stat] || g.stats[row.stat.split(":")[0]]);
  if (!h || row.population == null) return "";
  const V = t => esc(_heroVoice ? toYou(t) : t);
  const delta = row.value - row.population;
  if (delta > 0.03) return `<br><span class="hl">Over the field</span> \u2014 ${V(h.high)}`;
  if (delta < -0.03) return `<br><span class="hl">Under the field</span> \u2014 ${V(h.low)}`;
  return `<br><span class="muted">Near the field \u2014 neither over nor under yet.</span>`;
}

/* ---- tooltip ---- */
const tip = $("#tip");
function bindTip(el, html) {
  const place = (x, y) => {
    tip.innerHTML = html; tip.classList.add("on");
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let left = x + pad, top = y + pad;
    if (left + w > innerWidth - 8) left = x - w - pad;
    if (top + h > innerHeight - 8) top = y - h - pad;
    tip.style.left = Math.max(8, left) + "px";
    tip.style.top = Math.max(8, top) + "px";
  };
  const hide = () => tip.classList.remove("on");
  const anchor = () => {
    const r = el.getBoundingClientRect();
    place(r.left + r.width / 2, r.bottom - 6);
  };
  el.addEventListener("focus", anchor);
  el.addEventListener("blur", hide);
  el.addEventListener("touchstart", e => { e.preventDefault(); anchor(); }, {passive: false});
  el.addEventListener("mousemove", e => place(e.clientX, e.clientY));
  el.addEventListener("mouseleave", hide);
}
function el(tag, attrs, parent) {
  const node = document.createElementNS(SVG, tag);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(node);
  return node;
}
/* The HTML counterpart of `el`. Nearly every node this file builds is
   create-then-set-className-then-set-innerHTML, which was three statements and
   a repeated variable name at each of a hundred sites -- long enough to bury
   the markup that is the actual content of the line. */
function h(tag, cls, html) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (html != null) node.innerHTML = html;
  return node;
}
/* Every dialog in this app is a veil over a sheet, and ten places built that
   shell inline -- showEvidence built it three times, once per state, so its
   close handler was written three times too. This returns the `.sheet` to
   fill, and binds every `data-close` in it to dismiss the layer, so a dialog
   whose buttons sit at the bottom gets the same handling as one with a header
   button. `.sheet > .spread:first-child` is a real CSS hook, so the titled
   header stays the first child; `bare` is for the dialogs that want a plain
   heading and their own buttons underneath. */
function sheet(title, opts) {
  const {cls = "", host = "#modal", close = "Close", bare = false, body = ""} = opts || {};
  const layer = $(host);
  layer.innerHTML = `<div class="veil"><div class="sheet${cls ? " " + cls : ""}">${bare
    ? `<h2 style="margin-top:0">${title}</h2>`
    : `<div class="spread"><h2 style="margin:0">${title}</h2>
         <button class="act" data-close>${close}</button></div>`}${body}</div></div>`;
  for (const btn of layer.querySelectorAll("[data-close]"))
    btn.onclick = () => { layer.innerHTML = ""; };
  return $(".sheet", layer);
}

/* ---- the one mark this tool needs, over and over ----
   Interval band for the credible range, dot for the estimate, hairline tick
   for the field, warm tick for breakeven. Everything here is a frequency with
   uncertainty measured against a threshold, so it is all the same picture. */
function statTipHtml(row) {
  return `<b>${esc(row.label)}</b> \u2014 ${fmtPct(row.value)}<br>
    <span class="muted">95% range ${fmtPct(row.lo)}\u2013${fmtPct(row.hi)}</span><br>
    raw ${row.raw == null ? "\u2014" : fmtPct(row.raw)} of ${row.opps} ${esc(row.denominator)}<br>
    field ${fmtPct(row.population)}${row.breakeven != null
      ? `<br><span style="color:var(--warn)">${esc(row.breakeven_label)} ${fmtPct(row.breakeven)}</span>` : ""}
    ${fieldRead(row)}`;
}

function statRow(row) {
  // 44 rather than 34, and a 16px band rather than 12. This is the one picture
  // the whole tool turns on -- estimate, uncertainty, the field, the price of
  // being wrong -- and at the old scale the band read as a smudge and the
  // quarter marks did not exist, so the chart was decoration on a page whose
  // argument it was supposed to be carrying.
  const W = 300, H = 44, mid = 22, r = 6;
  const x = v => Math.max(0, Math.min(1, v)) * W;
  const svg = el("svg", {width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `${row.label}: ${fmtPct(row.value)}, range ${fmtPct(row.lo)} to ${fmtPct(row.hi)}`});
  // Quarter marks: without them the axis has no scale and 22% and 42% land in
  // visually identical places.
  for (const q of [0.25, 0.5, 0.75]) {
    el("line", {x1: x(q), y1: mid - 13, x2: x(q), y2: mid + 13,
                stroke: "var(--grid)", "stroke-width": 1}, svg);
  }
  el("line", {x1: 0, y1: mid, x2: W, y2: mid, stroke: "var(--grid)", "stroke-width": 1}, svg);
  // The interval gets an edge as well as a fill: a wash alone disappears into
  // the panel at any surface lighter than the page.
  const bw = Math.max(3, x(row.hi) - x(row.lo));
  el("rect", {x: x(row.lo), y: mid - 8, width: bw, height: 16, rx: 5,
              fill: "var(--band)", stroke: "var(--band-line)", "stroke-width": 1}, svg);
  // The field is a reference, not a measurement of this player, so it is
  // dashed -- solid hairline and solid dot read as two of the same kind.
  el("line", {x1: x(row.population), y1: mid - 13, x2: x(row.population), y2: mid + 13,
              stroke: "var(--axis)", "stroke-width": 1, "stroke-dasharray": "2 2"}, svg);
  if (row.breakeven != null) {
    el("line", {x1: x(row.breakeven), y1: mid - 14, x2: x(row.breakeven), y2: mid + 14,
                stroke: "var(--warn)", "stroke-width": 2.5, "stroke-linecap": "round"}, svg);
  }
  el("circle", {cx: x(row.value), cy: mid, r: r + 2.5, fill: "var(--panel)"}, svg);
  el("circle", {cx: x(row.value), cy: mid, r: r, fill: "var(--mark-3)"}, svg);
  const hit = el("rect", {x: 0, y: 0, width: W, height: H, fill: "transparent"}, svg);
  bindTip(hit, statTipHtml(row));
  return svg;
}

/* The same picture at HUD size: band, estimate, breakeven. No field hairline
   and no quarter marks -- at 12px tall they collide with the dot, and the cell
   already carries the figure in type. Hover gives the full reading. */
function sparkRow(row) {
  const W = 100, H = 12, mid = 6;
  const x = v => Math.max(0, Math.min(1, v)) * W;
  const svg = el("svg", {viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none",
    role: "img", "aria-label": `${row.label}: ${fmtPct(row.value)}`});
  el("line", {x1: 0, y1: mid, x2: W, y2: mid, stroke: "var(--grid)", "stroke-width": 1}, svg);
  el("rect", {x: x(row.lo), y: mid - 3.5, width: Math.max(2, x(row.hi) - x(row.lo)),
              height: 7, rx: 3, fill: "var(--band)", stroke: "var(--band-line)",
              "stroke-width": 0.75}, svg);
  if (row.breakeven != null) {
    el("line", {x1: x(row.breakeven), y1: mid - 5.5, x2: x(row.breakeven), y2: mid + 5.5,
                stroke: "var(--warn)", "stroke-width": 1.5}, svg);
  }
  el("circle", {cx: x(row.value), cy: mid, r: 3.6, fill: "var(--inset)"}, svg);
  el("circle", {cx: x(row.value), cy: mid, r: 2.6, fill: "var(--mark-3)"}, svg);
  return svg;
}

/* A 0-100 score as a filled arc. The score was a number inside a bordered
   circle, which drew a ring whose length said nothing; the arc is the same
   footprint with the quantity actually in it. */
function skillGauge(score, tone) {
  const S = 62, c = S / 2, r = 26, circ = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(100, score)) / 100;
  const svg = el("svg", {viewBox: `0 0 ${S} ${S}`, "aria-hidden": "true"});
  const g = el("g", {transform: `rotate(-90 ${c} ${c})`}, svg);
  // The track has to be visible or the arc is just a shape: "62" is only
  // readable as sixty-two out of a hundred if the hundred is drawn too.
  el("circle", {cx: c, cy: c, r, fill: "none", stroke: "var(--band)", "stroke-width": 3}, g);
  el("circle", {cx: c, cy: c, r, fill: "none", stroke: tone || "var(--mark-3)",
                "stroke-width": 3, "stroke-linecap": "round",
                "stroke-dasharray": `${(frac * circ).toFixed(2)} ${circ.toFixed(2)}`}, g);
  return svg;
}

function bar(value, max, color, width) {
  const W = width || 150, H = 14;
  const svg = el("svg", {width: W, height: H, viewBox: `0 0 ${W} ${H}`});
  el("rect", {x: 0, y: 3, width: W, height: 8, rx: 4, fill: "var(--grid)"}, svg);
  const w = max > 0 ? Math.max(3, (value / max) * W) : 3;
  el("rect", {x: 0, y: 3, width: w, height: 8, rx: 4, fill: color}, svg);
  return svg;
}

async function get(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  return res.json();
}
async function post(url, body) {
  const res = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"},
                                body: JSON.stringify(body || {})});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

/* The one loading state. Three places had grown their own -- a muted line of
   text reading "loading…", "reading the sitting…", "loading hand…" -- while
   the tab switch used a spinner, so whether the tool looked busy or looked
   broken depended on which corner of it you were in. */
function loadingBlock(label) {
  const div = h("div", "loading", `<span class="spinner" aria-hidden="true"></span>
    <span>${esc(label)}</span>`);
  return div;
}
