const $ = (s, r) => (r || document).querySelector(s);
const fmtPct = v => (100 * v).toFixed(0) + "%";
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const SVG = "http://www.w3.org/2000/svg";
const state = {tab: "players", session: null, player: null, glossary: null, game: null, lastEvent: null, stepTimer: null, descOn: true, revealed: false, checkFold: false, checkFoldHand: null, heroPoll: null,
               sessionId: null};

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
function withInfo(node, html) {
  const wrap = document.createElement("span");
  wrap.style.cssText = "display:inline-flex;align-items:baseline";
  wrap.appendChild(node);
  wrap.appendChild(info(html));
  return wrap;
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
  // Keyboard and touch reach it too: anchored to the element's own box, since
  // there is no cursor to hang it off.
  const anchor = () => {
    const r = el.getBoundingClientRect();
    place(r.left + r.width / 2, r.bottom - 6);
  };
  el.addEventListener("focus", anchor);
  el.addEventListener("blur", hide);
  el.addEventListener("touchstart", e => { e.preventDefault(); anchor(); }, {passive: false});
  el.addEventListener("mousemove", e => {
    tip.innerHTML = html; tip.classList.add("on");
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > innerWidth - 8) x = e.clientX - w - pad;
    if (y + h > innerHeight - 8) y = e.clientY - h - pad;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  });
  el.addEventListener("mouseleave", () => tip.classList.remove("on"));
}
function el(tag, attrs, parent) {
  const node = document.createElementNS(SVG, tag);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(node);
  return node;
}

/* ---- the one mark this tool needs, over and over ----
   Interval band for the credible range, dot for the estimate, hairline tick
   for the field, warm tick for breakeven. Everything here is a frequency with
   uncertainty measured against a threshold, so it is all the same picture. */
function statRow(row) {
  const W = 300, H = 34, mid = 17, r = 5;
  const x = v => Math.max(0, Math.min(1, v)) * W;
  const svg = el("svg", {width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `${row.label}: ${fmtPct(row.value)}, range ${fmtPct(row.lo)} to ${fmtPct(row.hi)}`});
  el("line", {x1: 0, y1: mid, x2: W, y2: mid, stroke: "var(--grid)", "stroke-width": 1}, svg);
  el("rect", {x: x(row.lo), y: mid - 6, width: Math.max(2, x(row.hi) - x(row.lo)),
              height: 12, rx: 4, fill: "var(--band)"}, svg);
  el("line", {x1: x(row.population), y1: mid - 10, x2: x(row.population), y2: mid + 10,
              stroke: "var(--axis)", "stroke-width": 1}, svg);
  if (row.breakeven != null) {
    el("line", {x1: x(row.breakeven), y1: mid - 11, x2: x(row.breakeven), y2: mid + 11,
                stroke: "var(--warn)", "stroke-width": 2, "stroke-linecap": "round"}, svg);
  }
  el("circle", {cx: x(row.value), cy: mid, r: r + 2, fill: "var(--panel)"}, svg);
  el("circle", {cx: x(row.value), cy: mid, r: r, fill: "var(--mark-3)"}, svg);
  const hit = el("rect", {x: 0, y: 0, width: W, height: H, fill: "transparent"}, svg);
  bindTip(hit, `<b>${esc(row.label)}</b> \u2014 ${fmtPct(row.value)}<br>
    <span class="muted">95% range ${fmtPct(row.lo)}\u2013${fmtPct(row.hi)}</span><br>
    raw ${row.raw == null ? "\u2014" : fmtPct(row.raw)} of ${row.opps} ${esc(row.denominator)}<br>
    field ${fmtPct(row.population)}${row.breakeven != null
      ? `<br><span style="color:var(--warn)">${esc(row.breakeven_label)} ${fmtPct(row.breakeven)}</span>` : ""}
    ${fieldRead(row)}`);
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
const TIER_COLOR = {strong: "var(--mark-3)", likely: "var(--mark-2)", tentative: "var(--mark-1)"};

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

/* ---- shared renderers ---- */
function rosterTable(players, opts) {
  const wrap = document.createElement("div");
  wrap.className = "scroller";
  wrap.innerHTML = `<table><thead><tr>
      <th data-k="name">player</th>
      <th data-k="hands" class="num">hands</th><th data-k="archetype">read</th>
      <th data-k="skill" class="num">skill</th>
      <th data-k="gto" class="num">GTO</th>
      <th data-k="exploitability" class="num">worth bb/100</th>
      <th data-k="top_leak">biggest leak</th>
    </tr></thead><tbody></tbody></table>`;
  const body = $("tbody", wrap);
  let sort = {key: "skill", dir: -1};
  function draw() {
    const rows = [...players].sort((a, b) => {
      let x = a[sort.key], y = b[sort.key];
      if (x == null) x = -Infinity;
      if (y == null) y = -Infinity;
      const cmp = (typeof x === "number" && typeof y === "number")
        ? x - y : String(x).localeCompare(String(y));
      return cmp * sort.dir;
    });
    body.innerHTML = "";
    for (const p of rows) {
      const tr = document.createElement("tr");
      const isHero = p.is_hero || (opts && opts.heroId != null && p.player_id === opts.heroId);
      if (isHero) tr.className = "hero-row-marker hero-scope";
      if (opts && opts.onClick && p.player_id != null) {
        tr.className = (tr.className ? tr.className + " " : "") + "clickable";
        tr.tabIndex = 0;
        tr.setAttribute("role", "button");
        tr.onkeydown = e => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); opts.onClick(p); }
        };
        tr.onclick = () => opts.onClick(p);
      }
      const shown = (p.session_names && p.session_names.length && p.db_name)
        ? p.session_names.join(" / ") : p.name;
      const linkBits = [];
      if (p.db_name) linkBits.push(`database: ${esc(p.db_name)}`);
      else if (p.session_names && p.session_names.length)
        linkBits.push(`as ${p.session_names.map(n => `\u201c${esc(n)}\u201d`).join(", ")}`);
      tr.innerHTML = `
        <td><span class="name">${esc(shown)}</span>${
            isHero ? '<span class="tag hero-tag">you</span>' : ""}
            ${linkBits.length
              ? `<div class="small muted">${linkBits.join(" \u00b7 ")}</div>` : ""}</td>
        <td class="num">${p.hands}</td>
        <td><span class="tag arch ${p.confidence >= 0.5 ? "on" : ""}">${esc(p.archetype)}</span>
            <div class="small muted">${fmtPct(p.confidence)} sure</div></td>
        <td class="num"></td>
        <td class="num gto-cell">${p.gto != null ? Math.round(p.gto) : "\u2014"}</td>
        <td class="num worth${p.exploitability > 10 ? " big" : ""}">${
          p.exploitability ? p.exploitability.toFixed(1) : "\u2014"}</td>
        <td class="small leakcell">${p.top_leak ? esc(p.top_leak)
          : '<span class="muted">nothing yet</span>'}</td>`;
      const holder = document.createElement("div");
      holder.style.cssText = "display:flex;gap:8px;align-items:center;justify-content:flex-end";
      const label = document.createElement("span");
      label.textContent = p.skill.toFixed(0);
      // Mapped to the observed domain, not 0-100: anchored at zero the whole
    // roster looked equally full -- p10 to p90 differed by 14px of bar.
    holder.append(bar(Math.max(0, p.skill - 40), 55, "var(--mark-3)", 66), label);
      // Sample quality moved onto the hands count as a tooltip: it qualifies
      // that number and nothing else, so it does not need its own line on
      // every row.
      const hcell = tr.children[1];
      if (hcell) {
        hcell.classList.add("q-" + String(p.sample_quality).split(" ")[0]);
        bindTip(hcell, `<b>${esc(p.sample_quality)}</b><br>${termTip(p.sample_quality)}`);
      }
      /* An unconfirmed read still belongs in the column -- "none clears the
         bar" left the weakest player at the table looking like the safest.
         The marker says which kind of claim it is. */
      if (p.top_leak && p.top_leak_status !== "confirmed") {
        const flag = document.createElement("span");
        flag.className = "flag";
        flag.textContent = "!";
        bindTip(flag, `<span class="hl">${p.top_leak_status === "watch"
          ? "not confirmed" : "from the rating, not a frequency"}</span><br>
          ${esc(p.top_leak_note)}`);
        $(".leakcell", tr).appendChild(flag);
      } else if (p.top_leak) {
        $(".leakcell", tr).appendChild(info(esc(p.top_leak_note)));
      }
      tr.children[3].appendChild(holder);
      bindTip(holder, `<b>${esc(p.skill_tier)}</b> ${p.skill.toFixed(0)}/100<br>
        <span class="muted">confidence ${fmtPct(p.skill_confidence)}</span>`);
      body.appendChild(tr);
    }
    wrap.querySelectorAll("th").forEach(th => {
      th.classList.toggle("sorted", th.dataset.k === sort.key);
      th.classList.toggle("desc", th.dataset.k === sort.key && sort.dir < 0);
    });
  }
  wrap.querySelectorAll("th").forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    sort = {key: k, dir: sort.key === k ? -sort.dir : (k === "name" ? 1 : -1)};
    draw();
  });
  draw();
  return wrap;
}

/* vs GTO -- how close a player's frequencies sit to an optimal baseline, with
   one rating. Preflop rows are a solver reference (exact as category
   frequencies), postflop are board-averaged benchmarks; the fidelity rides
   every row and the tooltip says which is which. */
function renderGto(gto) {
  if (!gto || gto.rating == null || !(gto.rows || []).length) return null;
  const box = document.createElement("div");
  box.className = "panel";
  box.innerHTML = `<div class="spread"><h2 style="margin:0">vs GTO</h2>
      <span class="gto-badge"><b>${Math.round(gto.rating)}</b><span class="of">/100</span></span>
    </div><div class="gto-rows"></div>`;
  $(".gto-badge", box).appendChild(info(`<span class="hl">GTO rating</span><br>
    How close these frequencies sit to a game-theory-optimal baseline — the
    part of a game a perfect opponent still could not exploit.<br><br>
    <b>Preflop</b> is a solver reference at 100bb, exact as category
    frequencies.<br><b>Postflop</b> is an equilibrium benchmark, board-averaged
    — not a live solver. Preflop rows weigh double in the score.`));
  const host = $(".gto-rows", box);
  for (const r of gto.rows.slice(0, 8)) {
    const dir = r.deviation > 0 ? "+" : "−";
    const row = document.createElement("div");
    row.className = "gto-row";
    row.innerHTML = `
      <span class="gto-name">${esc(statLabel(r.stat, null))}<span
        class="gto-fid ${r.fidelity === "solver" ? "solver" : ""}">${
        r.fidelity === "solver" ? "solver" : "bench"}</span></span>
      <span class="gto-nums small muted">you ${fmtPct(r.player)} · gto ${fmtPct(r.target)}</span>
      <span class="gto-dev">${dir}${Math.round(Math.abs(r.deviation) * 100)}</span>`;
    host.appendChild(row);
  }
  if (gto.rows.length > 8) {
    const link = document.createElement("button");
    link.className = "linkbtn how-link";
    link.textContent = `See all ${gto.rows.length}`;
    link.onclick = () => {
      const modal = $("#modal");
      modal.innerHTML = `<div class="veil"><div class="sheet">
        <div class="spread"><h2 style="margin:0">vs GTO — every stat</h2>
          <button class="act" id="close">Close</button></div>
        <div class="gto-rows" id="gto-all"></div></div></div>`;
      const all = $("#gto-all", modal);
      for (const r of gto.rows) {
        const dir = r.deviation > 0 ? "+" : "−";
        const row = document.createElement("div");
        row.className = "gto-row";
        row.innerHTML = `
          <span class="gto-name">${esc(statLabel(r.stat, null))}<span
            class="gto-fid ${r.fidelity === "solver" ? "solver" : ""}">${
            r.fidelity === "solver" ? "solver" : "bench"}</span></span>
          <span class="gto-nums small muted">you ${fmtPct(r.player)} · gto ${fmtPct(r.target)}</span>
          <span class="gto-dev">${dir}${Math.round(Math.abs(r.deviation) * 100)}</span>`;
        all.appendChild(row);
      }
      $("#close").onclick = () => { modal.innerHTML = ""; };
    };
    box.appendChild(link);
  }
  return box;
}

