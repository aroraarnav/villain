/* ---- tab 3: hero ---- */
const RANK_ORDER = "AKQJT98765432";

function rangeGrid(grid) {
  const wrap = h("div", "range-grid");
  for (let i = 0; i < 13; i++) {
    for (let j = 0; j < 13; j++) {
      const hi = RANK_ORDER[i], lo = RANK_ORDER[j];
      const cls = i === j ? hi + lo : i < j ? hi + lo + "s" : lo + hi + "o";
      const g = (grid || {})[cls];
      const dealt = g ? g.dealt : 0, played = g ? g.played : 0;
      const pct = dealt ? played / dealt : 0;
      const cell = h("div", "range-cell" + (pct > 0.5 ? " dark-text" : ""));
      cell.style.background =
        `color-mix(in oklab, var(--ink) ${Math.round(pct * 100)}%, var(--panel))`;
      cell.textContent = cls;
      bindTip(cell, `<b>${cls}</b><br>played ${dealt ? fmtPct(pct) : "—"}` +
        (dealt ? ` (${played} of ${dealt})` : " -- never dealt"));
      wrap.appendChild(cell);
    }
  }
  return wrap;
}

const POSITION_ORDER =
  ["UTG", "UTG1", "UTG2", "MP", "MP1", "MP2", "LJ", "HJ", "CO", "BTN", "SB", "BB"];

/* Position, drawn as the table it describes. A six-row bar chart is a correct
   picture of the same numbers and a worse one: a player does not hold "UTG,
   HJ, CO, BTN, SB, BB" as a list, they hold it as a ring with the button on
   it, and the whole point of the panel is how much wider the seats near the
   button get played. Shade carries magnitude, the same convention the range
   grid uses -- normalized against this player's own widest seat, because
   played frequencies live in a 14-23% band and a 0-100% ramp would render all
   six the same near-empty tint. */
function positionRing(ranges) {
  const seats = ranges.filter(r => r.hands)
    .sort((a, b) => POSITION_ORDER.indexOf(a.position) - POSITION_ORDER.indexOf(b.position));
  if (seats.length < 2) return null;
  const played = seats.map(r => (r.raised + r.called) / r.hands);
  const max = Math.max(...played) || 1;
  const W = 340, H = 208, cx = W / 2, cy = H / 2, rx = 122, ry = 66;
  const bw = 58, bh = 38;
  const svg = el("svg", {viewBox: `0 0 ${W} ${H}`, class: "pos-ring", role: "img",
    "aria-label": "how often each seat is played"});
  // The button anchors the diagram at the bottom right, so the picture is the
  // same shape whatever table size produced it.
  const btn = Math.max(0, seats.findIndex(r => r.position === "BTN"));
  const step = 360 / seats.length;
  // A filled oval, not an outline: the seats have to sit on something for the
  // arrangement to read as a table rather than as six boxes in a circle.
  el("ellipse", {cx, cy, rx: rx - 6, ry: ry - 6, fill: "var(--inset)",
                 stroke: "var(--line)", "stroke-width": 1}, svg);
  seats.forEach((r, i) => {
    const a = ((55 + (i - btn) * step) * Math.PI) / 180;
    const x = cx + rx * Math.cos(a), y = cy + ry * Math.sin(a);
    const shade = 0.12 + 0.78 * (played[i] / max);
    const g = el("g", {transform: `translate(${(x - bw / 2).toFixed(1)} ${(y - bh / 2).toFixed(1)})`}, svg);
    el("rect", {width: bw, height: bh, rx: 8, stroke: "var(--line)", "stroke-width": 1,
      fill: `color-mix(in oklab, var(--ink) ${Math.round(shade * 100)}%, var(--panel))`}, g);
    const ink = shade > 0.55 ? "var(--panel)" : "var(--ink)";
    const name = el("text", {x: bw / 2, y: 16, "text-anchor": "middle", fill: ink,
      "font-size": 11, "font-weight": 700, "letter-spacing": ".04em"}, g);
    name.textContent = r.position;
    const val = el("text", {x: bw / 2, y: 29, "text-anchor": "middle", fill: ink,
      "font-size": 11, class: "fig-t"}, g);
    val.textContent = fmtPct(played[i]);
    if (r.position === "BTN") {
      // The dealer disc: the one seat whose name every player reads as a
      // position on a circle rather than as a label.
      el("circle", {cx: bw + 11, cy: bh / 2, r: 8, fill: "var(--panel)",
                    stroke: "var(--edge)", "stroke-width": 1}, g);
      const d = el("text", {x: bw + 11, y: bh / 2 + 3.5, "text-anchor": "middle",
        fill: "var(--ink)", "font-size": 9, "font-weight": 700}, g);
      d.textContent = "D";
    }
    const hit = el("rect", {width: bw, height: bh, rx: 8, fill: "transparent"}, g);
    bindTip(hit, `<b>${esc(r.position)}</b> — played ${fmtPct(played[i])}<br>
      <span class="muted">${r.raised} raised · ${r.called} called of ${r.hands} dealt</span>`);
  });
  return svg;
}

