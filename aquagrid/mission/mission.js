(() => {
'use strict';
// Every string from the data goes in through textContent: alert text and agent traces are untrusted.
const D = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
const F = D.first, L = D.last, N = D.times.length;
const frameAt = m => D.frames[Math.max(F, Math.min(L, m)) - F];
const fmt = n => Math.round(n).toLocaleString('en-US');
const pad2 = n => String(n).padStart(2, '0');
const mean = a => a.reduce((s, v) => s + v, 0) / a.length;
const h = (tag, cls, text, parent) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; };
const s = (tag, attrs, parent, text) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; };
const spec = Object.fromEntries(D.specs.map(x => [x.id, x]));
const base = Object.fromEntries(Object.entries(D.series).map(([k, v]) => [k, mean(v.slice(0, 60))]));
const val = (id, m) => D.series[id][Math.max(0, Math.min(N - 1, m))];
const run = D.run || [];
const runBlocked = run.filter(r => /BLOCKED$/.test(r.Step)), runRejected = run.filter(r => /REJECTED_BY_GUARD$/.test(r.Step));

// ---------- alarms (derived from the code-computed frames) ----------
function alarmsOf(fr, m) {
  const out = [];
  for (const [sid, [st, why]] of Object.entries(fr.bad)) out.push({ key: 'Q' + sid, rank: 2, g: '▲', tag: sid, text: st + ' · ' + why });
  for (const [sec, f] of Object.entries(fr.sectors)) if (f.v === 'GENUINE_LEAK')
    out.push({ key: 'L' + sec, rank: 3, g: '◆', tag: f.pipe, text: sec + ' leak · isolate ' + f.valve + ' · residual ' + (f.resid > 0 ? '+' : '') + f.resid + ' L/s' });
  if (fr.roi.vetoed) out.push({ key: 'V', rank: 2, g: '▲', tag: 'RESERVE', text: 'throttle vetoed: ' + fr.roi.throttle_min_pct + '% < ' + fr.roi.floor_pct + '% floor' });
  if (fr.bloom) out.push({ key: 'B', rank: 1, g: '▲', tag: 'INTAKE', text: 'algal bloom alert · reserve floor +5%' });
  D.alerts.forEach((a, i) => { if (a.flags.length && m >= a.m) out.push({ key: 'I' + i, rank: 4, g: '■', tag: 'ALERT TEXT', text: 'prompt injection redacted [' + a.flags.join(', ') + ']' }); });
  if (m >= L && runBlocked.length) out.push({ key: 'RB', rank: 4, g: '■', tag: 'GATEWAY', text: runBlocked.length + ' bypass attempt(s) BLOCKED, nothing stored' });
  if (m >= L && runRejected.length) out.push({ key: 'RR', rank: 4, g: '■', tag: 'GUARD', text: runRejected.length + ' proposal(s) REJECTED_BY_GUARD' });
  return out;
}
const firstSeen = {};
for (let m = F; m <= L; m++) for (const a of alarmsOf(frameAt(m), m)) if (!(a.key in firstSeen)) firstSeen[a.key] = m;

// ---------- header ----------
$('scn').textContent = '';
$('scn').append('SCENARIO ', Object.assign(h('b'), { textContent: D.scenario }), ' · ' + D.name);
$('roiWin').textContent = pad2(D.peak[0]) + ':00–' + pad2(D.peak[1]) + ':00';

