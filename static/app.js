'use strict';
/* Social Link Analyzer UI: vanilla JS + Cytoscape. Charts are hand-built SVG/HTML. */

// ------------------------------------------------------------------ helpers
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const cssVar = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmtNum = (n) => (n == null ? '–' : Number(n).toLocaleString());
const fmtPct = (n, d = 0) => (n == null ? '–' : `${(n * 100).toFixed(d)}%`);
const fmtDate = (t) => (t ? new Date(t * 1000).toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : '–');
const fmtDay = (t) => (t ? new Date(t * 1000).toISOString().slice(0, 10) : '–');
const fmtMin = (m) => (m == null ? '–' : m < 90 ? `${Math.round(m)} min` : m < 2880 ? `${(m / 60).toFixed(1)} h` : `${(m / 1440).toFixed(1)} d`);
const fmtAge = (t) => { if (!t) return '–'; const d = (Date.now() / 1000 - t) / 86400; return d < 60 ? `${Math.round(d)} days` : d < 730 ? `${(d / 30.4).toFixed(0)} months` : `${(d / 365).toFixed(1)} years`; };
const acct = (id, label) => `<a class="link" data-account="${esc(id)}">${esc(label || id)}</a>`;
const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const isJson = (res.headers.get('content-type') || '').includes('json');
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) throw new Error((body && body.detail) || res.statusText);
  return body;
}
const post = (path, data) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data ?? {}) });

function toast(msg, ms = 3500) {
  const t = $('#toast');
  t.textContent = msg; t.style.display = 'block';
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.style.display = 'none'), ms);
}
function fail(e) { console.error(e); toast(`⚠ ${e.message || e}`, 6000); }
async function busy(btn, fn) {
  const label = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${label}`;
  try { return await fn(); } catch (e) { fail(e); } finally { btn.disabled = false; btn.innerHTML = label; }
}

// tooltip: any element with data-tip (HTML, already escaped for attributes)
const tip = $('#tip');
document.addEventListener('mouseover', (e) => {
  const el = e.target.closest('[data-tip]');
  if (!el) { tip.style.display = 'none'; return; }
  tip.innerHTML = el.dataset.tip; tip.style.display = 'block';
});
document.addEventListener('mousemove', (e) => {
  if (tip.style.display !== 'block') return;
  const r = tip.getBoundingClientRect();
  tip.style.left = `${Math.min(e.clientX + 14, innerWidth - r.width - 8)}px`;
  tip.style.top = `${Math.min(e.clientY + 14, innerHeight - r.height - 8)}px`;
});

// ------------------------------------------------------------------ theme
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  for (const cy of [state.cy, state.threadCy]) if (cy) cy.style(cy._styleFn());
}
$('#themeBtn').addEventListener('click', () => {
  const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const next = cur === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem('sla-theme', next); } catch { /* storage unavailable */ }
  applyTheme(next);
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => applyTheme(document.documentElement.dataset.theme));

// ------------------------------------------------------------------ state & navigation
const state = { version: 0, seen: {}, cy: null, threadCy: null, focus: null, routes: [], scan: null };

function showView(name) {
  $$('.tab').forEach((t) => t.setAttribute('aria-selected', String(t.dataset.view === name)));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
  try { localStorage.setItem('sla-view', name); } catch { /* ignore */ }
  ensure(name);
}
$$('.tab').forEach((t) => t.addEventListener('click', () => showView(t.dataset.view)));

function ensure(name) {
  if (state.seen[name] === state.version) return;
  state.seen[name] = state.version;
  ({ graph: loadGraph, watch: loadTopics, traction: loadPosts, bots: loadBotsView, linking: () => runScan(), regulars: loadRegulars,
    data: loadDataTab })[name]?.();
}
function dataChanged() {
  state.version++;
  refreshStatus(); refreshLists();
  const active = $('.tab[aria-selected="true"]').dataset.view;
  ensure(active);
}

async function refreshStatus() {
  try {
    const s = await api('/api/status');
    const c = s.counts;
    $('#stats').innerHTML = `<span><b>${fmtNum(c.accounts)}</b> accounts</span><span><b>${fmtNum(c.posts)}</b> posts</span>
      <span><b>${fmtNum(c.comments)}</b> comments</span><span><b>${c.platforms.length}</b> platform${c.platforms.length === 1 ? '' : 's'}</span>`;
    $('#apiMode').textContent = s.reddit.oauth ? 'Reddit API: OAuth configured' : 'Reddit API: no keys set (Reddit usually blocks keyless requests; see README)';
    $('#log').textContent = s.log.slice().reverse().join('\n') || 'No activity yet.';
    return s;
  } catch (e) { fail(e); }
}
async function refreshLists() {
  try {
    const [nodes, accounts, comms] = await Promise.all([api('/api/nodes?limit=400'), api('/api/accounts?limit=600'), api('/api/communities')]);
    $('#nodeList').innerHTML = nodes.map((n) => `<option value="${esc(n.id)}">${esc(n.label)} · ${n.kind}</option>`).join('');
    $('#accountList').innerHTML = accounts.map((a) => `<option value="${esc(a.id)}">${esc(a.label)} (${a.items})</option>`).join('');
    const opts = comms.filter((c) => c.items > 0).map((c) => `<option value="${esc(c.id)}">${esc(c.label)} (${fmtNum(c.items)})</option>`).join('');
    for (const [sel, first] of [['#postCommunity', 'All communities'], ['#regCommunity', 'Any community']]) {
      const cur = $(sel).value; $(sel).innerHTML = `<option value="">${first}</option>${opts}`; $(sel).value = cur;
    }
  } catch (e) { fail(e); }
}

// global click delegation
document.addEventListener('click', (e) => {
  const a = e.target.closest('[data-account]');
  if (a) { e.preventDefault(); openAccount(a.dataset.account); return; }
  const p = e.target.closest('[data-pair]');
  if (p) { const [x, y] = p.dataset.pair.split('|'); showView('linking'); showPair(x, y); }
});

// ------------------------------------------------------------------ chart primitives
function niceMax(v) { if (v <= 0) return 1; const p = 10 ** Math.floor(Math.log10(v)); const m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p; }
function topRounded(x, y, w, h, r = 4) {
  if (h <= 0) return '';
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** Vertical columns over time. points: [{x: minutes, y: count, tip}] */
function columnChart(points, { bucket = 5, marker = null, height = 190 } = {}) {
  const W = 640, H = height, L = 34, R = 8, T = 10, B = 24;
  const pw = W - L - R, ph = H - T - B, n = points.length || 1;
  const max = niceMax(Math.max(1, ...points.map((p) => p.y)));
  const bw = pw / n;
  const y = (v) => T + ph - (v / max) * ph;
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i, yy = y(v);
    g += `<line class="${i ? 'grid' : 'base'}" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/><text x="${L - 6}" y="${yy + 3}" text-anchor="end">${+v.toFixed(1)}</text>`;
  }
  const every = Math.max(1, Math.ceil(n / 8));
  points.forEach((p, i) => {
    const x = L + i * bw, h = (p.y / max) * ph;
    g += `<rect class="hit" x="${x}" y="${T}" width="${bw}" height="${ph}" data-tip="${esc(p.tip)}"/>`;
    g += `<path class="mark" d="${topRounded(x + 1, y(p.y), Math.max(1, bw - 2), h)}" fill="var(--s1)" pointer-events="none"/>`;
    if (i % every === 0) g += `<text class="axis-label" x="${x + bw / 2}" y="${H - 8}" text-anchor="middle">${esc(fmtMin(p.x))}</text>`;
  });
  if (marker != null && marker / bucket < n) {
    const mx = L + (marker / bucket) * bw;
    g += `<line x1="${mx}" x2="${mx}" y1="${T}" y2="${T + ph}" stroke="var(--ink-2)" stroke-dasharray="3 3"/><text x="${mx + 4}" y="${T + 10}">first ${marker} min</text>`;
  }
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img">${g}</svg></div>`;
}

/** Two-series line chart over 24 hours (share of each account's activity). */
function hoursChart(a, b, nameA, nameB) {
  const W = 640, H = 200, L = 36, R = 90, T = 12, B = 24, pw = W - L - R, ph = H - T - B;
  const norm = (arr) => { const s = arr.reduce((x, y) => x + y, 0) || 1; return arr.map((v) => v / s); };
  const A = norm(a), Bn = norm(b);
  const max = niceMax(Math.max(...A, ...Bn, 0.01));
  const x = (i) => L + (i / 23) * pw, y = (v) => T + ph - (v / max) * ph;
  let g = '';
  for (let i = 0; i <= 4; i++) { const v = (max / 4) * i; g += `<line class="${i ? 'grid' : 'base'}" x1="${L}" x2="${L + pw}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${fmtPct(v)}</text>`; }
  for (let h = 0; h < 24; h += 3) g += `<text class="axis-label" x="${x(h)}" y="${H - 8}" text-anchor="middle">${String(h).padStart(2, '0')}h</text>`;
  const line = (arr, color) => `<polyline fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" points="${arr.map((v, i) => `${x(i)},${y(v)}`).join(' ')}"/>`;
  g += line(A, 'var(--s1)') + line(Bn, 'var(--s2)');
  g += `<text x="${x(23) + 6}" y="${y(A[23]) + 4}" style="fill:var(--ink)">${esc(nameA.slice(0, 14))}</text><text x="${x(23) + 6}" y="${y(Bn[23]) + (Math.abs(y(A[23]) - y(Bn[23])) < 12 ? 16 : 4)}" style="fill:var(--ink)">${esc(nameB.slice(0, 14))}</text>`;
  for (let i = 0; i < 24; i++) {
    const t = `<b>${String(i).padStart(2, '0')}:00 UTC</b><br>${esc(nameA)}: <b>${fmtPct(A[i])}</b> (${a[i]})<br>${esc(nameB)}: <b>${fmtPct(Bn[i])}</b> (${b[i]})`;
    g += `<rect class="hit" x="${x(i) - pw / 48}" y="${T}" width="${pw / 24}" height="${ph}" data-tip="${esc(t)}"/>`;
  }
  return `<div class="legend"><span><i style="background:var(--s1)"></i>${esc(nameA)}</span><span><i style="background:var(--s2)"></i>${esc(nameB)}</span></div>
    <div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Activity by hour of day">${g}</svg></div>`;
}