/* A graded report (fold grades / missed value) shares one shape: graded,
   flagged, rate, by_street, by_texture, worst[]. One renderer for both. */
function renderGradedSection(el, section, opts) {
  if (!section || !section.graded) {
    el.innerHTML = `<div class="small muted">${esc(opts.emptyText)}</div>`;
    return;
  }
  // Two counts and the proportion between them, as figures and a bar rather
  // than a sentence with two bold numbers buried in it. How many were graded
  // and how many of those were wrong is the finding; the sentence made the
  // reader parse it out of prose every time.
  const summary = h("div", "graded-head", `
    <div class="stat-pair"><span class="v">${section.graded}</span>
      <span class="k">graded</span></div>
    <div class="stat-pair"><span class="v${section.flagged ? " warnv" : ""}">${
      section.flagged}</span><span class="k">flagged</span></div>
    <div class="graded-rate">
      <div class="small muted graded-verdict">${esc(opts.verdict)}</div>
      <div class="graded-bar"></div>
      <div class="small muted graded-pct">${fmtPct(section.rate)} of ${
        esc(opts.noun)} graded</div>
    </div>`);
  const rateBar = bar(section.rate, 1, section.flagged ? "var(--warn)" : "var(--mark-1)", 999);
  rateBar.setAttribute("preserveAspectRatio", "none");
  $(".graded-bar", summary).appendChild(rateBar);
  // The by-street and by-texture splits are detail -- one hover away, not two
  // more lines of muted text under every section.
  const byStreet = Object.entries(section.by_street)
    .map(([k, v]) => `${k} ${fmtPct(v.flagged / v.graded)}`).join(" \u00b7 ");
  const byTex = Object.entries(section.by_texture)
    .map(([k, v]) => `${k} ${fmtPct(v.flagged / v.graded)}`).join(" \u00b7 ");
  $(".graded-verdict", summary).appendChild(info(
    `<b>by street</b><br>${byStreet}<br><br><b>by texture</b><br>${byTex}`));
  el.appendChild(summary);

  for (const g of section.worst) {
    const row = h("div", "fold-row");
    // The same card chips the table and the replay draw. These were the one
    // place hole cards were rendered as mono text instead, so "9d Th" here and
    // a pair of cards everywhere else meant the same fact twice, two ways.
    row.innerHTML = `
      <span class="small muted">${esc(g.street)}</span>
      <span class="fold-cards"></span>
      <span class="small fold-summary">${esc(g.summary)}</span>
      <span class="small muted">${esc(g.texture)} board</span>`;
    $(".fold-cards", row).appendChild(cardsEl(g.hole_cards, {small: true}));
    bindTip($(".fold-summary", row), esc(g.in_words));
    row.onclick = () => showReplay(g.hand_id, opts.heroId,
      `${g.street} ${opts.noun.replace(/s$/, "")} -- ${g.hole_cards.join(" ")}`);
    el.appendChild(row);
  }
}

//: Shared by sizing and timing -- both need bets/raises, so a thin sample
//: reads the same way in either one.
const TELL_EMPTY_TEXT = "Not enough postflop bets or raises with a clean line to compare yet.";

/* sizing_tell and timing_tell share a shape too: street -> {strong, weak,
   in_words}. One renderer for both. */
/* A sizing or timing tell is one comparison -- what you do with the top half
   of your range against what you do with the bottom half -- and the tell is
   the *gap* between them. A sentence makes the reader hold two numbers in
   their head and subtract. Two bars on a shared scale make the gap the thing
   you see, which is the same reason the against-you panel is drawn this way,
   and this reuses that pattern rather than inventing a second one.

   `fmt` renders a bucket's average: pot fraction for sizing, seconds for
   timing. `unit` names it once per row instead of on both bars. */
function renderTellSection(el, section, opts) {
  const rows = Object.entries(section || {});
  if (!rows.length) {
    el.innerHTML = `<div class="small muted">${esc(TELL_EMPTY_TEXT)}</div>`;
    return;
  }
  opts = opts || {};
  const fmt = opts.fmt || fmtPct;
  // One scale across every street, so the flop bar and the river bar mean the
  // same length. Per-street scaling would make every street look like a tell.
  const max = Math.max(...rows.flatMap(([, v]) =>
    [v.strong && v.strong.avg, v.weak && v.weak.avg].filter(x => x != null)), 0) || 1;
  for (const [street, v] of rows) {
    const block = h("div", "tellblock" + (v.is_tell ? " on" : ""), `<div class="tellhead"><span class="street-label">${esc(street)}</span>${
      v.is_tell ? '<span class="tag hero-tag">tell</span>' : ""}</div>`);
    for (const [key, color] of [["strong", "var(--mark-3)"], ["weak", "var(--mark-1)"]]) {
      const b = v[key];
      if (!b || b.avg == null) continue;
      const row = h("div", "metric");
      const name = h("span", "small muted");
      name.textContent = key === "strong" ? "top half" : "bottom half";
      const val = h("span", "small tellval");
      val.textContent = fmt(b.avg);
      const drawn = bar(b.avg, max, color, 150);
      drawn.setAttribute("preserveAspectRatio", "none");
      row.append(name, drawn, val);
      bindTip(row, `<b>${esc(key === "strong" ? "top half" : "bottom half")} of your range</b><br>
        ${esc(fmt(b.avg))}${opts.unit ? " " + esc(opts.unit) : ""} over ${b.hands} hands`);
      block.appendChild(row);
    }
    if (v.in_words) {
      const note = h("div", "small muted tellnote");
      note.textContent = v.in_words;
      block.appendChild(note);
    }
    el.appendChild(block);
  }
}

