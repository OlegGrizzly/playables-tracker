'use strict';
/* Playables Tracker — дашборд. Данные: data.json (собирает tracker/build.py). */

const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const MAX_CMP = 4;
const PAGE = 100;

const S = {
  data: null,
  games: [],
  byId: new Map(),
  tab: 'hot',
  sort: {
    hot: ['hot', -1], new: ['peer', -1], all: ['hot', -1],
    genres: ['rateSum', -1], pubs: ['rateSum', -1], cmp: ['hot', -1],
  },
  limit: PAGE,
  cmp: loadCmp(),
  charts: [],
  viewCharts: [],
};

function loadCmp() { try { return JSON.parse(localStorage.getItem('pt_cmp') || '[]').slice(0, MAX_CMP); } catch { return []; } }
function saveCmp() { try { localStorage.setItem('pt_cmp', JSON.stringify(S.cmp)); } catch {} $('#cmpCount').textContent = S.cmp.length || ''; }

/* ---------- форматирование ---------- */
function fmt(n) {
  if (n == null || Number.isNaN(n)) return '—';
  const a = Math.abs(n), s = n < 0 ? '−' : '';
  const t = (v, d) => v.toFixed(d).replace(/\.0$/, '');
  if (a < 1e3) return s + (a < 10 && a % 1 ? t(a, 1) : Math.round(a));
  if (a < 1e4) return s + t(a / 1e3, 1) + 'K';
  if (a < 1e6) return s + Math.round(a / 1e3) + 'K';
  if (a < 1e7) return s + t(a / 1e6, 1) + 'M';
  if (a < 1e9) return s + Math.round(a / 1e6) + 'M';
  return s + t(a / 1e9, 1) + 'B';
}
function plural(n, one, few, many) {
  const a = Math.abs(n) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
}
const nGames = n => `${n} ${plural(n, 'игра', 'игры', 'игр')}`;
const fmtD = n => n == null ? '—' : (n > 0 ? '+' : '') + fmt(n);
const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
function fmtDate(iso, year = true) {
  if (!iso) return '—';
  const [y, m, d] = iso.split('-').map(Number);
  return `${d} ${MONTHS[m - 1]}${year ? ' ' + y : ''}`;
}
const dayMs = 864e5;
const toDay = iso => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / dayMs;
const ageTxt = a => a == null ? '—' : `${a} дн`;
const topPct = p => p == null ? '—' : `топ ${Math.max(1, Math.ceil((1 - p) * 100))}%`;
function rateTxt(g) {
  if (g.rate == null) return '—';
  if (g.lt) return `&lt;${fmt(g.prec)}`; // плеи не изменились — темп меньше шага округления
  return `${g.est ? '≈' : ''}${fmt(g.rate)}`;
}
// Сортировка «в день»: сначала измеренный прирост (по величине), затем «<X» —
// у них прирост не виден из-за округления, реальное значение от 0 до X.
function rateSortKey(g) {
  if (g.rate == null) return null;
  return g.lt ? -1e12 + (g.prec || 0) : g.rate;
}
function rateNote(g) {
  if (g.est) return 'оценка: плеи / возраст (нет 2 снимков)';
  if (g.lt) return `за ${g.span} дн число не изменилось — прирост меньше шага округления YouTube (${fmt(g.q)})`;
  return `за ${g.span} дн · точность ±${fmt(g.prec)} (округление YouTube)`;
}
function momHtml(m) {
  if (m == null) return '<span class="muted">—</span>';
  const cls = m >= 1.15 ? 'up' : m <= 0.85 ? 'down' : '';
  const ar = m >= 1.15 ? '↑' : m <= 0.85 ? '↓' : '→';
  return `<span class="${cls}">${ar} ×${m.toFixed(2)}</span>`;
}
function growthHtml(v) {
  if (v == null) return '<span class="muted">—</span>';
  return `<span class="${v > 0 ? 'up' : v < 0 ? 'down' : ''}">${v > 0 ? '+' : ''}${v >= 100 ? Math.round(v) : v.toFixed(1)}%</span>`;
}
function hotHtml(h) {
  if (h == null) return '—';
  return `<span class="hotbar"><i style="--w:${Math.max(0, Math.min(100, h))}%"></i>${h.toFixed(0)}</span>`;
}
function icon(g, size = 88) {
  if (!g.ic) return '<img alt="">';
  return `<img src="${esc(g.ic)}=s${size}" alt="" loading="lazy" referrerpolicy="no-referrer">`;
}
function gameCell(g) {
  const tags = (g.age != null && g.age <= 7 ? '<span class="tag">NEW</span>' : '') + (g.rm ? '<span class="tag rm">удалена</span>' : '');
  const star = S.cmp.includes(g.id) ? '<span class="star" title="В сравнении">★</span>' : '';
  return `<div class="gname">${icon(g)}<div><b>${star}${esc(g.n)}${tags}</b><small>${esc(g.p || '—')}<span class="sm-only">${g.g ? ' · ' + esc(g.g) : ''}${g.age != null ? ` · ${g.age} дн` : ''}</span></small></div></div>`;
}
function points(g) {
  const out = [];
  g.s.forEach((v, i) => { if (v != null) out.push({ date: S.data.dates[g.off + i], v }); });
  return out;
}
function spark(g) {
  const pts = points(g).slice(-30);
  if (pts.length < 2) return '<span class="muted">—</span>';
  const w = 80, h = 24, vs = pts.map(p => p.v), mn = Math.min(...vs), mx = Math.max(...vs);
  const xy = pts.map((p, i) => `${(i / (pts.length - 1) * (w - 2) + 1).toFixed(1)},${(mx === mn ? h / 2 : h - 2 - (p.v - mn) / (mx - mn) * (h - 4)).toFixed(1)}`);
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${xy.join(' ')}" fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
function whyChips(g) {
  const c = [];
  if (g.peer != null && g.peer >= 0.9) c.push(`ровесники ${topPct(g.peer)}`);
  if (g.prRate != null && g.prRate >= 0.9) c.push(`темп ${topPct(g.prRate)}`);
  if (g.mom != null && g.mom >= 1.3) c.push(`разгон ×${g.mom.toFixed(1)}`);
  if (g.growth != null && g.growth >= 20) c.push(`+${Math.round(g.growth)}%/нед`);
  if (g.age != null && g.age <= 7) c.push(`новинка, ${g.age} дн`);
  return `<div class="why">${c.map(x => `<span class="chip">${esc(x)}</span>`).join('')}</div>`;
}

/* ---------- колонки ---------- */
const COL = {
  rank: { label: '#', cls: 'hide-sm', nosort: true, html: (g, i) => i + 1 },
  game: { label: 'Игра', cls: 'game', get: g => (g.n || '').toLowerCase(), html: gameCell },
  genre: { label: 'Жанр', cls: 'txt hide-sm', get: g => g.g || '', html: g => esc(g.g || '—') },
  pub: { label: 'Опубл.', cls: 'hide-sm', title: 'Дата публикации на YouTube', get: g => g.pub || '', html: g => fmtDate(g.pub) },
  rm: { label: 'Удалена', title: 'Когда игру убрали из YouTube Playables', get: g => g.rm || '', html: g => g.rm ? fmtDate(g.rm) : '<span class="muted">—</span>' },
  age: { label: 'Возраст', cls: 'hide-sm', get: g => g.age, html: g => ageTxt(g.age) },
  plays: { label: 'Плеев', get: g => g.plays, html: g => fmt(g.plays) },
  d1: { label: 'За сутки', cls: 'hide-sm', get: g => g.d1, html: g => g.d1 == null ? '<span class="muted">—</span>' : fmtD(g.d1) },
  rate: { label: 'В день', short: '/день', title: 'Средний прирост в день за ≤7 дней. «<X» — число не изменилось, темп меньше шага округления; ≈ — оценка плеи/возраст (нет 2 снимков)', get: rateSortKey, html: g => g.rate == null ? '—' : `<span class="rt" title="${esc(rateNote(g))}">${rateTxt(g)}</span>` },
  growth: { label: 'Рост/нед', cls: 'hide-sm', get: g => g.growth, html: g => growthHtml(g.growth) },
  mom: { label: 'Ускорение', cls: 'hide-sm', title: 'Темп 3 последних дней / темп 7 дней до них', get: g => g.mom, html: g => momHtml(g.mom) },
  peer: { label: 'Ровесники', short: 'Ровес.', title: 'Насколько игра сильнее игр того же возраста', get: g => g.peer, html: g => topPct(g.peer) },
  hot: { label: '🔥 Hot', get: g => g.hot, html: g => hotHtml(g.hot) },
  why: { label: 'Почему', cls: 'txt hide-sm', nosort: true, html: whyChips },
  spark: { label: '30 дн', nosort: true, cls: 'hide-sm', html: spark },
  D1: { label: 'D1', title: 'Плеев на 1-й день после публикации', get: g => g.at['1'], html: g => fmt(g.at['1']) },
  D3: { label: 'D3', cls: 'hide-sm', get: g => g.at['3'], html: g => fmt(g.at['3']) },
  D7: { label: 'D7', cls: 'hide-sm', get: g => g.at['7'], html: g => fmt(g.at['7']) },
};
COL.rateLg = { ...COL.rate, cls: 'hide-sm' }; // на «Новинках» на телефоне важнее D1 и ровесники
const TAB_COLS = {
  hot: ['rank', 'game', 'genre', 'age', 'plays', 'rate', 'growth', 'mom', 'peer', 'hot', 'why', 'spark'],
  new: ['rank', 'game', 'genre', 'pub', 'age', 'plays', 'D1', 'D3', 'D7', 'rateLg', 'peer', 'hot', 'spark'],
  all: ['rank', 'game', 'genre', 'pub', 'age', 'plays', 'd1', 'rate', 'growth', 'mom', 'peer', 'hot', 'spark'],
  cmp: ['rank', 'game', 'genre', 'age', 'plays', 'rate', 'growth', 'mom', 'peer', 'hot'],
};

/* ---------- фильтры ---------- */
function filters() {
  return {
    q: $('#q').value.trim().toLowerCase(),
    genre: $('#fGenre').value,
    age: $('#fAge').value,
    min: +$('#fMin').value || 0,
    pub: $('#fPub').value.trim().toLowerCase(),
    status: $('#fStatus').value,
  };
}
function applyFilters(list, f, { skipGenre = false, skipPub = false } = {}) {
  let ageLo = -1, ageHi = Infinity;
  if (f.age) [ageLo, ageHi] = f.age.split('-').map(Number);
  return list.filter(g => {
    if (f.status === 'active' && g.rm) return false;
    if (f.status === 'removed' && !g.rm) return false;
    if (f.q && !(g.n || '').toLowerCase().includes(f.q) && !(g.p || '').toLowerCase().includes(f.q)) return false;
    if (!skipGenre && f.genre && g.g !== f.genre) return false;
    if (!skipPub && f.pub && (g.p || '').toLowerCase() !== f.pub) return false;
    if (f.age && (g.age == null || g.age < ageLo || g.age > ageHi)) return false;
    if (g.plays < f.min) return false;
    return true;
  });
}
function sortList(list, key, dir, cols = COL) {
  const get = cols[key]?.get || (g => g[key]);
  return list.slice().sort((a, b) => {
    const x = get(a), y = get(b);
    const xn = x == null, yn = y == null;
    if (xn || yn) return xn === yn ? 0 : xn ? 1 : -1; // null — всегда в конце
    return (x < y ? -1 : x > y ? 1 : 0) * dir;
  });
}

/* ---------- таблица ---------- */
function withRm(cols, f) {
  if (f.status === 'active') return cols;
  const i = cols.indexOf('plays');
  return [...cols.slice(0, i + 1), 'rm', ...cols.slice(i + 1)];
}
function tableHtml(tab, rows, cols, colDefs = COL, grouped = null, rowAttr = g => `data-id="${esc(g.id)}"${S.cmp.includes(g.id) ? ' class="fav"' : ''}`, sortOverride = null) {
  const [sk, sd] = sortOverride || S.sort[tab];
  const th = cols.map(k => {
    const c = colDefs[k];
    const cls = [c.cls || '', c.nosort ? 'nosort' : '', sk === k ? 'sorted' : ''].join(' ');
    const ar = sk === k ? (sd < 0 ? ' ↓' : ' ↑') : '';
    const label = c.short ? `<span class="lg-only">${c.label}</span><span class="sm-only">${c.short}</span>` : c.label;
    return `<th class="${cls}" data-k="${k}" ${c.title ? `title="${esc(c.title)}"` : ''}>${label}${ar}</th>`;
  }).join('');
  let body = '';
  const row = (g, i) => `<tr ${rowAttr(g)}>${cols.map(k => `<td class="${colDefs[k].cls || ''}">${colDefs[k].html(g, i)}</td>`).join('')}</tr>`;
  if (grouped) {
    for (const [label, items] of grouped) {
      body += `<tr class="grp"><td colspan="${cols.length}">${label}</td></tr>`;
      body += items.map(row).join('');
    }
  } else {
    body = rows.map(row).join('');
  }
  return `<div class="tw"><table class="t" data-tab="${tab}"><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function bindTable(root, tab) {
  root.querySelectorAll('th[data-k]').forEach(th => th.addEventListener('click', () => {
    const k = th.dataset.k; const def = (tab === 'genres' || tab === 'pubs' ? AGG_COL : COL)[k];
    if (!def || def.nosort) return;
    const [sk, sd] = S.sort[tab];
    S.sort[tab] = [k, sk === k ? -sd : (k === 'game' || k === 'name' ? 1 : -1)];
    render();
  }));
}

/* ---------- агрегаты (жанры / издатели) ---------- */
const AGG_COL = {
  rank: { label: '#', cls: 'hide-sm', nosort: true, html: (r, i) => i + 1 },
  name: { label: 'Название', cls: 'txt game', get: r => r.name.toLowerCase(), html: r => `<b>${esc(r.name)}</b>` },
  games: { label: 'Игр', get: r => r.games, html: r => r.games },
  new30: { label: 'Новых за 30 дн', cls: 'hide-sm', get: r => r.new30, html: r => r.new30 || '<span class="muted">0</span>' },
  plays: { label: 'Плеев всего', cls: 'hide-sm', get: r => r.plays, html: r => fmt(r.plays) },
  rateSum: { label: 'Плеев в день', title: 'Сумма темпов игр (≈ при оценке)', get: r => r.rateSum, html: r => fmt(r.rateSum) },
  perGame: { label: 'В день на игру', cls: 'hide-sm', get: r => r.perGame, html: r => fmt(r.perGame) },
  newPeer: { label: 'Новинки vs ровесники', cls: 'hide-sm', title: 'Медианный перцентиль новинок (≤30 дн) среди ровесников', get: r => r.newPeer, html: r => r.newPeer == null ? '<span class="muted">—</span>' : topPct(r.newPeer) },
  hot100: { label: 'В топ-100 Hot', get: r => r.hot100, html: r => r.hot100 || '<span class="muted">0</span>' },
  best: { label: 'Лидер сейчас', cls: 'txt hide-sm', nosort: true, html: r => r.best ? `<a href="#" data-open="${esc(r.best.id)}">${esc(r.best.n)}</a>` : '—' },
};
function aggregate(list, keyFn) {
  const hotTop = new Set(list.filter(g => g.hot != null).sort((a, b) => b.hot - a.hot).slice(0, 100).map(g => g.id));
  const m = new Map();
  for (const g of list) {
    const k = keyFn(g) || '—';
    let r = m.get(k);
    if (!r) m.set(k, r = { name: k, games: 0, new30: 0, plays: 0, rateSum: 0, peers: [], hot100: 0, best: null });
    r.games++; r.plays += g.plays; r.rateSum += g.rate || 0;
    if (g.age != null && g.age <= 30) { r.new30++; if (g.peer != null) r.peers.push(g.peer); }
    if (hotTop.has(g.id)) r.hot100++;
    if (g.hot != null && (!r.best || g.hot > r.best.hot)) r.best = g;
  }
  return [...m.values()].map(r => {
    r.perGame = r.rateSum / r.games;
    r.peers.sort((a, b) => a - b);
    r.newPeer = r.peers.length ? r.peers[Math.floor(r.peers.length / 2)] : null;
    return r;
  });
}

/* ---------- графики ---------- */
function cssVar(name, el = $('.viz-root')) { return getComputedStyle(el).getPropertyValue(name).trim(); }
function seriesColors(el) { return [1, 2, 3, 4].map(i => cssVar(`--series-${i}`, el)); }
function chartBase(el) {
  const muted = cssVar('--muted', el), grid = cssVar('--grid', el), axis = cssVar('--axis', el), txt = cssVar('--text-primary', el), surf = cssVar('--surface-1', el), border = cssVar('--border', el);
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = muted;
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false, labels: { boxWidth: 14, boxHeight: 3, color: cssVar('--text-secondary', el), filter: it => !String(it.text).startsWith('_') } },
      tooltip: {
        backgroundColor: surf, titleColor: txt, bodyColor: txt, borderColor: border, borderWidth: 1, padding: 8,
        filter: it => !String(it.dataset.label).startsWith('_'),
        callbacks: { label: c => ` ${c.dataset.label}: ${fmt(c.chart.options.indexAxis === 'y' ? c.parsed.x : c.parsed.y)}` },
      },
    },
    scales: {
      x: { grid: { display: false }, border: { color: axis }, ticks: { color: muted, maxRotation: 0, autoSkipPadding: 12 } },
      y: { grid: { color: grid }, border: { display: false }, ticks: { color: muted, callback: v => fmt(v) } },
    },
  };
}
function destroy(arr) { while (arr.length) arr.pop().destroy(); }
function noHistory(box, n) { box.innerHTML = `<div class="empty">Нужно минимум 2 снимка — сейчас ${n}. График появится после следующего сбора.</div>`; }

function peerBandDatasets(el, maxAge) {
  const c = S.data.curve, idx = c.age.map((a, i) => i).filter(i => c.age[i] <= maxAge);
  const pts = k => idx.map(i => ({ x: c.age[i], y: Math.max(1, c[k][i]) }));
  const muted = cssVar('--muted', el), grid = cssVar('--grid', el);
  return [
    { label: '_p25', data: pts('p25'), borderWidth: 0, pointRadius: 0, fill: false },
    { label: 'Ровесники: 25–75%', data: pts('p75'), borderWidth: 0, pointRadius: 0, fill: '-1', backgroundColor: grid + 'cc' },
    { label: 'Медиана ровесников', data: pts('p50'), borderColor: muted, borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 },
    { label: 'Топ-10% ровесников', data: pts('p90'), borderColor: muted, borderDash: [1, 3], borderWidth: 1.5, pointRadius: 0 },
  ];
}
function agePoints(g) {
  if (!g.pub) return [];
  const p0 = toDay(g.pub);
  return points(g).map(p => ({ x: Math.max(0, toDay(p.date) - p0), y: Math.max(1, p.v) }));
}
function ageChart(canvas, games, el) {
  const cols = seriesColors(el);
  const maxAge = Math.max(14, ...games.map(g => g.age || 0)) + 2;
  const base = chartBase(el);
  base.interaction = { mode: 'nearest', axis: 'x', intersect: false };
  base.plugins.legend.display = true;
  base.plugins.tooltip.callbacks = {
    title: items => `Возраст ${items[0].parsed.x} дн`,
    label: c => ` ${c.dataset.label}: ${fmt(c.parsed.y)}`,
  };
  base.scales.x = { ...base.scales.x, type: 'linear', min: 0, max: maxAge, title: { display: true, text: 'дней с публикации', color: cssVar('--muted', el) } };
  base.scales.y = { ...base.scales.y, type: 'logarithmic', ticks: { ...base.scales.y.ticks, callback: v => { const l = Math.log10(v); return Math.abs(l - Math.round(l)) < 1e-9 ? fmt(v) : ''; } } };
  const ds = peerBandDatasets(el, maxAge);
  games.forEach((g, i) => {
    const pts = agePoints(g);
    ds.push({ label: g.n, data: pts, borderColor: cols[i % 4], backgroundColor: cols[i % 4], borderWidth: 2, pointRadius: pts.length < 15 ? 4 : 0, pointHoverRadius: 5, tension: 0 });
  });
  return new Chart(canvas, { type: 'line', data: { datasets: ds }, options: base });
}

/* ---------- представления ---------- */
function render() {
  if (!S.data) return;
  destroy(S.viewCharts);
  document.querySelectorAll('#tabs button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === S.tab)));
  const f = filters();
  const view = $('#view');
  const tab = S.tab;
  let html = '', count = '';

  if (tab === 'hot' || tab === 'all') {
    // у удалённых нет Hot — по умолчанию сортируем их по дате удаления
    const sort = f.status === 'removed' && S.sort[tab][0] === 'hot' ? ['rm', -1] : S.sort[tab];
    const rows = sortList(applyFilters(S.games, f), ...sort);
    count = nGames(rows.length);
    html = tableHtml(tab, rows.slice(0, S.limit), withRm(TAB_COLS[tab], f), COL, null, undefined, sort);
    if (rows.length > S.limit) html += `<button class="more" id="more">Показать ещё (${rows.length - S.limit})</button>`;
  } else if (tab === 'new') {
    const keyOf = g => g.pub || null; // группировка — по дате публикации на YouTube
    const today = toDay(S.data.updated);
    let list = applyFilters(S.games, f);
    // по умолчанию — последние 30 дней; при поиске или фильтре возраста ограничение снимаем
    if (!f.age && !f.q) list = list.filter(g => keyOf(g) && today - toDay(keyOf(g)) <= 30);
    count = nGames(list.length);
    const [sk, sd] = S.sort.new;
    const groups = new Map();
    for (const g of list.slice().sort((a, b) => (keyOf(b) || '').localeCompare(keyOf(a) || ''))) {
      const k = keyOf(g) || '—';
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(g);
    }
    const grouped = [...groups.entries()].map(([d, items]) => [
      `${d === '—' ? 'Без даты' : fmtDate(d)} · ${nGames(items.length)}${items.length ? ` · медиана плеев ${fmt(sortList(items, 'plays', 1)[Math.floor(items.length / 2)].plays)}` : ''}`,
      sortList(items, sk, sd),
    ]);
    html = (list.length ? tableHtml('new', null, withRm(TAB_COLS.new, f), COL, grouped) : '<div class="empty">Нет новинок под фильтры</div>');
  } else if (tab === 'genres' || tab === 'pubs') {
    const list = applyFilters(S.games, f, { skipGenre: tab === 'genres', skipPub: tab === 'pubs' });
    const rows = sortList(aggregate(list, tab === 'genres' ? g => g.g : g => g.p), ...S.sort[tab], AGG_COL);
    count = `${rows.length} ${tab === 'genres' ? 'жанров' : 'издателей'} · ${nGames(list.length)}`;
    const cols = ['rank', 'name', 'games', 'new30', 'plays', 'rateSum', 'perGame', 'newPeer', 'hot100', 'best'];
    const shown = tab === 'pubs' ? rows.slice(0, S.limit) : rows;
    html = '';
    if (tab === 'genres') {
      html += `<div class="card" style="margin-bottom:12px"><h2>Плеев в день по жанрам</h2><div class="note" style="margin:0 0 8px">Сумма темпов игр жанра${S.data.dates.length < 8 ? ' (истории пока мало — у больших игр темп грубый из-за округления YouTube)' : ''}. Клик по строке — фильтр по жанру.</div><div class="chartbox"><canvas id="cGenres"></canvas></div></div>`;
    } else {
      html += '<div class="note" style="margin:0 0 8px">Клик по строке — все игры издателя. Названия студий как в каталоге YouTube (одна студия может встречаться в нескольких написаниях).</div>';
    }
    html += tableHtml(tab, shown, cols, AGG_COL, null, r => `data-agg="${esc(r.name)}"`);
    if (tab === 'pubs' && rows.length > S.limit) html += `<button class="more" id="more">Показать ещё (${rows.length - S.limit})</button>`;
    setTimeout(() => {
      if (tab !== 'genres' || !window.Chart) return;
      const el = $('.viz-root'), base = chartBase(el);
      base.indexAxis = 'y';
      base.interaction = { mode: 'nearest', axis: 'y', intersect: false };
      base.scales.x = { ...base.scales.y, beginAtZero: true };
      base.scales.y = { grid: { display: false }, border: { color: cssVar('--axis', el) }, ticks: { color: cssVar('--text-secondary', el) } };
      const top = sortList(rows, 'rateSum', -1);
      S.viewCharts.push(new Chart($('#cGenres'), {
        type: 'bar',
        data: { labels: top.map(r => r.name), datasets: [{ label: 'Плеев в день', data: top.map(r => Math.round(r.rateSum)), backgroundColor: cssVar('--series-1', el), borderRadius: 4, barPercentage: 0.8, categoryPercentage: 0.9 }] },
        options: base,
      }));
    });
  } else if (tab === 'cmp') {
    const games = S.cmp.map(id => S.byId.get(id)).filter(Boolean);
    count = `${games.length} из ${MAX_CMP}`;
    if (!games.length) {
      html = '<div class="empty">Добавьте игры в сравнение кнопкой «☆ В сравнение» в карточке игры (до 4).</div>';
    } else {
      html = `<div class="stack">${tableHtml('cmp', sortList(games, ...S.sort.cmp), TAB_COLS.cmp)}
        <div class="grid2">
          <div class="card"><h2>Кривая роста против ровесников</h2><div class="chartbox tall"><canvas id="cAge"></canvas></div><div class="note">Ось Y логарифмическая. Серая полоса — 25–75% игр того же возраста.</div></div>
          <div class="card"><h2>Всего плеев по дням</h2><div class="chartbox tall" id="cDatesBox"><canvas id="cDates"></canvas></div></div>
        </div>
        <div><button class="btn" id="cmpClear">Очистить сравнение</button></div></div>`;
      setTimeout(() => {
        if (!window.Chart) return;
        const el = $('.viz-root');
        S.viewCharts.push(ageChart($('#cAge'), games, el));
        if (S.data.dates.length < 2) return noHistory($('#cDatesBox'), S.data.dates.length);
        const base = chartBase(el), cols = seriesColors(el);
        base.plugins.legend.display = true;
        base.scales.y.type = 'logarithmic';
        S.viewCharts.push(new Chart($('#cDates'), {
          type: 'line',
          data: {
            labels: S.data.dates.map(d => fmtDate(d, false)),
            datasets: games.map((g, i) => ({ label: g.n, data: S.data.dates.map((_, j) => { const v = g.s[j - g.off]; return v == null ? null : v; }), borderColor: cols[i], backgroundColor: cols[i], borderWidth: 2, pointRadius: 0, spanGaps: true })),
          },
          options: base,
        }));
      });
    }
  }

  view.innerHTML = html;
  $('#count').textContent = count;
  const nAct = [f.genre, f.age, f.min, f.pub, f.status !== 'active'].filter(Boolean).length;
  $('#fActive').textContent = nAct || '';
  bindTable(view, tab);
  view.querySelectorAll('tr[data-id]').forEach(tr => tr.addEventListener('click', () => openGame(tr.dataset.id)));
  view.querySelectorAll('a[data-open]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); openGame(a.dataset.open); }));
  view.querySelectorAll('tr[data-agg]').forEach(tr => tr.addEventListener('click', () => {
    if (tab === 'genres') { $('#fGenre').value = tr.dataset.agg; setTab('hot'); }
    else { $('#fPub').value = tr.dataset.agg; setTab('all'); }
  }));
  $('#more')?.addEventListener('click', () => { S.limit += PAGE; render(); });
  $('#cmpClear')?.addEventListener('click', () => { S.cmp = []; saveCmp(); render(); });
}

/* ---------- карточка игры ---------- */
function openGame(id, push = true) {
  const g = S.byId.get(id);
  if (!g) return;
  if (push) setHash({ g: id });
  destroy(S.charts);
  const inCmp = S.cmp.includes(id);
  const at = Object.entries(g.at || {});
  const hotParts = g.hot == null ? '' : `темп ${topPct(g.prRate)} · ровесники ${topPct(g.peer)} · ускорение ${g.prMom == null ? 'нет данных' : topPct(g.prMom)}`;
  $('#dBody').innerHTML = `
    <div class="dhead">${icon(g, 144)}<div>
      <h2>${esc(g.n)}${g.age != null && g.age <= 7 ? '<span class="tag">NEW</span>' : ''}${g.rm ? `<span class="tag rm">удалена ${fmtDate(g.rm)}</span>` : ''}</h2>
      <div class="sub"><a href="#" id="dPub">${esc(g.p || '—')}</a> · ${esc(g.g || '—')}</div>
      <div class="sub">Опубликована ${fmtDate(g.pub)}${g.age != null ? ` · ${g.age} дн назад` : ''}</div>
    </div></div>
    <div class="dlinks">
      <a class="btn" href="https://www.youtube.com/playables/${encodeURIComponent(g.id)}" target="_blank" rel="noopener">▶ Открыть на YouTube</a>
      <a class="btn" href="https://playables.games/games/?q=${encodeURIComponent(g.n)}" target="_blank" rel="noopener">playables.games</a>
      <button class="btn ${inCmp ? 'on' : ''}" id="dCmp">${inCmp ? '★ В сравнении' : '☆ В сравнение'}</button>
    </div>
    <div class="kpis">
      <div class="kpi"><div class="l">Плеев всего</div><div class="v">${fmt(g.plays)}</div><div class="s">шаг округления ${fmt(g.q)}</div></div>
      <div class="kpi"><div class="l">За сутки</div><div class="v">${fmtD(g.d1)}</div><div class="s">${g.d1 == null ? 'нет снимка за вчера' : 'прирост к вчера'}</div></div>
      <div class="kpi"><div class="l">В день</div><div class="v">${rateTxt(g)}</div><div class="s">${esc(rateNote(g))}</div></div>
      <div class="kpi"><div class="l">Рост за неделю</div><div class="v">${growthHtml(g.growth)}</div><div class="s">к плеям неделю назад</div></div>
      <div class="kpi"><div class="l">Ускорение</div><div class="v">${momHtml(g.mom)}</div><div class="s">3 дн против 7 дн до них</div></div>
      <div class="kpi"><div class="l">Ровесники</div><div class="v">${topPct(g.peer)}</div><div class="s">${g.peerN ? `среди ${g.peerN} игр того же возраста` : 'мало ровесников'}</div></div>
      <div class="kpi"><div class="l">🔥 Hot</div><div class="v">${g.hot == null ? '—' : g.hot.toFixed(0)}</div><div class="s">${esc(hotParts)}</div></div>
    </div>
    ${at.length ? `<div class="kpis">${at.map(([a, v]) => `<div class="kpi"><div class="l">D${a}</div><div class="v">${fmt(v)}</div><div class="s">на ${a}-й день</div></div>`).join('')}</div>` : ''}
    <div class="stack">
      <div class="card"><h2>Кривая роста против ровесников</h2><div class="chartbox tall"><canvas id="dAge"></canvas></div>
        <div class="note">Ось Y логарифмическая. Выше пунктира — лучше половины игр того же возраста, выше точечной линии — в топ-10%.</div></div>
      <div class="card"><h2>Всего плеев по дням</h2><div class="chartbox" id="dTotBox"><canvas id="dTot"></canvas></div></div>
      <div class="card"><h2>Прирост плеев в день</h2><div class="chartbox" id="dDayBox"><canvas id="dDay"></canvas></div>
        <div class="note">Плеи округлены YouTube (шаг ${fmt(g.q)}), поэтому у больших игр прирост идёт ступеньками.</div></div>
    </div>`;
  const dr = $('#drawer');
  dr.hidden = false;
  document.body.style.overflow = 'hidden';
  $('#dPub').addEventListener('click', e => { e.preventDefault(); $('#fPub').value = g.p || ''; closeGame(); setTab('all'); });
  $('#dCmp').addEventListener('click', () => {
    const i = S.cmp.indexOf(id);
    if (i >= 0) S.cmp.splice(i, 1);
    else { if (S.cmp.length >= MAX_CMP) S.cmp.shift(); S.cmp.push(id); }
    saveCmp(); openGame(id, false); render(); // обновить подсветку в списке
  });
  if (!window.Chart) return;
  S.charts.push(ageChart($('#dAge'), [g], dr));
  const pts = points(g);
  if (pts.length < 2) { noHistory($('#dTotBox'), pts.length); noHistory($('#dDayBox'), pts.length); return; }
  const c1 = cssVar('--series-1', dr);
  const b1 = chartBase(dr);
  b1.plugins.tooltip.callbacks.title = it => fmtDate(pts[it[0].dataIndex].date);
  S.charts.push(new Chart($('#dTot'), {
    type: 'line',
    data: { labels: pts.map(p => fmtDate(p.date, false)), datasets: [{ label: 'Плеев всего', data: pts.map(p => p.v), borderColor: c1, backgroundColor: c1, borderWidth: 2, pointRadius: pts.length < 20 ? 3 : 0, tension: 0 }] },
    options: b1,
  }));
  const deltas = [];
  for (let i = 1; i < pts.length; i++) {
    const gap = toDay(pts[i].date) - toDay(pts[i - 1].date);
    deltas.push({ date: pts[i].date, v: (pts[i].v - pts[i - 1].v) / gap, gap });
  }
  const b2 = chartBase(dr);
  b2.plugins.tooltip.callbacks.title = it => fmtDate(deltas[it[0].dataIndex].date);
  b2.plugins.tooltip.callbacks.label = c => ` +${fmt(c.parsed.y)} в день${deltas[c.dataIndex].gap > 1 ? ` (среднее за ${deltas[c.dataIndex].gap} дн)` : ''}`;
  b2.scales.y.beginAtZero = true;
  S.charts.push(new Chart($('#dDay'), {
    type: 'bar',
    data: { labels: deltas.map(p => fmtDate(p.date, false)), datasets: [{ label: 'Прирост', data: deltas.map(p => Math.max(0, p.v)), backgroundColor: c1, borderRadius: 4, barPercentage: 0.85, categoryPercentage: 0.9 }] },
    options: b2,
  }));
}
function closeGame() {
  destroy(S.charts);
  $('#drawer').hidden = true;
  document.body.style.overflow = '';
  setHash({ g: null });
}

/* ---------- роутинг ---------- */
function readHash() { return new URLSearchParams(location.hash.slice(1)); }
function setHash(upd) {
  const h = readHash();
  for (const [k, v] of Object.entries(upd)) v == null ? h.delete(k) : h.set(k, v);
  const s = h.toString();
  history.replaceState(null, '', s ? '#' + s : location.pathname + location.search);
}
function setTab(tab) {
  S.tab = tab; S.limit = PAGE;
  setHash({ tab: tab === 'hot' ? null : tab });
  render();
  window.scrollTo({ top: 0 });
}

/* ---------- запуск ---------- */
async function init() {
  saveCmp();
  if (matchMedia('(min-width: 721px)').matches) $('#fbox').open = true;
  let data;
  try {
    const r = await fetch('data.json', { cache: 'no-cache' });
    data = await r.json();
  } catch (e) {
    $('#view').innerHTML = `<div class="empty">Не удалось загрузить data.json: ${esc(e.message)}</div>`;
    return;
  }
  S.data = data;
  S.games = data.games;
  for (const g of S.games) S.byId.set(g.id, g);

  const n = data.dates.length;
  $('#meta').textContent = `Данные на ${fmtDate(data.updated)} · снимков: ${n} (с ${fmtDate(data.dates[0])}) · игр в каталоге: ${S.games.filter(g => !g.rm).length}`;
  if (n < 11) {
    const w = $('#warn');
    w.hidden = false;
    w.innerHTML = `<b>История копится: ${n} ${plural(n, 'снимок', 'снимка', 'снимков')}.</b> «В день» считается по реальному приросту между снимками; YouTube округляет плеи, поэтому у больших игр темп пока грубый: <b>&lt;1M</b> значит, что число за день не изменилось. Окно до 7 дней сделает его точнее, ускорение появится примерно через 10 дней. Сравнение с ровесниками работает сразу.`;
  }
  $('#foot').textContent = `Источник: playables.games (публичный каталог YouTube Playables). Веса Hot: темп ${data.weights.rate}, ровесники ${data.weights.peer}, ускорение ${data.weights.mom}.`;

  const genres = [...new Set(S.games.map(g => g.g).filter(Boolean))].sort();
  $('#fGenre').insertAdjacentHTML('beforeend', genres.map(g => `<option>${esc(g)}</option>`).join(''));
  const pubs = [...new Set(S.games.map(g => g.p).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  $('#pubList').innerHTML = pubs.map(p => `<option value="${esc(p)}">`).join('');

  const h = readHash();
  if (h.get('tab') && document.querySelector(`#tabs [data-tab="${h.get('tab')}"]`)) S.tab = h.get('tab');
  render();
  if (h.get('g')) openGame(h.get('g'), false);

  $('#tabs').addEventListener('click', e => { const b = e.target.closest('button[data-tab]'); if (b) setTab(b.dataset.tab); });
  let t;
  $('#q').addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { S.limit = PAGE; render(); }, 120); });
  ['#fGenre', '#fAge', '#fMin', '#fStatus'].forEach(s => $(s).addEventListener('change', () => { S.limit = PAGE; render(); }));
  $('#fPub').addEventListener('change', () => { S.limit = PAGE; render(); });
  $('#fPub').addEventListener('input', () => { if (!$('#fPub').value) render(); });
  $('#dClose').addEventListener('click', closeGame);
  $('#drawer').addEventListener('click', e => { if (e.target.id === 'drawer') closeGame(); });
  addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#drawer').hidden) closeGame(); });
  addEventListener('hashchange', () => {
    const hh = readHash();
    const tab = hh.get('tab') || 'hot';
    if (tab !== S.tab && document.querySelector(`#tabs [data-tab="${tab}"]`)) { S.tab = tab; S.limit = PAGE; render(); }
    if (hh.get('g')) openGame(hh.get('g'), false); else if (!$('#drawer').hidden) closeGame();
  });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { render(); const id = readHash().get('g'); if (id) openGame(id, false); });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