/** Horizontal bars. rows: [{label, value, max?, text?, tip?, sub?, na?, html?}] */
function hbars(rows, { max = null, color = 'var(--s1)', fmt = fmtPct } = {}) {
  if (!rows.length) return '<div class="muted">Nothing to show.</div>';
  const m = max ?? Math.max(...rows.map((r) => r.value || 0), 1e-9);
  return rows.map((r) => `<div class="hbar${r.na ? ' na' : ''}" ${r.tip ? `data-tip="${esc(r.tip)}"` : ''}>
      <span class="lbl" title="${esc(r.label)}">${r.html || esc(r.label)}</span>
      <span class="track">${r.na ? '' : `<span class="fill" style="display:block;width:${Math.max(0, Math.min(100, (r.value / m) * 100))}%;background:${r.color || color}"></span>`}</span>
      <span class="val">${r.text ?? (r.na ? 'n/a' : fmt(r.value))}</span></div>${r.sub ? `<div class="sub" style="margin:-2px 0 6px">${esc(r.sub)}</div>` : ''}`).join('');
}

/** Two-part stacked horizontal bars (posts + comments). */
function stackedBars(rows, names, colors = ['var(--s1)', 'var(--s2)']) {
  const m = Math.max(...rows.map((r) => r.parts[0] + r.parts[1]), 1);
  return `<div class="legend">${names.map((n, i) => `<span><i style="background:${colors[i]}"></i>${esc(n)}</span>`).join('')}</div>` +
    rows.map((r) => `<div class="hbar" data-tip="${esc(`<b>${esc(r.label)}</b><br>${names[0]}: <b>${r.parts[0]}</b><br>${names[1]}: <b>${r.parts[1]}</b>${r.extra ? '<br>' + r.extra : ''}`)}">
      <span class="lbl">${r.html || esc(r.label)}</span>
      <span class="track" style="display:flex">${r.parts.map((p, i) => (p ? `<span class="fill seg" style="width:${(p / m) * 100}%;background:${colors[i]}"></span>` : '')).join('')}</span>
      <span class="val">${r.parts[0] + r.parts[1]}</span></div>`).join('');
}

/** Weekday x hour heatmap with a one-hue sequential ramp. */
function heatmap(matrix) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const max = Math.max(1, ...matrix.flat());
  let h = '<div class="heat"><span></span>' + Array.from({ length: 24 }, (_, i) => `<span class="h">${i % 3 === 0 ? i : ''}</span>`).join('');
  matrix.forEach((row, d) => {
    h += `<span>${days[d]}</span>`;
    row.forEach((v, hr) => {
      const step = v === 0 ? 0 : Math.min(7, 1 + Math.floor((v / max) * 6.99));
      h += `<div class="c" style="background:var(--seq-${step})" data-tip="${esc(`<b>${days[d]} ${String(hr).padStart(2, '0')}:00 UTC</b><br>${v} item${v === 1 ? '' : 's'}`)}"></div>`;
    });
  });
  return h + '</div><div class="sub" style="margin-top:4px">Hour of day (UTC). Darker = more activity.</div>';
}

function scoreBar(score, level) {
  const c = level === 'strong' ? 'var(--critical)' : level === 'moderate' ? 'var(--serious)' : 'var(--warning)';
  return `<div class="row" style="gap:6px;flex-wrap:nowrap"><span style="width:70px;height:8px;background:var(--surface-2);border-radius:4px;overflow:hidden;display:inline-block">
    <span style="display:block;height:100%;width:${score * 100}%;background:${level ? c : 'var(--muted)'}"></span></span><b style="font-variant-numeric:tabular-nums">${score.toFixed(2)}</b>
    ${level ? `<span class="chip ${level === 'strong' ? 'crit' : 'warn'}">${level}</span>` : ''}</div>`;
}

/** Status chip for a bot label (status colour + text, never colour alone). */
function botChip(label, score) {
  if (!label) return '<span class="muted">–</span>';
  const cls = label === 'likely automated' ? 'crit' : label === 'suspicious' ? 'warn' : label === 'likely human' ? 'ok' : '';
  return `<span class="chip ${cls}">${esc(label)}${score != null ? ` · ${Number(score).toFixed(2)}` : ''}</span>`;
}

/** Score over time (single series) with shaded vote-burst bands. points: [{min, score, comments, ratio}] */
function scoreChart(points, bursts = []) {
  const W = 640, H = 190, L = 44, R = 10, T = 10, B = 24, pw = W - L - R, ph = H - T - B;
  const maxX = Math.max(...points.map((p) => p.min), 1), maxY = niceMax(Math.max(...points.map((p) => p.score), 1));
  const x = (m) => L + (m / maxX) * pw, y = (v) => T + ph - (v / maxY) * ph;
  let g = '';
  for (const b of bursts) g += `<rect x="${x(b.fromMin)}" y="${T}" width="${Math.max(3, x(b.toMin) - x(b.fromMin))}" height="${ph}" fill="var(--s2)" opacity=".18"/>`;
  for (let i = 0; i <= 4; i++) { const v = (maxY / 4) * i; g += `<line class="${i ? 'grid' : 'base'}" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${fmtNum(Math.round(v))}</text>`; }
  for (let i = 0; i <= 6; i++) { const m = (maxX / 6) * i; g += `<text class="axis-label" x="${x(m)}" y="${H - 8}" text-anchor="middle">${esc(fmtMin(m))}</text>`; }
  g += `<polyline fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round" points="${points.map((p) => `${x(p.min)},${y(p.score)}`).join(' ')}"/>`;
  points.forEach((p, i) => {
    const x0 = i ? (x(points[i - 1].min) + x(p.min)) / 2 : L, x1 = i < points.length - 1 ? (x(p.min) + x(points[i + 1].min)) / 2 : W - R;
    g += `<rect class="hit" x="${x0}" y="${T}" width="${Math.max(1, x1 - x0)}" height="${ph}" data-tip="${esc(`<b>+${esc(fmtMin(p.min))}</b><br>score <b>${fmtNum(p.score)}</b> · ${p.comments ?? '–'} comments${p.ratio != null ? ` · ${fmtPct(p.ratio)} upvoted` : ''}`)}"/>`;
  });
  if (bursts.length) g += `<text x="${x(bursts[0].fromMin) + 4}" y="${T + 12}" style="fill:var(--ink)">vote burst</text>`;
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Score over time">${g}</svg></div>`;
}

// ------------------------------------------------------------------ 1. graph
const REL = {
  interactsWith: { color: '--s1', line: 'solid', arrow: 'triangle', label: 'replies to', family: 'Interaction' },
  mentions: { color: '--s1', line: 'dashed', arrow: 'vee', label: 'mentions (u/)', family: 'Interaction' },
  amplifies: { color: '--s2', line: 'solid', arrow: 'triangle', label: 'amplifies (early comments)', family: 'Coordination' },
  coSharesWith: { color: '--s2', line: 'dashed', arrow: 'none', label: 'co-shares links/text within minutes', family: 'Coordination' },
  memberOfNetwork: { color: '--s2', line: 'dotted', arrow: 'triangle', label: 'member of coordinated network', family: 'Coordination' },
  discloses: { color: '--s3', line: 'solid', arrow: 'triangle-backcurve', label: 'discloses profile', family: 'Identity' },
  possibleSameOperator: { color: '--s3', line: 'dashed', arrow: 'none', label: 'possible same operator', family: 'Identity' },
  sameAs: { color: '--s3', line: 'solid', arrow: 'none', label: 'same as (confirmed)', family: 'Identity', width: 5 },
  controlledBy: { color: '--s3', line: 'dotted', arrow: 'triangle', label: 'controlled by (operator)', family: 'Identity' },
  activeIn: { color: '--muted', line: 'solid', arrow: 'none', label: 'active in community', family: 'Membership' },
  linksTo: { color: '--muted', line: 'dotted', arrow: 'triangle', label: 'links to domain', family: 'Membership' },
};
const REL_DEFAULT_OFF = new Set(['linksTo']);
let ontologyInfo = null;

function buildRelToggles() {
  const fams = {};
  for (const [k, r] of Object.entries(REL)) (fams[r.family] ||= []).push(k);
  $('#relToggles').innerHTML = Object.entries(fams).map(([fam, keys]) => `<div style="margin-top:6px"><div class="sub" style="font-weight:600">${fam}</div>
    ${keys.map((k) => { const r = REL[k]; const dash = r.line === 'dashed' ? '6 4' : r.line === 'dotted' ? '1.5 3' : '';
      return `<label class="check"><input type="checkbox" data-rel="${k}" ${REL_DEFAULT_OFF.has(k) ? '' : 'checked'}>
        <svg class="legend-line" viewBox="0 0 30 10"><line x1="1" y1="5" x2="29" y2="5" stroke="var(${r.color})" stroke-width="${r.width ? 4 : 2.5}" stroke-dasharray="${dash}" stroke-linecap="round"/></svg>${esc(r.label)}</label>`; }).join('')}</div>`).join('');
  $$('#relToggles input').forEach((i) => i.addEventListener('change', () => loadGraph()));
}
const activeRels = () => $$('#relToggles input:checked').map((i) => i.dataset.rel);

function graphStyle() {
  const v = (n) => cssVar(n);
  const s = [
    { selector: 'node', style: { label: 'data(label)', 'font-size': 10, color: v('--ink'), 'text-outline-color': v('--surface'), 'text-outline-width': 2,
      'text-valign': 'bottom', 'text-margin-y': 3, 'min-zoomed-font-size': 7, 'background-color': v('--node'), width: 'data(size)', height: 'data(size)',
      'border-width': 0, 'overlay-opacity': 0 } },
    { selector: 'node[cls="ExternalAccount"]', style: { shape: 'hexagon' } },
    { selector: 'node[cls="ExternalAccount"][?hollow]', style: { 'background-color': v('--surface'), 'border-width': 2, 'border-color': v('--node') } },
    { selector: 'node[cls="Subreddit"], node[cls="Community"]', style: { shape: 'round-rectangle', 'background-color': v('--surface-2'), 'border-width': 1.5,
      'border-color': v('--axis'), 'font-weight': 600, 'text-valign': 'center', 'text-margin-y': 0, width: 'label', padding: 6, height: 22 } },
    { selector: 'node[cls="Domain"]', style: { shape: 'diamond', 'background-color': v('--surface'), 'border-width': 1.5, 'border-color': v('--muted') } },
    { selector: 'node[cls="Agent"]', style: { shape: 'star', 'background-color': v('--s3'), 'font-weight': 600 } },
    { selector: 'node[cls="CoordinatedNetwork"]', style: { shape: 'triangle', 'background-color': v('--s2'), 'font-weight': 600 } },
    { selector: 'node[botLabel="likely automated"]', style: { 'background-color': v('--critical') } },
    { selector: 'node[botLabel="suspicious"]', style: { 'background-color': v('--warning') } },
    { selector: 'node.cluster', style: { 'border-width': 3, 'border-color': v('--s3') } },
    { selector: 'node.focus', style: { 'border-width': 4, 'border-color': v('--ink') } },
    { selector: 'edge', style: { width: 'data(w)', 'curve-style': 'bezier', opacity: 0.7, 'arrow-scale': 0.8, 'line-color': v('--muted'), 'target-arrow-color': v('--muted') } },
  ];
  for (const [k, r] of Object.entries(REL)) {
    const c = v(r.color);
    s.push({ selector: `edge[type="${k}"]`, style: { 'line-color': c, 'target-arrow-color': c, 'line-style': r.line, 'target-arrow-shape': r.arrow,
      ...(r.line === 'dashed' ? { 'line-dash-pattern': [6, 4] } : {}), ...(r.width ? { width: r.width } : {}) } });
  }
  s.push(
    { selector: '.faded', style: { opacity: 0.08 } },
    { selector: 'edge.route', style: { width: 5, opacity: 1, 'z-index': 10 } },
    { selector: 'node.route', style: { 'border-width': 3, 'border-color': v('--ink'), 'z-index': 10, 'font-weight': 600 } },
    { selector: ':selected', style: { 'border-width': 3, 'border-color': v('--focus') } },
  );
  return s;
}