function profileCard(p, opts) {
  opts = opts || {};
  const isHero = p.is_hero || (opts.heroId != null && p.player_id === opts.heroId);
  const hero = opts.hero || isHero;
  _heroVoice = hero;                     // glossary tooltips read in your voice
  const card = document.createElement("div");
  card.className = isHero ? "dash hero-scope" : "dash";
  const leaks = p.leaks;
  // Full-width tiles are placed last: dropped mid-grid they force a row break
  // and strand whatever tile precedes them alone on a line.
  const wideTiles = [];

  const head = document.createElement("div");
  head.className = "panel wide";
  head.innerHTML = `
    <div class="hero-row">
      <div class="hero-name">
        <div class="hero">${esc(p.name || p.archetype)}</div>
        <div class="hero-sub">${esc(p.archetype)}</div>
      </div>
      <div class="skill-badge" id="skill-badge">
        <span class="score">${p.skill.score.toFixed(0)}</span>
        <span class="of">/100</span>
      </div>
    </div>
    <div class="read-meta" id="read-meta"></div>
    <div class="read-copy">
      <p class="summary">${esc(p.summary)}</p>
      ${hero ? "" : `<p class="plan">${esc(p.plan)}</p>`}
    </div>`;
  if (isHero) $(".hero-sub", head).insertAdjacentHTML(
    "afterbegin", '<span class="hero-badge">you</span> ');
  card.appendChild(head);

  const cols = document.createElement("div");
  cols.className = "dash-cols wide";
  card.appendChild(cols);

  const skillBox = document.createElement("div");
  skillBox.className = "panel";
  skillBox.innerHTML =
    `<h2>Skill breakdown <span class="muted" style="font-weight:400">\u00b7 ` +
    `${esc(p.skill.tier)}</span></h2><div class="skill-side" id="skill-side"></div>`;

  const badge = $("#skill-badge", head);
  bindTip(badge, `<b>${esc(p.skill.tier)}</b> ${p.skill.score.toFixed(0)}/100<br>
    <span class="muted">confidence ${fmtPct(p.skill.confidence)}</span>
    ${p.skill.observed_bb100 == null ? ""
      : `<br><span class="muted">${p.skill.observed_bb100.toFixed(1)} bb/100 observed</span>`}`);

  const meta = $("#read-meta", head);
  const line = document.createElement("span");
  line.append(document.createTextNode(`${p.hands} hands`));
  line.appendChild(info(`<b>${esc(p.sample_quality)}</b><br>${termTip(p.sample_quality)}`));
  const conf = document.createElement("span");
  conf.style.marginLeft = "12px";
  conf.append(document.createTextNode(
    `${esc(p.archetype)} ${fmtPct(p.archetype_confidence)} sure`));
  conf.appendChild(info(`${termTip("confidence")}<br><br>
    <span class="hl">also plausibly</span><br>${
      p.archetype_mix.slice(1, 4).map(([n, v]) => `${esc(n)} ${fmtPct(v)}`).join("<br>")}`));
  // Who they are on the hands they played against you. Only here, never on
  // the roster: the roster is how everybody plays the field, and two
  // references in one list is how "tag" stopped meaning anything.
  if (p.versus) {
    const vs = document.createElement("span");
    vs.style.marginLeft = "12px";
    vs.innerHTML = `<span class="tag arch on">vs you: ${esc(p.versus.archetype)}</span>`;
    vs.appendChild(info(`<span class="hl">against you</span><br>
      On the ${p.versus.decisions.toLocaleString()} decisions they made with you
      on the other side, ${esc(p.versus.regime_label)}, they play like
      <b>${esc(p.versus.archetype)}</b> (${fmtPct(p.versus.confidence)} sure)
      &mdash; against <b>${esc(p.archetype)}</b> for the field.<br><br>
      <span class="hl">also plausibly</span><br>${
        p.versus.mix.slice(1, 3).map(m =>
          `${esc(m.archetype)} ${fmtPct(m.share)}`).join("<br>")}<br><br>
      One table size, never pooled: a player can be one thing against you
      heads-up and another six-handed, and the average of those describes
      neither table you sat at.`));
    line.appendChild(vs);
  }
  const fieldChip = document.createElement("span");
  fieldChip.style.marginLeft = "12px";
  fieldChip.textContent = "vs the field";
  fieldChip.appendChild(info(`<span class="hl">the field</span><br>
    The average of the other players in your database at ${esc(p.regime_label)}.
    Your own pool once you have 8+ players; generic online norms before that.
    Empirical \u2014 not a GTO solver.`));
  meta.append(line, conf, fieldChip);
  if (p.contributions && Object.keys(p.contributions).length > 1) {
    const where = document.createElement("span");
    where.style.marginLeft = "12px";
    where.textContent = p.regime_label;
    where.appendChild(info(`<span class="hl">one read, several table sizes</span><br>
      Each table's hands are measured against that table's own norms, then
      pooled. Shown on ${esc(p.regime_label)} terms, where they play most.<br>
      ${esc(p.table_mix || "")}`));
    meta.appendChild(where);
  }

  // Seven bars spanning 77-100 rank a player without telling you anything.
  // Each component now carries what it measures, the figure behind it, and
  // whether it is the part of their game that is actually costing them.
  const skillSide = $("#skill-side", skillBox);
  skillSide.innerHTML = "";
  const comps = p.skill_components || p.skill.components;
  for (const c of [...comps].sort((a, b) => a.score - b.score)) {
    const row = document.createElement("div");
    row.className = "comp" + (c.weak ? " weak" : "");
    row.innerHTML = `<span class="comp-name">${esc(c.name)}</span>
      <span class="comp-bar"></span>
      <span class="comp-score">${c.score.toFixed(0)}</span>`;
    $(".comp-bar", row).appendChild(
      bar(c.score, 100, c.weak ? "var(--red)" : "var(--mark-1)", 999));
    // The sentence, the figure it rests on, and what it means all live in the
    // tooltip now, so the breakdown reads as a scorecard, not a wall of prose.
    const bits = [`<b>${esc(c.name)}</b>`];
    if (c.measures) bits.push(esc(c.measures));
    if (c.note) bits.push(`<span class="hl">${esc(c.note)}</span>`);
    if (c.meaning) bits.push(esc(c.meaning));
    bits.push(`<span class="muted">counts ${c.weight}x toward the rating</span>`);
    $(".comp-name", row).appendChild(info(bits.join("<br>")));
    skillSide.appendChild(row);
  }

  const doBox = document.createElement("div");
  doBox.className = "panel";
  doBox.innerHTML = `<div class="spread"><h2>${hero ? "your biggest leaks" : "What to Do"}</h2>
      <span class="small muted worth"></span></div><div class="leaks"></div>`;
  const worthLabel = $(".worth", doBox);
  worthLabel.append(document.createTextNode(
    leaks.length ? `${p.skill.exploitability_bb100.toFixed(1)} bb/100 available` : ""));
  if (leaks.length) worthLabel.appendChild(info(termTip("available")));
  cols.appendChild(doBox);

  const leakBox = $(".leaks", doBox);
  if (!leaks.length) {
    const nothing = (p.watchlist || []).length || (p.weak_spots || []).length
      ? `<div class="empty">Nothing clears the evidence bar yet. Below is
         what the numbers point at so far.</div>`
      : `<div class="empty">Nothing stands out yet. Play them straight and
         collect more hands.</div>`;
    leakBox.innerHTML = nothing;
  }
  for (const l of leaks) {
    const div = document.createElement("div");
    div.className = "leak";
    div.innerHTML = `
      <div class="leak-head">
        <div class="headline"><b>${esc(l.headline)}</b>
          <span class="tag tier">${esc(l.tier)}</span></div>
        <div class="num small muted">${l.severity_bb100.toFixed(2)} bb/100</div></div>
      ${hero ? "" : `<div class="leak-advice">${esc(l.do)}</div>`}
      <div class="small muted numbers"></div>`;
    $(".tier", div).after(info(`${termTip(l.tier)}<br><br>${esc(l.priority)}`));

    const numbers = $(".numbers", div);
    numbers.appendChild(document.createTextNode(
      `${fmtPct(l.value)} vs ${fmtPct(l.breakeven)} break-even \u00b7 `));
    if (p.player_id != null) {
      const link = document.createElement("button");
      link.className = "linkbtn";
      link.textContent = `${Math.round(l.sample)} hands`;
      link.title = "show the hands behind this";
      link.onclick = () => showEvidence(p.player_id, l.stat, l.headline);
      numbers.appendChild(link);
    } else {
      numbers.appendChild(document.createTextNode(`${Math.round(l.sample)} hands`));
    }
    numbers.appendChild(info(statTip(l.stat, l.headline)));

    // A popup, not an inline <details>: expanding it in place grew this panel
    // after the columns were balanced, which is what threw the layout off. Same
    // sheet the hands open in.
    const whydont = [["Why", l.why], ["Do not", l.dont]].filter(([, t]) => t);
    if (!hero && whydont.length) {
      const link = document.createElement("button");
      link.className = "linkbtn how-link";
      link.textContent = "Why, and what not to do";
      link.onclick = () => {
        const modal = $("#modal");
        modal.innerHTML = `<div class="veil"><div class="sheet">
          <div class="spread"><h2 style="margin:0">${esc(l.headline)}</h2>
            <button class="act" id="close">Close</button></div>
          <div class="how-body"></div></div></div>`;
        const how = $(".how-body", modal);
        for (const [label, text] of whydont) {
          const block = document.createElement("div");
          block.className = "howblock";
          block.innerHTML = `<div class="howlabel">${esc(label)}</div>
            <div>${esc(text)}</div>`;
          how.appendChild(block);
        }
        $("#close").onclick = () => { modal.innerHTML = ""; };
      };
      div.appendChild(link);
    }
    leakBox.appendChild(div);
  }

  for (const w of (p.watchlist || [])) {
    const div = document.createElement("div");
    div.className = "leak watch";
    div.innerHTML = `
      <div class="leak-head">
        <div class="headline"><b>${esc(w.headline)}</b>
          <span class="tag tier">watch</span></div>
        <div class="num small muted">${fmtPct(w.confidence)} sure</div></div>
      <div class="small muted numbers">${esc(w.in_words)}</div>`;
    $(".tier", div).after(info(termTip("watch")));
    leakBox.appendChild(div);
  }

  if (false && (p.weak_spots || []).length) {
    const weak = document.createElement("div");
    weak.className = "leak weakspots";
    weak.innerHTML = `<div class="headline"><b>Weakest parts of their game</b>
        <span class="tag">from the rating</span></div>
      <div class="small muted" style="margin:4px 0 8px">
        Not priced leaks \u2014 where their game is thinnest.</div>`;
    for (const spot of p.weak_spots) {
      const row = document.createElement("div");
      row.className = "metric";
      const label = document.createElement("span");
      label.className = "small";
      label.textContent = spot.name + (spot.note ? ` \u2014 ${spot.note}` : "");
      const val = document.createElement("span");
      val.className = "small muted"; val.style.textAlign = "right";
      val.textContent = spot.score.toFixed(0);
      row.append(label, bar(spot.score, 100, "var(--mark-2)", 150), val);
      if (spot.meaning) bindTip(row, `<b>${esc(spot.name)}</b><br>${esc(spot.meaning)}`);
      weak.appendChild(row);
    }
    leakBox.appendChild(weak);
  }

  for (const c of (p.combinations || [])) {
    const block = document.createElement("div");
    block.className = "leak";
    block.innerHTML = `<div class="headline"><b>${esc(c.headline)}</b>
      <span class="tag">these compound</span></div>
      <div class="leak-advice">${esc(c.body)}</div>`;
    leakBox.appendChild(block);
  }

  // How they play *you*. Rendered only when there is something in it: most
  // players against most opponents have no adjustment, and an empty panel
  // takes a column off a screen that is meant to be read mid-hand.
  const adjustments = p.adjustments || [];
  if (adjustments.length) {
    const adjBox = document.createElement("div");
    // Full width, below the two columns rather than inside the height-packed
    // masonry: a short reads-only panel could never balance against the tall
    // skill breakdown without leaving dead space on one side or the other.
    adjBox.className = "panel wide adjust hero-scope";
    adjBox.innerHTML = `<h2 style="margin-bottom:6px">Against You<span class="hero-badge">hero</span></h2>
      <div class="small muted" style="margin:0 0 14px">
        Measured against how they play everybody else, not against the
        field.</div>`;
    // The mark rides the title itself, like every other panel header (see the
    // hero dashboard): .info's own 5px offset places it, no wrapper needed.
    $("h2", adjBox).appendChild(info(termTip("adjustment")));
    const adjGrid = document.createElement("div");
    adjGrid.className = "adjust-grid";
    adjBox.appendChild(adjGrid);
    for (const a of adjustments) {
      const div = document.createElement("div");
      div.className = "leak";
      div.innerHTML = `
        <div class="leak-head">
          <div class="headline"><b>${esc(a.behavior)}</b>${
            a.regime_label ? ` <span class="tag">${esc(a.regime_label)}</span>` : ""}</div>
          <div class="num small muted">${fmtPct(Math.min(a.confidence, 0.99))} sure</div>
        </div>
        <div class="small muted numbers"></div>`;
      for (const [label, value, color, term] of [
            ["against you", a.versus, "var(--mark-3)", "against you"],
            ["otherwise", a.baseline, "var(--mark-1)", "otherwise"]]) {
        const row = document.createElement("div");
        row.className = "metric";
        const name = document.createElement("span");
        name.className = "small"; name.textContent = label;
        const val = document.createElement("span");
        val.className = "small muted"; val.style.textAlign = "right";
        val.textContent = fmtPct(value);
        row.append(name, bar(value, 1, color, 150), val);
        bindTip(row, termTip(term));
        div.insertBefore(row, $(".numbers", div));
      }
      const numbers = $(".numbers", div);
      const seen = `${Math.round(a.sample)} against you`;
      if (p.player_id != null) {
        // Opens on the against-you slice rather than its parent, so the hands
        // shown are the ones the read is actually about.
        const link = document.createElement("button");
        link.className = "linkbtn";
        link.textContent = seen;
        link.title = "show the hands behind this";
        link.onclick = () => showEvidence(p.player_id, a.evidence_stat, a.behavior);
        numbers.appendChild(link);
      } else {
        numbers.appendChild(document.createTextNode(seen));
      }
      numbers.appendChild(document.createTextNode(
        ` · ${Math.round(a.baseline_sample)} against everybody else`));
      numbers.appendChild(info(statTip(a.stat, a.behavior)));
      adjGrid.appendChild(div);
    }
    wideTiles.push(adjBox);
  }

  if (opts.narrate) {
    const suggestBox = document.createElement("div");
    suggestBox.className = "panel wide";
    suggestBox.innerHTML = `<h2>suggested exploits</h2>
      <div class="small muted" style="margin:-6px 0 12px">
        Not measured reads \u2014 check them against the hands.</div>`;
    const narrateBox = document.createElement("div");
    narrateBox.className = "narrate";
    suggestBox.appendChild(narrateBox);
    wideTiles.push(suggestBox);
    buildNarrator(narrateBox, p);
  }

  const tells = p.timing_tells || [];
  // Render only when some cell actually has a tell -- a grid of "not enough
  // data" is noise wearing a panel's clothes.
  if (tells.some(c => c.n >= 5 && !/no clear tell|not enough/i.test(c.label || ""))) {
    const timingBox = document.createElement("div");
    timingBox.className = "panel wide";
    const headRow = document.createElement("div");
    headRow.className = "spread";
    const title = document.createElement("div");
    title.className = "headline";
    const h2 = document.createElement("h2");
    h2.style.margin = "0";
    h2.textContent = "Timing Tells";
    const flag = document.createElement("span");
    flag.className = "flag";
    flag.textContent = "!";
    bindTip(flag, `<span class="hl">use with caution</span><br>
      Timing is noisy online. Each cell is the <em>share</em> of that action
      at this pace, plus whether they won / went to showdown / folded next
      <em>differently</em> than after the same action at normal pace. Use it
      to break ties \u2014 never as the whole basis of a decision.`);
    title.append(h2, flag);
    headRow.appendChild(title);
    const note = document.createElement("span");
    note.className = "small muted";
    note.textContent = "share of action + outcome vs normal pace";
    headRow.appendChild(note);
    timingBox.appendChild(headRow);

    const byKey = Object.fromEntries(
      tells.map(c => [`${c.pace}:${c.street}:${c.action}`, c]));
    for (const street of ["flop", "turn"]) {
      const block = document.createElement("div");
      block.className = "timing-street";
      block.innerHTML = `<div class="street-label">${street}</div>`;
      const grid = document.createElement("div");
      grid.className = "timing-grid";
      grid.innerHTML = `<div class="corner"></div>
        <div class="colhead">check</div>
        <div class="colhead">call</div>
        <div class="colhead">raise</div>`;
      for (const pace of ["snap", "tank"]) {
        const rowhead = document.createElement("div");
        rowhead.className = "rowhead";
        rowhead.textContent = pace;
        grid.appendChild(rowhead);
        for (const action of ["check", "call", "aggro"]) {
          const cell = byKey[`${pace}:${street}:${action}`] || {
            n: 0, total: 0, share: null, label: "Not enough data",
            read: "Need more timed actions."};
          const div = document.createElement("div");
          const quiet = cell.n < 5 || /no clear tell|not enough/i.test(cell.label || "");
          div.className = "timing-cell" + (quiet ? " thin" : " on");
          const share = cell.share == null ? ""
            : `${Math.round(100 * cell.share)}% of ${cell.total}`;
          const nLine = cell.n
            ? `${cell.n} timed${share ? ` \u00b7 ${share}` : ""}`
            : "no data yet";
          // Only a real tell prints a label; everything else is a quiet dash.
          // The read that used to crowd every cell is one hover away.
          div.innerHTML = quiet
            ? `<div class="tell">\u2014</div>`
            : `<div class="tell">${esc(cell.label)}</div><div class="n">${cell.n} timed</div>`;
          bindTip(div, `<b>${esc(cell.label)}</b><br>${esc(cell.read)}<br>
            <span class="muted">${esc(nLine)}</span>`);
          grid.appendChild(div);
        }
      }
      block.appendChild(grid);
      timingBox.appendChild(block);
    }
    wideTiles.push(timingBox);
  }

  cols.appendChild(skillBox);
  const gtoBox = renderGto(p.gto);
  if (gtoBox) cols.appendChild(gtoBox);

  const detail = document.createElement("div");
  detail.className = "panel";
  detail.innerHTML = `<h2>Key numbers</h2><div class="detail-body"></div>`;
  cols.appendChild(detail);
  for (const tile of wideTiles) card.appendChild(tile);
  requestAnimationFrame(() => balanceColumns(cols));
  const body = $(".detail-body", detail);

  // Six headline numbers, the rest one click away. Rendering all seventeen
  // open made this panel taller than the read, the plan and the exploits put
  // together, and it was the whole reason the page scrolled.
  const HEADLINE = ["vpip", "pfr", "three_bet", "fold_to_three_bet",
                    "cbet:flop", "fold_vs_bet:flop"];
  const rank = (r) => {
    const i = HEADLINE.indexOf(r.stat);
    return i === -1 ? HEADLINE.length : i;
  };
  const TIMING = /^(tank_fold|snap_call)(:|$)/;
  const ordered = [...p.rows]
    .filter((r) => !TIMING.test(r.stat))
    .sort((a, b) => rank(a) - rank(b));
  const headline = ordered.filter((r) => HEADLINE.includes(r.stat));
  const rest = ordered.filter((r) => !HEADLINE.includes(r.stat));

  const makeTable = () => {
    const table = document.createElement("div");
    table.className = "scroller";
    table.innerHTML = `<table><thead><tr><th>stat</th>
        <th style="width:40%">0% \u2014 100%</th>
        <th class="num">estimate</th><th class="num">sample</th></tr></thead>
      <tbody></tbody></table>`;
    return table;
  };
  const fill = (table, rows) => {
    const tbody = $("tbody", table);
    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="label"></td><td></td>
                      <td class="num">${fmtPct(row.value)}</td>
                      <td class="num small muted">${Math.round(row.opps)}</td>`;
      const label = $(".label", tr);
      label.appendChild(document.createTextNode(row.label));
      label.appendChild(info(statTip(row.stat, row.label, row)));
      tr.children[1].appendChild(statRow(row));
      tbody.appendChild(tr);
    }
  };

  const first = makeTable();
  fill(first, headline.length ? headline : ordered);
  body.appendChild(first);
  if (rest.length) {
    // The rest open in the sheet popup rather than an inline <details>, so
    // this panel never changes height after the columns are balanced.
    const link = document.createElement("button");
    link.className = "linkbtn how-link";
    link.textContent = `See all ${ordered.length} numbers`;
    link.onclick = () => {
      _heroVoice = hero;
      const modal = $("#modal");
      modal.innerHTML = `<div class="veil"><div class="sheet">
        <div class="spread"><h2 style="margin:0">Key numbers</h2>
          <button class="act" id="close">Close</button></div>
        <div class="modal-numbers"></div></div></div>`;
      const full = makeTable();
      fill(full, ordered);
      $(".modal-numbers", modal).appendChild(full);
      $("#close").onclick = () => { modal.innerHTML = ""; };
    };
    body.appendChild(link);
  }
  return card;
}

function buildNarrator(box, profile) {
  const actions = document.createElement("div");
  actions.className = "narrate-actions";
  const button = document.createElement("button");
  button.className = "act small";
  button.textContent = "Generate additional exploits";
  const toggle = document.createElement("button");
  toggle.className = "act small";
  toggle.disabled = true;
  toggle.textContent = "Hide";
  const out = document.createElement("div");
  out.className = "narration";
  actions.append(button, toggle);
  box.append(actions, out);

  let visible = true;
  toggle.onclick = () => {
    visible = !visible;
    out.classList.toggle("hidden", !visible);
    toggle.textContent = visible ? "Hide" : "Show";
  };

  button.onclick = async () => {
    button.disabled = true;
    toggle.disabled = true;
    const original = button.textContent;
    button.textContent = "writing\u2026";
    out.classList.remove("hidden");
    visible = true;
    toggle.textContent = "Hide";
    try {
      const result = await post("/api/narrate", {profile: profile});
      out.innerHTML = `${renderBullets(result.text)}
        <div class="small muted" style="margin-top:8px">suggested by
          ${esc(result.model)} from the numbers on this page \u2014 it is given
          the computed profile and cannot state a figure the profile did not
          produce. These are not measured reads: check them against the hands
          before trusting them.</div>`;
      button.textContent = "Generate again";
      toggle.disabled = false;
    } catch (err) {
      out.innerHTML = `<div class="small err">${esc(err.message)}</div>`;
      button.textContent = original;
      toggle.disabled = true;
    }
    button.disabled = false;
  };
}

/* The model returns bullets. Render them as a list rather than a wall of
   text, and fall back to paragraphs if it ignored the instruction. */
function renderBullets(text) {
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
  const bullets = lines.filter(l => /^[-*\u2022]\s+/.test(l));
  if (bullets.length < 2) {
    return lines.map(l => `<p>${esc(l)}</p>`).join("");
  }
  const items = bullets
    .map(l => `<li>${esc(l.replace(/^[-*\u2022]\s+/, ""))}</li>`).join("");
  return `<ul class="suggested">${items}</ul>`;
}

/* ---- a strip of player tabs over one profile at a time ---- */
function playerTabs(profiles, container, opts) {
  container.innerHTML = "";
  if (!profiles.length) {
    container.innerHTML = `<div class="panel"><div class="empty">No profiles.</div></div>`;
    return;
  }
  if (profiles.length === 1) {          // a strip of one is just clutter
    container.appendChild(profileCard(profiles[0], opts));
    return;
  }
  const strip = document.createElement("div");
  strip.className = "ptabs";
  const body = document.createElement("div");
  container.append(strip, body);

  let current = 0;
  function show(i) {
    current = i;
    [...strip.children].forEach((b, j) => b.classList.toggle("on", i === j));
    body.innerHTML = "";
    body.appendChild(profileCard(profiles[i], opts));
  }
  profiles.forEach((p, i) => {
    const b = document.createElement("button");
    b.className = "ptab";
    const shown = (p.session_names && p.session_names.length && p.db_name)
      ? p.session_names.join(" / ") : p.name;
    const meta = [esc(p.regime_label), `${p.hands}h`];
    if (p.db_name) meta.push(`database: ${esc(p.db_name)}`);
    b.innerHTML = `<span>${esc(shown)}</span>
      <span class="meta">${meta.join(" \u00b7 ")}</span>`;
    b.onclick = () => show(i);
    strip.appendChild(b);
  });
  show(0);
}

async function readFiles(list) {
  const payload = [];
  for (const f of [...list]) payload.push({name: f.name, content: await f.text()});
  return payload;
}

/* Import straight into the database: one session for the whole batch, the
   identity questions asked once across all of it, then a single commit. */
function showBusy(text) {
  const modal = $("#modal");
  modal.innerHTML = `<div class="veil busy"><div class="sheet busy-sheet">
    <div class="spinner" aria-hidden="true"></div>
    <div class="busy-body">
      <b id="busy-text"></b>
      <div id="busy-bar" class="busy-bar"><i></i></div>
    </div>
  </div></div>`;
  $("#busy-text").textContent = text;
  /* (message, fraction).
     A null message leaves the text alone, because progress updates arrive far
     more often than the step they belong to changes.
     A number fills the bar to it. `undefined` leaves the bar running but
     *unmeasured* -- one blocking call into Python that reports nothing back,
     where the only accurate thing to say is "working". A bar that invented a
     percentage there would be the one part of this interface that lies. */
  return (next, fraction) => {
    const el = $("#busy-text");
    if (el && next != null) el.textContent = next;
    const bar = $("#busy-bar");
    if (!bar) return;
    bar.classList.add("on");
    if (fraction == null) {
      // Width is left to the stylesheet: the travelling piece is a fraction of
      // the track, and setting it here would pin it full again.
      bar.classList.add("unmeasured");
      bar.firstChild.style.width = "";
      return;
    }
    bar.classList.remove("unmeasured");
    bar.firstChild.style.width = Math.round(Math.max(0, Math.min(1, fraction)) * 100) + "%";
  };
}

async function importFiles(list, status, done) {
  const files = [...list];
  if (!files.length) return;
  status.textContent = "";
  const setBusy = showBusy(`Reading ${files.length} file(s)\u2026`);
  try {
    const payload = await readFiles(files);
    // Parsed in pieces rather than in one call. In the browser the whole tool
    // runs on this thread, so a single parse of two hundred files is a frozen
    // window with no way to tell it apart from a crash; the same work in
    // tens is a counter that moves. The session is assembled server-side and
    // the identity questions are still asked once, over the whole batch.
    // Measured in bytes rather than in files. A run of these exports goes from
    // 3 KB to 2.6 MB, so counting files makes the bar jump and stall by turns;
    // counting bytes tracks the work actually being done.
    const totalBytes = payload.reduce((n, f) => n + (f.content ? f.content.length : 0), 0) || 1;
    const PIECE = 10;
    let token = null, sent = 0;
    for (let i = 0; i < payload.length; i += PIECE) {
      const piece = payload.slice(i, i + PIECE);
      setBusy("Reading hand histories\u2026", sent / totalBytes);
      const step = await post("/api/upload", {files: piece, token, more: true});
      token = step.token;
      sent += piece.reduce((n, f) => n + (f.content ? f.content.length : 0), 0);
      setBusy(null, sent / totalBytes);
    }
    // Closing the batch is its own step, and on a large import much the
    // longest: every hand is deduplicated and every name in it matched against
    // everybody already in the database. One call into Python that reports
    // nothing until it returns, so the bar says "working" rather than guessing.
    setBusy("Matching players across every file\u2026", undefined);
    const data = await post("/api/upload", {files: [], token, more: false});
    const skipped = (data.rejected || []).length
      ? ` \u00b7 skipped ${data.rejected.map(r => r.name).join(", ")}` : "";
    setBusy(`Parsed ${data.hands} hands\u2026`, undefined);
    const finish = async (answers) => {
      setBusy("Saving and rebuilding profiles\u2026", undefined);
      const r = await post(`/api/session/${data.token}/commit`,
                           answers ? {answers} : {});
      // In the browser the import is not finished when the hands are stored --
      // they are stored in this tab. Uploading is part of the same action, so
      // it happens under the same veil rather than silently afterwards.
      // Absent on the desktop, which has no account to save to.
      if (window.villainSaveNow) {
        setBusy("Saving to your account\u2026", 0);
        try {
          await window.villainSaveNow((fraction) => setBusy(null, fraction));
        } catch (err) {
          $("#modal").innerHTML = "";
          status.innerHTML = `<span class="err">Stored here, but not saved to your `
            + `account: ${esc(err.message)}</span>`;
          if (done) done(status.innerHTML);
          return;
        }
      }
      $("#modal").innerHTML = "";
      // Inline, not a modal: after a batch you want to be looking at the
      // roster you just changed, not dismissing a box in front of it.
      const bits = [`${r.hands_new} new hand(s) stored`];
      if (r.duplicates) bits.push(`${r.duplicates} already known`);
      if (r.unusable) bits.push(`${r.unusable} unreadable`);
      if (r.players_new) bits.push(`${r.players_new} new player(s)`);
      if (r.merged) bits.push(`${r.merged} merge(s)`);
      if (r.priors_fitted) {
        bits.push(`priors refitted from ${r.priors_fitted.players} players`);
      }
      status.innerHTML = esc(bits.join(" \u00b7 ")) + esc(skipped) +
        (r.blocked || []).map(b => `<div class="err">${esc(b)}</div>`).join("");
      if (done) done(status.innerHTML);
    };
    if (data.questions && data.questions.length && !data.answered) {
      $("#modal").innerHTML = "";
      askIdentity(data.token, data.questions, finish, data.linked, data.conflicts);
    } else {
      await finish(null);
    }
  } catch (err) {
    $("#modal").innerHTML = "";
    status.innerHTML = `<span class="err">${esc(err.message)}</span>`;
  }
}

/* Binds whichever import controls are on the page. Both states of the
   Database tab share one handler so they cannot drift apart. */
function wireImport() {
  const input = $("#db-file"), status = $("#db-status"), drop = $("#db-drop");
  if (!input || !status) return;
  const go = (files) => importFiles(files, status, async (summary) => {
    state.player = null;
    await viewPlayers();
    // Hands just arrived: Hero and Simulate may have become possible.
    paintTabs();
    const after = $("#db-status");
    if (after && summary) after.innerHTML = summary;
  });
  input.onchange = () => go(input.files);
  const button = $("#db-add");
  if (button && drop) {
    button.onclick = () => { drop.hidden = false; input.click(); };
  }
  if (drop) {
    drop.onclick = () => input.click();
    drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = e => {
      e.preventDefault(); drop.classList.remove("over");
      go(e.dataTransfer.files);
    };
  }
  // Dropping anywhere on the panel works too: hunting for a target is friction
  // on the one action this tab exists for.
  const panel = status.closest(".panel");
  if (panel && drop) {
    panel.ondragover = e => { e.preventDefault(); drop.hidden = false; };
    panel.ondrop = e => {
      e.preventDefault();
      go(e.dataTransfer.files);
    };
  }
}

/* ---- sittings, derived from the database ---- */
function whenLabel(ms, withTime) {
  if (!ms) return "";
  const d = new Date(ms);
  // The year matters: these sittings span months, so "Aug 12" and "Oct 29"
  // are ambiguous without it.
  const thisYear = d.getFullYear() === new Date().getFullYear();
  const day = d.toLocaleDateString([], {weekday: "short", day: "numeric",
    month: "short", ...(thisYear ? {} : {year: "numeric"})});
  return withTime ? `${day} \u00b7 ${d.toLocaleTimeString([],
    {hour: "2-digit", minute: "2-digit"})}` : day;
}

async function viewSessions() {
  const view = $("#view");
  // No loading panel here: switchTab has already painted the spinner, and a
  // second, plainer one underneath it only made the wait look like two waits.
  const sessions = await get("/api/sessions");
  if (!sessions.length) {
    if (!onScreen("sessions")) return;
    view.innerHTML = `<div class="panel"><h2>no sittings yet</h2>
      <p class="muted">Add hand histories on the Database tab.</p></div>`;
    return;
  }
  if (state.sessionId == null) state.sessionId = sessions[0].id;
  // The list lives beside the detail, not above it: twenty sittings pushed the
  // thing you came to read off the bottom of the screen, and switching meant
  // scrolling back up every time.
  if (!onScreen("sessions")) return;
  view.innerHTML = `<div class="sess-layout${state.sessListHidden ? " collapsed" : ""}"
      id="sess-layout">
      <div class="panel sess-list">
        <div class="spread"><h2 style="margin:0">sittings</h2>
          <button class="iconbtn" id="sess-toggle"
            title="hide the list">\u00ab</button></div>
        <div id="sess-rows"></div>
      </div>
      <div class="panel sess-main">
        <h2 id="sess-title">who played, and how</h2>
        <div id="sess-body"></div>
      </div>
    </div>`;
  const rows = $("#sess-rows");
  for (const sess of sessions) {
    const item = document.createElement("button");
    item.className = "sess-item" + (sess.id === state.sessionId ? " on" : "");
    const hrs = Math.floor(sess.minutes / 60), mins = sess.minutes % 60;
    item.innerHTML = `<span class="sess-when">${esc(whenLabel(sess.started_at, true))}</span>
      <span class="small muted">${hrs ? hrs + "h " : ""}${mins}m \u00b7 ${
        sess.hands} hands \u00b7 ${sess.players}p</span>`;
    item.onclick = () => { state.sessionId = sess.id; viewSessions(); };
    rows.appendChild(item);
  }
  const layout = $("#sess-layout"), toggle = $("#sess-toggle");
  toggle.onclick = () => {
    state.sessListHidden = !state.sessListHidden;
    layout.classList.toggle("collapsed", state.sessListHidden);
    toggle.textContent = state.sessListHidden ? "\u00bb" : "\u00ab";
    toggle.title = state.sessListHidden ? "show the list" : "hide the list";
  };
  if (state.sessListHidden) { toggle.textContent = "\u00bb"; }
  const chosen = sessions.find(x => x.id === state.sessionId);
  if (chosen) {
    $("#sess-title").textContent =
      `${whenLabel(chosen.started_at, true)} \u00b7 ${chosen.hands} hands`;
  }
  await drawSession(state.sessionId);
}

async function drawSession(id) {
  const body = $("#sess-body");
  body.innerHTML = `<div class="empty">reading the sitting\u2026</div>`;
  const data = await get(`/api/session-detail?id=${id}`);
  body.innerHTML = "";
  for (const p of data.players) {
    const div = document.createElement("div");
    div.className = "leak" + (p.is_hero ? " hero-scope hero-sitting" : "");
    const netTxt = p.net_bb > 0 ? `+${p.net_bb}` : `${p.net_bb}`;
    div.innerHTML = `<div class="leak-head">
        <div class="sess-who"><b class="linkish">${esc(p.name)}</b>${
          p.is_hero ? '<span class="tag hero-tag">you</span>' : ""}
          <span class="tag arch ${p.confidence >= 0.5 ? "on" : ""}">${esc(p.archetype)}</span>
          <span class="small muted">${p.hands} hands \u00b7 ${esc(p.regime_label || "")}</span>
        </div>
        <div class="num small"><b>${netTxt}</b> <span class="muted">bb \u00b7 ${p.skill} skill</span></div></div>
      <div class="sess-deltas"></div>`;
    $("b", div).onclick = () => switchTab("players", p.player_id);
    const box = $(".sess-deltas", div);
    if (!p.deltas.length) {
      box.innerHTML = `<div class="small muted">No trend yet \u2014 not enough
        hands at this table size outside this sitting to say what is usual.</div>`;
    } else {
      // One table size at a time, picked with a tab, rather than every table
      // size stacked under its own heading. Rendering a row per (stat,
      // regime) put VPIP on screen two or three times with the table size in
      // small print, which read as a duplicate rather than as two different
      // games -- stacked headings fixed the duplication but still made you
      // scroll past every table size to find the one you sat at. A player
      // who only played one table size gets no tabs at all.
      const byRegime = new Map();
      for (const d of p.deltas) {
        const key = d.regime_label || d.regime || "";
        if (!byRegime.has(key)) byRegime.set(key, []);
        byRegime.get(key).push(d);
      }
      const regimes = [...byRegime.keys()];
      const rows = document.createElement("div");
      const drawRows = label => {
        rows.innerHTML = "";
        for (const d of byRegime.get(label)) {
          const row = document.createElement("div");
          row.className = "sess-delta";
          const up = d.delta > 0;
          row.innerHTML = `<span>${esc(statLabel(d.stat, null))}</span>
            <span class="num">${fmtPct(d.session)}</span>
            <span class="small ${up ? "up" : "down"}">${up ? "\u25b2" : "\u25bc"}${
              Math.abs(Math.round(d.delta * 100))}pp</span>
            <span class="small muted">usually ${fmtPct(d.usual)}</span>`;
          rows.appendChild(row);
        }
      };
      if (regimes.length > 1) {
        const tabs = document.createElement("div");
        tabs.className = "sess-regime-tabs";
        regimes.forEach((label, i) => {
          const b = document.createElement("button");
          b.className = "sess-regime-tab" + (i === 0 ? " on" : "");
          b.textContent = label;
          b.onclick = () => {
            tabs.querySelectorAll(".sess-regime-tab").forEach(x => x.classList.remove("on"));
            b.classList.add("on");
            drawRows(label);
          };
          tabs.appendChild(b);
        });
        box.appendChild(tabs);
      }
      box.appendChild(rows);
      drawRows(regimes[0]);
    }
    body.appendChild(div);
  }
}

/* ---- tab 3: hero ---- */
const RANK_ORDER = "AKQJT98765432";

function rangeGrid(grid) {
  const wrap = document.createElement("div");
  wrap.className = "range-grid";
  for (let i = 0; i < 13; i++) {
    for (let j = 0; j < 13; j++) {
      const hi = RANK_ORDER[i], lo = RANK_ORDER[j];
      const cls = i === j ? hi + lo : i < j ? hi + lo + "s" : lo + hi + "o";
      const g = (grid || {})[cls];
      const dealt = g ? g.dealt : 0, played = g ? g.played : 0;
      const pct = dealt ? played / dealt : 0;
      const cell = document.createElement("div");
      cell.className = "range-cell" + (pct > 0.5 ? " dark-text" : "");
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

/* A graded report (fold grades / missed value) shares one shape: graded,
   flagged, rate, by_street, by_texture, worst[]. One renderer for both. */
function renderGradedSection(el, section, opts) {
  if (!section || !section.graded) {
    el.innerHTML = `<div class="small muted">${esc(opts.emptyText)}</div>`;
    return;
  }
  const summary = document.createElement("p");
  summary.innerHTML = `<b>${section.graded}</b> ${esc(opts.noun)} graded, <b>${
      section.flagged}</b> (${fmtPct(section.rate)}) ${esc(opts.verdict)}`;
  // The by-street and by-texture splits are detail -- one hover away, not two
  // more lines of muted text under every section.
  const byStreet = Object.entries(section.by_street)
    .map(([k, v]) => `${k} ${fmtPct(v.flagged / v.graded)}`).join(" \u00b7 ");
  const byTex = Object.entries(section.by_texture)
    .map(([k, v]) => `${k} ${fmtPct(v.flagged / v.graded)}`).join(" \u00b7 ");
  summary.appendChild(info(
    `<b>by street</b><br>${byStreet}<br><br><b>by texture</b><br>${byTex}`));
  el.appendChild(summary);

  for (const g of section.worst) {
    const row = document.createElement("div");
    row.className = "fold-row";
    row.innerHTML = `
      <span class="small muted">${esc(g.street)}</span>
      <span class="mono">${g.hole_cards.map(esc).join(" ")}</span>
      <span class="small fold-summary">${esc(g.summary)}</span>
      <span class="small muted">${esc(g.texture)} board</span>`;
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
function renderTellSection(el, section) {
  const rows = Object.entries(section || {});
  if (!rows.length) {
    el.innerHTML = `<div class="small muted">${esc(TELL_EMPTY_TEXT)}</div>`;
    return;
  }
  for (const [street, v] of rows) {
    const row = document.createElement("div");
    row.className = "metric";
    row.innerHTML = `<span>${esc(street)}${
        v.is_tell ? '<span class="tag hero-tag" style="margin-left:8px">tell</span>' : ""
      }</span><span class="small">${esc(v.in_words || "")}</span>`;
    el.appendChild(row);
  }
}

/* Hero read through the ordinary villain machinery -- their own priced leaks
   and headline numbers, since hero is just another player in the database.
   Rendered blue (the dash is hero-scoped) and framed for self-coaching. */
function renderHeroSelf(dash, self) {
  if (!self) return;
  const leaks = self.leaks || [];
  if (leaks.length) {
    const box = document.createElement("div");
    box.className = "panel wide";
    box.innerHTML = `<h2>your biggest leaks</h2>
      <div class="small muted" style="margin:-6px 0 14px">
        What a sharp opponent studying you would target, priced in the bb/100
        they could win from it. Fix the dear ones first.</div>
      <div class="leaks"></div>`;
    const host = $(".leaks", box);
    for (const l of leaks) {
      const div = document.createElement("div");
      div.className = "leak";
      div.innerHTML = `
        <div class="leak-head">
          <div class="headline"><b>${esc(l.headline)}</b>
            <span class="tag tier">${esc(l.tier)}</span></div>
          <div class="num small muted">${l.severity_bb100.toFixed(2)} bb/100</div></div>
        <div class="small muted numbers"></div>`;
      const numbers = $(".numbers", div);
      if (self.player_id != null) {
        const link = document.createElement("button");
        link.className = "linkbtn";
        link.textContent = `seen about ${Math.round(l.sample)} times`;
        link.title = "show the hands behind this";
        link.onclick = () => showEvidence(self.player_id, l.stat, l.headline);
        numbers.appendChild(link);
      } else {
        numbers.appendChild(document.createTextNode(
          `seen about ${Math.round(l.sample)} times`));
      }
      numbers.appendChild(info(statTip(l.stat, l.headline)));
      host.appendChild(div);
    }
    dash.appendChild(box);
  }

  const rows = self.rows || [];
  if (rows.length) {
    const box = document.createElement("div");
    box.className = "panel wide";
    box.innerHTML = `<h2>your tendencies</h2>
      <div class="small muted" style="margin:-6px 0 14px">
        Your rates against the pool you were measured against -- the bar is the
        estimate, hover a row for what is typical.</div>
      <div class="scroller"><table><thead><tr><th>stat</th>
        <th style="width:40%">0% \u2014 100%</th>
        <th class="num">you</th><th class="num">sample</th></tr></thead>
        <tbody></tbody></table></div>`;
    const tbody = $("tbody", box);
    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="label"></td><td></td>
        <td class="num">${fmtPct(row.value)}</td>
        <td class="num small muted">${Math.round(row.opps)}</td>`;
      const label = $(".label", tr);
      label.appendChild(document.createTextNode(row.label));
      label.appendChild(info(statTip(row.stat, row.label, row)));
      tr.children[1].appendChild(statRow(row));
      tbody.appendChild(tr);
    }
    dash.appendChild(box);
  }
}

