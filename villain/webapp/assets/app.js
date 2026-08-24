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
  const wrap = document.createElement("span");
  wrap.className = "guest-lock";
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
function openSheet(title, extraClass) {
  const modal = $("#modal");
  const sc = extraClass ? " " + extraClass : "";
  modal.innerHTML = `<div class="veil"><div class="sheet${sc}">
    <div class="spread"><h2 style="margin:0">${title}</h2>
      <button class="act" id="close">Close</button></div></div></div>`;
  $("#close").onclick = () => { modal.innerHTML = ""; };
  return modal;
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
  const div = document.createElement("div");
  div.className = "loading";
  div.innerHTML = `<span class="spinner" aria-hidden="true"></span>
    <span>${esc(label)}</span>`;
  return div;
}

/* ---- shared renderers ---- */
function rosterTable(players, opts) {
  const wrap = document.createElement("div");
  wrap.className = "scroller";
  // GTO dropped as a default column: a bare 0-100 integer beside the skill
  // bar reads as a second, differently-encoded skill score. It is still one
  // click away, on the profile it links to.
  wrap.innerHTML = `<table><thead><tr>
      <th data-k="name">player</th>
      <th data-k="hands" class="num">hands</th><th data-k="archetype">read</th>
      <th data-k="skill" class="num">skill</th>
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
        <td><span class="tag arch ${p.confidence >= 0.5 ? "on" : ""}">${esc(p.archetype)}</span></td>
        <td class="num"></td>
        <td class="num worth${p.exploitability > 10 ? " big" : ""}">${
          p.exploitability ? p.exploitability.toFixed(1) : "\u2014"}</td>
        <td class="small leakcell">${p.top_leak ? esc(p.top_leak)
          : '<span class="muted">nothing yet</span>'}</td>`;
      const holder = document.createElement("div");
      holder.style.cssText = "display:flex;gap:8px;align-items:center;justify-content:flex-end";
      const label = document.createElement("span");
      if (p.skill == null || p.skill_tier === "unknown") {
        label.className = "muted";
        label.textContent = "\u2014";
        bindTip(holder, `<b>unknown</b><br>${termTip("unknown")}`);
        holder.append(label);
      } else {
        label.textContent = p.skill.toFixed(0);
        // Mapped to the observed domain, not 0-100: anchored at zero the whole
        // roster looked equally full -- p10 to p90 differed by 14px of bar.
        holder.append(bar(Math.max(0, p.skill - 40), 55, "var(--mark-3)", 66), label);
        bindTip(holder, `<b>${esc(p.skill_tier)}</b> ${p.skill.toFixed(0)}/100<br>
          ${termTip("confidence")} ${fmtPct(p.skill_confidence)}`);
      }
      // Sample quality moved onto the hands count as a tooltip: it qualifies
      // that number and nothing else, so it does not need its own line on
      // every row.
      const hcell = tr.children[1];
      if (hcell) {
        hcell.classList.add("q-" + String(p.sample_quality).split(" ")[0]);
        bindTip(hcell, `<b>${esc(p.sample_quality)}</b><br>${termTip(p.sample_quality)}`);
      }
      // Confidence moves into the pill's own tooltip -- dashed vs solid
      // already carries the fact that it's uncertain; the number is one
      // hover away rather than a second line on every row of the roster.
      const archTag = $(".tag.arch", tr);
      if (archTag) bindTip(archTag, `${termTip("confidence")}<br><br>
        ${fmtPct(p.confidence)} sure`);
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
   every row and the tooltip says which is which.

   Rendered as a rating badge plus a link into every row, rather than a full
   panel of eight rows sitting beside Key numbers -- the same idea (a
   frequency next to a baseline) with less certainty drawn, taking a whole
   peer tile on the one screen meant to be read mid-hand. */
function gtoRows(rows) {
  const host = document.createElement("div");
  host.className = "gto-rows";
  for (const r of rows) {
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
  return host;
}

function gtoExplainer() {
  return `<span class="hl">GTO rating</span><br>
    How close these frequencies sit to a game-theory-optimal baseline — the
    part of a game a perfect opponent still could not exploit.<br><br>
    <b>Preflop</b> is a solver reference at 100bb, exact as category
    frequencies.<br><b>Postflop</b> is an equilibrium benchmark, board-averaged
    — not a live solver. Preflop rows weigh double in the score.`;
}

function openGtoModal(gto) {
  const modal = openSheet("vs GTO — every stat");
  $(".sheet", modal).appendChild(gtoRows(gto.rows));
}

/* A compact rating badge, `you N/100 GTO`, plus a link to the full row list --
   for a panel header, not a panel of its own. */
function renderGtoBadge(gto) {
  if (!gto || gto.rating == null || !(gto.rows || []).length) return null;
  const wrap = document.createElement("span");
  wrap.className = "small muted";
  wrap.style.cssText = "display:inline-flex;align-items:center;gap:6px";
  const badge = document.createElement("span");
  badge.className = "gto-badge";
  badge.innerHTML = `<b>${Math.round(gto.rating)}</b><span class="of">/100 GTO</span>`;
  badge.appendChild(info(gtoExplainer()));
  const link = document.createElement("button");
  link.className = "linkbtn";
  link.textContent = "See vs GTO";
  link.onclick = () => openGtoModal(gto);
  wrap.append(badge, link);
  return wrap;
}

/* Profile: one function per tile. `hero` changes the wording, not the shape. */

function profileHead(p, isHero, hero) {
  const head = document.createElement("div");
  head.className = "panel wide";
  head.innerHTML = `
    <div class="profile-head">
      <div class="profile-id">
        <div class="hero">${esc(p.name || p.archetype)}${
          isHero ? '<span class="hero-badge">you</span>' : ""}</div>
        <div class="read-meta" id="read-meta"></div>
        <div class="read-copy">
          <p class="summary">${esc(p.summary)}</p>
        </div>
      </div>
      <div class="profile-stats">
        <div class="stat-pair ring">
          <div class="skill-ring" id="skill-ring">
            <span class="score">${p.skill.measured === false ? "\u2014" : p.skill.score.toFixed(0)}</span>
          </div>
          <span class="k">${p.skill.measured === false ? "unknown" : "skill"}</span>
        </div>
        <div class="stat-pair" id="worth-stat">
          <span class="v">${p.skill.exploitability_bb100.toFixed(1)}</span>
          <span class="k">bb/100 available</span>
        </div>
      </div>
    </div>`;
  if (p.skill.measured !== false) {
    $("#skill-ring", head).prepend(skillGauge(p.skill.score));
  }
  $("#worth-stat .k", head).appendChild(info(termTip("available")));
  if (!hero && p.plan) {
    const planLink = document.createElement("button");
    planLink.className = "linkbtn how-link";
    planLink.textContent = "How to play them";
    planLink.onclick = () => {
      openSheet(`How to play ${esc(p.name || p.archetype)}`);
      $(".sheet").insertAdjacentHTML("beforeend",
        `<div class="how-body">${esc(p.plan)}</div>`);
    };
    $(".read-copy", head).appendChild(planLink);
  }

  const badge = $("#skill-ring", head);
  bindTip(badge, p.skill.measured === false
    ? `<b>unknown</b><br>${termTip("unknown")}`
    : `<b>${esc(p.skill.tier)}</b> ${p.skill.score.toFixed(0)}/100<br>
    <span class="muted">confidence ${fmtPct(p.skill.confidence)}</span>
    ${p.skill.observed_bb100 == null ? ""
      : `<br><span class="muted">${p.skill.observed_bb100.toFixed(1)} bb/100 observed</span>`}`);

  // The meta row is a flex row on a spacing step, so nothing in it carries a
  // hand-set margin any more.
  const meta = $("#read-meta", head);
  // The archetype now lives here as a pill, not as a subtitle under the name
  // plus a second sentence repeating it -- dashed means the read hasn't
  // cleared 50%, solid means it has, same convention the roster already uses.
  const archPill = document.createElement("span");
  archPill.className = "tag arch" + (p.archetype_confidence >= 0.5 ? " on" : "");
  archPill.textContent = p.archetype;
  archPill.appendChild(info(`${termTip("confidence")}<br><br>
    ${fmtPct(p.archetype_confidence)} sure<br><br>
    <span class="hl">also plausibly</span><br>${
      p.archetype_mix.slice(1, 4).map(([n, v]) => `${esc(n)} ${fmtPct(v)}`).join("<br>")}`));
  const line = document.createElement("span");
  line.append(document.createTextNode(`${p.hands} hands`));
  line.appendChild(info(`<b>${esc(p.sample_quality)}</b><br>${termTip(p.sample_quality)}`));
  meta.append(archPill, line);
  // Who they are on the hands they played against you. Only here, never on
  // the roster: the roster is how everybody plays the field, and two
  // references in one list is how "tag" stopped meaning anything.
  if (p.versus) {
    const vs = document.createElement("span");
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
    meta.appendChild(vs);
  }
  if (p.contributions && Object.keys(p.contributions).length > 1) {
    const where = document.createElement("span");
    where.textContent = p.regime_label;
    where.appendChild(info(`<span class="hl">one read, several table sizes</span><br>
      Each table's hands are measured against that table's own norms, then
      pooled. Shown on ${esc(p.regime_label)} terms, where they play most.<br>
      ${esc(p.table_mix || "")}`));
    meta.appendChild(where);
  }

  return head;
}


function skillPanel(p) {
  const skillBox = document.createElement("div");
  skillBox.className = "panel wide p-skill";
  skillBox.innerHTML =
    `<div class="spread"><h2>Skill breakdown <span class="muted" style="font-weight:400">\u00b7 ` +
    `${esc(p.skill.tier)}</span></h2></div>` +
    `<div class="skill-side two-up" id="skill-side"></div>`;
  const gtoBadge = renderGtoBadge(p.gto);
  if (gtoBadge) $(".spread", skillBox).appendChild(gtoBadge);
  const skillSide = $("#skill-side", skillBox);
  skillSide.innerHTML = "";
  const comps = p.skill_components || p.skill.components;
  for (const c of [...comps].sort((a, b) => a.score - b.score)) {
    const row = document.createElement("div");
    row.className = "comp" + (c.weak ? " weak" : "");
    row.innerHTML = `<span class="comp-name">${esc(c.name)}</span>
      <span class="comp-bar"></span>
      <span class="comp-score">${c.score.toFixed(0)}</span>`;
    // Stretched to the cell rather than scaled into it: with the aspect ratio
    // preserved a 999-wide bar in a 250px column drew a 3px hairline.
    const compBar = bar(c.score, 100, c.weak ? "var(--warn)" : "var(--mark-1)", 999);
    compBar.setAttribute("preserveAspectRatio", "none");
    $(".comp-bar", row).appendChild(compBar);
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

  return skillBox;
}


function whatToDoTile(p, hero, leaks) {
  /* The tile the screen exists for, and the only one on the primary surface:
     priced leaks, then the unconfirmed watchlist, then the synthesis row. */
  const doBox = document.createElement("div");
  doBox.className = "panel wide primary p-do";
  doBox.innerHTML = `<h2>${hero ? "Your biggest leaks" : "What to do"}</h2>
    <div class="leaks"></div>`;

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
    div.className = `leak priced t-${esc(l.tier)}`;
    // The glance view gets the first clause -- ranked by bb/100, this is the
    // column that should read as a scorecard, not a stack of essays. The full
    // sentence is never dropped, only moved into "Why, and what not to do".
    const doFirst = l.do ? l.do.split(/\.\s+/)[0].replace(/\.+$/, "") + "." : "";
    const doTruncated = !!(l.do && doFirst.length < l.do.length);
    // One decimal on the glance surface, two only in the numbers line beside
    // the sample it rests on. A leak under 0.1 prints as "<0.1" rather than
    // "0.00": a list sorted by price should never show a price of zero, and
    // rounding a real figure to nothing is the one thing worse than the extra
    // digit.
    const thin = l.severity_bb100 < 0.1;
    div.innerHTML = `
      <div class="leak-price${thin ? " thin" : ""}">
        <span class="v">${thin ? "&lt;0.1" : l.severity_bb100.toFixed(1)}</span>
        <span class="u">bb/100</span>
      </div>
      <div class="leak-body">
        <div class="headline"><b>${esc(l.headline)}</b>
          <span class="tag tier ${esc(l.tier)}">${esc(l.tier)}</span></div>
        ${hero ? "" : `<div class="leak-advice">${esc(doFirst)}</div>`}
        <div class="small muted numbers"></div>
      </div>`;
    bindTip($(".leak-price", div), `<span class="hl">${
      l.severity_bb100.toFixed(2)} bb/100</span><br>${termTip("available")}`);
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
    // sheet the hands open in. "Do" only appears here when the glance view cut
    // it short -- otherwise the one sentence already on screen is the whole of it.
    const whydont = [doTruncated ? ["Do", l.do] : null, ["Why", l.why], ["Do not", l.dont]]
      .filter(Boolean).filter(([, t]) => t);
    // On the evidence line rather than a line of its own. Five leaks with a
    // disclosure each was five extra rows of height on the one panel that has
    // to fit above the fold, and the link belongs with the rest of the
    // "where this came from" apparatus anyway.
    if (!hero && whydont.length) {
      numbers.appendChild(document.createTextNode(" · "));
      const link = document.createElement("button");
      link.className = "linkbtn how-link";
      link.textContent = "Why, and what not to do";
      link.onclick = () => {
        const modal = openSheet(esc(l.headline));
        $(".sheet", modal).insertAdjacentHTML("beforeend", `<div class="how-body"></div>`);
        const how = $(".how-body", modal);
        for (const [label, text] of whydont) {
          const block = document.createElement("div");
          block.className = "howblock";
          block.innerHTML = `<div class="howlabel">${esc(label)}</div>
            <div>${esc(text)}</div>`;
          how.appendChild(block);
        }
      };
      numbers.appendChild(link);
    }
    leakBox.appendChild(div);
  }

  // Same row shape as a priced leak, because it is the same kind of claim one
  // step short of the evidence bar -- but the left cell holds the confidence
  // rather than a price, and the rail is dashed, so it never reads as another
  // number in the ranking.
  for (const w of (p.watchlist || [])) {
    const div = document.createElement("div");
    div.className = "leak priced watch";
    div.innerHTML = `
      <div class="leak-price thin">
        <span class="v">${fmtPct(w.confidence)}</span>
        <span class="u">sure</span>
      </div>
      <div class="leak-body">
        <div class="headline"><b>${esc(w.headline)}</b>
          <span class="tag tier">watch</span></div>
      </div>`;
    $(".tier", div).after(info(`${termTip("watch")}<br><br>${esc(w.in_words)}`));
    leakBox.appendChild(div);
  }


  // Not a priced row -- a synthesis of the rows above it -- so it stops
  // borrowing their shape: no price cell, a recessed ground, and it sits at
  // the foot of the list where a summary belongs.
  for (const c of (p.combinations || [])) {
    const block = document.createElement("div");
    block.className = "leak compound";
    block.innerHTML = `<div class="headline"><b>${esc(c.headline)}</b>
      <span class="tag">these compound</span></div>
      <div class="leak-advice">${esc(c.body)}</div>`;
    leakBox.appendChild(block);
  }

  return doBox;
}


function adjustmentsTile(p) {
  /* How they play *you*, or null. Most players against most opponents have no
     adjustment, and an empty panel takes a column off a screen meant to be
     read mid-hand. */
  const adjustments = p.adjustments || [];
  if (adjustments.length) {
    const adjBox = document.createElement("div");
    // Full width, below the two columns rather than inside the height-packed
    // masonry: a short reads-only panel could never balance against the tall
    // skill breakdown without leaving dead space on one side or the other.
    adjBox.className = "panel wide adjust hero-scope";
    adjBox.innerHTML = `<h2>Against you<span class="hero-badge">hero</span></h2>
      <div class="panel-lead">
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
    return adjBox;
  }
  return null;
}


function timingTellsTile(p) {
  /* Null unless some cell actually has a tell -- a grid of "not enough data"
     is noise wearing a panel's clothes. */
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
    h2.textContent = "Timing tells";
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
    return timingBox;
  }
  return null;
}


function keyNumbersTile(p, hero) {
  /* The HUD line: the six every tracker prints, in the order they print them,
     because that order is what a player's eye already knows. */
  // The HUD line: the six every tracker prints, in the order they print them,
  // because that order is what a player's eye already knows. Cutting this to
  // three rows of a table did not make the screen calmer -- it left a column
  // 300px empty beside a full one. Six figures in one strip is more numbers in
  // less height than three rows were, and it reads as an instrument.
  const HUD = [["vpip", "VPIP"], ["pfr", "PFR"], ["three_bet", "3-bet"],
               ["fold_to_three_bet", "Fold to 3B"], ["cbet:flop", "C-bet flop"],
               ["fold_vs_bet:flop", "Fold v bet F"]];
  const TIMING = /^(tank_fold|snap_call)(:|$)/;
  const byStat = Object.fromEntries(p.rows.map((r) => [r.stat, r]));
  const ordered = [...p.rows]
    .filter((r) => !TIMING.test(r.stat))
    .sort((a, b) => {
      const rank = (r) => {
        const i = HUD.findIndex(([s]) => s === r.stat);
        return i === -1 ? HUD.length : i;
      };
      return rank(a) - rank(b);
    });

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

  const hudBox = document.createElement("div");
  hudBox.className = "panel wide p-hud";
  hudBox.innerHTML = `<div class="spread"><h2>Key numbers</h2>
    <span class="small muted hud-actions"></span></div><div class="hud"></div>`;
  const hudHost = $(".hud", hudBox);
  for (const [stat, label] of HUD) {
    const row = byStat[stat];
    const cell = document.createElement("div");
    cell.className = "hud-cell" + (row ? "" : " thin");
    // The percent sign is a unit, not a digit: at full weight beside a 21px
    // figure it competes with the number it is qualifying.
    cell.innerHTML = `<div class="k">${esc(label)}</div>
      <div class="v">${row ? `${(100 * row.value).toFixed(0)}<span class="pc">%</span>`
                           : "—"}</div>`;
    if (row) {
      cell.appendChild(sparkRow(row));
      // One hover gives what the strip cannot: what the figure counts, the
      // interval around it, the sample under it, and the threshold. Composed
      // here rather than concatenating the two existing tip builders, which
      // between them would print the label and the field reading twice.
      bindTip(cell, `${statTip(row.stat, row.label, row)}
        <div class="dir"><span class="muted">95% range ${fmtPct(row.lo)}–${fmtPct(row.hi)}
        · ${row.opps} ${esc(row.denominator)} · field ${fmtPct(row.population)}</span></div>${
        row.breakeven == null ? ""
          : `<div class="dir" style="color:var(--warn)">${
             esc(row.breakeven_label)} ${fmtPct(row.breakeven)}</div>`}`);
    } else {
      bindTip(cell, `<b>${esc(label)}</b><br>No sample for this yet.`);
    }
    hudHost.appendChild(cell);
  }
  if (ordered.length) {
    // The rest open in the sheet popup rather than an inline <details>, so the
    // strip never changes height after it is drawn.
    const link = document.createElement("button");
    link.className = "linkbtn";
    link.textContent = `See all ${ordered.length} numbers`;
    link.onclick = () => {
      _heroVoice = hero;
      const modal = openSheet("Key numbers");
      $(".sheet", modal).insertAdjacentHTML("beforeend", `<div class="modal-numbers"></div>`);
      const full = makeTable();
      fill(full, ordered);
      $(".modal-numbers", modal).appendChild(full);
    };
    $(".hud-actions", hudBox).appendChild(link);
  }
  return hudBox;
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

  const head = profileHead(p, isHero, hero);
  card.appendChild(head);

  const doBox = whatToDoTile(p, hero, leaks);
  card.appendChild(doBox);

  for (const tile of [adjustmentsTile(p), timingTellsTile(p)]) {
    if (tile) wideTiles.push(tile);
  }

  card.appendChild(skillPanel(p));
  for (const tile of wideTiles) card.appendChild(tile);

  // Between the header band and What to do: the six are reference you read
  // before the plan, not after it.
  card.insertBefore(keyNumbersTile(p, hero), doBox);
  return card;
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

/* A rebuild the reader did not ask for, reported while it runs.

   A definitions bump makes the next request rebuild every book from every
   stored hand -- about a minute on a real database -- inside whichever
   request happened to arrive first. There is no way to know in advance which
   one that is, so the bar raises itself when the first tick arrives and tears
   itself down when the ticks stop.

   The veil is deliberate, not decoration: it is what stops a second tab from
   being opened mid-migration and starting a second rebuild behind the first.
   Everything is a modal veil until the counters are whole. */
const REBUILD_PHASES = {
  "reading hands": "Reading your stored hands",
  "reading players": "Rebuilding every player's numbers",
};
let rebuildBar = null, rebuildIdle = null;

window.__villainRebuild = (msg) => {
  if (!rebuildBar) {
    rebuildBar = showBusy("Updating your database…");
  }
  const label = REBUILD_PHASES[msg.phase] || "Working";
  const done = Number(msg.done), total = Number(msg.total);
  if (total > 0) {
    rebuildBar(`${label}… ${done.toLocaleString()} of ${total.toLocaleString()}`,
               done / total);
  } else {
    rebuildBar(`${label}…`, undefined);
  }
  // No "finished" tick exists -- the rebuild simply stops calling. A short
  // idle after the last one is what tells us it is over, and it has to be
  // longer than the gap between ticks (~100ms at the slowest phase).
  if (rebuildIdle) clearTimeout(rebuildIdle);
  rebuildIdle = setTimeout(() => {
    rebuildBar = rebuildIdle = null;
    const modal = $("#modal");
    if (modal) modal.innerHTML = "";
  }, 1500);
};

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
    state.roster = null;
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
  body.innerHTML = "";
  body.appendChild(loadingBlock("Reading the sitting\u2026"));
  const data = await get(`/api/session-detail?id=${id}`);
  body.innerHTML = "";
  for (const p of data.players) {
    const div = document.createElement("div");
    div.className = "sess-row" + (p.is_hero ? " hero-scope hero-sitting" : "");
    const netTxt = p.net_bb > 0 ? `+${p.net_bb}` : `${p.net_bb}`;
    // Net bb and skill are the only measurements on this page, and they were
    // 12.5px muted text at the right margin. Same stat-pair the profile header
    // uses -- one figure treatment across the app, not a per-screen decision.
    div.innerHTML = `<div class="sess-head">
        <div class="sess-id">
          <div class="sess-who"><b class="linkish">${esc(p.name)}</b>${
            p.is_hero ? '<span class="tag hero-tag">you</span>' : ""}
            <span class="tag arch ${p.confidence >= 0.5 ? "on" : ""}">${esc(p.archetype)}</span>
            <span class="sitting-note">this sitting</span>
          </div>
          <div class="small muted">${p.hands} hands \u00b7 ${esc(p.regime_label || "")}</div>
        </div>
        <div class="sess-stats">
          <div class="stat-pair">
            <span class="v ${p.net_bb >= 0 ? "up" : "down"}">${netTxt}</span>
            <span class="k">bb</span>
          </div>
          <div class="stat-pair sess-skill">
            <span class="v">${Math.round(p.skill)}</span>
            <span class="k">skill</span>
          </div>
        </div>
      </div>
      <div class="sess-deltas"></div>`;
    $("b", div).onclick = () => switchTab("players", p.player_id);
    const skillBar = bar(p.skill, 100, "var(--mark-2)", 999);
    skillBar.setAttribute("preserveAspectRatio", "none");
    $(".sess-skill", div).appendChild(skillBar);
    // This pill is a sitting-only read, not the pooled one on their Database
    // page -- Database and Sessions disagreeing about the same person is
    // correct (a sitting can look nothing like the season), but only if it
    // says so rather than looking like the same claim twice.
    $(".sitting-note", div).appendChild(info(`<span class="hl">this sitting</span><br>
      Measured on just tonight's hands here, not the pooled read on their
      Database page. The two can disagree, and when they do the difference is
      the point.`));
    const box = $(".sess-deltas", div);
    if (!p.deltas.length) {
      // One line on the row, not a paragraph per player. Six of these stacked
      // was the whole page on a database with no outside sample yet.
      box.innerHTML = `<div class="small muted sess-none">No outside sample at
        this table size to compare tonight against.</div>`;
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
      // Tonight against usually, as two bars on one scale -- the same picture
      // the against-you panel draws, for the same reason: the finding is the
      // gap, and a four-column text grid makes the reader do the subtraction
      // that a pair of bars does for them.
      const drawRows = label => {
        rows.innerHTML = "";
        const set = byRegime.get(label);
        const max = Math.max(...set.flatMap(d => [d.session, d.usual]), 0.01);
        for (const d of set) {
          const row = document.createElement("div");
          row.className = "sess-delta";
          const up = d.delta > 0;
          row.innerHTML = `<div class="sess-delta-head">
              <span class="sess-stat">${esc(statLabel(d.stat, null))}</span>
              <span class="small ${up ? "up" : "down"}">${up ? "\u25b2" : "\u25bc"}${
                Math.abs(Math.round(d.delta * 100))}pp</span>
            </div>`;
          for (const [name, v, color] of [["tonight", d.session, "var(--mark-3)"],
                                          ["usually", d.usual, "var(--mark-1)"]]) {
            const line = document.createElement("div");
            line.className = "metric";
            const label2 = document.createElement("span");
            label2.className = "small muted"; label2.textContent = name;
            const val = document.createElement("span");
            val.className = "small tellval"; val.textContent = fmtPct(v);
            const drawn = bar(v, max, color, 150);
            drawn.setAttribute("preserveAspectRatio", "none");
            line.append(label2, drawn, val);
            row.appendChild(line);
          }
          bindTip($(".sess-stat", row), statTip(d.stat, statLabel(d.stat, null)));
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
  const summary = document.createElement("div");
  summary.className = "graded-head";
  summary.innerHTML = `
    <div class="stat-pair"><span class="v">${section.graded}</span>
      <span class="k">graded</span></div>
    <div class="stat-pair"><span class="v${section.flagged ? " warnv" : ""}">${
      section.flagged}</span><span class="k">flagged</span></div>
    <div class="graded-rate">
      <div class="small muted graded-verdict">${esc(opts.verdict)}</div>
      <div class="graded-bar"></div>
      <div class="small muted graded-pct">${fmtPct(section.rate)} of ${
        esc(opts.noun)} graded</div>
    </div>`;
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
    const row = document.createElement("div");
    row.className = "fold-row";
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
    const block = document.createElement("div");
    block.className = "tellblock" + (v.is_tell ? " on" : "");
    block.innerHTML = `<div class="tellhead"><span class="street-label">${esc(street)}</span>${
      v.is_tell ? '<span class="tag hero-tag">tell</span>' : ""}</div>`;
    for (const [key, color] of [["strong", "var(--mark-3)"], ["weak", "var(--mark-1)"]]) {
      const b = v[key];
      if (!b || b.avg == null) continue;
      const row = document.createElement("div");
      row.className = "metric";
      const name = document.createElement("span");
      name.className = "small muted";
      name.textContent = key === "strong" ? "top half" : "bottom half";
      const val = document.createElement("span");
      val.className = "small tellval";
      val.textContent = fmt(b.avg);
      const drawn = bar(b.avg, max, color, 150);
      drawn.setAttribute("preserveAspectRatio", "none");
      row.append(name, drawn, val);
      bindTip(row, `<b>${esc(key === "strong" ? "top half" : "bottom half")} of your range</b><br>
        ${esc(fmt(b.avg))}${opts.unit ? " " + esc(opts.unit) : ""} over ${b.hands} hands`);
      block.appendChild(row);
    }
    if (v.in_words) {
      const note = document.createElement("div");
      note.className = "small muted tellnote";
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
  const b = document.createElement("button");
  b.className = "act" + (cls ? " " + cls : "");
  b.textContent = label; b.onclick = on; return b;
}

async function viewPlay() {
  const view = $("#view");
  if (!onScreen("play")) return;
  if (state.analysis) { renderAnalysis(view, state.analysis); return; }
  if (state.game) {
    if (!state.paused) thawSimClock();
    renderTable(view, state.game);
    return;
  }
  view.innerHTML = `<div class="panel"><h2>Simulate</h2>
    <div class="small muted" style="margin:-6px 0 16px">Sit at a table and play real hands
      against players from your database. Each villain acts from their own measured profile —
      loose ones call wide, nits fold, aggressive ones barrel — so it plays like practice
      against the people you actually face. Pick up to five and sit down.</div>
    <div id="pick-list" class="pick-list"></div>
    <div class="sit-controls">
      <label class="small muted">stack <input id="sit-stack" type="number" value="200" min="20"></label>
      <label class="small muted">blinds <input id="sit-sb" type="number" value="1" min="1">
        / <input id="sit-bb" type="number" value="2" min="2"></label>
      <button class="act primary" id="sit-go" disabled>Sit down</button>
    </div></div>`;
  $("#pick-list").appendChild(loadingBlock("Reading your database\u2026"));
  const roster = await get("/api/roster");
  if (!onScreen("play") || state.game || state.analysis) return;
  const players = (roster.players || [])
    .filter(p => p.player_id != null && p.player_id !== roster.hero_id && p.hands >= 30)
    .sort((a, b) => b.hands - a.hands);
  const list = $("#pick-list");
  if (!list) return;
  list.innerHTML = "";
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
      resetSimClock();
      state.simGen++;
      state.game = data;
      state.paused = false;          // a hold belongs to the table you left
      state.analysis = null;
      if (onScreen("play")) renderTable($("#view"), data);
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

function formatLogLine(line) {
  const street = line.match(/^(flop|turn|river):/i);
  if (street) return street[1][0].toUpperCase() + street[1].slice(1).toLowerCase();
  return line.replace(/\braises to\b/, "raises").replace(/\braise to\b/, "raise");
}

function logLineKind(line) {
  if (/^(flop|turn|river):/i.test(line)) return "log-street";
  if (/\bposts?\b/.test(line)) return "log-post";
  if (/\bwin[s]?\b/.test(line)) return "log-win";
  return "log-act";
}

function actionText(ev) {
  if (!ev) return "";
  if (ev.action === "fold") return "Folds";
  if (ev.action === "check") return "Checks";
  if (ev.action === "call") return "Calls";
  if (ev.opening) return `Bets ${ev.amount}`;
  return `Raises to ${ev.amount}`;
}

async function simPost(route, extra) {
  if (!state.game) return;
  const token = state.game.token;
  const gen = state.simGen;
  clearSimTimer();
  try {
    const d = await post(route, Object.assign({token}, extra || {}));
    if (gen !== state.simGen || !state.game || state.game.token !== token) return;
    state.game = {token, state: d.state};
    state.stepUntil = null;
    state.clockHold = null;
    if (route === "/api/sim/next") {
      state.lastEvent = null; state.revealed = false;
      state.dealHand = null; state.dealUntil = null;
    }
    if (state.tab === "play" && !state.analysis) renderTable($("#view"), state.game);
    else armSimClock();
  } catch (err) { /* game gone or navigated away */ }
}

function clearSimTimer() {
  if (state.stepTimer) { clearTimeout(state.stepTimer); state.stepTimer = null; }
}

function resetSimClock() {
  clearSimTimer();
  state.dealHand = null; state.dealUntil = null; state.stepUntil = null;
  state.clockHold = null; state.revealed = false;
}

// Leaving the Simulate tab, pausing, or hiding the browser tab used to either
// keep firing into a missing DOM or clear the timer and never put it back.
// Hold the remaining wait; thaw it when the table is visible again.
function holdSimClock() {
  clearSimTimer();
  if (state.clockHold) return;
  if (!state.dealUntil && !state.stepUntil) return;
  state.clockHold = {
    dealUntil: state.dealUntil, stepUntil: state.stepUntil, heldAt: Date.now(),
  };
  state.dealUntil = null;
  state.stepUntil = null;
}

function thawSimClock() {
  if (!state.clockHold) return;
  const dt = Date.now() - state.clockHold.heldAt;
  if (state.clockHold.dealUntil) state.dealUntil = state.clockHold.dealUntil + dt;
  if (state.clockHold.stepUntil) state.stepUntil = state.clockHold.stepUntil + dt;
  state.clockHold = null;
}

function armSimClock() {
  clearSimTimer();
  if (!state.game || state.paused || state.clockHold || state.analysis) return;
  if (state.tab !== "play") return;
  const st = state.game.state;
  if (!st.over && !st.your_turn) {
    if (state.stepUntil == null) {
      const wait = (state.lastEvent && state.lastEvent.action === "fold") ? 2000 : SIM_DELAY;
      state.stepUntil = Date.now() + wait;
    }
    const left = Math.max(0, state.stepUntil - Date.now());
    const token = state.game.token;
    const gen = state.simGen;
    state.stepTimer = setTimeout(() => {
      state.stepUntil = null;
      if (gen !== state.simGen || !state.game || state.game.token !== token) return;
      stepBots(token);
    }, left);
  } else if (st.over && !state.revealed) {
    if (state.dealHand !== st.hand_no) {
      state.dealHand = st.hand_no;
      state.dealUntil = Date.now() + SIM_DELAY;
    }
    tickDealCountdown();
  } else {
    paintDealCount();
  }
}

function renderTable(view, data) {
  // Paint only while Simulate is on screen. A reply that lands after a tab
  // switch used to either draw the table over Database, or clear the timer
  // and leave the session frozen when you came back.
  if (state.tab !== "play" || state.analysis) return;
  clearSimTimer();
  const st = data.state, n = st.seats.length;
  const pnl = st.pnl || 0;
  const pnlBb = st.bb ? (pnl / st.bb).toFixed(1) : "0";
  view.innerHTML = `<div class="panel sim-panel"><div class="sim-layout">
    <div class="sim-main">
      <div class="spread"><h2 style="margin:0">hand ${st.hand_no} <span
        class="muted" style="font-weight:400">· ${esc(st.street)}</span></h2>
        <button class="linkbtn" id="leave">Leave table</button></div>
      <div class="poker-table" id="ptable">
        <div class="felt-oval"></div>
        <div class="table-center">
          <div class="pot-pill">pot <b>${st.pot_mid ?? st.pot}</b></div>
          <div class="board" id="board"></div>
          <div class="deal-count idle" id="deal-count" aria-hidden="true"></div>
        </div>
      </div>
      <div class="controls" id="controls"></div>
    </div>
    <div class="sim-side">
      <div class="side-label">session P/L</div>
      <div class="pnl-big ${pnl >= 0 ? "up" : "down"}">${pnl >= 0 ? "+" : ""}${pnl}</div>
      <div class="small muted">${pnl >= 0 ? "+" : ""}${pnlBb} bb · ${st.hand_no} hands</div>
      <div class="small muted" style="margin-top:3px">blinds ${st.sb}/${st.bb}</div>
      <!-- Three session modes, one control shape. Two of them were checkboxes
           and the third (check/fold) a filled pill, which made "armed" mean two
           different things on one screen. They are all cf-toggles now: filled
           is on, outlined is off, readable from across the table view. -->
      <div class="side-modes">
        <button class="act small cf-toggle${state.descOn ? " on" : ""}"
          id="desc-on" aria-pressed="${state.descOn}">Explain</button>
        <button class="act small cf-toggle${state.muted ? "" : " on"}"
          id="snd-on" aria-pressed="${!state.muted}">Sounds</button>
        <button class="act small cf-toggle${state.paused ? " on" : ""}"
          id="pause-on" aria-pressed="${state.paused}">Pause</button>
      </div>
      <button class="act small" id="end-session">End and analyze</button>
      <div class="handlog">
        <div class="handlog-title">Hand log</div>
        <div class="handlog-body" id="handlog"></div>
      </div>
    </div>
  </div></div>`;
  const table = $("#ptable");
  st.seats.forEach((s, i) => {
    const theta = Math.PI / 2 + (i / n) * 2 * Math.PI;   // you (0) at the bottom
    // Sit on the felt rim, inside the stage. 45/44 hung the hero through the
    // bottom of the box and over the action bar.
    const x = 50 + 40 * Math.cos(theta), y = 50 + 36 * Math.sin(theta);
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
          s.is_hero && s.name.toLowerCase() !== "you"
            ? ' <span class="tag hero-tag">you</span>' : ""}</div>
        ${s.is_hero ? `<div class="tseat-made${s.made ? "" : " blank"}">${
          s.made ? esc(s.made) : "—"
        }</div>` : ""}
        <div class="tseat-stack">${s.stack}${
          st.over && s.net ? ` <span class="won-amt${s.net < 0 ? " down" : ""}">${
            s.net > 0 ? "+" : ""}${s.net}</span>` : ""}</div>
      </div>`;
    const ev = state.lastEvent;
    if (ev && ev.seat === i && !s.is_hero) {
      const why = (ev.reason || "").split("—").slice(1).join("—").trim();
      const bubble = document.createElement("div");
      // Outward off the felt, and anchored to whichever edge keeps it inside
      // the table. Centered on the seat, a bubble on a right-hand seat ran off
      // the table and covered the sidebar's End button while a villain thought.
      bubble.className = "think-bubble" + (y > 50 ? " below" : "")
        + (x > 66 ? " from-right" : x < 34 ? " from-left" : "");
      bubble.innerHTML = `<b>${esc(actionText(ev))}</b>${
        state.descOn && why ? `<div class="why">${esc(why)}</div>` : ""}`;
      seat.appendChild(bubble);
    }
    table.appendChild(seat);
    if (s.is_button) {
      const d = document.createElement("div"); d.className = "dealer-btn"; d.textContent = "D";
      d.style.left = (50 + 29 * Math.cos(theta - 0.4)) + "%";
      d.style.top = (50 + 24 * Math.sin(theta - 0.4)) + "%";
      table.appendChild(d);
    }
    if (s.committed > 0 && !st.over) {
      const chip = document.createElement("div"); chip.className = "tbet";
      chip.style.left = (50 + 23 * Math.cos(theta)) + "%";
      chip.style.top = (50 + 19 * Math.sin(theta)) + "%";
      chip.innerHTML = `<span class="chip-dot"></span>${s.committed}`;
      table.appendChild(chip);
    }
  });
  const boardCards = st.board || [];
  $("#board").innerHTML = [0, 1, 2, 3, 4].map(i =>
    boardCards[i] ? cardHtml(boardCards[i], true)
      : '<span class="card big ghost" aria-hidden="true"></span>').join("");
  const logEl = $("#handlog");
  logEl.innerHTML = (st.log || []).map(l =>
    `<div class="log-line ${logLineKind(l)}">${esc(formatLogLine(l))}</div>`).join("");
  logEl.scrollTop = logEl.scrollHeight;
  $("#leave").onclick = () => {
    state.simGen++;
    resetSimClock();
    state.game = null; state.lastEvent = null; state.analysis = null;
    viewPlay();
  };
  const armToggle = (el, on) => {
    el.classList.toggle("on", on);
    el.setAttribute("aria-pressed", String(on));
  };
  $("#desc-on").onclick = (e) => {
    state.descOn = !state.descOn;
    armToggle(e.currentTarget, state.descOn);
  };
  $("#snd-on").onclick = (e) => {
    state.muted = !state.muted;
    armToggle(e.currentTarget, !state.muted);
    if (!state.muted) ensureAudio();
  };
  // Pause holds the *auto-advance*, not the game: the villains stop stepping
  // and the next hand stops dealing itself, but your own controls stay live,
  // so a spot you want to sit with does not disappear at the pace of a timer.
  $("#pause-on").onclick = (e) => {
    state.paused = !state.paused;
    armToggle(e.currentTarget, state.paused);
    if (state.paused) holdSimClock();
    else {
      thawSimClock();
      armSimClock();
    }
    paintDealCount();
  };
  $("#end-session").onclick = () => endSession();
  renderControls($("#controls"), data);
  paintDealCount();
  if (state.paused) return;
  armSimClock();
}

function dealSecsLeft() {
  if (state.clockHold && state.clockHold.dealUntil) {
    return Math.max(0, Math.ceil((state.clockHold.dealUntil - state.clockHold.heldAt) / 1000));
  }
  if (!state.dealUntil) return 0;
  return Math.max(0, Math.ceil((state.dealUntil - Date.now()) / 1000));
}

function paintDealCount() {
  const el = $("#deal-count");
  if (!el) return;
  const over = state.game && state.game.state && state.game.state.over;
  const counting = over && !state.revealed
    && !!(state.dealUntil || (state.clockHold && state.clockHold.dealUntil));
  el.classList.toggle("idle", !counting);
  el.setAttribute("aria-hidden", String(!counting));
  if (!counting) return;
  el.innerHTML = `Next hand<b>${Math.max(dealSecsLeft(), 1)}</b>`;
}

function tickDealCountdown() {
  paintDealCount();
  if (state.paused || state.clockHold || state.tab !== "play") return;
  const left = (state.dealUntil || 0) - Date.now();
  if (left <= 0) {
    state.dealUntil = null;
    state.dealHand = null;
    simPost("/api/sim/next");
    return;
  }
  state.stepTimer = setTimeout(tickDealCountdown, Math.min(200, left));
}

async function stepBots(token) {
  const gen = state.simGen;
  try {
    const r = await post("/api/sim/step", {token});
    if (gen !== state.simGen || !state.game || state.game.token !== token) return;
    if (r.event) actionSound(r.event.action);
    state.game = {token, state: r.state};
    state.lastEvent = r.event;
    state.stepUntil = null;
    if (state.clockHold) state.clockHold.stepUntil = null;
    if (state.tab === "play" && !state.analysis) renderTable($("#view"), state.game);
    else armSimClock();
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
        clearSimTimer();
        state.revealed = true;
        state.dealUntil = null; state.dealHand = null;
        if (state.clockHold) state.clockHold.dealUntil = null;
        renderTable($("#view"), data);
      }));
    }
    el.appendChild(actbtn("Deal now", () => simPost("/api/sim/next"), "primary"));
    el.appendChild(cfToggle(st));
    const won = st.seats.filter(s => s.won);
    if (won.length) {
      const note = document.createElement("span"); note.className = "small muted";
      note.textContent = won.map(s => `${s.name} +${s.won}`).join(" · ");
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
  const leave = document.createElement("div");
  leave.className = "controls-leave";
  if (lg.can_fold) leave.appendChild(actbtn("Fold", () => act("fold")));
  if (lg.can_check) leave.appendChild(actbtn("Check", () => act("check")));
  if (leave.childNodes.length) el.appendChild(leave);
  if (lg.can_raise) {
    const facing = lg.can_call && !lg.can_check;
    const callTo = lg.committed + lg.call_amount;
    const potAfter = st.pot + lg.call_amount;
    const sizeTo = (frac) => {
      const raw = lg.can_check ? Math.round(frac * st.pot)
        : callTo + Math.round(frac * potAfter);
      return Math.min(lg.max_raise_to, Math.max(lg.min_raise_to, raw));
    };
    const snap = (v) => {
      v = +v;
      if (facing && v > callTo && v < lg.min_raise_to) {
        const mid = (callTo + lg.min_raise_to) / 2;
        return v < mid ? callTo : lg.min_raise_to;
      }
      return v;
    };
    const labelFor = (v) => {
      v = snap(v);
      if (facing && v <= callTo) return `Call ${lg.call_amount}`;
      return lg.can_check ? `Bet ${v}` : `Raise to ${v}`;
    };
    const wrap = document.createElement("div"); wrap.className = "raise-wrap";
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = facing ? callTo : lg.min_raise_to;
    slider.max = lg.max_raise_to;
    slider.value = facing ? callTo : sizeTo(0.66);
    const amt = document.createElement("span"); amt.className = "raise-amt";
    const go = actbtn("", () => {
      const v = snap(+slider.value);
      if (facing && v <= callTo) act("call");
      else act("raise", v);
    }, "primary commit");
    const upd = () => {
      const v = snap(+slider.value);
      slider.value = v;
      amt.textContent = facing && v <= callTo ? "call" : `to ${v}`;
      go.textContent = labelFor(v);
    };
    slider.oninput = upd; upd();
    const presets = document.createElement("div"); presets.className = "presets";
    if (facing) {
      presets.appendChild(actbtn("call", () => { slider.value = callTo; upd(); }, "small"));
    }
    for (const [label, frac] of [["⅓", 1 / 3], ["½", 0.5], ["⅔", 2 / 3], ["pot", 1.0]]) {
      presets.appendChild(actbtn(label, () => { slider.value = sizeTo(frac); upd(); }, "small"));
    }
    presets.appendChild(actbtn("all-in", () => { slider.value = lg.max_raise_to; upd(); }, "small"));
    wrap.append(presets, slider, amt);
    el.appendChild(wrap);
    el.appendChild(go);
  } else if (lg.can_call) {
    el.appendChild(actbtn(`Call ${lg.call_amount}`, () => act("call"), "primary commit"));
  }
}