function prepElements(g) {
  const clusterMembers = new Set(g.edges.filter((e) => e.data.type === 'controlledBy').map((e) => e.data.source));
  const nodes = g.nodes.map((n) => {
    const d = n.data;
    let size = 14;
    if (d.cls === 'RedditAccount' || d.cls === 'ExternalAccount') size = 10 + 2.6 * Math.log2(1 + (d.items || 0));
    if (d.cls === 'Agent' || d.cls === 'CoordinatedNetwork') size = 34;
    if (d.cls === 'Domain') size = 16;
    return { data: { ...d, size, hollow: d.cls === 'ExternalAccount' && !d.collected }, classes: clusterMembers.has(d.id) ? 'cluster' : '' };
  });
  const edges = g.edges.map((e) => ({ data: { ...e.data, w: Math.min(6, 1 + Math.log2(1 + (e.data.weight || 1)) * 0.8) } }));
  return [...nodes, ...edges];
}

function makeCy(container, elements, styleFn) {
  const cy = cytoscape({ container, elements, style: styleFn(), wheelSensitivity: 0.25, minZoom: 0.1, maxZoom: 3 });
  cy._styleFn = styleFn;
  return cy;
}

function runLayout(cy, name = $('#layoutSel').value) {
  const opts = { name, animate: false, fit: true, padding: 30, stop: () => { if (cy.zoom() > 1.4) { cy.zoom(1.4); cy.center(); } } };
  if (name === 'cose') Object.assign(opts, { nodeRepulsion: () => 9000, idealEdgeLength: (e) => (e.data('type') === 'activeIn' ? 110 : 70), gravity: 0.25, numIter: 1500, randomize: true });
  if (name === 'concentric') Object.assign(opts, { concentric: (n) => n.degree(), levelWidth: () => 3, minNodeSpacing: 12 });
  if (name === 'breadthfirst') Object.assign(opts, { directed: false, spacingFactor: 1.1, roots: state.focus ? [state.focus] : undefined });
  cy.layout(opts).run();
}

async function loadGraph() {
  if (typeof cytoscape === 'undefined') { $('#cy').innerHTML = '<div class="empty">The graph library (Cytoscape from cdnjs) could not load. Check the internet connection.</div>'; return; }
  const params = new URLSearchParams({ edges: activeRels().join(','), min_weight: $('#minWeight').value, depth: $('#depthSel').value, max_nodes: $('#maxNodesSel').value });
  if (state.focus) params.set('focus', state.focus);
  $('#expTtl').href = `/api/export/graph.ttl?${params}`; $('#expJsonld').href = `/api/export/graph.jsonld?${params}`;
  try {
    const g = await api(`/api/graph?${params}`);
    if (state.cy) state.cy.destroy();
    state.cy = makeCy($('#cy'), prepElements(g), graphStyle);
    runLayout(state.cy);
    if (state.focus) state.cy.getElementById(state.focus).addClass('focus');
    const types = Object.entries(g.stats.edgeTypes).map(([k, n]) => `${n} ${REL[k]?.label || k}`).join(' · ');
    $('#graphMeta').textContent = g.nodes.length ? `${g.stats.nodes} nodes · ${g.stats.edges} links${types ? ' — ' + types : ''}` : '';
    if (!g.nodes.length) $('#graphMeta').textContent = 'No relations yet: load the demo or collect data in Data sources.';
    state.cy.on('tap', 'node', (e) => inspectNode(e.target));
    state.cy.on('tap', 'edge', (e) => inspectEdge(e.target));
    state.cy.on('tap', (e) => { if (e.target === state.cy) $('#inspector').classList.remove('open'); });
    state.cy.on('mouseover', 'edge', (e) => { const d = e.target.data(); tip.innerHTML = `<b>${esc(REL[d.type]?.label || d.type)}</b>${d.weight != null ? ` · weight <b>${d.weight}</b>` : ''}`; tip.style.display = 'block'; });
    state.cy.on('mouseout', 'edge', () => (tip.style.display = 'none'));
    if (state.routes.length && state.routeSel != null) highlightRoute(state.routeSel);
  } catch (e) { fail(e); }
}

function relCounts(node) {
  const c = {};
  node.connectedEdges().forEach((e) => { const t = e.data('type'); c[t] = (c[t] || 0) + 1; });
  return Object.entries(c).map(([t, n]) => `<span class="chip">${n} ${esc(REL[t]?.label || t)}</span>`).join('');
}

function inspectNode(n) {
  const d = n.data();
  const isAcct = d.cls === 'RedditAccount' || d.cls === 'ExternalAccount';
  const cls = ontologyInfo?.classes?.[d.cls];
  let kv = `<dt>Class</dt><dd>slo:${esc(d.cls)}${cls ? ` <span class="muted">(${esc(cls.label)})</span>` : ''}</dd><dt>Id</dt><dd><code>${esc(d.id)}</code></dd>`;
  if (d.platform) kv += `<dt>Platform</dt><dd>${esc(d.platform)}</dd>`;
  if (isAcct) kv += `<dt>Collected items</dt><dd>${fmtNum(d.items)}</dd><dt>Created</dt><dd>${d.created_utc ? `${fmtDay(d.created_utc)} (${fmtAge(d.created_utc)} ago)` : '–'}</dd><dt>Karma</dt><dd>${fmtNum(d.karma)}</dd>`;
  if (d.botLabel) kv += `<dt>Bot likelihood</dt><dd>${botChip(d.botLabel, d.botScore)}</dd>`;
  if (d.subscribers) kv += `<dt>Subscribers</dt><dd>${fmtNum(d.subscribers)}</dd>`;
  if (d.confidence != null) kv += `<dt>Mean score</dt><dd>${d.confidence}</dd>`;
  if (d.bio) kv += `<dt>Bio</dt><dd>${esc(d.bio)}</dd>`;
  const acts = [];
  if (isAcct && d.items) acts.push(`<button class="btn small" data-account="${esc(d.id)}">Profile</button>`, `<button class="btn small" data-act="similar">Similar accounts</button>`);
  acts.push(`<button class="btn small" data-act="focus">Focus here</button>`, `<button class="btn small" data-act="from">Route from</button>`, `<button class="btn small" data-act="to">Route to</button>`);
  if (d.cls === 'Subreddit' || d.cls === 'Community') acts.push(`<button class="btn small" data-act="regulars">Regular posters here</button>`);
  if (!d.items && isAcct && d.platform === 'reddit') acts.push(`<button class="btn small" data-act="collect">Collect this user</button>`);
  const box = $('#inspector');
  box.innerHTML = `<div class="card-head"><h3>${esc(d.label)}</h3><button class="icon-btn" data-act="close" aria-label="Close">✕</button></div>
    <dl class="kv">${kv}</dl><div style="margin:10px 0">${relCounts(n)}</div><div class="row">${acts.join('')}</div>`;
  box.classList.add('open');
  box.onclick = (e) => {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'close') box.classList.remove('open');
    if (act === 'focus') { state.focus = d.id; state.routeSel = null; $('#focusInput').value = d.id; loadGraph(); }
    if (act === 'from') $('#routeFrom').value = d.id;
    if (act === 'to') $('#routeTo').value = d.id;
    if (act === 'similar') { showView('linking'); $('#simInput').value = d.id; runSimilar(); }
    if (act === 'regulars') { showView('regulars'); $('#regCommunity').value = d.id; $('#regBasis').value = 'all'; $('#regMin').value = 5; loadRegulars(); }
    if (act === 'collect') { showView('data'); $('#cUser').value = d.id.split(':')[1]; }
  };
}

function inspectEdge(e) {
  const d = e.data();
  const r = ontologyInfo?.relations?.[d.type];
  const label = (id) => state.cy.getElementById(id).data('label') || id;
  let kv = `<dt>Relation</dt><dd>slo:${esc(d.type)}</dd><dt>From</dt><dd>${esc(label(d.source))}</dd><dt>To</dt><dd>${esc(label(d.target))}</dd>`;
  for (const k of ['weight', 'posts', 'comments', 'confidence', 'level', 'strength', 'evidence', 'note']) if (d[k] != null && d[k] !== '') kv += `<dt>${k}</dt><dd>${esc(d[k])}</dd>`;
  if (d.sources) kv += `<dt>sources</dt><dd>${esc(d.sources.join(', '))}</dd>`;
  const box = $('#inspector');
  box.innerHTML = `<div class="card-head"><h3>${esc(REL[d.type]?.label || d.type)}</h3><button class="icon-btn" data-act="close" aria-label="Close">✕</button></div>
    ${r?.desc ? `<p class="sub" style="margin-top:0">${esc(r.desc)}</p>` : ''}<dl class="kv">${kv}</dl>
    ${d.type === 'possibleSameOperator' ? `<div class="row" style="margin-top:10px"><button class="btn small" data-pair="${esc(d.source)}|${esc(d.target)}">Compare evidence</button></div>` : ''}`;
  box.classList.add('open');
  box.onclick = (ev) => { if (ev.target.closest('[data-act="close"]')) box.classList.remove('open'); };
}

$('#focusBtn').addEventListener('click', () => { state.focus = $('#focusInput').value.trim() || null; state.routeSel = null; loadGraph(); });
$('#allBtn').addEventListener('click', () => { state.focus = null; state.routeSel = null; $('#focusInput').value = ''; loadGraph(); });
$('#focusInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#focusBtn').click(); });
['#depthSel', '#maxNodesSel', '#minWeight'].forEach((s) => $(s).addEventListener('change', () => loadGraph()));
$('#layoutSel').addEventListener('change', () => state.cy && runLayout(state.cy));
$('#fitBtn').addEventListener('click', () => state.cy && state.cy.fit(undefined, 30));