const SUIT = {s: "♠", h: "♥", d: "♦", c: "♣"};
function cardHtml(txt, big) {
  const red = txt[1] === "h" || txt[1] === "d";
  return `<span class="card ${red ? "red" : "black"}${big ? " big" : ""}"><span
    class="r">${esc(txt[0] === "T" ? "10" : txt[0])}</span>${SUIT[txt[1]] || ""}</span>`;
}
function actbtn(label, on, cls) {
  const b = document.createElement("button");
  b.className = "act" + (cls ? " " + cls : "");
  b.textContent = label; b.onclick = on; return b;
}

async function viewPlay() {
  const view = $("#view");
  if (state.game) { renderTable(view, state.game); return; }
  if (!onScreen("play")) return;
  view.innerHTML = `<div class="panel"><h2>Simulate</h2>
    <div class="small muted" style="margin:-6px 0 16px">Sit at a table and play real hands
      against players from your database. Each villain acts from their own measured profile —
      loose ones call wide, nits fold, aggressive ones barrel — so it plays like practice
      against the people you actually face. Pick up to five and sit down.</div>
    <div id="pick-list" class="pick-list"><div class="empty">loading…</div></div>
    <div class="sit-controls">
      <label class="small muted">stack <input id="sit-stack" type="number" value="200" min="20"></label>
      <label class="small muted">blinds <input id="sit-sb" type="number" value="1" min="1">
        / <input id="sit-bb" type="number" value="2" min="2"></label>
      <button class="act primary" id="sit-go" disabled>Sit down</button>
    </div></div>`;
  const roster = await get("/api/roster");
  const players = (roster.players || [])
    .filter(p => p.player_id != null && p.player_id !== roster.hero_id && p.hands >= 30)
    .sort((a, b) => b.hands - a.hands);
  const list = $("#pick-list"); list.innerHTML = "";
  if (!players.length) { list.innerHTML = `<div class="small muted">No players with
    enough hands yet — import some on the Database tab.</div>`; return; }
  const picked = new Set();
  for (const p of players) {
    const b = document.createElement("button");
    b.className = "pick";
    b.innerHTML = `<span class="name">${esc(p.name)}</span>
      <span class="small muted">${p.hands} hands · ${esc(p.archetype)} · GTO ${
        p.gto != null ? Math.round(p.gto) : "—"}</span>`;
    b.onclick = () => {
      if (picked.has(p.player_id)) { picked.delete(p.player_id); b.classList.remove("on"); }
      else if (picked.size < 5) { picked.add(p.player_id); b.classList.add("on"); }
      $("#sit-go").disabled = picked.size === 0;
    };
    list.appendChild(b);
  }
  $("#sit-go").onclick = async () => {
    $("#sit-go").disabled = true;
    ensureAudio();
    try {
      const data = await post("/api/sim/new", {villains: [...picked],
        stack: +$("#sit-stack").value, sb: +$("#sit-sb").value, bb: +$("#sit-bb").value});
      state.game = data;
      renderTable($("#view"), data);
    } catch (err) { $("#sit-go").disabled = false; alert(err.message); }
  };
}

