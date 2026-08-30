let allIncidents = [];
let currentFilter = 'all';

function setFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderIncidents();
}

function renderIncidents() {
  const list = document.getElementById('incident-list');
  const filtered = currentFilter === 'all' ? allIncidents
    : allIncidents.filter(i => currentFilter === 'down' ? !i.online : i.online);

  if (!filtered.length) {
    list.innerHTML = '<div class="empty">Sin incidencias registradas</div>';
    return;
  }

  list.innerHTML = filtered.map(i => {
    const cls = i.online ? 'up' : 'down';
    const label = i.online ? 'Recuperado' : 'Caída detectada';
    const dur = (!i.online && i.duration) ? `<div class="duration">Duración ${i.duration}</div>` : '';
    return `
      <div class="incident">
        <div class="incident-bar ${cls}"></div>
        <div class="incident-info">
          <div class="name">${i.name}</div>
          <div class="detail">${label}</div>
          ${dur}
        </div>
        <div class="incident-meta">${i.ts_human}</div>
      </div>`;
  }).join('');
}

async function load() {
  try {
    const res = await fetch('/api/incidents');
    const data = await res.json();
    allIncidents = data.incidents;
    renderIncidents();
  } catch {
    document.getElementById('incident-list').innerHTML =
      '<div class="empty">Error al cargar el historial</div>';
  }
}

const t = localStorage.getItem('homem_theme') || 'dark';
document.documentElement.setAttribute('data-theme', t);
load();

// Delegación: el HTML usa data-action en vez de onclick para que el CSP
// pueda prohibir 'unsafe-inline' en script-src.
document.addEventListener('click', e => {
  const btn = e.target.closest('[data-action="set-filter"]');
  if (btn) setFilter(btn.dataset.filter, btn);
});