// routes
$('#routeBtn').addEventListener('click', (e) => busy(e.currentTarget, findRoutes));
$('#routeClear').addEventListener('click', () => { state.routes = []; state.routeSel = null; $('#routes').innerHTML = ''; state.cy?.elements().removeClass('faded route'); });

async function findRoutes() {
  const s = $('#routeFrom').value.trim(), t = $('#routeTo').value.trim();
  if (!s || !t) return toast('Pick both ends of the route.');
  const params = new URLSearchParams({ source: s, target: t, k: 4, edges: [...activeRels(), 'linksTo', 'activeIn'].join(',') });
  const res = await api(`/api/route?${params}`);
  state.routes = res.routes; state.routeSel = null;
  if (!res.routes.length) { $('#routes').innerHTML = `<p class="sub">${esc(res.message)}</p>`; return; }
  // make sure every route element is on the canvas
  const cy = state.cy;
  const add = prepElements({ nodes: res.elements.nodes.filter((n) => !cy.getElementById(n.data.id).length), edges: [] });
  const addE = prepElements({ nodes: [], edges: res.elements.edges.filter((e) => !cy.getElementById(e.data.id).length) });
  if (add.length || addE.length) { cy.add(add); cy.add(addE); runLayout(cy); }
  $('#routes').innerHTML = `<p class="sub" style="margin:0 0 6px">${esc(res.message)}; lower cost = more direct relationship.</p>` + res.routes.map((r, i) => `
    <div class="route" data-route="${i}"><div class="row" style="justify-content:space-between"><b>Route ${i + 1}</b><span class="sub">${r.hops.length} hop${r.hops.length > 1 ? 's' : ''} · strength ${r.strength.toFixed(2)}</span></div>
    <ol class="hops">${r.hops.map((h) => `<li>${esc(label(cy, h.from))} ${h.relations.slice(0, 2).map((x) => `<span class="rel" style="color:var(${REL[x.type]?.color || '--muted'})">${esc(x.direction === '←' ? '← ' : '')}${esc(x.label)}${esc(x.direction === '→' ? ' →' : x.direction === '↔' ? ' ↔' : '')}</span>`).join(' ')} ${esc(label(cy, h.to))}</li>`).join('')}</ol></div>`).join('');
  $$('#routes .route').forEach((el) => el.addEventListener('click', () => highlightRoute(+el.dataset.route)));
  highlightRoute(0);
}
const label = (cy, id) => cy.getElementById(id).data('label') || id;

function highlightRoute(i) {
  const r = state.routes[i]; const cy = state.cy;
  if (!r || !cy) return;
  state.routeSel = i;
  $$('#routes .route').forEach((el) => el.classList.toggle('sel', +el.dataset.route === i));
  cy.elements().removeClass('route').addClass('faded');
  const els = cy.collection();
  r.nodes.forEach((n) => els.merge(cy.getElementById(n)));
  r.hops.forEach((h) => h.relations.forEach((x) => els.merge(cy.getElementById(x.edgeId))));
  els.removeClass('faded').addClass('route');
  cy.animate({ fit: { eles: els, padding: 80 } }, { duration: 300 });
}

// ------------------------------------------------------------------ 2. traction
const loadPosts = debounce(async () => {
  const q = new URLSearchParams({ q: $('#postSearch').value, community: $('#postCommunity').value, limit: 150 });
  try {
    const posts = await api(`/api/posts?${q}`);
    $('#postList').innerHTML = posts.length ? posts.map((p) => `<div class="item" data-post="${esc(p.id)}">
      <div class="t">${esc(p.title)}</div><div class="sub">${esc(p.author)} · ${esc(p.community)} · ${p.comments} comments${p.crossposts ? ` · ${p.crossposts} crossposts` : ''}</div></div>`).join('')
      : '<div class="empty">No posts yet.</div>';
    $$('#postList .item').forEach((el) => el.addEventListener('click', () => { $$('#postList .item').forEach((x) => x.classList.remove('sel')); el.classList.add('sel'); analyzePost(el.dataset.post); }));
  } catch (e) { fail(e); }
}, 200);
$('#postSearch').addEventListener('input', loadPosts);
$('#postCommunity').addEventListener('change', loadPosts);

function flagChips(flags) {
  return flags.map((f) => `<span class="chip ${f.startsWith('possible alt') ? 'id' : f === 'early' ? '' : 'co'}">${esc(f)}</span>`).join('');
}

async function analyzePost(pid) {
  const out = $('#tractionOut');
  out.innerHTML = '<div class="card empty"><span class="spinner"></span> Analysing…</div>';
  let r;
  try { r = await api(`/api/traction?post_id=${encodeURIComponent(pid)}`); } catch (e) { out.innerHTML = ''; return fail(e); }
  const m = r.metrics, p = r.post, t = r.targets;
  const lvlColor = r.amplificationLevel === 'high' ? 'var(--critical)' : r.amplificationLevel === 'elevated' ? 'var(--warning)' : 'var(--good)';
  const tl = r.timeline;
  const points = tl.buckets.map((b, i) => ({ x: b, y: tl.counts[i], tip: `<b>${fmtMin(b)}–${fmtMin(b + tl.bucketMinutes)}</b> after posting<br><b>${tl.counts[i]}</b> comments · ${tl.cumulative[i]} total so far` }));
  const engRows = r.engagers.map((e) => `<tr><td>${acct(e.account, e.label)}</td><td class="num">${fmtMin(e.firstDelayMin)}</td><td class="num">${e.comments}</td>
    <td class="num">${e.accountAgeDays == null ? '–' : Math.round(e.accountAgeDays) + ' d'}</td><td class="num">${fmtNum(e.karma)}</td><td class="num">${e.priorPostsOfAuthorEngaged}</td>
    <td>${botChip(e.botLabel, e.botScore)}</td><td>${flagChips(e.flags.filter((f) => !/automated|bot-like/.test(f)))}</td></tr>`).join('');
  const v = r.votes || { series: [], bursts: [] };
  const aud = (rows) => hbars(rows.map((a) => ({ label: a.community, value: a.share, tip: `<b>${esc(a.community)}</b><br>${a.engagers} engagers active there` })), { max: 1 });
  out.innerHTML = `
  <div class="card"><div class="card-head"><div><h2>${esc(p.title || '(untitled)')}</h2>
    <div class="sub">${acct(p.authorId, p.author)} ${r.authorBot?.items ? botChip(r.authorBot.label, r.authorBot.score) : ''}${r.authorNetwork ? ` <span class="chip co">${esc(r.authorNetwork.replace('network:', 'network #'))}</span>` : ''}
      in ${esc(p.community)} · ${fmtDate(p.created_utc)}${p.permalink ? ` · <a href="${esc(p.permalink)}" target="_blank" rel="noopener">open on Reddit</a>` : ''}</div></div>
    ${p.platform === 'reddit' ? `<button class="btn small" id="trackBtn">${r.tracked ? 'Score tracked ✓' : 'Track score every 5 min'}</button>` : ''}</div>
    ${p.body ? `<div class="quote">${esc(p.body.slice(0, 600))}</div>` : ''}${p.stub ? '<p class="note">Only a stub of this post is stored (seen via a comment). Collect the post itself for full analysis.</p>' : ''}</div>
  <div class="tiles" style="margin-top:12px">
    <div class="tile hero"><div class="k">Amplification index</div><div class="v">${r.amplificationIndex.toFixed(2)}</div>
      <div class="status-line"><span class="status-dot" style="background:${lvlColor}"></span>${esc(r.amplificationLevel)}</div></div>
    <div class="tile"><div class="k">Comments</div><div class="v">${fmtNum(m.comments)}</div><div class="d">${fmtNum(m.uniqueCommenters)} people · score ${fmtNum(m.score)}</div></div>
    <div class="tile"><div class="k">First comment</div><div class="v">${fmtMin(m.firstCommentMin)}</div><div class="d">half of comments by ${fmtMin(m.halfLifeMin)}</div></div>
    <div class="tile"><div class="k">In first 30 min</div><div class="v">${fmtPct(m.shareFirst30Min)}</div><div class="d">community median ${fmtPct(m.communityMedianShareFirst30Min)}</div></div>
    <div class="tile"><div class="k">Crossposts</div><div class="v">${r.crossposts.length}</div><div class="d">upvote ratio ${m.upvoteRatio ?? '–'}</div></div>
  </div>
  <div class="card" style="margin-top:12px"><h2 style="margin-bottom:8px">What happened</h2><ul class="narrative">${r.narrative.map((s) => `<li>${esc(s)}</li>`).join('')}</ul></div>
  <div class="grid2" style="margin-top:12px">
    <div class="card"><div class="card-head"><h2>Comments over time</h2><span class="sub">per ${tl.bucketMinutes}-minute bucket</span></div>
      ${points.length ? columnChart(points, { bucket: tl.bucketMinutes, marker: 30 }) : '<div class="empty">No timed comments.</div>'}</div>
    <div class="card"><div class="card-head"><h2>Amplification signals</h2><span class="sub">0 = organic, 1 = strong signal</span></div>
      ${hbars(r.signals.map((s) => ({ label: s.name, value: s.value, text: s.value.toFixed(2), sub: s.detail, tip: esc(s.detail) })), { max: 1, color: 'var(--s2)' })}</div>
  </div>
  <div class="card" style="margin-top:12px"><div class="card-head"><h2>Score over time</h2>
    <span class="sub">${v.series.length ? `${v.series.length} snapshots · shaded = points arriving far faster than discussion` : 'no snapshots yet'}</span></div>
    ${v.series.length >= 3 ? scoreChart(v.series, v.bursts) + (v.bursts.length ? `<div class="sub">${v.bursts.map((b) => `+${fmtNum(b.scoreGain)} points and ${b.commentGain} comments between ${fmtMin(b.fromMin)} and ${fmtMin(b.toMin)} (${b.pointsPerMin}/min vs a typical ${v.medianPointsPerMin}/min)`).join('<br>')}</div>` : '')
      : `<p class="note" style="margin:0">Reddit does not reveal who voted, so vote manipulation shows up only as sudden score jumps. Click <b>Track score</b> (needs API keys) to record the score every 5 minutes for 48 hours.</p>`}
    ${r.coPushers.length ? `<div style="margin-top:8px"><h4>Same link pushed within minutes by</h4>${r.coPushers.map((x) => `<span class="chip co">${acct(x.account, x.label)}</span>`).join('')}</div>` : ''}</div>
  <div class="card" style="margin-top:12px"><div class="card-head"><h2>Who engaged</h2><span class="sub">ordered by first comment</span></div>
    <div class="table-scroll"><table><thead><tr><th>Account</th><th class="num">First reply</th><th class="num">Comments</th><th class="num">Account age</th><th class="num">Karma</th>
    <th class="num" title="Other posts by this author they commented on">Prior posts</th><th>Bot likelihood</th><th>Flags</th></tr></thead><tbody>${engRows || '<tr><td colspan="8" class="muted">No commenters.</td></tr>'}</tbody></table></div></div>
  <div class="grid2" style="margin-top:12px">
    <div class="card stack"><h2>Who it targets</h2>
      <div><h4>Communities</h4><div>${[`<span class="chip">${esc(t.homeCommunity.label)} · home${t.homeCommunity.subscribers ? ' · ' + fmtNum(t.homeCommunity.subscribers) : ''}</span>`,
        ...t.crosspostCommunities.map((x) => `<span class="chip co">${esc(x.label)} · crossposted by ${esc(x.by)}${x.subscribers ? ' · ' + fmtNum(x.subscribers) : ''}</span>`)].join('')}</div></div>
      ${t.mentionedUsers.length || t.mentionedCommunities.length ? `<div><h4>Explicitly addressed</h4>${[...t.mentionedUsers, ...t.mentionedCommunities].map((x) => `<span class="chip">${esc(x)}</span>`).join('')}</div>` : ''}
      ${t.linkedDomains.length ? `<div><h4>Drives traffic to</h4>${t.linkedDomains.map((x) => `<span class="chip">${esc(x)}</span>`).join('')}</div>` : ''}
      <div><h4>Distinctive terms</h4>${t.keywords.map((x) => `<span class="chip">${esc(x)}</span>`).join('')}</div>
      ${t.authorRepliedTo.length ? `<div><h4>Author replied to</h4>${t.authorRepliedTo.map((x) => `<div class="quote"><b>${esc(x.to)}</b>${x.theirStance < 0 ? ' <span class="chip warn">critical</span>' : ''}: ${esc(x.theirText)}<br>↳ ${esc(x.reply)}</div>`).join('')}</div>` : ''}
    </div>
    <div class="card stack"><div class="card-head"><h2>Audience reached</h2><span class="sub">other communities of engagers (${t.engagersWithHistory} with history)</span></div>
      <div><h4>Organic engagers</h4>${aud(t.audienceOrganic)}</div>
      <div><h4>Flagged engagers (new, low-karma or linked)</h4>${aud(t.audienceSuspicious)}</div>
      ${t.engagersWithHistory === 0 ? '<p class="note">Collect commenter histories (Data sources → post, "Commenter histories") to see where the audience comes from.</p>' : ''}
    </div>
  </div>
  <div class="card" style="margin-top:12px"><div class="card-head"><h2>Thread map</h2>
    <div class="legend" style="margin:0"><span><i style="background:var(--ink)"></i>post / author</span><span><i style="background:var(--critical)"></i>bot-like or coordinated</span>
    <span><i style="background:var(--s3)"></i>possible alt of author</span><span><i style="background:var(--s2)"></i>new or low-karma</span><span><i style="background:var(--muted)"></i>other</span><span><i style="background:var(--s1)"></i>crosspost</span></div></div>
    <div class="mini-graph"><div id="threadCy"></div></div><p class="sub">Edges point from a reply to what it replies to. Hover a node to read the comment.</p></div>
  ${r.drivers.length ? `<div class="card" style="margin-top:12px"><h2 style="margin-bottom:8px">Comments that drove the discussion</h2>${r.drivers.map((d) => `<div class="quote"><b>${acct(d.account, d.label)}</b> · ${d.replies} replies · posted after ${fmtMin(d.delayMin)}<br>${esc(d.text)}</div>`).join('')}</div>` : ''}`;
  renderThread(r.thread, r.post.id);
  $('#trackBtn')?.addEventListener('click', async (e) => {
    try { await post('/api/watch/posts', { post_id: pid, hours: 48, interval_min: 5 }); e.target.textContent = 'Score tracked ✓'; toast('Score will be recorded every 5 minutes for 48 hours (needs Reddit API keys).'); } catch (err) { fail(err); }
  });
}