const SIM_DELAY = 4000;                 // ~4s per action so you can watch it
let _actx = null;
function ensureAudio() {
  try {
    _actx = _actx || new (window.AudioContext || window.webkitAudioContext)();
    if (_actx.state === "suspended") _actx.resume();
  } catch (e) {}
}
function chipSound() {
  if (state.muted || !_actx) return;
  try {
    const ctx = _actx, t = ctx.currentTime;
    for (let k = 0; k < 3; k++) {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "triangle"; o.frequency.value = 850 + Math.random() * 550;
      const s0 = t + k * 0.028;
      g.gain.setValueAtTime(0.0001, s0);
      g.gain.exponentialRampToValueAtTime(0.07, s0 + 0.004);
      g.gain.exponentialRampToValueAtTime(0.0001, s0 + 0.06);
      o.connect(g); g.connect(ctx.destination);
      o.start(s0); o.stop(s0 + 0.07);
    }
  } catch (e) {}
}

// A knuckle-rap on the felt: two short low thuds, the way a check sounds at a
// real table. Deliberately duller and quieter than the chips so a table full
// of checks never drowns out the action that costs someone money.
function checkSound() {
  if (state.muted || !_actx) return;
  try {
    const ctx = _actx, t = ctx.currentTime;
    for (let k = 0; k < 2; k++) {
      const o = ctx.createOscillator(), g = ctx.createGain(), f = ctx.createBiquadFilter();
      o.type = "triangle"; o.frequency.value = 220 - k * 40;
      f.type = "lowpass"; f.frequency.value = 1400;
      const s0 = t + k * 0.075;
      g.gain.setValueAtTime(0.0001, s0);
      g.gain.exponentialRampToValueAtTime(0.16, s0 + 0.003);
      g.gain.exponentialRampToValueAtTime(0.0001, s0 + 0.11);
      o.connect(f); f.connect(g); g.connect(ctx.destination);
      o.start(s0); o.stop(s0 + 0.1);
    }
  } catch (e) {}
}

// Two cards sliding away across the felt: a short noise burst under a lowpass
// that closes as it goes, which is what a muck actually sounds like. Softest
// of the three -- folding is the one action nobody needs announced.
let _noise = null;
function noiseBuffer(ctx) {
  if (_noise) return _noise;
  const n = Math.floor(ctx.sampleRate * 0.3);
  _noise = ctx.createBuffer(1, n, ctx.sampleRate);
  const d = _noise.getChannelData(0);
  for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
  return _noise;
}
function foldSound() {
  if (state.muted || !_actx) return;
  try {
    const ctx = _actx, t = ctx.currentTime;
    const src = ctx.createBufferSource(), g = ctx.createGain(), f = ctx.createBiquadFilter();
    src.buffer = noiseBuffer(ctx);
    f.type = "lowpass";
    f.frequency.setValueAtTime(3200, t);
    f.frequency.exponentialRampToValueAtTime(700, t + 0.2);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.13, t + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.24);
    src.connect(f); f.connect(g); g.connect(ctx.destination);
    src.start(t); src.stop(t + 0.22);
  } catch (e) {}
}

//: One place that maps an action to the noise it makes.
function actionSound(action) {
  if (action === "call" || action === "raise") chipSound();
  else if (action === "check") checkSound();
  else if (action === "fold") foldSound();
}

function actionText(ev) {
  if (!ev) return "";
  if (ev.action === "fold") return "Folds";
  if (ev.action === "check") return "Checks";
  if (ev.action === "call") return "Calls";
  return `Raises to ${ev.amount}`;
}

async function simPost(route, extra) {
  if (state.stepTimer) { clearTimeout(state.stepTimer); state.stepTimer = null; }
  const token = state.game.token;
  try {
    const d = await post(route, Object.assign({token}, extra || {}));
    state.game = {token, state: d.state};
    if (route === "/api/sim/next") { state.lastEvent = null; state.revealed = false; }
    renderTable($("#view"), state.game);
  } catch (err) { /* game gone or navigated away */ }
}

function renderTable(view, data) {
  if (state.stepTimer) { clearTimeout(state.stepTimer); state.stepTimer = null; }
  // The simulator outlives the tab. A queued auto-step, or a reply that lands
  // after you have moved on, used to paint the table over whichever tab you
  // had switched to. The game is kept in state.game and drawn again by
  // viewPlay when you come back to it.
  if (state.tab !== "play") return;
  const st = data.state, n = st.seats.length;
  const pnl = st.pnl || 0;
  const pnlBb = st.bb ? (pnl / st.bb).toFixed(1) : "0";
  view.innerHTML = `<div class="panel sim-panel"><div class="sim-layout">
    <div class="sim-main">
      <div class="spread"><h2 style="margin:0">hand ${st.hand_no} <span
        class="muted" style="font-weight:400">· ${esc(st.street)}</span></h2>
        <button class="linkbtn" id="leave">leave table</button></div>
      <div class="poker-table" id="ptable">
        <div class="felt-oval"></div>
        <div class="table-center">
          <div class="pot-pill">pot <b>${st.pot}</b></div>
          <div class="board" id="board"></div>
        </div>
      </div>
      <div class="controls" id="controls"></div>
      <div class="handlog small muted" id="handlog"></div>
    </div>
    <div class="sim-side">
      <div class="side-label">session P/L</div>
      <div class="pnl-big ${pnl >= 0 ? "up" : "down"}">${pnl >= 0 ? "+" : ""}${pnl}</div>
      <div class="small muted">${pnl >= 0 ? "+" : ""}${pnlBb} bb · ${st.hand_no} hands</div>
      <div class="small muted" style="margin-top:3px">blinds ${st.sb}/${st.bb}</div>
      <label class="desc-toggle"><input type="checkbox" id="desc-on"${
        state.descOn ? " checked" : ""}> explain decisions</label>
      <label class="desc-toggle" style="margin-top:8px"><input type="checkbox" id="snd-on"${
        state.muted ? "" : " checked"}> table sounds</label>
      <button class="act small" id="end-session" style="margin-top:18px">End &amp; analyze</button>
    </div>
  </div></div>`;
  const table = $("#ptable");
  st.seats.forEach((s, i) => {
    const theta = Math.PI / 2 + (i / n) * 2 * Math.PI;   // you (0) at the bottom
    const x = 50 + 45 * Math.cos(theta), y = 50 + 44 * Math.sin(theta);
    const seat = document.createElement("div");
    seat.className = "tseat" + (s.is_hero ? " me hero-scope" : "")
      + (s.folded ? " folded" : "") + (s.to_act ? " acting" : "") + (s.won ? " won" : "");
    seat.style.left = x + "%"; seat.style.top = y + "%";
    const shownHole = (state.revealed && s.all_hole) ? s.all_hole : s.hole;
    const cards = shownHole ? shownHole.map(c => cardHtml(c, s.is_hero)).join("")
      : (s.folded ? "" : '<span class="cardback sm"></span><span class="cardback sm"></span>');
    seat.innerHTML = `<div class="tseat-cards">${cards}</div>
      <div class="tseat-body">
        <div class="tseat-name">${esc(s.name)}${
          s.is_hero ? ' <span class="tag hero-tag">you</span>' : ""}</div>
        <div class="tseat-stack">${s.stack}${
          st.over && s.net ? ` <span class="won-amt${s.net < 0 ? " down" : ""}">${
            s.net > 0 ? "+" : ""}${s.net}</span>` : ""}</div>
      </div>`;
    const ev = state.lastEvent;
    if (ev && ev.seat === i && !s.is_hero) {
      const why = (ev.reason || "").split("—").slice(1).join("—").trim();
      const bubble = document.createElement("div");
      bubble.className = "think-bubble" + (y > 50 ? " below" : "");  // outward, off the felt
      bubble.innerHTML = `<b>${esc(actionText(ev))}</b>${
        state.descOn && why ? `<div class="why">${esc(why)}</div>` : ""}`;
      seat.appendChild(bubble);
    }
    table.appendChild(seat);
    if (s.is_button) {
      const d = document.createElement("div"); d.className = "dealer-btn"; d.textContent = "D";
      d.style.left = (50 + 33 * Math.cos(theta - 0.4)) + "%";
      d.style.top = (50 + 32 * Math.sin(theta - 0.4)) + "%";
      table.appendChild(d);
    }
    if (s.committed > 0 && !st.over) {
      const chip = document.createElement("div"); chip.className = "tbet";
      chip.style.left = (50 + 27 * Math.cos(theta)) + "%";
      chip.style.top = (50 + 26 * Math.sin(theta)) + "%";
      chip.innerHTML = `<span class="chip-dot"></span>${s.committed}`;
      table.appendChild(chip);
    }
  });
  $("#board").innerHTML = st.board.length ? st.board.map(c => cardHtml(c, true)).join("") : "";
  $("#handlog").innerHTML = (st.log || []).map(l => `<div>${esc(l)}</div>`).join("");
  $("#leave").onclick = () => {
    if (state.stepTimer) clearTimeout(state.stepTimer);
    state.game = null; state.lastEvent = null; viewPlay();
  };
  $("#desc-on").onchange = (e) => { state.descOn = e.target.checked; };
  $("#snd-on").onchange = (e) => { state.muted = !e.target.checked; if (!state.muted) ensureAudio(); };
  $("#end-session").onclick = () => endSession();
  renderControls($("#controls"), data);
  if (!st.over && !st.your_turn) {           // pace the villains so you can watch
    const wait = (state.lastEvent && state.lastEvent.action === "fold") ? 2000 : SIM_DELAY;
    state.stepTimer = setTimeout(() => stepBots(data.token), wait);
  } else if (st.over && !state.revealed) {     // auto-deal next, unless paused to reveal
    state.stepTimer = setTimeout(() => simPost("/api/sim/next"), SIM_DELAY);
  }
}