// ---------- map ----------
const map = $('map');
const ST = D.story, SEC = [...ST.sectors.filter(x => x !== ST.loop_sector), ST.loop_sector]; // loop sector drawn last (bottom, with its bypass)
const POS = { PLANT: [46, 170], R1: [140, 170] };
const rowY = [50, 120, 190];
for (let n = 1; n <= 20; n++) {
  const k = (n - 1) % 5, slot = SEC.indexOf('S' + (Math.floor((n - 1) / 5) + 1));
  POS['J' + pad2(n)] = slot < 3 ? [300 + k * 58, rowY[slot]] : k < 4 ? [300 + k * 58, 290] : [387, 355];
}
const sectorOfJ = id => 'S' + (Math.floor((parseInt(id.slice(1), 10) - 1) / 5) + 1);
const gPipes = s('g', {}, map), gNodes = s('g', {}, map), gTags = s('g', {}, map);
const pipeEls = {}, nodeEls = {};
for (const p of D.pipes) {
  const inlet = p.from_node === 'R1', a = POS[p.from_node], b = POS[p.to_node];
  const d = inlet ? 'M165 170H230V' + b[1] + 'H' + b[0] : 'M' + a[0] + ' ' + a[1] + 'L' + b[0] + ' ' + b[1];
  const sec = inlet ? p.id.split('-')[1] : sectorOfJ(p.from_node);
  const line = s('path', { class: 'pipe', d }, gPipes), flow = s('path', { class: 'flow', d }, gPipes);
  s('title', {}, line, p.id);
  pipeEls[p.id] = { line, flow, sec };
  if (p.valve_id) {
    const mx = inlet ? 262 : (a[0] + b[0]) / 2, my = inlet ? b[1] : (a[1] + b[1]) / 2;
    const v = s('rect', { class: 'valve', x: mx - 5, y: my - 5, width: 10, height: 10 }, gPipes);
    s('title', {}, v, p.valve_id + ' · ' + (D.valves[p.valve_id] ?? 100) + '% open');
  }
}
s('path', { class: 'pipe', d: 'M78 170H115' }, gPipes); s('path', { class: 'flow', d: 'M78 170H115', style: '--spd:1.2s' }, gPipes);
s('rect', { x: 14, y: 152, width: 64, height: 36, fill: 'none', stroke: 'var(--info)', 'stroke-width': 1.5 }, gNodes);
s('text', { x: 46, y: 168, 'text-anchor': 'middle', fill: 'var(--ink)', 'font-size': 9 }, gNodes, ST.plant_id);
const plantTxt = s('text', { x: 46, y: 181, 'text-anchor': 'middle', fill: 'var(--mut)', 'font-size': 9 }, gNodes, '');
s('rect', { x: 115, y: 135, width: 50, height: 70, fill: 'var(--bg)', stroke: 'var(--info)', 'stroke-width': 1.5 }, gNodes);
const tank = s('rect', { x: 117, y: 190, width: 46, height: 13, fill: 'var(--ok-d)' }, gNodes);
const floorLn = s('line', { x1: 112, x2: 168, y1: 170, y2: 170, stroke: 'var(--warn)', 'stroke-width': 1.5, 'stroke-dasharray': '3 2' }, gNodes);
s('text', { x: 140, y: 124, 'text-anchor': 'middle', fill: 'var(--ink)', 'font-size': 10, 'font-weight': 600 }, gNodes, 'R1');
const tankTxt = s('text', { x: 140, y: 218, 'text-anchor': 'middle', fill: 'var(--mut)', 'font-size': 9 }, gNodes, '');
const flowTxt = {}, secLbl = {};
SEC.forEach((sec, i) => {
  const y = i < 3 ? rowY[i] : 290, x = 560;
  secLbl[sec] = s('text', { class: 'slbl', x, y: y - 3 }, gNodes, sec);
  flowTxt[sec] = s('text', { class: 'ftag', x, y: y + 10 }, gNodes, '');
});
for (let n = 1; n <= 20; n++) {
  const id = 'J' + pad2(n), [x, y] = POS[id], hosp = id === ST.critical_node, g = s('g', {}, gNodes);
  const ping = s('circle', { class: 'ping', cx: x, cy: y, r: 8, style: 'display:none' }, g);
  const node = hosp ? s('rect', { class: 'node hosp', x: x - 9, y: y - 9, width: 18, height: 18 }, g) : s('circle', { class: 'node', cx: x, cy: y, r: 7 }, g);
  if (hosp) s('text', { x, y: y + 4, 'text-anchor': 'middle', fill: 'var(--info)', 'font-size': 13, 'font-weight': 700 }, g, '+');
  s('text', { class: 'nlbl', x, y: y + (hosp ? 22 : 19) }, g, hosp ? id + ' HOSPITAL' : id);
  nodeEls[id] = { node, ping };
}