function threadStyle() {
  const v = (n) => cssVar(n);
  return [
    { selector: 'node', style: { 'background-color': v('--muted'), width: 12, height: 12, label: '', 'border-width': 0 } },
    { selector: 'node[kind="post"]', style: { 'background-color': v('--ink'), width: 26, height: 26, shape: 'round-rectangle', label: 'data(label)', 'font-size': 10, color: v('--ink'), 'text-valign': 'bottom', 'text-outline-color': v('--surface'), 'text-outline-width': 2 } },
    { selector: 'node[kind="author"]', style: { 'background-color': v('--ink') } },
    { selector: 'node[kind="crosspost"]', style: { 'background-color': v('--s1'), shape: 'round-rectangle', width: 20, height: 20, label: 'data(label)', 'font-size': 10, color: v('--ink'), 'text-valign': 'bottom', 'text-outline-color': v('--surface'), 'text-outline-width': 2 } },
    { selector: 'node.susp', style: { 'background-color': v('--s2') } },
    { selector: 'node.alt', style: { 'background-color': v('--s3'), width: 16, height: 16 } },
    { selector: 'node.bot', style: { 'background-color': v('--critical'), width: 16, height: 16 } },
    { selector: 'edge', style: { width: 1.2, 'line-color': v('--axis'), 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'target-arrow-color': v('--axis'), 'arrow-scale': 0.6 } },
    { selector: 'edge[type="crosspostedTo"]', style: { 'line-color': v('--s1'), 'target-arrow-color': v('--s1'), 'line-style': 'dashed', width: 2 } },
  ];
}

function renderThread(elements, root) {
  if (typeof cytoscape === 'undefined') return;
  if (state.threadCy) state.threadCy.destroy();
  const els = elements.map((e) => {
    const f = e.data.flags || [];
    const cls = f.some((x) => /automated|bot-like|coordinated/.test(x)) ? 'bot' : f.some((x) => x.startsWith('possible alt')) ? 'alt'
      : f.some((x) => x === 'new account' || x === 'low karma') ? 'susp' : '';
    return { ...e, classes: cls };
  });
  const cy = state.threadCy = makeCy($('#threadCy'), els, threadStyle);
  cy.layout({ name: 'breadthfirst', roots: [root], directed: false, spacingFactor: 0.9, animate: false, padding: 16 }).run();
  cy.on('mouseover', 'node', (e) => {
    const d = e.target.data();
    tip.innerHTML = d.kind === 'post' ? `<b>${esc(d.label)}</b>` : `<b>${esc(d.label)}</b>${d.delayMin != null ? ` · +${esc(fmtMin(d.delayMin))}` : ''}${(d.flags || []).length ? `<br>${esc(d.flags.join(', '))}` : ''}${d.text ? `<br>${esc(d.text)}` : ''}`;
    tip.style.display = 'block';
  });
  cy.on('mouseout', 'node', () => (tip.style.display = 'none'));
  cy.on('tap', 'node', (e) => { const a = e.target.data('account'); if (a) openAccount(a); });
}

// ------------------------------------------------------------------ 3. multi-account linking
$('#scanBtn').addEventListener('click', (e) => busy(e.currentTarget, runScan));
$('#simBtn').addEventListener('click', (e) => busy(e.currentTarget, runSimilar));
$('#simInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#simBtn').click(); });

function pairRows(pairs, labelA = (p) => p.labelA) {
  if (!pairs.length) return '<div class="empty">No pairs above this score.</div>';
  return `<table><thead><tr><th>Account A</th><th>Account B</th><th>Score</th><th>Top signals</th></tr></thead><tbody>${pairs.map((p) => `
    <tr class="click" data-pair="${esc(p.a)}|${esc(p.b)}"><td>${esc(labelA(p))}</td><td>${esc(p.labelB)}${p.crossPlatform ? ' <span class="chip id">cross-platform</span>' : ''}${p.confirmed ? ' <span class="chip ok">confirmed</span>' : ''}</td>
    <td>${scoreBar(p.score, p.level)}</td><td class="sub">${esc((p.topSignals || []).join(' · '))}</td></tr>`).join('')}</tbody></table>`;
}

async function runScan() {
  try {
    const q = new URLSearchParams({ min_score: $('#minScore').value, cross_platform: $('#crossOnly').checked });
    const s = state.scan = await api(`/api/linking/scan?${q}`);
    $('#pairsTitle').textContent = 'Linked pairs';
    $('#pairsSub').textContent = `${s.pairs.length} pairs · ${fmtNum(s.pairsCompared)} compared among ${s.profiles} accounts with 3+ items`;
    $('#pairs').innerHTML = pairRows(s.pairs);
    const names = {};
    s.pairs.forEach((p) => { names[p.a] = p.labelA; names[p.b] = p.labelB; });
    $('#clusters').className = '';
    $('#clusters').innerHTML = s.clusters.length ? s.clusters.map((c) => `<div class="quote" style="border-left-color:var(--s3)">
      <div class="row" style="justify-content:space-between"><b>${esc(c.label)}</b><span class="sub">mean pair score ${c.score.toFixed(2)} · ${esc(c.platforms.join(', '))}</span></div>
      <div style="margin-top:4px">${c.members.map((m) => `<span class="chip id">${acct(m, names[m] || m)}</span>`).join('')}</div>
      <div class="row" style="margin-top:6px"><button class="btn small" data-graph-focus="${esc(c.id)}">Show in graph</button></div></div>`).join('')
      : '<div class="empty">No account group passes the strong threshold.</div>';
    $$('[data-graph-focus]').forEach((b) => b.addEventListener('click', () => { state.focus = b.dataset.graphFocus; state.routeSel = null; $('#focusInput').value = state.focus; $('#depthSel').value = '1'; showView('graph'); loadGraph(); }));
  } catch (e) { fail(e); }
}

async function runSimilar() {
  const id = $('#simInput').value.trim();
  if (!id) return toast('Enter an account id such as reddit:username');
  const r = await api(`/api/linking/account?account=${encodeURIComponent(id)}&top=20`);
  $('#pairsTitle').textContent = `Most similar to ${id}`;
  $('#pairsSub').textContent = r.message || 'all platforms, highest score first';
  $('#pairs').innerHTML = r.matches.length ? pairRows(r.matches, () => id) : `<div class="empty">${esc(r.message || 'No matches.')}</div>`;
}