async function stepBots(token) {
  try {
    const r = await post("/api/sim/step", {token});
    if (r.event) actionSound(r.event.action);
    state.game = {token, state: r.state};
    state.lastEvent = r.event;
    renderTable($("#view"), state.game);
  } catch (err) { /* game gone or navigated away */ }
}

function cfArmed(st) {
  // Armed for *this hand*, not for the session. It has to survive the whole
  // hand -- you arm it on the flop and it should still fold the river -- but
  // a standing instruction that outlives the hand is how you fold aces two
  // hands later without touching anything.
  return state.checkFold && state.checkFoldHand === st.hand_no;
}

function cfToggle(st) {
  // Shown in every state of the table, including while the villains act,
  // which is exactly when you want to set it.
  const armed = cfArmed(st);
  const b = document.createElement("button");
  b.className = "act cf-toggle" + (armed ? " on" : "");
  b.textContent = "Check / Fold";
  b.setAttribute("aria-pressed", armed ? "true" : "false");
  b.title = armed
    ? "Armed for this hand: checks when it can, folds when it cannot. Click to disarm."
    : "Check when possible, fold when facing a bet — for the rest of this hand.";
  b.onclick = () => {
    const on = !cfArmed(st);
    state.checkFold = on;
    state.checkFoldHand = on ? st.hand_no : null;
    renderTable($("#view"), state.game);
  };
  return b;
}

function renderControls(el, data) {
  const st = data.state; el.innerHTML = "";
  if (st.over) {
    if (!state.revealed && st.seats.some(x => x.all_hole && !x.is_hero)) {
      el.appendChild(actbtn("Reveal cards", () => {
        if (state.stepTimer) { clearTimeout(state.stepTimer); state.stepTimer = null; }
        state.revealed = true; renderTable($("#view"), data);
      }));
    }
    el.appendChild(actbtn("Deal now", () => simPost("/api/sim/next"), "primary"));
    el.appendChild(cfToggle(st));
    el.appendChild(actbtn("End & analyze", () => endSession()));
    const won = st.seats.filter(s => s.won);
    if (won.length) {
      const note = document.createElement("span"); note.className = "small muted";
      note.style.marginLeft = "10px";
      note.textContent = won.map(s => `${s.name} +${s.won}`).join(" · ") + " · next hand dealing…";
      el.appendChild(note);
    }
    return;
  }
  if (!st.your_turn) {
    const note = document.createElement("span");
    note.className = "small muted"; note.textContent = "villains acting…";
    el.appendChild(note);
    el.appendChild(cfToggle(st));
    return;
  }
  const lg = st.legal;
  const act = (kind, amount) => {
    actionSound(kind);
    simPost("/api/sim/act", {kind, amount: amount || 0});
  };
  if (cfArmed(st)) {
    // `act` is declared above this on purpose -- calling it from here while
    // it was still a `const` below threw a ReferenceError every time, so the
    // option silently did nothing.
    act(lg.can_check ? "check" : "fold");
    return;
  }
  el.appendChild(cfToggle(st));
  if (lg.can_fold) el.appendChild(actbtn("Fold", () => act("fold")));
  if (lg.can_check) el.appendChild(actbtn("Check", () => act("check")));
  else if (lg.can_call) el.appendChild(actbtn(`Call ${lg.call_amount}`, () => act("call")));
  if (lg.can_raise) {
    const potAfter = st.pot + lg.call_amount;
    const sizeTo = (frac) => {
      const raw = lg.can_check ? Math.round(frac * st.pot)
        : (lg.committed + lg.call_amount) + Math.round(frac * potAfter);
      return Math.min(lg.max_raise_to, Math.max(lg.min_raise_to, raw));
    };
    const wrap = document.createElement("div"); wrap.className = "raise-wrap";
    const slider = document.createElement("input");
    slider.type = "range"; slider.min = lg.min_raise_to; slider.max = lg.max_raise_to;
    slider.value = sizeTo(0.66);
    const amt = document.createElement("span"); amt.className = "raise-amt";
    const upd = () => { amt.textContent = `to ${slider.value}`; };
    slider.oninput = upd; upd();
    const presets = document.createElement("div"); presets.className = "presets";
    for (const [label, frac] of [["½", 0.5], ["⅔", 0.66], ["pot", 1.0]]) {
      presets.appendChild(actbtn(label, () => { slider.value = sizeTo(frac); upd(); }, "small"));
    }
    presets.appendChild(actbtn("all-in", () => { slider.value = lg.max_raise_to; upd(); }, "small"));
    wrap.append(slider, amt, presets,
      actbtn(lg.can_check ? "Bet" : "Raise", () => act("raise", +slider.value), "primary"));
    el.appendChild(wrap);
  }
}

async function endSession() {
  if (state.stepTimer) { clearTimeout(state.stepTimer); state.stepTimer = null; }
  const token = state.game.token;
  const r = await post("/api/sim/analysis", {token});
  renderAnalysis($("#view"), r.analysis);
}

function renderAnalysis(view, a) {
  const money = (c, bb) => `${c >= 0 ? "+" : ""}${c} <span class="muted">(${
    c >= 0 ? "+" : ""}${bb} bb)</span>`;
  const stat = (label, v) => `<div class="astat"><div class="astat-v">${
    v == null ? "—" : v + "%"}</div><div class="astat-l">${label}</div></div>`;
  view.innerHTML = `<div class="panel"><div class="spread"><h2 style="margin:0">session analysis</h2>
      <button class="linkbtn" id="a-back">back to setup</button></div>
    <div class="small muted" style="margin:-4px 0 16px">${a.hands} hands played.</div>
    <div class="a-headline">
      <div class="pnl-big ${a.pnl >= 0 ? "up" : "down"}">${a.pnl >= 0 ? "+" : ""}${a.pnl}</div>
      <div class="small muted">${a.pnl_bb >= 0 ? "+" : ""}${a.pnl_bb} bb · ${
        a.bb100 >= 0 ? "+" : ""}${a.bb100} bb/100 over ${a.hands} hands</div>
    </div>
    <h3 style="margin:20px 0 8px">your line this session</h3>
    <div class="astats">${stat("VPIP", a.vpip)}${stat("PFR", a.pfr)}${
      stat("aggression", a.aggression)}${stat("to showdown", a.went_to_showdown)}${
      stat("won at SD", a.won_at_showdown)}</div>
    <h3 style="margin:22px 0 8px">against each villain</h3>
    <div class="a-vs"></div>
    <h3 style="margin:22px 0 8px">swings</h3>
    <div class="small">Biggest pot won: ${a.best ? `hand ${a.best.hand_no}, ${money(a.best.net, (a.best.net/1).toFixed(0))}`.replace(/\(.*\)/, "") : "—"}</div>
    <div class="small">Worst hand: ${a.worst ? `hand ${a.worst.hand_no}, ${a.worst.net}` : "—"}</div>
  </div>`;
  const vs = $(".a-vs", view);
  for (const v of a.vs) {
    const row = document.createElement("div"); row.className = "a-vs-row";
    row.innerHTML = `<span>${esc(v.name)}</span>
      <span class="${v.net >= 0 ? "up" : "down"}" style="font-variant-numeric:tabular-nums">${
        v.net >= 0 ? "+" : ""}${v.net} <span class="muted">(${
        v.net_bb >= 0 ? "+" : ""}${v.net_bb} bb)</span></span>`;
    vs.appendChild(row);
  }
  $("#a-back").onclick = () => { state.game = null; state.lastEvent = null; viewPlay(); };
}

async function viewHero() {
  const view = $("#view");
  // Blocking, because in the browser this genuinely blocks: there is no thread
  // to build the hero model on, so the tab cannot answer anything -- including
  // a click on another tab -- until it is done. Three minutes of that with an
  // inline spinner reads as a hung page. The veil says what is happening, and
  // the tab lock that comes with it turns "nothing responds" into "not yet".
  //
  // Only for a cold build. Once the cache is warm this returns immediately and
  // a veil would be a flash of furniture.
  let cold = false;
  try {
    cold = (await get("/api/hero?peek=1")).status === "cold";
  } catch (err) {
    // Do not quietly assume warm. Guessing wrong here means a build that takes
    // minutes runs with no veil and no progress registered -- a bare spinner,
    // which is the one outcome this whole path exists to avoid. Treat an
    // unanswerable peek as cold and say so.
    console.warn("hero: could not check the cache, assuming cold", err);
    cold = true;
  }
  const done = cold
    ? showBusy("Reading your own hands\u2026", undefined)
    : null;
  if (cold) {
    // Say the two things somebody watching a long wait needs to know: that it
    // will finish, and that it will not happen again.
    const note = $("#busy-text");
    if (note) {
      note.insertAdjacentHTML("afterend",
        '<div class="small muted" style="margin-top:6px;max-width:34rem">'
        + 'Fitting a hand-strength model over every hand you have played, so '
        + 'your folds and your sizing can be graded against what you actually '
        + 'held. <b>This runs once</b> — after it, the Hero tab opens instantly '
        + 'until your next import.</div>');
    }
    // Real progress, reported from inside the Python as it walks. The walk
    // over hands can be counted; fitting the trees cannot, and says so by
    // sending no total rather than by inventing one.
    const PHASES = {
      starting: "Opening your database",
      finding: "Finding which seat is yours",
      loading: "Reading your hand histories",
      reading: "Scoring every hand you played",
      fitting: "Fitting the model to what you held",
      grading: "Grading your folds and your sizing",
    };
    window.__villainProgress = (msg) => {
      const label = PHASES[msg.phase] || "Working";
      if (msg.total > 0) {
        done(`${label}\u2026 ${msg.done.toLocaleString()} of ${msg.total.toLocaleString()}`,
             msg.done / msg.total);
      } else {
        // No total means this phase cannot be counted -- or has not started
        // counting yet. Name it and show the travelling bar rather than a
        // number nobody measured, so there is never a spinner on its own.
        done(`${label}\u2026`, undefined);
      }
    };
  }
  let data;
  try {
    data = await get("/api/hero");
    delete window.__villainProgress;
    if (done) $("#modal").innerHTML = "";
    // A cold build takes minutes; the reader may well be somewhere else by now.
    if (!onScreen("hero")) return;
    if (data && data.status === "building") {
      // The build runs once per import and takes about a minute and a half.
      // Say so and keep asking, rather than holding the request open with a
      // blank tab behind it.
      view.innerHTML = `<div class="panel"><h2>Hero</h2>
        <p>${esc(data.message || "Reading your hands…")}</p>
        <p class="small muted">This page will appear on its own when it is ready.</p></div>`;
      if (state.heroPoll) clearTimeout(state.heroPoll);
      state.heroPoll = setTimeout(() => { if (state.tab === "hero") viewHero(); }, 4000);
      return;
    }
    if (state.heroPoll) { clearTimeout(state.heroPoll); state.heroPoll = null; }
  } catch (err) {
    delete window.__villainProgress;
    $("#modal").innerHTML = "";
    view.innerHTML = `<div class="panel"><h2>hero</h2>
      <p class="err">${esc(err.message)}</p></div>`;
    return;
  }
  if (done) $("#modal").innerHTML = "";

  // Hero is a player, so render the full profile card -- header, skill
  // breakdown, your leaks, key numbers: everything a villain's page has, in the
  // same dashboard layout -- then hang the hero-only deep-dives below it.
  // hero:true drops the two opponent-directed pieces (the plan, and the
  // per-leak "do this to them").
  const dash = profileCard(data.self, {heroId: data.hero_id, hero: true});

  // Grid and position breakdown side by side: two views of the same range,
  // one by hand the other by seat. dash-cols is the two-panel layout the
  // skill/read split already uses on a player's own page.
  const rangeCols = document.createElement("div");
  rangeCols.className = "dash-cols wide";
  const gridCol = document.createElement("div");
  gridCol.className = "col";
  gridCol.innerHTML = `<div class="panel">
    <h2 id="hero-range-head">preflop range</h2>
    <div class="small muted" style="margin:-6px 0 10px">Cards known on ${fmtPct(data.visibility)} of ${data.hands} hands \u2014 only your own export shows this.</div>
    <div id="hero-grid"></div>
    <div class="range-legend"><span>never</span><span class="ramp"></span><span>always</span></div>
  </div>`;
  const posCol = document.createElement("div");
  posCol.className = "col";
  posCol.innerHTML = `<div class="panel">
    <h2>by position</h2>
    <div id="hero-positions"></div>
  </div>`;
  rangeCols.append(gridCol, posCol);
  dash.appendChild(rangeCols);

  const gradesPanel = document.createElement("div");
  gradesPanel.className = "panel wide";
  gradesPanel.innerHTML = `
    <h2 id="hero-grades-head">fold grades &amp; missed value</h2>
    <h3>fold grades</h3>
    <div id="hero-folds"></div>
    <h3>missed value</h3>
    <div id="hero-missed"></div>`;
  dash.appendChild(gradesPanel);

  const tellsPanel = document.createElement("div");
  tellsPanel.className = "panel wide";
  tellsPanel.innerHTML = `
    <h2 id="hero-tells-head">sizing &amp; timing tells</h2>
    <h3>sizing</h3>
    <div id="hero-sizing"></div>
    <h3>timing</h3>
    <div id="hero-timing"></div>`;
  dash.appendChild(tellsPanel);

  const narrowingPanel = document.createElement("div");
  narrowingPanel.className = "panel wide";
  narrowingPanel.innerHTML = `
    <h2 id="hero-narrowing-head">range narrowing</h2>
    <div id="hero-narrowing"></div>`;
  dash.appendChild(narrowingPanel);

  $("#modal").innerHTML = "";          // dismiss the loader
  view.innerHTML = "";
  view.appendChild(dash);

  $("#hero-range-head", dash).appendChild(info(
    `Every hand you were ever dealt, not just the ones you played -- something
    only your own export can show. Darker means played (raised or called)
    more often.`));
  $("#hero-grades-head", dash).appendChild(info(
    `${termTip("percentile")}<br><br><span class="hl">fold grades</span> --
    postflop folds, graded against what a bet like that one usually turns out
    to be.<br><br><span class="hl">missed value</span> -- the mirror question,
    asked of checks that could have bet instead.`));
  $("#hero-tells-head", dash).appendChild(info(
    `Does your bet size, or think time, change with the hand behind it?
    Nobody's hand strength is known often enough to ask a villain this --
    yours is known on every bet, not just the ones that reached showdown.`));
  $("#hero-narrowing-head", dash).appendChild(info(
    `A continuing range is supposed to get stronger street by street, as the
    wide ones give up along the way. Average hand strength among hands still
    live, by street, says whether yours does.`));

  $("#hero-grid", dash).appendChild(rangeGrid(data.grid));

  const positions = $("#hero-positions", dash);
  positions.className = "hero-pos";
  const ranges = [...(data.ranges || [])].sort(
    (a, b) => POSITION_ORDER.indexOf(a.position) - POSITION_ORDER.indexOf(b.position));
  for (const r of ranges) {
    if (!r.hands) continue;
    const played = (r.raised + r.called) / r.hands;
    const row = document.createElement("div");
    row.className = "pos-row";
    row.innerHTML = `
      <span class="pos-name">${esc(r.position)}<span class="small muted"> ${r.hands}h</span></span>
      <span class="pos-bar"></span>
      <span class="pos-val small muted">${fmtPct(played)} played</span>`;
    const b = bar(played, 1, "var(--mark-3)", 150);
    b.setAttribute("preserveAspectRatio", "none");
    $(".pos-bar", row).appendChild(b);
    positions.appendChild(row);
  }

  if (data.grade_error) {
    $("#hero-folds", dash).innerHTML = `<div class="small muted">${esc(data.grade_error)}</div>`;
    $("#hero-missed", dash).innerHTML = "";
  } else {
    renderGradedSection($("#hero-folds", dash), data.fold_grades, {
      noun: "folds", heroId: data.hero_id,
      verdict: "had more edge than the bet typically shows",
      emptyText: "Not enough postflop folds with a clean line to grade yet.",
    });
    renderGradedSection($("#hero-missed", dash), data.missed_value, {
      noun: "checks", heroId: data.hero_id,
      verdict: "had more edge than the check typically shows",
      emptyText: "Not enough postflop checks with a clean line to grade yet.",
    });
  }

  renderTellSection($("#hero-sizing", dash), data.sizing);
  renderTellSection($("#hero-timing", dash), data.timing);

  const narrowing = $("#hero-narrowing", dash);
  if (!data.narrowing || !data.narrowing.length) {
    narrowing.innerHTML = `<div class="small muted">Not enough hands reaching
      each street yet.</div>`;
  } else {
    const row = document.createElement("p");
    row.textContent = data.narrowing
      .map(s => `${s.street} ${fmtPct(s.avg_strength)} (${s.hands})`).join("   ");
    narrowing.appendChild(row);
    const strengths = data.narrowing.map(s => s.avg_strength);
    if (strengths.length >= 2) {
      const monotone = strengths.every((v, i) => i === 0 || v >= strengths[i - 1]);
      const note = document.createElement("p");
      note.className = "small muted";
      note.textContent = monotone
        ? "Narrows street by street, as a continuing range should."
        : "Does not narrow monotonically -- worth a look at which street gives it back.";
      narrowing.appendChild(note);
    }
  }
}

