// Diagrama de dependencias entre monitores.
// El árbol sale del campo depends_on de cada monitor: un nodo cuelga de aquel
// del que depende, y los que no dependen de nadie son raíces. Los conectores
// se dibujan en un SVG por encima, midiendo las cajas ya maquetadas, que es la
// única forma de que las flechas caigan exactamente donde están.

const REFRESH_MS = 15000;
const THEME_KEY = 'homem_theme';
const INDENT = 28;      // sangría por nivel (debe dejar sitio al codo y la flecha)
const MAX_INDENT = 4;   // a partir de aquí no se sangra más (pantalla estrecha)

// ─── Tema (compartido con el dashboard) ───
function applyTheme(t){
  document.documentElement.setAttribute('data-theme', t);
  const icon = document.getElementById('theme-icon');
  if(icon){
    icon.innerHTML = t === 'dark'
      ? '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
      : '<circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.2M12 19.8V22M2 12h2.2M19.8 12H22M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M19.1 4.9l-1.6 1.6M6.5 17.5l-1.6 1.6"/>';
  }
  try{ localStorage.setItem(THEME_KEY, t); }catch{}
}
function toggleTheme(){
  applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
  requestAnimationFrame(drawWires);
}
try{ applyTheme(localStorage.getItem(THEME_KEY) || 'dark'); }catch{ applyTheme('dark'); }

// ─── Utilidades ───
const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');

const STATE_LABEL = {
  up:'Operativo', down:'Caído', degraded:'Degradado',
  maintenance:'Mantenimiento', pending:'Sin datos',
};
const TYPE_LABEL = {
  system:'raspberry', remote_system:'agente', http:'http', ping:'ping',
  port:'puerto', dns:'dns', tls:'tls', docker:'docker',
  ha_entity:'ha', ha_switch:'switch', heartbeat:'latido',
};

function stateOf(d){
  if(d.in_maintenance) return 'maintenance';
  return d.state || (d.online ? 'up' : 'down');
}

// ─── Construcción del árbol ───
function buildForest(devices){
  const byId = new Map();
  devices.forEach(d => byId.set(d.id, Object.assign({}, d, {children:[]})));

  const roots = [];
  byId.forEach(node => {
    const parent = node.depends_on ? byId.get(node.depends_on) : null;
    if(parent && parent !== node) parent.children.push(node);
    else roots.push(node);
  });

  // Un ciclo (a→b→a) deja nodos fuera del bosque: se rescatan como raíces
  // para que nunca desaparezcan del diagrama.
  const seen = new Set();
  (function walk(list){
    list.forEach(n => { if(!seen.has(n.id)){ seen.add(n.id); walk(n.children); } });
  })(roots);
  byId.forEach(node => { if(!seen.has(node.id)){ seen.add(node.id); roots.push(node); } });

  const order = {down:0, degraded:1, maintenance:2, pending:3, up:4};
  const sort = list => {
    list.sort((a,b) => (b.children.length - a.children.length)
      || (order[stateOf(a)] - order[stateOf(b)])
      || a.name.localeCompare(b.name));
    list.forEach(n => sort(n.children));
  };
  sort(roots);
  return roots;
}

// ─── Render ───
function nodeHtml(d){
  const st = stateOf(d);
  const rows = [];
  rows.push(['Estado', STATE_LABEL[st] || st]);
  if(d.since_human) rows.push([st === 'up' ? 'En línea' : 'Desde hace', d.since_human]);
  if(d.cpu_pct != null) rows.push(['CPU', d.cpu_pct + '%']);
  if(d.ram_pct != null) rows.push(['RAM', d.ram_pct + '%']);
  if(d.temp_c != null) rows.push(['Temp', d.temp_c + '°C']);
  if(d.cpu_pct == null && d.response_ms != null) rows.push(['Latencia', d.response_ms + ' ms']);
  if(st !== 'up' && d.last_error) rows.push(['Error', d.last_error]);

  const body = rows.map(([k,v]) =>
    `<div class="node-line"><span class="k">${esc(k)}</span>` +
    `<span class="rule"></span><span class="v">${esc(v)}</span></div>`).join('');

  const cls = st === 'down' ? ' is-down' : st === 'degraded' ? ' is-degraded' : '';
  return `<div class="node${cls}">
    <div class="node-head">
      <span class="led ${st}"></span>
      <span class="node-name">${esc(d.name)}</span>
      <span class="node-kind">${esc(TYPE_LABEL[d.type] || d.type || '—')}</span>
    </div>
    <div class="node-body">${body}</div>
  </div>`;
}

let placed = [];   // {node, el, depth} para poder dibujar los cables