async function showPair(a, b) {
  const out = $('#pairDetail');
  out.innerHTML = '<div class="card empty"><span class="spinner"></span> Comparing…</div>';
  let d;
  try { d = await api(`/api/linking/pair?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`); } catch (e) { out.innerHTML = ''; return fail(e); }
  $$('#pairs tr.click').forEach((tr) => tr.classList.toggle('sel', tr.dataset.pair === `${a}|${b}`));
  const L = d.labels;
  const feats = Object.entries(d.features).map(([k, f]) => ({
    label: f.label, value: f.evidence ?? 0, na: !f.applicable, text: f.applicable ? f.evidence.toFixed(2) : 'n/a',
    tip: `<b>${esc(f.label)}</b><br>evidence ${f.evidence ?? 'n/a'} · weight ${esc(f.weight)}<br>raw ${esc(typeof f.raw === 'number' ? f.raw : f.raw ?? '–')}${f.applicable ? '' : '<br>not applicable (other platform or too little data)'}`,
  }));
  const disc = d.features.disclosure;
  const inter = d.interactions;
  out.innerHTML = `<div class="card stack">
    <div class="card-head"><div><h2>${acct(a, L.a)} ↔ ${acct(b, L.b)}</h2><div class="sub">${d.crossPlatform ? 'cross-platform · ' : ''}data sufficiency ${fmtPct(d.dataSufficiency)} · ${d.items.a} vs ${d.items.b} items</div></div>
      ${scoreBar(d.score, d.level)}</div>
    <div class="row"><button class="btn small" id="confirmLink">Confirm as same person (sameAs)</button><button class="btn small" id="routePair">Show route in graph</button></div>
    ${disc.applicable ? `<p class="note" style="margin:0"><b>Cross-link:</b> ${esc(disc.raw)}</p>` : ''}
    <div><h4>Evidence by signal</h4>${hbars(feats, { max: 1, color: 'var(--ink-2)' })}</div>
    <div><h4>Active hours (share of each account's activity, UTC)</h4>${hoursChart(d.hours.a, d.hours.b, L.a, L.b)}</div>
    <div><h4>Shared rare phrases &amp; spellings</h4>${d.sharedPhrases.length ? d.sharedPhrases.map((s) => `<span class="chip" data-tip="${esc(`used ${s.countA}× by A, ${s.countB}× by B; ${s.accountsUsing} accounts in the dataset use it`)}">${esc(s.text)} <span class="muted">${s.countA}/${s.countB}</span></span>`).join('') : '<span class="muted">None.</span>'}</div>
    <div class="table-scroll"><h4>Typing habits</h4><table><thead><tr><th>Habit</th><th class="num">${esc(L.a)}</th><th class="num">${esc(L.b)}</th></tr></thead><tbody>
      ${d.habits.map((h) => `<tr><td>${esc(h.name)}</td><td class="num">${h.a}</td><td class="num">${h.b}</td></tr>`).join('')}</tbody></table></div>
    <div class="grid2"><div><h4>Shared communities</h4>${d.sharedCommunities.length ? d.sharedCommunities.map((c) => `<span class="chip">${esc(c.label)} <span class="muted">${c.a}/${c.b}</span></span>`).join('') : '<span class="muted">None.</span>'}</div>
      <div><h4>Same threads (${d.sharedThreadCount})</h4>${d.sharedThreads.slice(0, 6).map((t) => `<div class="sub">• ${esc(t.title.slice(0, 70))} <span class="muted">${esc(t.community)}</span></div>`).join('') || '<span class="muted">None.</span>'}</div></div>
    <div><h4>Interactions</h4><div class="sub">${inter.counts.replies || 0} replies between them · ${inter.counts.early_comments || 0} comments within 30 min of the other's posts</div>
      ${inter.examples.map((x) => `<div class="quote"><b>${esc(x.from)} → ${esc(x.to)}</b> · ${fmtDate(x.created_utc)}<br>${esc(x.text)}</div>`).join('')}</div>
    ${d.sharedDomains.length ? `<div><h4>Shared uncommon domains</h4>${d.sharedDomains.map((x) => `<span class="chip">${esc(x)}</span>`).join('')}</div>` : ''}
    <div class="grid2"><div><h4>${esc(L.a)} · created ${fmtDay(d.created.a)}</h4>${d.samples.a.map((s) => `<div class="quote">${esc(s)}</div>`).join('')}</div>
      <div><h4>${esc(L.b)} · created ${fmtDay(d.created.b)}</h4>${d.samples.b.map((s) => `<div class="quote">${esc(s)}</div>`).join('')}</div></div>
  </div>`;
  $('#confirmLink').addEventListener('click', async () => {
    const note = prompt('How did you verify that these accounts belong to the same person?', '');
    if (note === null) return;
    try { await post('/api/links', { a, b, relation: 'sameAs', note }); toast('Saved as a confirmed sameAs link.'); dataChanged(); } catch (e) { fail(e); }
  });
  $('#routePair').addEventListener('click', () => { $('#routeFrom').value = a; $('#routeTo').value = b; showView('graph'); setTimeout(() => $('#routeBtn').click(), 400); });
}

// ------------------------------------------------------------------ 4. regular posters
$('#regBtn').addEventListener('click', (e) => busy(e.currentTarget, loadRegulars));
async function loadRegulars() {
  const q = new URLSearchParams({ community: $('#regCommunity').value, basis: $('#regBasis').value, min_items: $('#regMin').value || 8, min_regularity: $('#regScore').value });
  try {
    const r = await api(`/api/activity/regulars?${q}`);
    $('#regSub').textContent = `${r.posters.length} accounts${r.community ? ` posting regularly in ${r.community}` : ''}`;
    $('#regTable').innerHTML = r.posters.length ? `<table><thead><tr><th>Account</th><th>Pattern</th><th class="num">Regularity</th><th class="num">Per day</th>
      <th class="num" title="Share of days with activity">Active days</th><th class="num">Peak hour</th><th>Home</th><th>Also active in</th></tr></thead><tbody>
      ${r.posters.map((p) => `<tr class="click" data-account="${esc(p.account)}"><td><span class="link">${esc(p.label)}</span>${p.external.length ? `<div class="sub">${p.external.map(esc).join(', ')}</div>` : ''}</td>
        <td><span class="chip ${p.pattern.startsWith('scheduled') ? 'warn' : ''}" title="${esc(p.pattern)}">${esc(p.pattern.split(' (')[0])}</span><div>${botChip(p.botLabel, p.botScore)}</div>${p.network ? `<span class="chip co">${esc(p.network.replace('network:', 'network #'))}</span>` : ''}</td>
        <td class="num">${p.regularity.toFixed(2)}</td><td class="num">${p.perDay}</td><td class="num">${fmtPct(p.activeDayRatio)}</td><td class="num">${String(p.peakHourUTC).padStart(2, '0')}:00</td>
        <td>${esc(p.home || '–')}</td><td>${p.otherCommunities.slice(0, 4).map((o) => `<span class="chip" data-tip="${esc(`${o.total} items · ${fmtPct(o.share)} of activity`)}">${esc(o.label)}</span>`).join('')}${p.otherCount > 4 ? `<span class="muted">+${p.otherCount - 4}</span>` : ''}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">No account meets these thresholds. Lower "Min items" or "Min regularity".</div>';
    $('#regShared').innerHTML = hbars(r.sharedOtherCommunities.map((c) => ({ label: c.label, value: c.share, tip: `<b>${esc(c.label)}</b><br>${c.regulars} of the listed regulars are active here` })), { max: 1 });
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ account drawer
function botPanel(b, network) {
  if (!b) return '';
  const sigs = b.signals.filter((s) => s.evidence != null).sort((x, y) => y.cap * y.evidence - x.cap * x.evidence);
  const na = b.signals.filter((s) => s.evidence == null);
  return `<div class="card"><div class="card-head"><h4>Bot likelihood</h4>${botChip(b.label, b.score)}</div>
    <div class="sub" style="margin-bottom:6px">Based on ${b.items} collected items (confidence ${fmtPct(b.confidence)}). Bar = how strongly each behaviour is present; hover for details.</div>
    ${hbars(sigs.map((s) => ({ label: s.label, value: s.evidence, text: s.evidence.toFixed(2), tip: `<b>${esc(s.label)}</b><br>${esc(s.detail)}<br>can add up to ${s.cap}` ,
      color: s.evidence >= 0.5 ? 'var(--critical)' : 'var(--ink-2)' })), { max: 1 })}
    ${na.length ? `<div class="sub" style="margin-top:6px">Not enough data for: ${na.map((s) => esc(s.label.toLowerCase())).join('; ')}.</div>` : ''}
    ${network ? `<p class="note" style="margin:8px 0 0"><b>${esc(network.label)}</b>: shared the same links/texts as other accounts within minutes ${network.coSharedItems} times (median gap ${network.medianDelaySec ?? '–'} s).</p>` : ''}
  </div>`;
}

async function openAccount(id) {
  const dr = $('#drawer');
  dr.classList.add('open');
  dr.innerHTML = '<div class="empty"><span class="spinner"></span> Loading profile…</div>';
  let p;
  try { p = await api(`/api/activity/account?account=${encodeURIComponent(id)}`); } catch (e) { dr.innerHTML = `<div class="drawer-head"><h2>${esc(id)}</h2><button class="icon-btn" id="drClose">✕</button></div><div class="empty">${esc(e.message)}</div>`; $('#drClose').onclick = () => dr.classList.remove('open'); return; }
  const i = p.info || {}, cp = p.cadencePosts, ca = p.cadenceAll;
  const cad = (c, title) => (c ? `<div><h4>${title}</h4><dl class="kv"><dt>Pattern</dt><dd>${esc(c.pattern)}</dd><dt>Regularity</dt><dd>${c.regularity}</dd><dt>Per day</dt><dd>${c.perDay}</dd>
    <dt>Active days</dt><dd>${c.activeDays} of ${Math.ceil(c.spanDays)} (${fmtPct(c.activeDayRatio)})</dd><dt>Longest streak</dt><dd>${c.longestStreakDays} days</dd>
    <dt>Median gap</dt><dd>${c.medianGapHours} h</dd><dt>Peak hour</dt><dd>${String(c.peakHourUTC).padStart(2, '0')}:00 UTC</dd><dt>Window</dt><dd>${fmtDay(c.first)} → ${fmtDay(c.last)}</dd></dl></div>` : '');
  dr.innerHTML = `<div class="drawer-head"><div><h2>${esc(p.label)}</h2><div class="sub">${esc(p.platform)} account${i.status ? ' · ' + esc(i.status) : ''}${i.source ? ' · via ' + esc(i.source) : ''}</div></div>
      <button class="icon-btn" id="drClose" aria-label="Close">✕</button></div>
    <div class="stack">
      <div class="card"><dl class="kv"><dt>Created</dt><dd>${i.created_utc ? `${fmtDay(i.created_utc)} (${fmtAge(i.created_utc)} old)` : '–'}</dd>
        <dt>Karma</dt><dd>${i.link_karma != null ? `${fmtNum(i.link_karma)} post · ${fmtNum(i.comment_karma)} comment` : '–'}</dd>${i.bio ? `<dt>Bio</dt><dd>${esc(i.bio)}</dd>` : ''}</dl>
        <div class="row" style="margin-top:10px"><button class="btn small" id="drFocus">Focus in graph</button><button class="btn small" id="drSimilar">Similar accounts</button></div></div>
      ${botPanel(p.bot, p.network)}
      ${p.external.length ? `<div class="card"><h4>Profiles on other platforms</h4><div style="margin-top:6px">${p.external.map((x) => `<span class="chip id" data-tip="${esc(`found in ${x.source}${x.hits > 1 ? ` (${x.hits}×)` : ''}<br>${esc(x.evidence || '')}`)}">${esc(x.platform)}:@${esc(x.handle)} <span class="muted">${esc(x.source)}</span></span>`).join('')}</div></div>` : ''}
      <div class="tiles"><div class="tile"><div class="k">Posts</div><div class="v">${fmtNum(p.counts.posts)}</div></div><div class="tile"><div class="k">Comments</div><div class="v">${fmtNum(p.counts.comments)}</div></div>
        <div class="tile"><div class="k">Communities</div><div class="v">${p.communities.length}</div><div class="d">home: ${esc(p.home || '–')}</div></div></div>
      <div class="card"><h4>Where they are active</h4>${stackedBars(p.communities.slice(0, 15).map((c) => ({ label: c.label, parts: [c.posts, c.comments], extra: `${fmtPct(c.share)} of activity · ${fmtDay(c.first)} → ${fmtDay(c.last)}` })), ['posts', 'comments'])}</div>
      <div class="card"><h4>When (weekday × hour)</h4>${heatmap(p.heatmap)}</div>
      <div class="card grid2">${cad(cp, 'Posting cadence') || '<div class="sub">Fewer than 2 posts.</div>'}${cad(ca, 'All activity')}</div>
      ${p.similarAccounts.length ? `<div class="card"><h4>Possibly the same operator</h4>${p.similarAccounts.map((s) => `<div class="row" style="justify-content:space-between;padding:4px 0">
        <a class="link" data-pair="${esc(p.account)}|${esc(s.b)}">${esc(s.labelB)}</a>${scoreBar(s.score, s.level)}</div>`).join('')}</div>` : ''}
      <div class="card grid2"><div><h4>Replies to</h4>${p.repliesTo.map((x) => `<div class="sub">${acct(x.account, x.label)} · ${x.count}</div>`).join('') || '<span class="muted">–</span>'}</div>
        <div><h4>Replied to by</h4>${p.repliedBy.map((x) => `<div class="sub">${acct(x.account, x.label)} · ${x.count}</div>`).join('') || '<span class="muted">–</span>'}</div></div>
      <div class="card"><h4>Recent activity</h4>${p.recent.map((x) => `<div class="quote"><span class="muted">${x.kind} · ${esc(x.community)} · ${fmtDate(x.created_utc)}</span><br>${esc(x.text)}</div>`).join('') || '<span class="muted">Nothing collected.</span>'}</div>
    </div>`;
  $('#drClose').onclick = () => dr.classList.remove('open');
  $('#drFocus').onclick = () => { dr.classList.remove('open'); state.focus = id; state.routeSel = null; $('#focusInput').value = id; showView('graph'); loadGraph(); };
  $('#drSimilar').onclick = () => { dr.classList.remove('open'); showView('linking'); $('#simInput').value = id; runSimilar().catch(fail); };
}
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('#drawer').classList.remove('open'); });