/* ---- tab 1: session ---- */
function viewSession() {
  const view = $("#view");
  view.innerHTML = `
    <div class="panel">
      <div class="spread"><h2>read a session</h2>
        <span class="small muted">nothing is saved until you ask</span></div>
      <div class="drop" id="drop">
        <div style="font-size:15px;color:var(--ink)">Drop hand history files here</div>
        <div class="small" style="margin-top:4px">or click to choose \u00b7 PokerNow JSON exports</div>
      </div>
      <input type="file" id="file" multiple accept=".json,.txt" hidden>
      <div id="upload-status" class="small muted" style="margin-top:10px"></div>
    </div>
    <div id="session-body"></div>`;

  const drop = $("#drop"), input = $("#file"), status = $("#upload-status");
  drop.onclick = () => input.click();
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => {
    e.preventDefault(); drop.classList.remove("over");
    handleFiles(e.dataTransfer.files);
  };
  input.onchange = () => handleFiles(input.files);

  async function handleFiles(list) {
    const files = [...list];
    if (!files.length) return;
    status.textContent = `reading ${files.length} file(s)\u2026`;
    try {
      const payload = [];
      for (const f of files) payload.push({name: f.name, content: await f.text()});
      status.textContent = "parsing\u2026";
      const data = await post("/api/upload", {files: payload});
      state.session = data;
      renderSession();
      if (data.questions && data.questions.length && !data.answered) {
        askIdentity(data.token, data.questions, null, data.linked, data.conflicts);
      }
      status.innerHTML = data.rejected && data.rejected.length
        ? `<span class="err">skipped: ${data.rejected.map(r => esc(r.name)).join(", ")}</span>`
        : "";
    } catch (err) {
      status.innerHTML = `<span class="err">${esc(err.message)}</span>`;
    }
  }

  if (state.session) renderSession();
}

function renderSession() {
  const data = state.session, box = $("#session-body");
  if (!box) return;
  box.innerHTML = `
    <div class="panel">
      <div class="spread">
        <div><h2>this session</h2>
          <div class="small muted">${data.hands} hands \u00b7
            ${data.files.map(f => esc(f.name)).join(", ")}</div></div>
        <div class="row">
          <span class="small muted" id="save-note">${data.saved
            ? "saved to database" : "not in the database"}</span>
          <button class="act primary" id="save" ${data.saved ? "disabled" : ""}>
            ${data.saved ? "saved" : "Add to database"}</button>
        </div>
      </div>
      ${data.auto_merged ? `<p class="small muted" style="margin:8px 0 0">
        Linked ${data.auto_merged} known player match(es) automatically
        (kept existing database names).</p>` : ""}
      <div id="session-roster" style="margin-top:12px"></div>
    </div>
    <div id="session-profiles"></div>`;
  $("#session-roster").appendChild(rosterTable(data.players, {onClick: null}));
  playerTabs(data.profiles, $("#session-profiles"));
  const save = $("#save");
  if (save && !data.saved) save.onclick = () => commit(data.token);
}

/* ---- identity, settled at upload ---- */
/* Asked when the file lands rather than when it is saved, so the session you
   are reading has already pooled the accounts. Merging also asks what to call
   the result: choosing silently files a player under a name they have stopped
   using. */
/* Accounts that might be one person arrive as pairs, but three accounts that
   are all the same human arrive as three separate questions -- and nothing
   stops you answering them inconsistently. Union the pairs into components so
   each *person* is one decision. Applying the links is still pairwise, which
   is what makes this safe: the co-occurrence guard is enforced per link, so a
   group can never smuggle through a merge the tool would refuse. */
function sideKey(side) {
  if (!side) return "";
  return side.player_id != null ? `db${side.player_id}` : `ac${side.account}`;
}
function groupQuestions(questions, linked) {
  const parent = {};
  const find = k => { while (parent[k] !== k) k = parent[k] = parent[parent[k]]; return k; };
  const union = (a, b) => {
    parent[a] = parent[a] ?? a; parent[b] = parent[b] ?? b;
    parent[find(a)] = find(b);
  };
  // Settled pairs join clusters even though nobody is asked about them. Two
  // accounts whose names normalise the same are merged without a question, and
  // leaving that edge out split one knot of accounts into two unrelated
  // dialogs -- "tin"/"tintin" over here, "Tins white gf"/"Tin" over there.
  for (const q of [...questions, ...(linked || [])]) {
    const a = sideKey(q.left), b = sideKey(q.right);
    parent[a] = parent[a] ?? a; parent[b] = parent[b] ?? b;
    union(a, b);
  }
  // Also join accounts whose names match outright. Two accounts both called
  // "tin" are related whether or not they can be merged -- they often cannot,
  // having sat at the table together -- and splitting them across two dialogs
  // asked about half a knot twice and explained neither.
  const byName = new Map();
  for (const q of [...questions, ...(linked || [])]) {
    for (const side of [q.left, q.right]) {
      const key = displayKey(side.name || "");
      if (!key) continue;
      if (byName.has(key)) union(byName.get(key), sideKey(side));
      else byName.set(key, sideKey(side));
    }
  }
  const groups = new Map();
  const at = (root) => {
    if (!groups.has(root)) {
      groups.set(root, {questions: [], members: new Map(), together: []});
    }
    return groups.get(root);
  };
  for (const q of questions) {
    const g = at(find(sideKey(q.left)));
    g.questions.push(q);
    for (const side of [q.left, q.right]) g.members.set(sideKey(side), side);
  }
  for (const q of (linked || [])) {
    const g = at(find(sideKey(q.left)));
    g.together.push([sideKey(q.left), sideKey(q.right)]);
    for (const side of [q.left, q.right]) g.members.set(sideKey(side), side);
  }
  return [...groups.values()];
}

/* Sorting accounts into people.
 *
 * The old control was one radio pair for the whole cluster: merge all six, or
 * keep all six apart. Six accounts are rarely one answer -- a regular with two
 * devices, their brother on a third, and a stranger whose name happens to
 * shorten the same way -- and neither choice was right, so the reader had to
 * pick the less wrong one and repair it afterwards.
 *
 * A column per person, accounts moved between them. The answer the server
 * wants is still pairwise: for every pair it asked about, "same" is simply
 * whether the two ended up in the same column.
 *
 * The starting split is by name, not "everything together": identical names
 * are the reconnect case and belong together, while two different names are
 * two people until somebody says otherwise -- merging being the expensive
 * mistake, and the one that used to need a database reset to undo.
 */
function buildPartition(card, members, together, conflicts) {
  const assigned = new Map();          // account key -> column id
  const columnOf = new Map();          // column id -> chosen display name
  for (const m of members) {
    const key = displayKey(m.name);
    if (!columnOf.has(key)) columnOf.set(key, m.name);
    assigned.set(sideKey(m), key);
  }
  for (const [a, b] of (together || [])) {
    const to = assigned.get(a), from = assigned.get(b);
    if (to === undefined || from === undefined || to === from) continue;
    for (const [key, col] of [...assigned]) if (col === from) assigned.set(key, to);
  }

  // Pairs that can never be one person. Kept as a lookup so a drop can be
  // refused before it happens, rather than accepted and then rejected by the
  // database with a message about hands nobody remembers playing.
  const cannot = new Map();
  for (const [a, b] of (conflicts || [])) {
    if (!assigned.has(a) || !assigned.has(b)) continue;
    if (!cannot.has(a)) cannot.set(a, new Set());
    if (!cannot.has(b)) cannot.set(b, new Set());
    cannot.get(a).add(b);
    cannot.get(b).add(a);
  }
  const blocks = (key, col) => {
    const foes = cannot.get(key);
    if (!foes) return null;
    for (const [other, its] of assigned) {
      if (its === col && foes.has(other)) {
        const who = members.find(m => sideKey(m) === other);
        if (!who) return "another account";
        // The account id when two members share a name, for the same reason.
        const twins = members.filter(x => x.name === who.name).length > 1;
        return twins && who.account ? `${who.name} (${who.account})` : (who.name || who.account);
      }
    }
    return null;
  };
  // Nothing may start in a column it is not allowed to be in.
  for (const m of members) {
    const key = sideKey(m);
    if (blocks(key, assigned.get(key))) assigned.set(key, `solo:${key}`);
  }

  card._partition = assigned;
  card._columnName = columnOf;

  const holder = card.querySelector(".people");
  const note = card.querySelector(".partition-note");
  let picked = null;                   // click-to-place, for touch and keyboard

  const say = (text) => { if (note) note.textContent = text || ""; };

  const place = (key, col) => {
    const why = blocks(key, col);
    if (why) {
      const m = members.find(x => sideKey(x) === key);
      say(`${m ? m.name : "That account"} and ${why} played hands at the same `
          + "table, so they cannot be the same person.");
      return false;
    }
    assigned.set(key, col);
    say("");
    return true;
  };

  const draw = () => {
    const columns = [...new Set(assigned.values())];
    holder.innerHTML = "";
    columns.forEach((col, i) => {
      const wrap = document.createElement("div");
      wrap.className = "person";
      const mine = members.filter(m => assigned.get(sideKey(m)) === col);
      const names = [...new Set(mine.map(m => m.name))];
      const hands = mine.reduce((n, m) => n + (m.hands || 0), 0);
      wrap.innerHTML = `<div class="person-head"><span>Person ${i + 1}</span>
        <span class="small muted">${hands.toLocaleString()} hands</span></div>`;

      for (const m of mine) {
        const key = sideKey(m);
        const chip = document.createElement("div");
        chip.className = "member" + (picked === key ? " picked" : "");
        chip.draggable = true;
        chip.tabIndex = 0;
        chip.innerHTML = `<b>${esc(m.name)}</b>
          <span class="small muted">${(m.hands || 0).toLocaleString()} hands`
          + (m.account ? ` · <span class="mono">${esc(String(m.account))}</span>` : "")
          + `</span>`;
        chip.ondragstart = (e) => {
          e.dataTransfer.setData("text/plain", key);
          e.dataTransfer.effectAllowed = "move";
          chip.classList.add("dragging");
        };
        chip.ondragend = () => chip.classList.remove("dragging");
        // Click to pick up, click a column to put down. Drag is faster; this
        // is the one that works on a phone and from a keyboard.
        chip.onclick = (e) => {
          // Without this the click reaches the column underneath, which reads
          // it as "put it here" and drops the card back where it started.
          if (e) e.stopPropagation();
          picked = picked === key ? null : key;
          say("");
          draw();
        };
        chip.onkeydown = (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); chip.onclick(e); }
        };
        wrap.appendChild(chip);
      }

      if (names.length > 1) {
        const pick = document.createElement("select");
        pick.className = "keepname";
        pick.innerHTML = names.map(n =>
          `<option value="${esc(n)}" ${n === columnOf.get(col) ? "selected" : ""}>`
          + `keep “${esc(n)}”</option>`).join("");
        pick.onchange = () => columnOf.set(col, pick.value);
        pick.onclick = (e) => e.stopPropagation();
        wrap.appendChild(pick);
      }
      if (!mine.some(m => m.name === columnOf.get(col))) {
        columnOf.set(col, mine.length ? mine[0].name : columnOf.get(col));
      }

      wrap.onclick = () => { if (picked && place(picked, col)) { picked = null; } draw(); };
      wrap.ondragover = (e) => { e.preventDefault(); wrap.classList.add("over"); };
      wrap.ondragleave = () => wrap.classList.remove("over");
      wrap.ondrop = (e) => {
        e.preventDefault();
        wrap.classList.remove("over");
        const key = e.dataTransfer.getData("text/plain");
        if (key && assigned.get(key) !== col) place(key, col);
        draw();
      };
      holder.appendChild(wrap);
    });

    if (members.length > columns.length) {
      const spare = document.createElement("div");
      spare.className = "person spare";
      spare.innerHTML = `<div class="person-head"><span>Someone else</span></div>
        <div class="small muted">${picked ? "click to move here" : "drag here"}</div>`;
      const put = (key) => { assigned.set(key, `solo:${key}`); say(""); };
      spare.onclick = () => { if (picked) { put(picked); picked = null; draw(); } };
      spare.ondragover = (e) => { e.preventDefault(); spare.classList.add("over"); };
      spare.ondragleave = () => spare.classList.remove("over");
      spare.ondrop = (e) => {
        e.preventDefault();
        const key = e.dataTransfer.getData("text/plain");
        if (key) { put(key); draw(); }
      };
      holder.appendChild(spare);
    }
  };

  const all = card.querySelector(".allone");
  if (all) all.onclick = () => {
    // Everything that is allowed to be together, together. Accounts that
    // cannot join keep their own column rather than silently not moving.
    const first = sideKey(members[0]);
    const home = assigned.get(first);
    for (const m of members) {
      const key = sideKey(m);
      if (!blocks(key, home)) assigned.set(key, home);
    }
    const stuck = members.filter(m => assigned.get(sideKey(m)) !== home);
    // Named by account when the names collide: "dev and dev cannot be the same
    // person" is a true sentence that tells the reader nothing about which.
    const label = (m) => {
      const twins = members.filter(x => x.name === m.name).length > 1;
      return twins && m.account ? `${m.name} (${m.account})` : m.name;
    };
    say(stuck.length
      ? `${stuck.map(label).join(" and ")} sat at the table with the rest, `
        + "so cannot be the same person."
      : "");
    picked = null;
    draw();
  };
  const apart = card.querySelector(".allapart");
  if (apart) apart.onclick = () => {
    for (const m of members) assigned.set(sideKey(m), `solo:${sideKey(m)}`);
    for (const [a, b] of (together || [])) {
      if (assigned.has(a) && assigned.has(b)) assigned.set(b, assigned.get(a));
    }
    picked = null;
    say("");
    draw();
  };

  draw();
}

function sideMeta(side) {
  // Only "already in the database" earns a label; everything else in this
  // dialog came from the files being added, so saying so on every row is noise.
  const bits = [];
  if (side.player_id != null) bits.push("in the database");
  bits.push(`${side.hands || 0} hands`);
  const id = side.account ? String(side.account) : "";
  if (id) bits.push(`<span class="mono">${esc(id.length <= 10 ? id
    : `${id.slice(0, 6)}\u2026${id.slice(-3)}`)}</span>`);
  return bits.join(" \u00b7 ");
}