function updateMap(m) {
  const fr = frameAt(m);
  const leakPipes = new Set(Object.values(fr.sectors).filter(f => f.v === 'GENUINE_LEAK').map(f => f.pipe));
  for (const [id, p] of Object.entries(pipeEls)) {
    const isLeak = leakPipes.has(id);
    p.line.classList.toggle('leak', isLeak); p.flow.classList.toggle('leak', isLeak);
    const fl = val('F-' + p.sec, m);
    p.flow.style.setProperty('--spd', (isLeak ? 0.7 : Math.max(0.8, 3 - fl / 40)).toFixed(2) + 's');
  }
  for (const [id, n] of Object.entries(nodeEls)) {
    const sid = 'P-' + id, leak = fr.leak.includes(sid), bad = sid in fr.bad;
    n.node.classList.toggle('leak', leak); n.node.classList.toggle('bad', bad && !leak);
    n.ping.style.display = leak && !reduce ? '' : 'none';
  }
  for (const sec of SEC) {
    const f = fr.sectors[sec];
    flowTxt[sec].textContent = 'F-' + sec + ' ' + val('F-' + sec, m).toFixed(0) + ' L/s' + (f.v === 'GENUINE_LEAK' ? ' ◆' : f.v === 'SENSOR_FAULT' ? ' ▲' : '');
    secLbl[sec].style.fill = f.v === 'GENUINE_LEAK' ? 'var(--alarm-t)' : f.v === 'SENSOR_FAULT' ? 'var(--warn)' : '';
  }
  const lvl = fr.level, top = 137, hgt = 66, fy = top + hgt * (1 - fr.floor / 100) + 0, ty = top + hgt * (1 - lvl / 100);
  tank.setAttribute('y', ty); tank.setAttribute('height', Math.max(1, top + hgt - ty));
  floorLn.setAttribute('y1', fy); floorLn.setAttribute('y2', fy);
  tankTxt.textContent = lvl.toFixed(1) + '% · floor ' + fr.floor.toFixed(0) + '%';
  plantTxt.textContent = (val('F-PLANT', m) * 3.6).toFixed(0) + ' m³/h';
  $('mapSub').textContent = ST.critical_node + ' hospital: ' + (D.valves[ST.intake_valve] === 0 ? 'CUT OFF' : 'supplied');
  gTags.replaceChildren();
  const ids = [...new Set([...fr.leak, ...Object.keys(fr.bad)])].filter(id => id.startsWith('P-J'));
  ids.forEach((sid, i) => {
    const j = spec[sid].loc, [x, y] = POS[j], leak = fr.leak.includes(sid), v = val(sid, m);
    const label = leak ? sid + '  ' + v.toFixed(1) + ' m  Δ' + (v - base[sid]).toFixed(1) : sid + '  ' + fr.bad[sid][0] + '  ' + v.toFixed(1);
    const w = label.length * 6 + 10, up = i % 2 === 0, ty2 = up ? y - 34 : y + 30;
    const tx = Math.max(4, Math.min(640 - w - 4, x - w / 2));
    const g = s('g', { class: 'tag ' + (leak ? 'alarm' : 'warn') }, gTags);
    s('line', { x1: x, y1: up ? y - 9 : y + 9, x2: x, y2: up ? ty2 + 14 : ty2 }, g);
    s('rect', { x: tx, y: ty2, width: w, height: 14 }, g);
    s('text', { x: tx + 5, y: ty2 + 10.5 }, g, label);
  });
}