// ------------------------------------------------------------------ watchlist
state.topicSel = null;
async function loadTopics() {
  try {
    const [topics, watched] = await Promise.all([api('/api/watch/topics'), api('/api/watch/posts')]);
    $('#topicList').innerHTML = topics.length ? topics.map((t) => `<div class="item${t.id === state.topicSel ? ' sel' : ''}" data-topic="${t.id}">
      <div class="row" style="justify-content:space-between"><b>${esc(t.name)}</b><span class="chip ${t.active ? 'ok' : ''}">${t.active ? `every ${t.interval_min} min` : 'paused'}</span></div>
      <div class="sub">${esc([t.keywords, t.domains, t.communities].filter(Boolean).join(' · ').slice(0, 120))}</div>
      ${t.last_status ? `<div class="sub muted">last run ${t.last_run_utc ? fmtDate(t.last_run_utc) : ''}: ${esc(t.last_status)}</div>` : ''}</div>`).join('')
      : '<p class="sub">No topics yet.</p>';
    $$('#topicList .item').forEach((el) => el.addEventListener('click', () => showTopic(+el.dataset.topic)));
    $('#watchedPosts').innerHTML = watched.length ? watched.slice(0, 30).map((w) => `<div class="row" style="justify-content:space-between;padding:3px 0">
      <a class="link" data-open-post="${esc(w.post_id)}">${esc((w.title || w.post_id).slice(0, 48))}</a><span class="muted">${w.snapshots} pts ${w.until_utc > Date.now() / 1000 ? '' : '· ended'}</span></div>`).join('') : 'None.';
    $$('[data-open-post]').forEach((a) => a.addEventListener('click', () => openPost(a.dataset.openPost)));
    if (!state.topicSel && topics.length) showTopic(topics[0].id);
    state.topics = topics;
  } catch (e) { fail(e); }
}

function openPost(pid) { showView('traction'); analyzePost(pid); }

function editTopic(t) {
  $('#topicForm').hidden = false;
  $('#topicFormTitle').textContent = t ? `Edit “${t.name}”` : 'New topic';
  $('#tId').value = t?.id || ''; $('#tName').value = t?.name || ''; $('#tKeywords').value = t?.keywords || '';
  $('#tDomains').value = t?.domains || ''; $('#tCommunities').value = t?.communities || '';
  $('#tInterval').value = String(t?.interval_min || 30); $('#tActive').checked = t ? !!t.active : true;
  $('#tName').focus();
}
$('#topicNew').addEventListener('click', () => editTopic(null));
$('#tCancel').addEventListener('click', () => ($('#topicForm').hidden = true));
$('#tSave').addEventListener('click', async () => {
  try {
    const t = await post('/api/watch/topics', { id: $('#tId').value || null, name: $('#tName').value.trim(), keywords: $('#tKeywords').value,
      domains: $('#tDomains').value, communities: $('#tCommunities').value, interval_min: +$('#tInterval').value, active: $('#tActive').checked });
    $('#topicForm').hidden = true; state.topicSel = t.id; await loadTopics(); showTopic(t.id);
  } catch (e) { fail(e); }
});