/* Average strength of the hands still live, street by street. A continuing
   range is supposed to get stronger as the wide parts give up along the way,
   so the shape of the line *is* the finding -- printed as "flop 48% (61)
   turn 52% (39) river 55% (22)" it was three numbers the reader had to plot
   themselves. */
function narrowingChart(rows) {
  // Axis, labels and values all live inside the SVG. Put the street names in a
  // sibling flex row and they line up with the dots only by luck -- the dots
  // are inset by the plot padding and the labels are not.
  // The right margin is a gutter for the median label, not slack: anchored to
  // the plot edge it sat directly on the line it was naming.
  const W = 380, H = 152;
  const x0 = 40, x1 = W - 84, y0 = 18, y1 = 92;
  const y = v => y1 - Math.max(0, Math.min(1, v)) * (y1 - y0);
  const x = i => rows.length < 2 ? (x0 + x1) / 2 : x0 + (i * (x1 - x0)) / (rows.length - 1);
  const svg = el("svg", {viewBox: `0 0 ${W} ${H}`, class: "narrow-chart", role: "img",
    "aria-label": rows.map(r => `${r.street} ${fmtPct(r.avg_strength)}`).join(", ")});
  // The full 0-100 scale, labeled. Without it a range that holds steady near
  // the median draws a flat line in the middle of an unlabeled box, which
  // reads as a broken chart rather than as the finding it is.
  for (const q of [0, 0.5, 1]) {
    const median = q === 0.5;
    el("line", {x1: x0, y1: y(q), x2: x1, y2: y(q),
                stroke: median ? "var(--axis)" : "var(--grid)", "stroke-width": 1,
                "stroke-dasharray": median ? "3 3" : null}, svg);
    const lab = el("text", {x: x0 - 8, y: y(q) + 3.5, "text-anchor": "end",
      "font-size": 10, fill: "var(--muted)", class: "fig-t"}, svg);
    lab.textContent = `${q * 100}`;
  }
  // 0.5 is the median hand the board allows -- the same split the sizing tell
  // buckets on -- so it is a real reference, not a threshold someone chose.
  const medLab = el("text", {x: x1 + 8, y: y(0.5) + 3.5, "text-anchor": "start",
    "font-size": 9.5, fill: "var(--muted)", "letter-spacing": ".06em"}, svg);
  medLab.textContent = "MEDIAN HAND";
  el("polyline", {points: rows.map((r, i) => `${x(i)},${y(r.avg_strength)}`).join(" "),
    fill: "none", stroke: "var(--hero)", "stroke-width": 2,
    "stroke-linejoin": "round", "stroke-linecap": "round"}, svg);
  rows.forEach((r, i) => {
    el("circle", {cx: x(i), cy: y(r.avg_strength), r: 5.5, fill: "var(--panel)"}, svg);
    el("circle", {cx: x(i), cy: y(r.avg_strength), r: 3.5, fill: "var(--hero)"}, svg);
    const name = el("text", {x: x(i), y: H - 26, "text-anchor": "middle", "font-size": 10,
      "font-weight": 600, "letter-spacing": ".06em", fill: "var(--muted)"}, svg);
    name.textContent = r.street.toUpperCase();
    const val = el("text", {x: x(i), y: H - 11, "text-anchor": "middle", "font-size": 11,
      fill: "var(--ink)", class: "fig-t"}, svg);
    val.textContent = fmtPct(r.avg_strength);
    const hit = el("rect", {x: x(i) - 26, y: 0, width: 52, height: H, fill: "transparent"}, svg);
    bindTip(hit, `<b>${esc(r.street)}</b> — average strength ${fmtPct(r.avg_strength)}<br>
      <span class="muted">over ${r.hands} hands still live</span>`);
  });
  return svg;
}

const SUIT = {s: "♠", h: "♥", d: "♦", c: "♣"};
function cardHtml(txt, big) {
  const red = txt[1] === "h" || txt[1] === "d";
  return `<span class="card ${red ? "red" : "black"}${big ? " big" : ""}"><span
    class="r">${esc(txt[0] === "T" ? "10" : txt[0])}</span>${SUIT[txt[1]] || ""}</span>`;
}
function actbtn(label, on, cls) {
  const b = h("button", "act" + (cls ? " " + cls : ""));
  b.textContent = label; b.onclick = on; return b;
}