// ---------- radar ----------
const GROUPS = [...ST.sectors, 'SYS'], WEDGE = Math.PI * 2 / 5;
const blips = [];
GROUPS.forEach((g, gi) => {
  const list = D.specs.filter(x => (x.sector || 'SYS') === g);
  list.forEach((x, i) => blips.push({ id: x.id, g, a: -Math.PI / 2 + gi * WEDGE + (i + 0.5) / list.length * WEDGE, r: 0.86 + 0.04 * ((i % 3) - 1), jit: 0.04 * ((i % 3) - 1) }));
});
let secBlips = [], radarState = { anom: 0, sec: 0 };
function setRadar(m) {
  const fr = frameAt(m);
  for (const b of blips) b.t = (fr.leak.includes(b.id) ? 0.3 : b.id in fr.bad ? 0.58 : 0.86 + b.jit);
  secBlips = alarmsOf(fr, m).filter(a => a.rank === 4).map((a, i, arr) => ({ label: a.tag, a: -Math.PI / 2 + 4 * WEDGE + (arr.length === 1 ? 0.8 : 0.22 + 0.7 * i / (arr.length - 1)) * WEDGE, r: 0.36 + 0.08 * (i % 2) }));
  const anom = blips.filter(b => b.t < 0.8).length;
  radarState = { anom, sec: secBlips.length };
  $('radSub').textContent = blips.length + ' tracks · ' + anom + ' anomalous · ' + secBlips.length + ' security';
}
const cv = $('radar'), cx2 = cv.getContext('2d');
function drawRadar(now) {
  const dpr = devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  if (!W || !H) return;
  if (cv.width !== W * dpr || cv.height !== H * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  const c = cx2, st = getComputedStyle(document.documentElement);
  const col = n => st.getPropertyValue(n).trim();
  c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
  const X = W / 2, Y = H / 2, R = Math.min(W, H) / 2 - 24;
  c.lineWidth = 1; c.strokeStyle = col('--line2'); c.fillStyle = col('--dim'); c.font = '10px ' + col('--mono');
  for (const f of [0.3, 0.58, 0.86, 1]) { c.beginPath(); c.arc(X, Y, R * f, 0, 7); c.globalAlpha = f === 1 ? 0.9 : 0.5; c.stroke(); }
  c.globalAlpha = 0.5;
  GROUPS.forEach((g, i) => {
    const a = -Math.PI / 2 + i * WEDGE;
    c.beginPath(); c.moveTo(X, Y); c.lineTo(X + Math.cos(a) * R, Y + Math.sin(a) * R); c.stroke();
    const m = a + WEDGE / 2; c.globalAlpha = 1; c.textAlign = 'center'; c.fillStyle = col('--mut');
    c.fillText(g === 'SYS' ? 'SYS · SEC' : g, X + Math.cos(m) * (R + 13), Y + Math.sin(m) * (R + 13) + 3); c.globalAlpha = 0.5;
  });
  c.globalAlpha = 1; c.textAlign = 'left'; c.fillStyle = col('--dim');
  [['LEAK', 0.3], ['FAULT', 0.58], ['NOMINAL', 0.86]].forEach(([t, f]) => c.fillText(t, X + 4, Y - R * f + 11));
  const th = reduce ? -Math.PI / 2 : (now / 6500) * Math.PI * 2 - Math.PI / 2;
  const ok = col('--ok'), red = col('--alarm-t'), amber = col('--warn');
  if (!reduce) {
    const gr = c.createConicGradient(th - 1.1, X, Y);
    gr.addColorStop(0, 'transparent'); gr.addColorStop(0.17, 'oklch(0.8 0.12 185 / .3)'); gr.addColorStop(0.1701, 'transparent');
    c.fillStyle = gr; c.beginPath(); c.arc(X, Y, R, 0, 7); c.fill();
    c.strokeStyle = ok; c.globalAlpha = 0.9; c.beginPath(); c.moveTo(X, Y); c.lineTo(X + Math.cos(th) * R, Y + Math.sin(th) * R); c.stroke();
  }
  const glow = a => { const d = (((th - a) % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2); return d < 2.6 ? Math.exp(-d * 1.4) : 0; };
  c.textAlign = 'left';
  for (const b of blips) {
    if (b.t == null) continue;
    b.r += (b.t - b.r) * (reduce ? 1 : 0.06);
    const px = X + Math.cos(b.a) * R * b.r, py = Y + Math.sin(b.a) * R * b.r, leak = b.t < 0.4, bad = b.t < 0.8 && !leak;
    const gl = reduce ? 1 : glow(b.a);
    c.globalAlpha = leak || bad ? 0.6 + 0.4 * gl : 0.2 + 0.7 * gl;
    c.fillStyle = c.strokeStyle = leak ? red : bad ? amber : ok;
    c.beginPath();
    if (leak) { c.moveTo(px, py - 8); c.lineTo(px + 7, py); c.lineTo(px, py + 8); c.lineTo(px - 7, py); c.closePath(); c.fill(); }
    else if (bad) { c.moveTo(px, py - 7); c.lineTo(px + 7, py + 5); c.lineTo(px - 7, py + 5); c.closePath(); c.fill(); }
    else { c.arc(px, py, 2.6, 0, 7); c.fill(); }
    if (leak || bad) {
      if (leak && !reduce) { const k = (now % 1500) / 1500; c.globalAlpha = 1 - k; c.beginPath(); c.arc(px, py, 8 + k * 16, 0, 7); c.stroke(); }
      c.globalAlpha = 1; c.fillStyle = leak ? red : amber; c.fillText(b.id, px + 10, py + 3);
    }
  }
  for (const b of secBlips) {
    const px = X + Math.cos(b.a) * R * b.r, py = Y + Math.sin(b.a) * R * b.r, gl = reduce ? 1 : glow(b.a);
    c.globalAlpha = 0.7 + 0.3 * gl; c.fillStyle = c.strokeStyle = red;
    c.fillRect(px - 6, py - 6, 12, 12); c.strokeStyle = col('--bg'); c.lineWidth = 2;
    c.beginPath(); c.moveTo(px - 3, py - 3); c.lineTo(px + 3, py + 3); c.moveTo(px + 3, py - 3); c.lineTo(px - 3, py + 3); c.stroke(); c.lineWidth = 1;
    if (!reduce) { const k = (now % 1100) / 1100; c.strokeStyle = red; c.globalAlpha = 1 - k; c.strokeRect(px - 6 - k * 10, py - 6 - k * 10, 12 + k * 20, 12 + k * 20); }
    c.globalAlpha = 1; c.fillStyle = red; c.fillText(b.label, px - 24, py + 22);
  }
  c.globalAlpha = 1; c.fillStyle = col('--ok'); c.beginPath(); c.arc(X, Y, 3, 0, 7); c.fill();
}

// ---------- agent console ----------
const cons = $('cons'), cur = h('div', null); cur.appendChild(h('span', 'cur')); cons.appendChild(cur);
const typing = []; let lastEvent = -1e9, consM = F - 1;
const evBy = {}; D.events.forEach(e => (evBy[e.m] = evBy[e.m] || []).push(e));
function addLine(time, actor, level, text, anim) {
  const ln = h('div', 'ln ' + level + (anim ? ' new' : '')); h('span', 't', time, ln); h('span', 'a ' + actor, actor, ln);
  const x = h('span', 'x', anim && actor === 'AGENT' && !reduce ? '' : text, ln);
  if (anim && actor === 'AGENT' && !reduce) typing.push({ el: x, full: text, i: 0 });
  cons.insertBefore(ln, cur);
  while (cons.children.length > 160) cons.removeChild(cons.firstChild);
  if (anim && level !== 'sys') lastEvent = performance.now();
}
function minuteLines(mm, anim) {
  const t = D.times[mm], fr = frameAt(mm);
  if (mm === F) {
    addLine(t, 'CODE', 'info', 'guards armed: stale/range/flatline/drift checks, mass balance, reserve floor ' + D.base_floor + '%', anim);
    addLine(t, 'AGENT', 'info', 'online. tools: read_network_telemetry, read_tariff_and_quality_feeds (read-only), stage_gated_valve_or_setpoint_action (queues for human approval).', anim);
  }
  for (const e of evBy[mm] || []) addLine(e.ts, e.actor, e.level, '[' + e.tag + '] ' + e.text, anim);
  const bad = Object.keys(fr.bad).length;
  addLine(t, 'CODE', 'sys', 'poll ' + (D.specs.length - bad) + '/' + D.specs.length + ' · R1 ' + fr.level.toFixed(1) + '% · floor ' + fr.floor.toFixed(1) + '% · ' + ST.leak_sector + ' Δ' + fr.sectors[ST.leak_sector].resid + ' L/s', anim);
  if (mm === L) {
    if (run.length) for (const r of run) {
      const sec = r.Who === 'ATTACKER' || /BLOCKED|REJECTED/.test(r.Step);
      addLine(t, r.Who, sec ? 'sec' : 'info', r.Step + ' · ' + r.Detail, anim);
    } else addLine(t, 'CODE', 'info', 'live monitor idle at 14:00. Press "Run agent" to let the agent read the network and stage proposals.', anim);
  }
}
function syncConsole(m, jumped) {
  if (jumped || m < consM) { while (cons.children.length > 1) cons.removeChild(cons.firstChild); typing.length = 0; consM = F - 1; }
  const from = jumped || m < consM ? Math.max(F, m - 40) : consM + 1;
  if (from > consM + 1) consM = from - 1;
  for (let mm = consM + 1; mm <= m; mm++) minuteLines(mm, !jumped && !(m - consM > 6));
  consM = m; cons.scrollTop = cons.scrollHeight;
}

// ---------- ROI ----------
const roiEl = $('roi'), nums = {};
function rrow(cls, label, key, sub, unit) {
  const r = h('div', 'rrow ' + cls, null, roiEl), l = h('span', null, label, r);
  if (sub) h('small', null, sub, l);
  const b = h('b', null, '0', r); nums[key] = { el: b, cur: 0, to: 0, unit };
}
rrow('', 'Opportunity: throttle to 500 m³/h', 'opportunity_kwh', 'peak kWh available if the floor allowed it', ' kWh');
rrow('banked', 'Banked within the floor', 'banked_kwh', 'peak kWh the guarded plan avoids', ' kWh');
rrow('blocked', 'Blocked by the floor', 'blocked_kwh', 'safety beat tariff savings', ' kWh');
rrow('', 'Peak cost avoided', 'aed_saved', 'banked kWh x peak tariff (AED/kWh)', ' AED');
const bar = h('div', 'bar', null, roiEl), mk = {};
for (const [k, c] of [['fl', 'fl'], ['th', 'th'], ['pl', 'pl']]) mk[k] = Object.assign(h('i', c, null, bar), {});
const leakRow = h('div', 'rrow', null, roiEl); h('span', null, ST.leak_sector + ' leak waste', leakRow); const leakB = h('b', null, '', leakRow);
const spark = s('svg', { id: 'roispark', viewBox: '0 0 300 62', preserveAspectRatio: 'none' }); roiEl.appendChild(spark);
const sMax = Math.max(...D.frames.map(f => Math.max(f.roi.opportunity_kwh, f.roi.banked_kwh))) * 1.08 || 1;
const sx = m => (m - F) / (L - F) * 300, sy = v => 58 - v / sMax * 54;
const poly = k => D.frames.map(f => sx(f.m).toFixed(1) + ',' + sy(f.roi[k]).toFixed(1)).join(' ');
s('polyline', { points: poly('opportunity_kwh'), fill: 'none', stroke: 'var(--dim)', 'stroke-dasharray': '3 3', 'stroke-width': 1.5, 'vector-effect': 'non-scaling-stroke' }, spark);
s('polyline', { points: poly('banked_kwh'), fill: 'none', stroke: 'var(--ok)', 'stroke-width': 1.8, 'vector-effect': 'non-scaling-stroke' }, spark);
const sph = s('line', { y1: 0, y2: 62, stroke: 'var(--ink)', 'stroke-width': 1, 'vector-effect': 'non-scaling-stroke' }, spark);
function updateRoi(m) {
  const r = frameAt(m).roi;
  for (const k in nums) nums[k].to = r[k];
  const pos = v => Math.max(0, Math.min(100, (v - 20) / 40 * 100)) + '%';
  mk.fl.style.left = pos(r.floor_pct); mk.fl.dataset.l = 'floor ' + r.floor_pct.toFixed(0) + '%';
  mk.th.style.left = pos(r.throttle_min_pct); mk.th.dataset.l = 'throttle ' + r.throttle_min_pct.toFixed(1) + '%';
  mk.pl.style.left = pos(r.min_level_pct); mk.pl.dataset.l = 'plan ' + r.min_level_pct.toFixed(1) + '%';
  leakB.textContent = r.leak_loss_m3h ? fmt(r.leak_loss_m3h) + ' m³/h · ' + fmt(r.leak_kwh_per_h) + ' kWh/h' : 'none detected';
  leakB.style.color = r.leak_loss_m3h ? 'var(--alarm-t)' : 'var(--dim)';
  const x = sx(Math.max(F, m)); sph.setAttribute('x1', x); sph.setAttribute('x2', x);
}

// ---------- alarm list + sensor matrix ----------
let almSig = '';
function updateAlarms(m) {
  const list = alarmsOf(frameAt(m), m).sort((a, b) => b.rank - a.rank || firstSeen[a.key] - firstSeen[b.key]);
  $('almSub').textContent = list.length + ' active';
  const sig = list.map(a => a.key).join('|'); if (sig === almSig) return; almSig = sig;
  const host = $('alm'); host.replaceChildren();
  if (!list.length) { h('div', 'empty', 'No active alarms. All sectors within baseline.', host); return; }
  const t = h('table', 'tbl', null, host), hd = h('tr', null, null, h('thead', null, null, t));
  ['', 'SINCE', 'TAG', 'CONDITION'].forEach(x => h('th', null, x, hd));
  const tb = h('tbody', null, null, t);
  for (const a of list) {
    const tr = h('tr', 's' + a.rank, null, tb); h('td', 'g', a.g, tr); h('td', null, D.times[firstSeen[a.key]], tr); h('td', null, a.tag, tr); h('td', null, a.text, tr);
  }
}
const mx = $('mx'), cells = {};
const short = x => x.kind === 'pressure' ? x.loc.slice(1) : x.id === 'F-PLANT' ? 'FP' : x.kind === 'flow' ? 'F' : x.kind === 'chlorine' ? 'Cl' : x.kind === 'turbidity' ? 'T' : 'L';
GROUPS.forEach(g => {
  const row = h('div', 'mrow', null, mx); h('b', null, g, row); const box = h('div', 'cells', null, row);
  D.specs.filter(x => (x.sector || 'SYS') === g).forEach(x => { cells[x.id] = h('div', 'c', short(x), box); cells[x.id].dataset.sh = short(x); });
});
h('div', 'legend', '▲ quarantined   ◆ moved with leak   □ trusted', mx);
function updateMatrix(m) {
  const fr = frameAt(m); let bad = 0;
  for (const [id, el] of Object.entries(cells)) {
    const leak = fr.leak.includes(id), q = id in fr.bad, cls = 'c' + (leak ? ' leak' : q ? ' bad' : '');
    if (leak || q) bad++;
    const txt = leak ? '◆' : q ? '▲' : el.dataset.sh;
    if (el.className.replace(' flip', '') !== cls) { el.className = cls + (reduce ? '' : ' flip'); }
    el.textContent = txt;
    el.title = id + ' · ' + (q ? fr.bad[id][0] + ' · ' + fr.bad[id][1] : leak ? 'leak-affected' : 'OK') + ' · latest ' + val(id, m).toFixed(2);
  }
  $('mxSub').textContent = (D.specs.length - Object.keys(fr.bad).length) + '/' + D.specs.length + ' trusted';
  const ps = $('pSens'); ps.classList.toggle('bad', Object.keys(fr.bad).length > 0);
  ps.lastChild.textContent = 'SENSORS ' + (D.specs.length - Object.keys(fr.bad).length) + '/' + D.specs.length;
}

// ---------- banner ----------
function updateBanner(m) {
  const fr = frameAt(m), al = alarmsOf(fr, m), top = al.reduce((a, b) => Math.max(a, b.rank), 0);
  const kind = ['nominal', 'advisory', 'advisory', 'alarm', 'security'][top], b = $('banner');
  const nFault = Object.keys(fr.bad).length, nLeak = Object.values(fr.sectors).filter(f => f.v === 'GENUINE_LEAK').length;
  let msg = 'NOMINAL · ALL SECTORS WITHIN BASELINE', g = '●';
  if (kind === 'advisory') {
    g = '▲';
    const parts = [];
    if (nFault) parts.push(nFault + ' SENSOR' + (nFault > 1 ? 'S' : '') + ' QUARANTINED');
    if (fr.roi.vetoed) parts.push('PEAK THROTTLE VETOED BY RESERVE FLOOR');
    if (fr.bloom) parts.push('ALGAL BLOOM · FLOOR ' + fr.floor.toFixed(0) + '%');
    msg = 'ADVISORY · ' + parts.join(' · ');
  }
  if (kind === 'alarm') { g = '◆'; const lk = Object.values(fr.sectors).find(f => f.v === 'GENUINE_LEAK'); msg = 'ALARM · LEAK ' + lk.pipe + ' · ISOLATE ' + lk.valve + ' · AWAITING OPERATOR APPROVAL'; }
  if (kind === 'security') { g = '■'; msg = 'SECURITY ALERT · PROMPT INJECTION DETECTED · UNTRUSTED TEXT REDACTED · WRITES LOCKED TO HUMAN APPROVAL'; }
  b.dataset.s = kind; $('bG').textContent = g; $('bT').textContent = msg;
  $('bC').textContent = nFault + ' fault · ' + nLeak + ' leak · ' + al.filter(a => a.rank === 4).length + ' security · floor ' + fr.floor.toFixed(0) + '%';
  document.body.classList.toggle('attack', kind === 'security');
}

// ---------- scrubber ----------
const rng = $('rng'), spk = $('spark');
spk.setAttribute('viewBox', '0 0 1000 46');
const lv = D.series['L-R1'], fl = D.frames.map(f => f.floor);
const lo = Math.min(...lv, ...fl) - 4, hi = Math.max(...lv, ...fl) + 4;
const yy = v => 42 - (v - lo) / (hi - lo) * 34, xx = m => m / (N - 1) * 1000;
s('polyline', { points: lv.map((v, m) => xx(m).toFixed(1) + ',' + yy(v).toFixed(1)).join(' '), fill: 'none', stroke: 'var(--info)', 'stroke-width': 1.6, 'vector-effect': 'non-scaling-stroke' }, spk);
s('polyline', { points: D.frames.map(f => xx(f.m).toFixed(1) + ',' + yy(f.floor).toFixed(1)).join(' '), fill: 'none', stroke: 'var(--warn)', 'stroke-dasharray': '4 3', 'stroke-width': 1.4, 'vector-effect': 'non-scaling-stroke' }, spk);
const mkSeen = new Set();
for (const e of D.events) {
  const cls = { LEAK: 'alarm', VETO: 'alarm', INJECTION: 'sec', QUARANTINE: 'warn', ALERT: 'warn' }[e.tag]; if (!cls || mkSeen.has(e.m + cls)) continue; mkSeen.add(e.m + cls);
  const k = h('div', 'mk ' + cls, null, $('mks')); k.style.left = e.m / (N - 1) * 100 + '%'; k.title = e.ts + ' ' + e.tag;
}

// ---------- main loop ----------
// React shell: a reload (new run, new data) resumes where the viewer was via ?at=<minute>, and we report our minute back.
const at = +new URLSearchParams(location.search).get('at');
let t = at >= F && at <= L ? at : F, playing = !reduce && t < L, rate = 4, lastM = -1, prev = performance.now(), jumpFlag = true, lastPost = 0;
const bPlay = $('bPlay');
function setPlaying(p) { playing = p; bPlay.textContent = p ? 'PAUSE' : t >= L ? 'DONE' : 'PLAY'; }
function setRate(k) { rate = k; document.querySelectorAll('[data-k]').forEach(b => b.setAttribute('aria-pressed', String(+b.dataset.k === k))); }
function seek(v, jumped) { t = Math.max(0, Math.min(L, v)); if (jumped) jumpFlag = true; if (t < L && !playing && !jumped) { } }
function onMinute(m) {
  updateBanner(m); updateMap(m); setRadar(m); updateAlarms(m); updateMatrix(m); updateRoi(m); syncConsole(m, jumpFlag); jumpFlag = false;
  rng.value = m; $('playhead').style.left = (Math.max(t, 0) / (N - 1) * 100) + '%';
}
function frame(now) {
  const dt = Math.min(0.25, (now - prev) / 1000); prev = now;
  if (playing) { t += dt * rate; if (t >= L) { t = L; setPlaying(false); } }
  const m = Math.floor(t);
  if (now - lastPost > 500) { lastPost = now; try { parent.postMessage({ aquagridPlayhead: t }, '*'); } catch (e) { /* standalone page */ } }
  if (m !== lastM) { lastM = m; onMinute(m); }
  $('clock').replaceChildren(D.times[m] + ':' + pad2(Math.floor((t - m) * 60)), Object.assign(h('small'), { textContent: '2026-11-20 GST' }));
  $('playhead').style.left = (t / (N - 1) * 100) + '%';
  for (const k in nums) { const n = nums[k]; n.cur += (n.to - n.cur) * (reduce ? 1 : 0.18); if (Math.abs(n.to - n.cur) < 0.5) n.cur = n.to; n.el.textContent = fmt(n.cur) + n.unit; }
  while (typing.length) { const ty = typing[0]; ty.i = Math.min(ty.full.length, ty.i + 4); ty.el.textContent = ty.full.slice(0, ty.i); if (ty.i >= ty.full.length) typing.shift(); else break; }
  $('aState').dataset.s = (performance.now() - lastEvent < 1400 || typing.length) ? 'think' : 'mon';
  $('aTxt').textContent = $('aState').dataset.s === 'think' ? 'ANALYSING' : 'MONITORING';
  drawRadar(now);
  requestAnimationFrame(frame);
}
bPlay.onclick = () => { if (t >= L) { t = F; jumpFlag = true; } setPlaying(!playing); };
$('bRestart').onclick = () => { t = F; jumpFlag = true; setPlaying(true); };
document.querySelectorAll('[data-k]').forEach(b => b.onclick = () => setRate(+b.dataset.k));
rng.oninput = () => { t = Math.max(F, +rng.value); jumpFlag = true; if (!playing) setPlaying(false); };
addEventListener('keydown', e => {
  if (e.code === 'Space') { e.preventDefault(); bPlay.click(); }
  if (e.code === 'ArrowRight') { t = Math.min(L, Math.floor(t) + 1); jumpFlag = true; }
  if (e.code === 'ArrowLeft') { t = Math.max(F, Math.floor(t) - 1); jumpFlag = true; }
});
setRate(4); setPlaying(playing);
requestAnimationFrame(frame);
})();