async function showTopic(id) {
  state.topicSel = id;
  $$('#topicList .item').forEach((el) => el.classList.toggle('sel', +el.dataset.topic === id));
  const out = $('#watchOut');
  out.innerHTML = '<div class="card empty"><span class="spinner"></span> Analysing matching posts…</div>';
  let r;
  try { r = await api(`/api/watch/topics/${id}/report`); } catch (e) { out.innerHTML = ''; return fail(e); }
  const s = r.summary, t = r.topic;
  const lvl = (x) => (x >= 0.5 ? 'var(--critical)' : x >= 0.3 ? 'var(--warning)' : 'var(--good)');
  const tlPoints = r.timeline.map((d, i) => ({ x: i, y: d.posts, tip: `<b>${d.day}</b><br>${d.posts} posts · ${d.comments} comments` }));
  out.innerHTML = `<div class="card"><div class="card-head"><div><h2>${esc(t.name)}</h2>
      <div class="sub">${[t.keywords && `keywords: ${esc(t.keywords)}`, t.domains && `sites: ${esc(t.domains)}`, t.communities && `subreddits: ${esc(t.communities)}`].filter(Boolean).join(' · ')}</div></div>
      <div class="row"><button class="btn small" id="topicEdit">Edit</button><button class="btn small" id="topicRun">Collect now</button><button class="btn small" id="topicDel">Delete</button></div></div>
      <p class="note" style="margin:0">Posts are flagged for how their traction was produced (bots, coordinated networks, vote bursts, early boosting), not for what they say. Organic posts on the same topic stay unflagged whatever their viewpoint.</p></div>
    <div class="tiles" style="margin-top:12px">
      <div class="tile hero"><div class="k">Artificially amplified posts</div><div class="v">${s.amplifiedPosts}</div><div class="d">of ${s.posts} matching (index ≥ 0.5)</div></div>
      <div class="tile"><div class="k">Flagged pushers</div><div class="v">${s.flaggedPushers}</div><div class="d">of ${s.pushers} who posted or engaged early</div></div>
      <div class="tile"><div class="k">Coordinated networks</div><div class="v">${s.networks.length}</div><div class="d">${s.networks.map((n) => esc(n.id.replace('network:', '#'))).join(', ') || 'none'}</div></div>
      <div class="tile"><div class="k">Comments</div><div class="v">${fmtNum(s.comments)}</div><div class="d">median amplification ${s.medianAmplification ?? '–'}</div></div>
    </div>
    <div class="card" style="margin-top:12px"><h2 style="margin-bottom:8px">Summary</h2><ul class="narrative">${r.narrative.map((x) => `<li>${esc(x)}</li>`).join('') || '<li>No matching posts in the collected data yet.</li>'}</ul></div>
    <div class="card" style="margin-top:12px"><div class="card-head"><h2>Matching posts</h2><span class="sub">most amplified first · click to open the traction analysis</span></div>
      <div class="table-scroll"><table><thead><tr><th>Post</th><th>Author</th><th class="num">Amplification</th><th class="num">Comments</th><th class="num">Score</th><th>Signals</th></tr></thead><tbody>
      ${r.posts.slice(0, 60).map((p) => `<tr class="click" data-open-post="${esc(p.id)}"><td><span class="link">${esc((p.title || '').slice(0, 80))}</span><div class="sub">${esc(p.community)} · ${fmtDate(p.created_utc)}</div></td>
        <td>${acct(p.authorId, p.author)}<div>${p.authorBot && p.authorBot !== 'likely human' ? botChip(p.authorBot, p.authorBotScore) : ''}${p.authorNetwork ? `<span class="chip co">${esc(p.authorNetwork.replace('network:', 'network #'))}</span>` : ''}</div></td>
        <td class="num"><span class="status-line" style="justify-content:flex-end"><span class="status-dot" style="background:${lvl(p.amplificationIndex)}"></span><b>${p.amplificationIndex.toFixed(2)}</b></span><div class="sub">${esc(p.amplificationLevel)}</div></td>
        <td class="num">${p.comments}</td><td class="num">${fmtNum(p.score)}</td>
        <td>${p.voteBursts ? '<span class="chip crit">vote burst</span>' : ''}${p.crossposts ? `<span class="chip">${p.crossposts} crossposts</span>` : ''}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">No matches yet.</td></tr>'}</tbody></table></div></div>
    <div class="grid2" style="margin-top:12px;align-items:start">
      <div class="card"><div class="card-head"><h2>Accounts pushing this topic</h2><span class="sub">authors and early engagers</span></div>
        <div class="table-scroll"><table><thead><tr><th>Account</th><th class="num">Posts</th><th class="num" title="Comments in the first 30 minutes">Early</th><th>Bot likelihood</th><th>Linked to</th></tr></thead><tbody>
        ${r.accounts.filter((a) => a.posts || a.earlyEngagements).slice(0, 40).map((a) => `<tr><td>${acct(a.account, a.label)}</td><td class="num">${a.posts}</td><td class="num">${a.earlyEngagements}</td>
          <td>${botChip(a.bot, a.botScore)}</td><td>${a.network ? `<span class="chip co">${esc(a.network.replace('network:', 'network #'))}</span>` : ''}${a.operator ? `<span class="chip id">${esc(a.operator.replace('agent:', 'operator #'))}</span>` : ''}</td></tr>`).join('')}</tbody></table></div></div>
      <div class="stack">
        <div class="card"><div class="card-head"><h2>Posts per day</h2></div>${tlPoints.length ? columnChart(tlPoints.map((p, i) => ({ ...p, x: i })), { bucket: 1 }).replace(/>\d+ min</g, '><') : '<div class="muted">–</div>'}
          <div class="sub">${r.timeline.length ? `${esc(r.timeline[0].day)} → ${esc(r.timeline[r.timeline.length - 1].day)}` : ''}</div></div>
        <div class="card"><h4>Communities</h4>${hbars(r.communities.map((x) => ({ label: x.label, value: x.posts, text: String(x.posts) })))}</div>
        <div class="card"><h4>Websites linked</h4>${hbars(r.domains.map((x) => ({ label: x.label, value: x.posts, text: String(x.posts) })))}</div>
      </div>
    </div>`;
  $$('#watchOut [data-open-post]').forEach((tr) => tr.addEventListener('click', (e) => { if (!e.target.closest('[data-account]')) openPost(tr.dataset.openPost); }));
  $('#topicEdit').onclick = () => editTopic(state.topics?.find((x) => x.id === id) || t);
  $('#topicDel').onclick = async () => { if (!confirm(`Delete topic “${t.name}”? Collected posts stay in the database.`)) return; await api(`/api/watch/topics/${id}`, { method: 'DELETE' }); state.topicSel = null; $('#watchOut').innerHTML = ''; loadTopics(); };
  $('#topicRun').onclick = (e) => busy(e.currentTarget, async () => {
    const res = await post(`/api/watch/topics/${id}/run`);
    toast(`Topic run: ${res.topic?.last_status || 'done'}`, 7000);
    dataChanged(); showTopic(id);
  });
}

// ------------------------------------------------------------------ bots & coordination
['#botLabel', '#botMin'].forEach((s) => $(s).addEventListener('change', loadBots));
['#coordWindow', '#coordRepeats'].forEach((s) => $(s).addEventListener('change', loadCoordination));
function loadBotsView() { loadBots(); loadCoordination(); }

async function loadBots() {
  try {
    const r = await api(`/api/bots?${new URLSearchParams({ label: $('#botLabel').value, min_score: $('#botMin').value })}`);
    const c = r.counts;
    $('#botCounts').innerHTML = ['likely automated', 'suspicious', 'likely human', 'insufficient data'].map((k) => `${c[k] || 0} ${k}`).join(' · ');
    $('#botTable').innerHTML = r.accounts.length ? `<table><thead><tr><th>Account</th><th>Label</th><th class="num">Items</th><th>Main reasons</th></tr></thead><tbody>
      ${r.accounts.slice(0, 300).map((a) => `<tr class="click" data-account="${esc(a.account)}"><td><span class="link">${esc(a.labelText)}</span></td><td>${botChip(a.label, a.score)}</td>
        <td class="num">${a.items}</td><td class="sub">${esc(a.reasons.join(' · ') || '–')}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">No accounts with this label.</div>';
  } catch (e) { fail(e); }
}

async function loadCoordination() {
  try {
    const r = await api(`/api/coordination?${new URLSearchParams({ window: $('#coordWindow').value, min_repeats: $('#coordRepeats').value })}`);
    $('#coordSub').textContent = `${r.networks.length} network${r.networks.length === 1 ? '' : 's'} · ${r.pairs.length} linked pairs`;
    $('#networks').innerHTML = r.networks.length ? r.networks.map((n) => `<div class="quote" style="border-left-color:var(--s2)">
      <div class="row" style="justify-content:space-between"><b>${esc(n.label)}</b><span class="sub">${n.coSharedItems} items co-shared · median gap ${n.medianDelaySec ?? '–'} s · ${n.automatedMembers} of ${n.size} likely automated</span></div>
      <div style="margin:4px 0">${n.members.map((m) => `<span class="chip co">${acct(m, n.labels[m])}</span>`).join('')}</div>
      <div class="sub">Active ${fmtDate(n.first)} → ${fmtDate(n.last)} in ${n.communities.map((c) => esc(c.label)).join(', ')}</div>
      <div style="margin-top:4px">${n.items.slice(0, 5).map((it) => `<div class="sub">• <span class="chip">${it.kind}</span> ${esc(it.display.slice(0, 90))} <span class="muted">${it.accounts} accounts · ${it.shares} posts · typical gap ${it.medianGapSec ?? '–'} s</span></div>`).join('')}</div>
      <div class="row" style="margin-top:6px"><button class="btn small" data-net="${esc(n.id)}">Show in graph</button></div></div>`).join('')
      : '<div class="empty">No accounts repeatedly shared the same items within this window.</div>';
    $$('[data-net]').forEach((b) => b.addEventListener('click', () => { state.focus = b.dataset.net; state.routeSel = null; $('#focusInput').value = state.focus; $('#depthSel').value = '1'; showView('graph'); loadGraph(); }));
    $('#bursts').innerHTML = r.bursts.length ? `<table><thead><tr><th>Link or text</th><th class="num">Accounts</th><th class="num">Spread</th><th>Where</th></tr></thead><tbody>
      ${r.bursts.slice(0, 40).map((b) => `<tr><td><span class="chip">${b.kind}</span> ${esc(b.display.slice(0, 80))}<div class="sub">${fmtDate(b.first)}${b.networks.length ? ` · ${esc(b.networks.join(', '))}` : ''}</div></td>
        <td class="num">${b.accounts}</td><td class="num">${b.spreadSec < 120 ? b.spreadSec + ' s' : Math.round(b.spreadSec / 60) + ' min'}</td><td class="sub">${esc(b.communities.join(', '))}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">No bursts.</div>';
  } catch (e) { fail(e); }
}

// ------------------------------------------------------------------ 5. data sources
async function loadDataTab() {
  await refreshStatus();
  try {
    const links = await api('/api/links');
    $('#linkList').innerHTML = links.length ? `<table><tbody>${links.map((l) => `<tr><td><code>${esc(l.a)}</code></td><td>${esc(l.relation)}</td><td><code>${esc(l.b)}</code></td>
      <td class="sub">${esc(l.note || '')}</td><td><button class="btn small" data-del="${esc(l.a)}|${esc(l.b)}|${esc(l.relation)}">Remove</button></td></tr>`).join('')}</tbody></table>`
      : '<p class="sub">No confirmed links yet.</p>';
    $$('[data-del]').forEach((b) => b.addEventListener('click', async () => {
      const [a, bb, rel] = b.dataset.del.split('|');
      try { await api(`/api/links?${new URLSearchParams({ a, b: bb, relation: rel })}`, { method: 'DELETE' }); dataChanged(); loadDataTab(); } catch (e) { fail(e); }
    }));
  } catch (e) { fail(e); }
}

$('#demoBtn').addEventListener('click', (e) => busy(e.currentTarget, async () => {
  const r = await post('/api/demo');
  toast(`Demo loaded: ${r.accounts} accounts, ${r.posts} posts, ${r.comments} comments.`);
  dataChanged();
}));
$('#resetBtn').addEventListener('click', (e) => {
  if (!confirm('Delete everything in the local database? This cannot be undone.')) return;
  busy(e.currentTarget, async () => { await post('/api/reset'); toast('Database cleared.'); dataChanged(); });
});
$$('[data-collect]').forEach((btn) => btn.addEventListener('click', () => busy(btn, async () => {
  const kind = btn.dataset.collect;
  const body = kind === 'user' ? { username: $('#cUser').value.trim(), limit: +$('#cUserLimit').value }
    : kind === 'post' ? { ref: $('#cPost').value.trim(), expandCommenters: +$('#cPostExpand').value }
      : { name: $('#cSub').value.trim(), limit: +$('#cSubLimit').value, expandAuthors: +$('#cSubExpand').value };
  if (!Object.values(body)[0]) return toast('Fill in the field first.');
  toast('Collecting… this can take a few minutes because of Reddit rate limits.', 8000);
  const poll = setInterval(refreshStatus, 3000);
  try {
    const r = await post(`/api/collect/${kind}`, body);
    toast(`Done: ${Object.entries(r).map(([k, v]) => `${k} ${v}`).join(', ')}`, 7000);
    dataChanged();
  } finally { clearInterval(poll); refreshStatus(); }
})));
$('#importFile').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  try {
    const r = await api('/api/import', { method: 'POST', body: await f.text(), headers: { 'Content-Type': 'text/plain' } });
    toast(`Imported: ${Object.entries(r).map(([k, v]) => `${v} ${k}`).join(', ')}`);
    dataChanged();
  } catch (err) { fail(err); } finally { e.target.value = ''; }
});
$('#linkAdd').addEventListener('click', async () => {
  const a = $('#lA').value.trim(), b = $('#lB').value.trim();
  if (!a || !b) return toast('Enter both account ids (platform:username).');
  try { await post('/api/links', { a, b, relation: $('#lRel').value, note: $('#lNote').value }); $('#lA').value = $('#lB').value = $('#lNote').value = ''; dataChanged(); loadDataTab(); } catch (e) { fail(e); }
});

// ------------------------------------------------------------------ boot
(async function boot() {
  try { const t = localStorage.getItem('sla-theme'); if (t) applyTheme(t); } catch { /* ignore */ }
  buildRelToggles();
  ontologyInfo = await api('/api/ontology').catch(() => null);
  const s = await refreshStatus();
  await refreshLists();
  let view = 'graph';
  try { view = localStorage.getItem('sla-view') || 'graph'; } catch { /* ignore */ }
  if (s && s.counts.accounts === 0) { view = 'data'; toast('The database is empty: load the demo dataset or collect from Reddit.', 6000); }
  showView(view);
})();