function renderForest(roots){
  const host = document.getElementById('nodes');
  host.innerHTML = '';
  placed = [];

  (function place(list, depth){
    list.forEach(node => {
      const row = document.createElement('div');
      row.className = 'node-row';
      row.style.paddingLeft = (Math.min(depth, MAX_INDENT) * INDENT) + 'px';
      row.innerHTML = nodeHtml(node);
      host.appendChild(row);
      placed.push({node, el: row.firstElementChild, depth});
      place(node.children, depth + 1);
    });
  })(roots, 0);
}

// ─── Conectores ───
// Se dibujan tras el layout: una vertical que baja del padre y una rama
// horizontal con punta de flecha hacia cada hijo (el patrón de Cloudflare,
// girado a vertical porque esto se mira en el móvil).
function drawWires(){
  const svg = document.getElementById('wires');
  const graph = document.getElementById('graph');
  if(!svg || !graph) return;
  svg.innerHTML = `<defs><marker id="arrow" viewBox="0 0 8 8" refX="6.5" refY="4"
      markerWidth="7" markerHeight="7" orient="auto">
      <path d="M0.8 0.9 L6.8 4 L0.8 7.1 z" fill="var(--wire)" stroke="none"/>
    </marker></defs>`;

  const base = graph.getBoundingClientRect();
  const box = new Map(placed.map(p => [p.node.id, p.el.getBoundingClientRect()]));
  const R = 5;        // radio del codo
  const GAP = 6;      // hueco entre la punta y la caja

  const add = (d, arrow) => {
    const el = document.createElementNS('http://www.w3.org/2000/svg','path');
    el.setAttribute('d', d);
    if(arrow) el.setAttribute('marker-end', 'url(#arrow)');
    svg.appendChild(el);
  };

  placed.forEach(({node}) => {
    if(!node.children.length) return;
    const pr = box.get(node.id);
    if(!pr) return;

    const x  = pr.left - base.left + 11;   // vertical alineada con el LED
    const y0 = pr.bottom - base.top;
    const kids = node.children.map(c => box.get(c.id)).filter(Boolean);
    if(!kids.length) return;

    const lastY = kids[kids.length-1].top - base.top + kids[kids.length-1].height/2;
    add(`M${x} ${y0} L${x} ${lastY - R}`, false);   // tronco

    kids.forEach(cr => {
      const cy = cr.top - base.top + cr.height/2;
      const cx = cr.left - base.left - GAP;
      // El codo consume R en horizontal; si el tramo restante fuera negativo
      // la flecha saldría girada, así que la rama se dibuja solo si cabe.
      if(cx <= x + R) return;
      add(`M${x} ${cy - R} Q${x} ${cy} ${x + R} ${cy} L${cx} ${cy}`, true);
    });
  });

  svg.setAttribute('height', graph.getBoundingClientRect().height);
}

// ─── Resumen y avisos ───
function renderSummary(devices){
  const count = st => devices.filter(d => stateOf(d) === st).length;
  const bits = [
    ['up','operativos'], ['down','caídos'],
    ['degraded','degradados'], ['maintenance','en mantenimiento'],
  ].filter(([st]) => count(st) > 0)
   .map(([st,label]) => `<span class="chip"><span class="led ${st}"></span>${count(st)} ${label}</span>`);
  document.getElementById('summary').innerHTML = bits.join('');
}

function renderHint(devices){
  const withDeps = devices.filter(d => d.depends_on).length;
  const hint = document.getElementById('hint');
  hint.innerHTML = withDeps === 0
    ? 'Ningún monitor tiene dependencias configuradas, así que todos aparecen al mismo nivel.<br>' +
      'Edita un dispositivo y elige <code>Depende de</code> para colgarlo de otro.'
    : `${withDeps} de ${devices.length} monitores cuelgan de otro.`;
}

// ─── Ciclo de actualización ───
async function refresh(){
  try{
    const res = await fetch('/api/status');
    if(res.status === 401){ location.href = '/login'; return; }
    const data = await res.json();
    const devices = data.devices || [];
    if(!devices.length){
      document.getElementById('nodes').innerHTML =
        '<div class="empty">No hay dispositivos configurados.</div>';
      return;
    }
    renderSummary(devices);
    renderForest(buildForest(devices));
    renderHint(devices);
    requestAnimationFrame(drawWires);
  }catch{}
}

document.addEventListener('click', e => {
  const el = e.target.closest('[data-action]');
  if(!el) return;
  if(el.dataset.action === 'toggle-theme') toggleTheme();
  if(el.dataset.action === 'reload') refresh();
});

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(drawWires, 120);
});

refresh();
setInterval(refresh, REFRESH_MS);