async function endSession() {
  if (!state.game) return;
  const token = state.game.token;
  state.simGen++;
  const gen = state.simGen;
  clearSimTimer();
  try {
    const r = await post("/api/sim/analysis", {token});
    if (gen !== state.simGen) return;
    state.analysis = r.analysis;
    state.game = null;
    state.lastEvent = null;
    resetSimClock();
    if (onScreen("play")) renderAnalysis($("#view"), state.analysis);
  } catch (err) {
    if (state.game && onScreen("play")) armSimClock();
    alert(err.message);
  }
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
    ${(a.lessons && a.lessons.length) ? `<div class="lessons">${
      a.lessons.map(l => `<div class="lesson">${esc(l)}</div>`).join("")
    }</div>` : ""}
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
  $("#a-back").onclick = () => {
    state.analysis = null; state.game = null; state.lastEvent = null;
    viewPlay();
  };
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
    const peek0 = await get("/api/hero?peek=1");
    // "building" is the same wait as "cold": the work is already running,
    // usually because this tab asked a moment ago and the reader came back.
    // Treating it as warm dropped the veil and left a blank poll.
    cold = peek0.status === "cold" || peek0.status === "building";
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
    // Real progress, reported from inside the Python as it walks. Walks over
    // hands are counted per hand; fitting is counted per cross-validation
    // fold. A total of zero means this phase has nothing honest to count.
    const PHASES = {
      starting: "Opening your database",
      finding: "Finding which seat is yours",
      loading: "Reading your hand histories",
      measuring: "Measuring the hands you played",
      reading: "Scoring every hand you played",
      fitting: "Fitting the model to what you held",
      grading: "Grading your folds and your sizing",
    };
    window.__villainProgress = (msg) => {
      const label = PHASES[msg.phase] || "Working";
      const counted = Number(msg.done);
      const total = Number(msg.total);
      if (total > 0) {
        done(`${label}\u2026 ${counted.toLocaleString()} of ${total.toLocaleString()}`,
             counted / total);
      } else {
        done(`${label}\u2026`, undefined);
      }
    };
  }
  let data;
  try {
    data = await get("/api/hero");
    // Local ``villain test`` answers 202 and builds on a thread. Keep the
    // counted veil up and poll peek for the same phases the in-process
    // (browser) build reports directly -- dropping it for "this page will
    // appear on its own" was a loader with no bar.
    while (data && data.status === "building") {
      if (!onScreen("hero")) {
        delete window.__villainProgress;
        return;
      }
      const peek = await get("/api/hero?peek=1");
      if (window.__villainProgress && peek.phase) {
        window.__villainProgress(peek);
      }
      if (peek.status !== "building") {
        data = await get("/api/hero");
        break;
      }
      await new Promise((r) => setTimeout(r, 100));
    }
    delete window.__villainProgress;
    if (done) $("#modal").innerHTML = "";
    if (!onScreen("hero")) return;
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

  const gradesPanel = document.createElement("div");
  gradesPanel.className = "panel wide";
  gradesPanel.innerHTML = `
    <h2 id="hero-grades-head">fold grades &amp; missed value</h2>
    <h3>fold grades</h3>
    <div id="hero-folds"></div>
    <h3>missed value</h3>
    <div id="hero-missed"></div>`;
  dash.appendChild(gradesPanel);
  dash.appendChild(rangeCols);

  const tellsPanel = document.createElement("div");
  tellsPanel.className = "panel wide";
  tellsPanel.innerHTML = `
    <h2 id="hero-tells-head">sizing &amp; timing tells</h2>
    <div class="tellcols">
      <div><h3>sizing</h3><div id="hero-sizing"></div></div>
      <div><h3>timing</h3><div id="hero-timing"></div></div>
    </div>`;
  dash.appendChild(tellsPanel);

  const narrowingPanel = document.createElement("div");
  narrowingPanel.className = "panel wide";
  narrowingPanel.innerHTML = `
    <h2 id="hero-narrowing-head">range narrowing</h2>
    <div id="hero-narrowing"></div>`;
  dash.appendChild(narrowingPanel);

  // Self machinery first. profileCard builds the same tiles it builds for a
  // villain -- key numbers, priced leaks, skill -- and on this tab those are
  // the *least* interesting thing on the page: they are what any opponent with
  // your hand histories could work out. Fold grades, your real range, and the
  // tells only your own export can see go above them, and the villain view of
  // you is relabelled as what it is and moved to the foot of the page.
  const villainView = document.createElement("div");
  villainView.className = "wide villain-view";
  villainView.innerHTML = `<div class="villain-view-head">
    <span class="label-t">How you look as a villain</span>
    <span class="small muted">The same read the tool would give an opponent
      studying you.</span></div>`;
  for (const sel of [".p-hud", ".p-do", ".p-skill"]) {
    const panel = $(sel, dash);
    if (panel) villainView.appendChild(panel);
  }
  dash.appendChild(villainView);

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
  const ranges = [...(data.ranges || [])].sort(
    (a, b) => POSITION_ORDER.indexOf(a.position) - POSITION_ORDER.indexOf(b.position));
  const ring = positionRing(ranges);
  if (ring) {
    positions.appendChild(ring);
    positions.insertAdjacentHTML("beforeend",
      `<div class="panel-lead pos-note">Share of hands played from each seat,
       shaded against your widest.</div>`);
  } else {
    // One seat is not a ring. Fall back to the row the rest of the app uses.
    positions.className = "hero-pos";
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

  // Sizing is a share of the pot, timing is seconds -- same comparison, two
  // units, so the formatter travels with the call.
  renderTellSection($("#hero-sizing", dash), data.sizing, {unit: "of pot"});
  renderTellSection($("#hero-timing", dash), data.timing, {
    unit: "to act", fmt: v => `${v.toFixed(1)}s`});

  const narrowing = $("#hero-narrowing", dash);
  if (!data.narrowing || !data.narrowing.length) {
    narrowing.innerHTML = `<div class="small muted">Not enough hands reaching
      each street yet.</div>`;
  } else {
    const chart = document.createElement("div");
    chart.className = "narrow-wrap";
    chart.appendChild(narrowingChart(data.narrowing));
    narrowing.appendChild(chart);
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
  if (isGuest()) {
    guestLockDrop(drop);
    if (state.session) renderSession();
    return;
  }
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
  if (save && !data.saved) {
    if (isGuest()) guestLock(save);
    else save.onclick = () => commit(data.token);
  }
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
  // Rebuilding the roster from Python is the slow call on this tab. Keep the
  // last one so "all players" can paint immediately instead of looking dead
  // while the same list is computed again. Anything that changes who is in
  // the database drops it.
  let data = state.roster;
  if (!data) {
    data = await get("/api/roster");
    state.roster = data;
  }
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
    if (isGuest()) guestLockDrop($("#db-drop"));
    else wireImport();
    return;
  }
  /* One table, sorted however you like. A separate leaderboard tab was the
     same rows in a different order. */
  if (!onScreen("players")) return;
  const guest = isGuest();
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
  if (guest) {
    guestLock($("#db-add"));
    guestLock($("#reset"));
  } else {
    wireImport();
    $("#reset").onclick = () => confirmReset(data);
  }
  $("#db-roster").appendChild(rosterTable(data.players, {
    onClick: p => {
      if (p.player_id === data.hero_id) { switchTab("hero"); return; }
      state.player = p.player_id; viewPlayer(p.player_id);
    },
    heroId: data.hero_id,
  }));

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
  $("#back").onclick = () => {
    state.player = null;
    if (state.roster) { viewPlayers(); return; }
    renderWithSpinner();
  };

  const holder = document.createElement("div");
  view.appendChild(holder);
  state.heroId = data.hero_id;
  playerTabs(data.profiles, holder, {heroId: data.hero_id});

  // Accounts pooled into this player. A list, not a control surface: the
  // splitting moved into its own dialog behind the actions at the foot of the
  // page, so that a wrong merge and a wrong player are undone from the same
  // place instead of one being a button hidden in a table.
  if (data.aliases && data.aliases.length > 1) {
    const panel = document.createElement("div");
    panel.className = "panel";
    panel.innerHTML = `<h2>Accounts</h2>
      <div class="panel-lead">
        ${data.aliases.length} accounts are pooled as this player.</div>
      <div id="alias-rows"></div>`;
    view.appendChild(panel);
    const rows = panel.querySelector("#alias-rows");
    for (const a of data.aliases) {
      const row = document.createElement("div");
      row.className = "alias-row";
      row.innerHTML = `<span><b>${esc(a.name || a.account)}</b>
          <span class="small muted mono">${esc(a.account)}</span></span>
        <span class="small muted num">${a.hands} hands</span>`;
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

  const actions = playerActions(data);
  if (actions) view.appendChild(actions);
}

/* The two things you can do to a player rather than with one. Both are
   destructive in different degrees, so both are a dialog rather than a button
   that acts, and both live at the foot of the page rather than beside the read
   -- nothing here is part of playing a hand. */
function playerActions(data) {
  const box = document.createElement("div");
  box.className = "panel player-actions";
  box.innerHTML = `<h2>Manage this player</h2>
    <div class="panel-lead">Neither of these touches a stored hand. Hands
      belong to a table, not to a person.</div>
    <div class="row"></div>`;
  const row = $(".row", box);

  const split = document.createElement("button");
  split.className = "act";
  split.textContent = "Split player\u2026";
  const del = document.createElement("button");
  del.className = "act danger";
  del.textContent = "Delete player\u2026";

  if (isGuest()) {
    row.append(guestLock(split), guestLock(del));
    return box;
  }

  const canSplit = (data.aliases || []).length > 1;
  split.disabled = !canSplit;
  if (canSplit) split.onclick = () => splitDialog(data);
  else bindTip(split, `<span class="hl">nothing to split</span><br>
    Only one account is pooled as this player, so there is no second identity
    to separate out.`);

  const isHero = data.hero_id != null && data.player_id === data.hero_id;
  del.disabled = isHero;
  if (isHero) bindTip(del, `<span class="hl">this is you</span><br>
    The Hero tab is built from this identity. Reset the database if you really
    mean to start over.`);
  else del.onclick = () => deleteDialog(data);

  row.append(split, del);
  return box;
}

/* Splitting is per account, not per player, because that is the grain the
   mistake happens at -- one stray account swept into somebody else, not a
   whole identity gone wrong. */
function splitDialog(data) {
  const modal = $("#modal");
  modal.innerHTML = `<div class="veil"><div class="sheet">
    <div class="spread"><h2 style="margin:0">Split ${esc(data.display_name)}</h2>
      <button class="act" id="close">Close</button></div>
    <div class="panel-lead">These ${data.aliases.length} accounts are pooled as
      one person. Split one out if it is somebody else \u2014 the hands stay
      put, only who they belong to changes, and both profiles are rebuilt from
      them.</div>
    <div id="split-rows"></div></div></div>`;
  $("#close").onclick = () => { modal.innerHTML = ""; };
  const rows = $("#split-rows", modal);
  for (const a of data.aliases) {
    const row = document.createElement("div");
    row.className = "alias-row";
    row.innerHTML = `<span><b>${esc(a.name || a.account)}</b>
        <span class="small muted mono">${esc(a.account)}</span></span>
      <span class="small muted num">${a.hands} hands</span>
      <button class="act small">Split out</button>`;
    const button = $("button", row);
    button.onclick = async () => {
      button.disabled = true;
      button.textContent = "Splitting\u2026";
      try {
        const r = await post("/api/unlink",
          {player_id: data.player_id, site: a.site, account: a.account});
        modal.innerHTML = "";
        // Straight to whoever this just became: the point of splitting is to
        // look at them on their own.
        state.roster = null;
        state.player = r.player_id;
        await viewPlayer(r.player_id);
      } catch (err) {
        button.disabled = false;
        button.textContent = "Split out";
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

/* One click, not a typed phrase. Reset asks you to type the words because it
   costs every hand you have; this costs one identity and leaves the hands
   behind. The veil stays up through the delete *and* the roster rebuild --
   dismissing first left the profile on screen looking frozen. */
function deleteDialog(data) {
  const modal = $("#modal");
  const draw = (err) => {
    modal.innerHTML = `<div class="veil"><div class="sheet">
      <h2 style="margin-top:0">Delete ${esc(data.display_name)}?</h2>
      <p>The profile goes. The hands stay.</p>
      <div class="row" style="justify-content:flex-end;margin-top:16px">
        <button class="act" id="cancel-del">Cancel</button>
        <button class="act danger" id="do-del">Delete</button>
      </div>
      ${err ? `<div class="small err" style="margin-top:10px">${esc(err)}</div>` : ""}
    </div></div>`;
    $("#cancel-del").onclick = () => { modal.innerHTML = ""; };
    $("#do-del").onclick = async () => {
      const setBusy = showBusy("Deleting\u2026");
      try {
        await post("/api/player/delete", {player_id: data.player_id});
        setBusy("Opening the roster\u2026", undefined);
        state.player = null;
        state.roster = null;
        await viewPlayers();
        modal.innerHTML = "";
      } catch (e) {
        draw(e.message || "could not delete");
      }
    };
  };
  draw();
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
      state.player = null; state.session = null; state.roster = null;
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
  // Same spinner as the rest of the tool. A muted "finding the hands…"
  // with no motion looked like a hung page on a large sample, which is
  // exactly when this call is slow.
  modal.innerHTML = `<div class="veil"><div class="sheet${sc}">
    <div class="spread"><h2 style="margin:0">${esc(headline)}</h2>
      <button class="act" id="close">Close</button></div>
    <div class="loading"><span class="spinner" aria-hidden="true"></span>
      <span>Finding the hands\u2026</span></div></div></div>`;
  $("#close").onclick = () => { modal.innerHTML = ""; };
  let data;
  try {
    data = await get(`/api/evidence?player=${playerId}&stat=${encodeURIComponent(stat)}`);
  } catch (err) {
    if (!modal.querySelector(".sheet")) return;   // closed while it was working
    modal.innerHTML = `<div class="veil"><div class="sheet${sc}">
      <div class="spread"><h2 style="margin:0">${esc(headline)}</h2>
        <button class="act" id="close">Close</button></div>
      <p class="err">${esc(err.message)}</p>
      </div></div>`;
    $("#close").onclick = () => { modal.innerHTML = ""; };
    return;
  }
  if (!modal.querySelector(".sheet")) return;     // closed while it was working
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
    <div id="replay"></div>
  </div></div>`;
  $("#close-replay").onclick = () => { layer.innerHTML = ""; };
  const box = $("#replay", layer);
  box.appendChild(loadingBlock("Loading the hand\u2026"));
  const r = await get(`/api/hand/${handId}?focus=${playerId}`);
  box.innerHTML = "";
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
  if (state.tab === "play" && tab !== "play") holdSimClock();
  state.tab = tab;
  // Bare "go to the database tab" clears which player was open; a link that
  // names a specific player (from Sessions, say) opens straight to them
  // instead of dropping back to the general roster.
  if (tab === "players") state.player = playerId != null ? playerId : null;
  document.querySelectorAll("nav button").forEach(b =>
    b.classList.toggle("on", b.dataset.tab === tab));
  $("#meta").textContent = "";
  renderWithSpinner();
}

/* In the browser the whole tool runs on this thread, so the work a tab does to
   build itself is work the page cannot paint through. Put a spinner up, give
   the browser a frame to actually draw it, and only then start. Two frames,
   because one only schedules the paint -- the second runs after it. A hidden
   tab never delivers those frames, so waiting for them is how a switch to
   another tab froze the load until you came back. */
const nextFrame = () => new Promise((done) => {
  if (document.visibilityState === "hidden") {
    done();
    return;
  }
  let settled = false;
  const finish = () => {
    if (settled) return;
    settled = true;
    document.removeEventListener("visibilitychange", onHide);
    done();
  };
  const onHide = () => {
    if (document.visibilityState === "hidden") finish();
  };
  document.addEventListener("visibilitychange", onHide);
  requestAnimationFrame(() => requestAnimationFrame(finish));
});

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

document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  for (const id of ["#modal2", "#modal"]) {
    const layer = $(id);
    if (layer && layer.innerHTML.trim()) { layer.innerHTML = ""; return; }
  }
});

document.addEventListener("visibilitychange", () => {
  if (!state.game || state.analysis) return;
  if (document.visibilityState === "hidden") {
    holdSimClock();
    return;
  }
  if (state.tab === "play" && !state.paused) {
    thawSimClock();
    armSimClock();
    paintDealCount();
  }
});

renderWithSpinner();