const STAT_LABELS = {
  "vpip": "VPIP", "pfr": "PFR", "three_bet": "3-bet", "limp": "limps",
  "wtsd": "went to showdown", "wsd": "won at showdown",
  "aggression:flop": "flop aggression", "aggression:turn": "turn aggression",
  "aggression:river": "river aggression",
  "fold_vs_bet:flop": "fold vs flop bet", "fold_vs_bet:turn": "fold vs turn bet",
  "fold_vs_bet:river": "fold vs river bet",
};
const SUITS = {s: "\u2660", h: "\u2665", d: "\u2666", c: "\u2663"};
/* A board is the one thing in this tool that is not a statistic, and reading
   "7s Jd 3c" as text is slower than seeing it. */
function cardsEl(list, opts) {
  const wrap = document.createElement("span");
  wrap.className = "cards-row" + ((opts && opts.small) ? " small-cards" : "");
  for (const raw of (list || [])) {
    const text = String(raw);
    const rank = text.slice(0, -1).replace("T", "10");
    const suit = text.slice(-1).toLowerCase();
    const card = document.createElement("span");
    card.className = "card " + (suit === "h" || suit === "d" ? "red" : "black");
    card.innerHTML = `<span class="r">${esc(rank)}</span><span class="s">${
      SUITS[suit] || esc(suit)}</span>`;
    wrap.appendChild(card);
  }
  return wrap;
}

/* "4 limps out of 2,945 chances" is a number; "essentially never limps, which
   is a strong player's habit" is a read. The rate decides which way, the
   glossary supplies the words. */
function evidenceVerdict(d) {
  if (!d.count) return "No hands where this could have happened yet.";
  const rate = d.rate != null ? d.rate : d.hits / d.count;
  const pop = d.population;
  const pct = Math.round(rate * 100);
  const against = d.compared_to || "the field";
  let scale;
  if (d.hits === 0) scale = "never";
  else if (rate < 0.02) scale = "almost never";
  // Without something to compare against, say the rate and stop. Claiming it
  // is "about as often as the field" when no field frequency was involved
  // describes a comparison that never happened.
  else if (pop == null) scale = "";
  else if (rate > pop * 1.35) scale = `far more than ${against}`;
  else if (rate < pop * 0.65) scale = `far less than ${against}`;
  else scale = `about as often as ${against}`;
  const head = scale ? `${d.hits} of ${d.count} \u2014 ${pct}%, ${scale}.`
                     : `${d.hits} of ${d.count} \u2014 ${pct}%.`;
  return d.reading ? `${head} ${d.reading}` : head;
}

function statLabel(stat, rows) {
  const hit = (rows || []).find(r => r.stat === stat);
  return hit ? hit.label : (STAT_LABELS[stat] || stat);
}

function normalizeName(name) {
  const text = String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  return text.replace(/\d+$/, "") || text;
}

/* The pairwise reason says "both shorten to jay", which is wrong on a group of
   three. Describe the group itself. */
function groupReason(members, questions) {
  const roots = new Set(members.map(m => normalizeName(m.name)));
  const exact = new Set(members.map(m => displayKey(m.name)));
  if (exact.size === 1) {
    return `all ${members.length} appeared as \u201c${members[0].name}\u201d`;
  }
  if (roots.size === 1) {
    return `all ${members.length} shorten to \u201c${[...roots][0]}\u201d`;
  }
  return [...new Set(questions.map(q => q.detail))].join("; ");
}

/* Longest-processing-time bin packing: tallest panel first, then each next one
   into whichever column is currently shorter. Unlike CSS columns it cannot
   strand a panel on its own. */
function balanceColumns(host) {
  if (!host) return;
  const tiles = [...host.children].filter(el => el.classList.contains("panel"));
  if (tiles.length < 2) return;
  const measured = tiles.map(el => ({el, h: el.getBoundingClientRect().height}));
  const wide = getComputedStyle(host).gridTemplateColumns.split(" ").length > 1;
  const cols = [];
  for (let i = 0; i < (wide ? 2 : 1); i++) {
    const c = document.createElement("div");
    c.className = "col";
    cols.push({node: c, h: 0});
  }
  for (const item of [...measured].sort((a, b) => b.h - a.h)) {
    const target = cols.reduce((a, b) => (a.h <= b.h ? a : b));
    target.node.appendChild(item.el);
    target.h += item.h + 14;
  }
  for (const c of cols) host.appendChild(c.node);
}

function displayKey(name) {
  return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}
function isExactName(q) {
  return displayKey(q.left && q.left.name) === displayKey(q.right && q.right.name);
}

/* When both sides show the same screen name the name tells you nothing, so
   the account id has to be on screen to make the question answerable. */
function sideAccount(side) {
  if (!side || !side.account) return "";
  const id = String(side.account);
  const short = id.length <= 10 ? id : `${id.slice(0, 6)}\u2026${id.slice(-3)}`;
  return ` \u00b7 <span class="mono">${esc(short)}</span>`;
}

async function askIdentity(token, questions, onDone, linked, conflicts) {
  const modal = $("#modal");
  modal.innerHTML = `
    <div class="veil"><div class="sheet">
      <h2 style="margin-top:0">Same player?</h2>
      <p class="small muted" style="margin-top:0">
        Same-id accounts were merged already. These are different ids.</p>
      <label class="bulk" id="bulk-wrap" hidden>
        <input type="checkbox" id="merge-exact" checked>
        <span><b>Merge exact name matches</b>
          <span class="small muted" id="bulk-count"></span></span>
      </label>
      <div id="questions"></div>
      <div class="row" style="justify-content:flex-end;margin-top:18px">
        <button class="act" id="cancel">Keep them separate</button>
        <button class="act primary" id="confirm">Apply</button>
      </div>
    </div></div>`;
  const box = $("#questions");
  const groups = groupQuestions(questions, linked);

  for (const g of groups.filter(x => x.questions.length > 1)) {
    const members = [...g.members.values()]
      .sort((a, b) => (b.hands || 0) - (a.hands || 0));
    const allExact = g.questions.every(isExactName);
    const div = document.createElement("div");
    div.className = "q group" + (allExact ? " exact" : "");
    div.dataset.group = g.questions.map(q => q.id).join("|");
    div.innerHTML = `
      <div class="q-prompt">${members.length} accounts with similar names</div>
      <div class="small muted">${esc(groupReason(members, g.questions))}</div>
      <div class="small muted" style="margin-top:6px">One column per person.
        Drag an account to move it, or tap it and then tap a column.</div>
      <div class="row" style="margin-top:8px;gap:10px">
        <button class="act small allone" type="button">All one person</button>
        <button class="act small allapart" type="button">All separate</button>
      </div>
      <div class="people"></div>
      <div class="small err partition-note"></div>`;
    box.appendChild(div);
    buildPartition(div, members, g.together, conflicts);
  }

  const singles = new Set(
    groups.filter(x => x.questions.length === 1).map(x => x.questions[0].id));
  for (const q of questions.filter(x => singles.has(x.id))) {
    const div = document.createElement("div");
    div.className = isExactName(q) ? "q exact" : "q";
    // Two identical strings are not a choice, so the picker goes away rather
    // than offering "keep Pratul" or "keep Pratul".
    const distinct = [...new Map((q.names || []).map(n => [displayKey(n), n])).values()];
    const names = distinct.length < 2 ? "" : distinct.map(n => `
      <label><input type="radio" name="name-${esc(q.id)}" value="${esc(n)}"
        ${n === q.default_name ? "checked" : ""}>keep \u201c${esc(n)}\u201d</label>`).join("");
    div.innerHTML = `
      <div class="q-prompt">${esc(q.prompt)}</div>
      <div class="sides">
        <div class="side"><b>${esc(q.left.name)}</b>
          <div class="small muted">${sideMeta(q.left)}</div></div>
        <div class="side"><b>${esc(q.right.name)}</b>
          <div class="small muted">${sideMeta(q.right)}</div></div>
      </div>
      <div class="small muted">${esc(q.detail)}</div>
      <div class="choice">
        <label><input type="radio" name="${esc(q.id)}" value="yes"
          ${q.default ? "checked" : ""}>Same player</label>
        <label><input type="radio" name="${esc(q.id)}" value="no"
          ${q.default ? "" : "checked"}>Different people</label>
      </div>
      ${names ? `<div class="choice namechoice">
        <span class="small muted namelabel">keep the name</span>${names}</div>` : ""}`;
    box.appendChild(div);

    // The name only means anything if they are one person, so it greys out
    // rather than disappearing -- a control that vanishes leaves you wondering
    // whether you missed something.
    const setEnabled = same => {
      const group = $(".namechoice", div);
      if (!group) return;
      group.classList.toggle("disabled", !same);
      group.querySelectorAll("input").forEach(input => { input.disabled = !same; });
    };
    setEnabled(q.default);
    div.querySelectorAll(`input[name="${CSS.escape(q.id)}"]`).forEach(radio =>
      radio.onchange = () => setEnabled(radio.value === "yes"));
  }
  // Exact-name pairs are the bulk of a batch and all get the same answer, so
  // they collapse behind one switch instead of being asked one at a time.
  const exact = questions.filter(isExactName);
  const bulk = $("#merge-exact");
  if (exact.length) {
    $("#bulk-wrap").hidden = false;
    $("#bulk-count").textContent = `\u00b7 ${exact.length} of ${questions.length}`;
    const sync = () => {
      box.querySelectorAll(".q.exact").forEach(el => { el.hidden = bulk.checked; });
    };
    bulk.onchange = sync;
    sync();
  }

  const groupAnswer = (q) => {
    const card = box.querySelector(`.q.group[data-group*="${CSS.escape(q.id)}"]`);
    if (!card || !card._partition) return undefined;
    // The pairwise answer falls straight out of where the two accounts were
    // put: same column, same person.
    const left = card._partition.get(sideKey(q.left));
    const right = card._partition.get(sideKey(q.right));
    if (left === undefined || right === undefined) return undefined;
    const same = left === right;
    return {same,
            name: same ? (card._columnName.get(left) || q.default_name)
                       : q.default_name};
  };

  const answerFor = (q, forceSame) => {
    if (forceSame !== undefined) return {same: forceSame, name: q.default_name};
    const picked = modal.querySelector(`input[name="${CSS.escape(q.id)}"]:checked`);
    const name = modal.querySelector(`input[name="name-${CSS.escape(q.id)}"]:checked`);
    return {same: picked ? picked.value === "yes" : q.default,
            name: name ? name.value : q.default_name};
  };

  const send = async (answers) => {
    showBusy("Applying\u2026", undefined);
    try {
      // The full preview is asked for only when it is on screen. During an
      // import nothing is showing it, and building it costs as much as the
      // import itself.
      const showing = !!(state.session && state.session.token === token);
      const refreshed = await post(
        `/api/session/${token}/identity${showing ? "?full=1" : ""}`, {answers});
      if (showing) {
        state.session = refreshed;
        renderSession();
      }
      if (onDone) await onDone();
      else $("#modal").innerHTML = "";
    } catch (err) {
      // Loudly, and in place. This used to clear the dialog and rethrow to a
      // status line the reader may not have been looking at, so a failed apply
      // looked like an apply that silently did nothing -- with the hands still
      // unsaved and the database still empty.
      const sheet = modal.querySelector(".sheet");
      if (sheet) {
        let note = sheet.querySelector(".apply-err");
        if (!note) {
          note = document.createElement("p");
          note.className = "err apply-err";
          sheet.appendChild(note);
        }
        note.textContent = /expired/i.test(err.message || "")
          ? "This upload timed out while the dialog was open. Nothing was saved — add the files again."
          : `Not applied — ${err.message}`;
      } else {
        $("#modal").innerHTML = "";
      }
      throw err;
    }
  };

  $("#cancel").onclick = async () => {
    // Say "no" explicitly for every question. An empty object meant
    // "unanswered", which falls back to each question's default -- and now that
    // an identical screen name defaults to *merge*, "Keep them separate" was
    // merging the very accounts it promised to leave alone.
    const answers = {};
    for (const q of questions) answers[q.id] = {same: false, name: q.default_name};
    await send(answers);
  };
  $("#confirm").onclick = async () => {
    const answers = {};
    for (const q of questions) {
      const grouped = groupAnswer(q);
      if (grouped) { answers[q.id] = grouped; continue; }
      const forced = (bulk && bulk.checked && isExactName(q)) ? true : undefined;
      answers[q.id] = answerFor(q, forced);
    }
    await send(answers);
  };
}

async function commit(token) {
  try {
    const result = await post(`/api/session/${token}/commit`, {});
    state.session.saved = true;
    renderSession();
    showResult(result);
  } catch (err) {
    showResult({error: err.message});
  }
}

function showResult(result) {
  const modal = $("#modal");
  const body = result.error
    ? `<p class="err">${esc(result.error)}</p>`
    : result.reset
    ? `<p>Removed ${result.reset.hands} hands and ${result.reset.players} players.</p>`
    : `<p>${result.hands_new} new hands stored${result.duplicates
        ? `, ${result.duplicates} already known` : ""}.</p>
       <p class="small muted">${result.players_new} new player(s)\u00b7
         ${result.merged} merge(s) applied.</p>
       ${(result.blocked || []).map(b => `<p class="err small">${esc(b)}</p>`).join("")}`;
  const heading = result.error ? "Could not save" : result.reset ? "Database reset" : "Saved";
  modal.innerHTML = `<div class="veil"><div class="sheet">
    <h2 style="margin-top:0">${heading}</h2>
    ${body}
    <div class="row" style="justify-content:flex-end;margin-top:16px">
      <button class="act" id="close">Close</button>
      ${result.error || result.reset ? "" : '<button class="act primary" id="godb">Open players</button>'}
    </div></div></div>`;
  $("#close").onclick = () => { modal.innerHTML = ""; };
  const go = $("#godb");
  if (go) go.onclick = () => { modal.innerHTML = ""; switchTab("players"); };
}

/* ---- tab 2: players ---- */
async function viewPlayers() {
  const view = $("#view");
  if (state.player) return viewPlayer(state.player);
  const data = await get("/api/roster");
  state.heroId = data.hero_id;
  $("#meta").textContent = `${data.hands} hands \u00b7 ${data.players.length} players`;
  if (!data.players.length) {
    if (!onScreen("players")) return;
    view.innerHTML = `<div class="panel"><h2>nothing stored yet</h2>
      <p class="muted">Drop your hand history exports here.</p>
      <div class="drop" id="db-drop">
        <div style="font-size:15px;color:var(--ink)">Drop hand history files here</div>
        <div class="small" style="margin-top:4px">or click to choose \u00b7
          any number at once</div>
      </div>
      <input type="file" id="db-file" multiple accept=".json,.txt" hidden>
      <div id="db-status" class="small muted" style="margin-top:10px"></div></div>`;
    wireImport();
    return;
  }
  /* One table, sorted however you like. A separate leaderboard tab was the
     same rows in a different order. */
  if (!onScreen("players")) return;
  view.innerHTML = `<div class="panel">
      <div class="spread"><h2>database</h2>
        <div class="row">
          <span class="small muted" id="db-meta">click a column to re-rank</span>
          <button class="act small nowrap" id="db-add">Add hands</button>
        </div></div>
      <div class="drop compact" id="db-drop" hidden>
        <div style="font-size:14px;color:var(--ink)">Drop hand history files here</div>
        <div class="small" style="margin-top:4px">any number at once</div>
      </div>
      <input type="file" id="db-file" multiple accept=".json,.txt" hidden>
      <div id="db-status" class="small muted" style="margin-bottom:10px"></div>
      <div id="db-roster"></div></div>
    <p class="footnote">
      <button class="linkbtn danger-link" id="reset">reset database</button>
      <span class="muted">deletes every hand, player and merge decision</span></p>`;
  wireImport();
  $("#db-roster").appendChild(rosterTable(data.players, {
    onClick: p => {
      if (p.player_id === data.hero_id) { switchTab("hero"); return; }
      state.player = p.player_id; viewPlayer(p.player_id);
    },
    heroId: data.hero_id,
  }));
  $("#reset").onclick = () => confirmReset(data);

  // Priors are fitted on import now, not on request. What is worth showing is
  // which population the reads on this page were measured against -- that is
  // the one thing the automatic fit changes about how you should read them.
  const fit = data.fit_priors;
  if (fit && fit.has_fitted) {
    $("#db-meta").innerHTML =
      `measured against your own pool \u00b7 ${fit.players} players`;
  } else if (fit) {
    $("#db-meta").innerHTML =
      `measured against generic online norms \u2014 your pool takes over at 8 players`;
  }


}

async function viewPlayer(id) {
  const view = $("#view");
  const data = await get("/api/player/" + id);
  if (!onScreen("players")) return;
  const names = [...new Set(data.aliases.map(a => a.name))];
  $("#meta").textContent = names.length > 1
    ? `also known as ${names.slice(1).join(", ")}` : "";
  view.innerHTML = "";
  const back = document.createElement("p");
  back.innerHTML = `<button class="linkbtn" id="back">\u2190 all players</button>`;
  view.appendChild(back);
  $("#back").onclick = () => { state.player = null; viewPlayers(); };

  const holder = document.createElement("div");
  view.appendChild(holder);
  state.heroId = data.hero_id;
  playerTabs(data.profiles, holder, {narrate: true, heroId: data.hero_id});

  // Accounts pooled into this player, each splittable on its own.
  //
  // Merging was previously one-way from the interface: the only way out of a
  // wrong "same person" was to reset the database. Splitting is per account,
  // not per player, because that is the grain the mistake happens at -- one
  // stray account swept into somebody else, not a whole identity gone wrong.
  if (data.aliases && data.aliases.length > 1) {
    const panel = document.createElement("div");
    panel.className = "panel";
    panel.innerHTML = `<h2>accounts</h2>
      <div class="small muted" style="margin:-8px 0 12px">
        ${data.aliases.length} accounts are pooled as this player. Split one out
        if it is somebody else — the hands stay put, only who they belong to
        changes, and both profiles are rebuilt from them.</div>
      <div id="alias-rows"></div>`;
    view.appendChild(panel);
    const rows = panel.querySelector("#alias-rows");
    for (const a of data.aliases) {
      const row = document.createElement("div");
      row.className = "leak";
      row.innerHTML = `<div class="leak-head">
        <div><b>${esc(a.name || a.account)}</b>
          <div class="small muted">${esc(a.account)} · ${a.hands} hand(s)</div></div>
        <div class="row"><button class="act small">split out</button></div>
      </div>`;
      const button = row.querySelector("button");
      button.onclick = async () => {
        button.disabled = true;
        button.textContent = "splitting\u2026";
        try {
          const r = await post("/api/unlink",
            {player_id: id, site: a.site, account: a.account});
          // Straight to whoever this just became: the point of splitting is
          // to look at them on their own.
          state.player = r.player_id;
          await viewPlayer(r.player_id);
        } catch (err) {
          button.disabled = false;
          button.textContent = "split out";
          let msg = row.querySelector(".split-err");
          if (!msg) {
            msg = document.createElement("div");
            msg.className = "small err split-err";
            row.appendChild(msg);
          }
          msg.textContent = err.message || "could not split";
        }
      };
      rows.appendChild(row);
    }
  }

  if (data.by_table && data.by_table.length > 1) {
    const panel = document.createElement("div");
    panel.className = "panel";
    panel.innerHTML = `<h2>split by table size</h2>
      <div class="small muted" style="margin:0 0 12px">
        The read above pools these.</div>
      <div class="scroller"><table><thead><tr>
        <th>table</th><th class="num">hands</th><th>read</th>
        <th class="num">skill</th><th>biggest leak</th>
      </tr></thead><tbody></tbody></table></div>`;
    const body = $("tbody", panel);
    for (const t of data.by_table) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${esc(t.regime_label)}</td>
        <td class="num">${t.hands}</td>
        <td>${esc(t.archetype)} <span class="small muted">${fmtPct(t.archetype_confidence)}</span></td>
        <td class="num">${t.skill.score.toFixed(0)}</td>
        <td class="small">${t.leaks.length ? esc(t.leaks[0].headline)
          : '<span class="muted">none</span>'}</td>`;
      body.appendChild(tr);
    }
    view.appendChild(panel);
  }
}

/* Destructive and irreversible, so it asks for the words rather than a click:
   a stray Enter on a normal dialog should not cost a season of hands. */
function confirmReset(data) {
  const modal = $("#modal");
  modal.innerHTML = `<div class="veil"><div class="sheet">
    <h2 style="margin-top:0">Reset the database?</h2>
    <p>This deletes <b>${data.hands} hands</b> and
       <b>${data.players.length} profiles</b>, along with every merge and rename
       decision you have made.</p>
    <p class="small muted">The original export files are untouched \u2014 you can
       import them again, but the identity decisions are gone for good.</p>
    <p class="small">Type <b>delete everything</b> to confirm:</p>
    <input id="confirm-text" style="width:100%;padding:8px 10px;border-radius:8px;
      border:1px solid var(--line);background:transparent;color:var(--ink);font:inherit">
    <div class="row" style="justify-content:flex-end;margin-top:16px">
      <button class="act" id="cancel">Cancel</button>
      <button class="act danger" id="go" disabled>Reset</button>
    </div></div></div>`;
  const text = $("#confirm-text"), go = $("#go");
  text.focus();
  text.oninput = () => { go.disabled = text.value.trim().toLowerCase() !== "delete everything"; };
  $("#cancel").onclick = () => { modal.innerHTML = ""; };
  go.onclick = async () => {
    go.disabled = true; go.textContent = "resetting\u2026";
    try {
      const result = await post("/api/reset", {confirm: "delete everything"});
      modal.innerHTML = "";
      state.player = null; state.session = null;
      viewPlayers();
      paintTabs();               // an emptied database closes tabs again
      showResult({reset: result});
    } catch (err) { showResult({error: err.message}); }
  };
}

/* ---- evidence: the hands behind a number ---- */
async function showEvidence(playerId, stat, headline) {
  const modal = $("#modal");
  const sc = String(stat).startsWith("vs:") ? " hero-scope" : "";
  modal.innerHTML = `<div class="veil"><div class="sheet${sc}">
    <h2 style="margin-top:0">${esc(headline)}</h2>
    <div class="empty">finding the hands\u2026</div></div></div>`;
  let data;
  try {
    data = await get(`/api/evidence?player=${playerId}&stat=${encodeURIComponent(stat)}`);
  } catch (err) {
    modal.innerHTML = `<div class="veil"><div class="sheet${sc}">
      <p class="err">${esc(err.message)}</p>
      <div class="row" style="justify-content:flex-end"><button class="act" id="close">Close</button></div>
      </div></div>`;
    $("#close").onclick = () => { modal.innerHTML = ""; };
    return;
  }
  modal.innerHTML = `<div class="veil"><div class="sheet${sc}">
    <div class="spread"><h2 style="margin:0">${esc(headline)}</h2>
      <button class="act" id="close">Close</button></div>
    <p class="ev-verdict">${esc(evidenceVerdict(data))}</p>
    <p class="small muted">${data.hits
      ? `Showing the most recent \u2014 click one to replay it.`
      : `Never, in ${data.count} chance${data.count === 1 ? "" : "s"}.`}
      ${data.count > data.hits
      ? `<label class="onlyhits"><input type="checkbox" id="show-all">
           show the ones where it did not</label>` : ""}</p>
    <div id="evlist"></div>
    </div></div>`;
  $("#close").onclick = () => { modal.innerHTML = ""; };

  const list = $("#evlist");
  // The hands that moved the number are what you opened this to check; the
  // denominator is one click away, because 19 of 60 and 19 of 20 are different
  // players and hiding that would misrepresent the rate.
  const showAll = $("#show-all");
  if (showAll) {
    showAll.onchange = () => list.querySelectorAll(".ev").forEach(el => {
      el.hidden = !showAll.checked && !el.classList.contains("counted");
    });
  }
  for (const h of data.hands) {
    const row = document.createElement("div");
    row.className = "ev";
    const when = h.started_at ? new Date(h.started_at).toLocaleString() : "";
    row.innerHTML = `
      <span class="ev-board"></span>
      <span class="ev-what"><span class="ev-summary">${esc(h.summary)}</span>
        <span class="small muted ev-when">${esc(when)}</span></span>
      <span class="ev-net ${h.net_bb < 0 ? "lost" : ""}">${
        h.net_bb > 0 ? "+" : ""}${h.net_bb} bb</span>`;
    const boardCell = $(".ev-board", row);
    if ((h.board || []).length) boardCell.appendChild(cardsEl(h.board, {small: true}));
    else boardCell.innerHTML = `<span class="small muted">no flop</span>`;
    if ((h.hole_cards || []).length) {
      const hole = cardsEl(h.hole_cards, {small: true});
      hole.classList.add("hole");
      boardCell.appendChild(hole);
    }
    row.classList.toggle("counted", !!h.hit);
    if (!h.hit) row.hidden = true;
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.onkeydown = e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault(); showReplay(h.hand_id, playerId, headline);
      }
    };
    row.onclick = () => showReplay(h.hand_id, playerId, headline);
    list.appendChild(row);
  }
}

async function showReplay(handId, playerId, headline) {
  // Its own layer: the replay used to append under a list you had scrolled
  // through, so the hand you clicked ended up off screen.
  const layer = $("#modal2");
  layer.innerHTML = `<div class="veil"><div class="sheet">
    <div class="spread"><h2 style="margin:0">Hand replay</h2>
      <button class="act" id="close-replay">Back</button></div>
    ${headline ? `<p class="small muted" style="margin:4px 0 0">${esc(headline)}</p>` : ""}
    <div id="replay"><div class="empty">loading hand\u2026</div></div>
  </div></div>`;
  $("#close-replay").onclick = () => { layer.innerHTML = ""; };
  const box = $("#replay", layer);
  const r = await get(`/api/hand/${handId}?focus=${playerId}`);
  const seatLine = document.createElement("div");
  seatLine.className = "small muted seatline";
  for (const st of r.seats) {
    const chunk = document.createElement("span");
    chunk.className = "seatchunk";
    chunk.textContent = `${st.position} ${st.name} `;
    if (st.hole_cards.length) chunk.appendChild(cardsEl(st.hole_cards, {small: true}));
    seatLine.appendChild(chunk);
  }
  box.innerHTML = `<div class="panel" style="margin-top:14px">
    <div class="small muted">${r.pot_bb} bb pot \u00b7 won by ${esc(r.winners.join(", ") || "\u2014")}</div>
    <div id="seats"></div>
    <div id="streets"></div></div>`;
  $("#seats").appendChild(seatLine);
  const streets = $("#streets");
  for (const st of r.streets) {
    const div = document.createElement("div");
    div.className = "street";
    div.innerHTML = `<h4>${esc(st.name)}</h4>`;
    if ((st.new_cards || []).length) $("h4", div).appendChild(cardsEl(st.new_cards));
    for (const a of st.actions) {
      const line = document.createElement("div");
      line.className = "act" + (a.focus ? " focus" : "") + (a.post ? " post" : "");
      const amount = a.act.startsWith("check") || a.act.startsWith("fold")
        ? "" : `${a.to_bb} bb`;
      line.innerHTML = `<span class="small muted">${esc(a.position)}</span>
        <span class="who">${esc(a.name)} ${esc(a.act)}</span>
        <span class="amt">${amount}</span>
        <span class="amt small">pot ${a.pot_bb}</span>`;
      div.appendChild(line);
    }
    streets.appendChild(div);
  }

}

/* ---- tabs ---- */
function switchTab(tab, playerId) {
  // A blocking operation owns the window until it finishes. Leaving mid-import
  // replaces the view it is still writing into, and the veil it put up is
  // cleared by whatever it was doing rather than by the tab that replaced it --
  // which left a full-screen dim with nothing on it.
  if (document.querySelector(".veil.busy")) return;
  state.tab = tab;
  // Bare "go to the database tab" clears which player was open; a link that
  // names a specific player (from Sessions, say) opens straight to them
  // instead of dropping back to the general roster.
  if (tab === "players") state.player = playerId != null ? playerId : null;
  document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  for (const id of ["#modal2", "#modal"]) {
    const layer = $(id);
    if (layer && layer.innerHTML.trim()) { layer.innerHTML = ""; return; }
  }
});
document.querySelectorAll("nav button").forEach(b =>
    b.classList.toggle("on", b.dataset.tab === tab));
  $("#meta").textContent = "";
  renderWithSpinner();
}

/* In the browser the whole tool runs on this thread, so the work a tab does to
   build itself is work the page cannot paint through. Put a spinner up, give
   the browser a frame to actually draw it, and only then start. Two frames,
   because one only schedules the paint -- the second runs after it. */
const nextFrame = () => new Promise((done) =>
  requestAnimationFrame(() => requestAnimationFrame(() => done())));

async function renderWithSpinner() {
  const view = $("#view");
  if (view) {
    view.innerHTML = `<div class="panel loading">
      <div class="spinner" aria-hidden="true"></div>
      <span>Loading\u2026</span></div>`;
    await nextFrame();
  }
  return render();
}

/* True while `tab` is the one on screen.
 *
 * Views build themselves asynchronously and then write. A view that started
 * before the reader switched away must not write at all -- correcting it
 * afterwards leaves a visible flash of the wrong screen. */
function onScreen(tab) { return state.tab === tab; }

/* Which render is the current one.
 *
 * Every tab builds itself asynchronously, and a slow one -- Database on a big
 * roster, Hero on a cold cache -- finishes long after the reader has moved on,
 * writing its screen over whatever they switched to. The simulator had this
 * and was fixed in place; the same thing was true of all of them, so the guard
 * belongs here rather than in each view.
 *
 * A render that is no longer the current one throws its output away, and the
 * tab that *is* current is drawn instead.
 */
let renderSeq = 0;

async function render() {
  const mine = ++renderSeq;
  const forTab = state.tab;
  try {
    if (!state.glossary) state.glossary = await get("/api/glossary");
    if (mine !== renderSeq) return;          // superseded while we waited
    if (state.tab === "session") viewSession();
    else if (state.tab === "sessions") await viewSessions();
    else if (state.tab === "hero") await viewHero();
    else if (state.tab === "play") await viewPlay();
    else await viewPlayers();
  } catch (err) {
    if (mine !== renderSeq) return;
    $("#view").innerHTML = `<div class="panel err">${esc(err.message)}</div>`;
    return;
  }
  // Finished into a tab nobody is looking at any more: draw the real one.
  if (mine === renderSeq && state.tab !== forTab) render();
}
document.querySelectorAll("nav button").forEach(b =>
  b.onclick = () => {
    if (b.disabled) return;
    if (document.querySelector(".veil.busy")) {
      // Nudge the thing that is holding the window, so the click reads as
      // "not now" rather than as nothing happening at all.
      const sheet = document.querySelector(".busy-sheet");
      if (sheet) {
        sheet.animate(
          [{ transform: "translateX(0)" }, { transform: "translateX(-4px)" },
           { transform: "translateX(4px)" }, { transform: "translateX(0)" }],
          { duration: 180 });
      }
      return;
    }
    switchTab(b.dataset.tab);
  });

/* ---- tabs with nothing to show yet ----
   Hero needs a hand history with your own cards in it; Simulate needs somebody
   measured enough to play against. Both are ordinary on a new database, and
   both used to be found out by clicking, reading an explanation and clicking
   back. The server says which are ready and why not, and the reason sits on
   the tab itself. */
async function paintTabs() {
  let tabs;
  try {
    tabs = (await get("/api/meta")).tabs || {};
  } catch {
    return;                     // never let this stop the interface loading
  }
  for (const button of document.querySelectorAll("nav button")) {
    const state_ = tabs[button.dataset.tab];
    const blocked = state_ && state_.ok === false;
    button.disabled = !!blocked;
    button.classList.toggle("off", !!blocked);
    if (blocked) button.title = state_.why || "";
    else button.removeAttribute("title");
  }
  // Landing on a tab that has since become unavailable -- a reset, say --
  // would otherwise leave the view stranded on a dead screen.
  const here = document.querySelector(`nav button[data-tab="${state.tab}"]`);
  if (here && here.disabled) switchTab("players");
}
paintTabs();

/* Sun when it is dark (click for light), moon when it is light. The icon
   shows what you get, not what you have. */
const SUN = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none"
  stroke="currentColor" stroke-width="2" stroke-linecap="round">
  <circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2
  M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>`;
const MOON = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none"
  stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/></svg>`;
const themeBtn = $("#theme");
function isDark() {
  const set = document.documentElement.getAttribute("data-theme");
  if (set) return set === "dark";
  return matchMedia("(prefers-color-scheme: dark)").matches;
}
function paintTheme() { themeBtn.innerHTML = isDark() ? SUN : MOON; }
themeBtn.onclick = () => {
  document.documentElement.setAttribute("data-theme", isDark() ? "light" : "dark");
  paintTheme();
};
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintTheme);
paintTheme();

renderWithSpinner();
