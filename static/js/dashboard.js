const REFRESH_MS=15000,ORDER_KEY='homem_order',COLLAPSED_KEY='homem_collapsed';
let editingId=null;
let csrfToken='';

// Fetch CSRF token on load
(async()=>{try{const r=await(await fetch('/api/csrf-token')).json();csrfToken=r.token;}catch{}})();

// Helper for POST/PUT/DELETE with CSRF
function fetchApi(url, opts={}){
  opts.headers=opts.headers||{};
  opts.headers['X-CSRF-Token']=csrfToken;
  if(opts.body && typeof opts.body==='object' && !(opts.body instanceof FormData)){
    opts.headers['Content-Type']='application/json';
    opts.body=JSON.stringify(opts.body);
  }
  return fetch(url, opts);
}

// Reloj
function updateClock(){document.getElementById('clock').textContent=new Date().toLocaleTimeString('es-ES');}
setInterval(updateClock,1000);updateClock();

// Tema — icono SVG outline (luna / sol)
function applyTheme(t){
  document.documentElement.setAttribute('data-theme',t);
  localStorage.setItem('homem_theme',t);
  const icon=document.getElementById('theme-icon');
  if(icon){
    icon.innerHTML = t==='dark'
      ? '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'  // luna
      : '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>';  // sol
  }
  document.querySelector('meta[name=theme-color]')?.setAttribute('content',t==='dark'?'#0e0f13':'#f5f6f8');
}
function toggleTheme(){applyTheme(document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark');}
applyTheme(localStorage.getItem('homem_theme')||'dark');

// Orden
function loadOrder(){try{return JSON.parse(localStorage.getItem(ORDER_KEY))||[];}catch{return[];}}
function saveOrder(ids){localStorage.setItem(ORDER_KEY,JSON.stringify(ids));}
function applyOrder(devices){
  const o=loadOrder();if(!o.length)return devices;
  return[...devices].sort((a,b)=>{const ia=o.indexOf(a.id),ib=o.indexOf(b.id);if(ia===-1&&ib===-1)return 0;if(ia===-1)return 1;if(ib===-1)return-1;return ia-ib;});
}

// Colapsado — el estado se persiste por dispositivo.
// Para ha_switch (AdGuard) el valor por defecto es PLEGADO (true),
// para el resto es DESPLEGADO (false). toggleCollapsed respeta el estado real del DOM.
function loadCollapsed(){try{return JSON.parse(localStorage.getItem(COLLAPSED_KEY))||{};}catch{return{};}}
function saveCollapsed(m){localStorage.setItem(COLLAPSED_KEY,JSON.stringify(m));}
function toggleCollapsed(id){
  const r=document.querySelector(`.device-row[data-id="${id}"]`);
  if(!r)return;
  const isNowCollapsed=r.classList.toggle('collapsed');   // invierte el estado visual real
  const m=loadCollapsed();
  m[id]=isNowCollapsed;
  saveCollapsed(m);
}

// Latencia
function latencyHtml(ms){
  if(ms==null)return'';
  let cls='fast',lbl=ms+'ms';
  if(ms>2000){cls='vslow';lbl=(ms/1000).toFixed(1)+'s';}
  else if(ms>500){cls='slow';}
  return`<div class="latency ${cls}">${lbl}</div>`;
}

// Sparkline SVG
const sparkCache={};
function sparklineHtml(id){
  const pts=sparkCache[id];
  if(!pts||pts.length<2)return'';
  const W=100,H=28,max=Math.max(...pts,1),min=Math.min(...pts,0);
  const range=max-min||1;
  const avg=pts.reduce((a,b)=>a+b,0)/pts.length;
  const color=avg>2000?'var(--red)':avg>500?'var(--amber)':'var(--green)';
  const coords=pts.map((v,i)=>`${(i/(pts.length-1)*W).toFixed(1)},${(H-(v-min)/range*H).toFixed(1)}`).join(' ');
  return`<div class="sparkline-wrap" title="Latencia (${pts.length} muestras, media ${Math.round(avg)}ms)"><svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><polyline points="${coords}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/></svg></div>`;
}

// Summary
function renderSummary(devices){
  const bar=document.getElementById('summary-bar');
  const total=devices.length;
  const down=devices.filter(d=>d.state==='down').length;
  const degraded=devices.filter(d=>d.state==='degraded').length;
  const maint=devices.filter(d=>d.state==='maintenance').length;
  const up=total-down-degraded-maint;
  
  if(!down && !degraded){
    bar.innerHTML=`<div class="chip"><span class="dot"></span>${total} dispositivos · todo operativo</div>`;
  } else {
    let html='';
    if(down) html+=`<div class="chip alert"><span class="dot"></span>${down} caído${down>1?'s':''}</div>`;
    if(degraded) html+=`<div class="chip" style="color:var(--amber)"><span class="dot" style="background:var(--amber)"></span>${degraded} degradado${degraded>1?'s':''}</div>`;
    html+=`<div class="chip">${up} operativo${up>1?'s':''}</div>`;
    bar.innerHTML=html;
  }
}

// ───── Iconos SVG reutilizables ─────
const ICONS={
  chevron:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
  grip:'<svg viewBox="0 0 24 24"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>',
  edit:'<svg viewBox="0 0 24 24" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  wrench:'<svg viewBox="0 0 24 24" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
  trash:'<svg viewBox="0 0 24 24" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"/></svg>',
  poweroff:'<svg viewBox="0 0 24 24" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>'
};

// Render de tarjetas — render diferencial in-place para evitar flicker.
const HB_CACHE = {};           // heartbeats por id (para no perderlos al refrescar)
const UPTIME_CACHE = {};       // uptime % por id
const ROW_SIG = {};            // signature de la última renderización por id

function deviceSignature(d){
  // Campos que afectan a la cabecera + cuerpo estático (no a datos async)
  return JSON.stringify([
    d.id, d.name, d.type, d.state, d.online, d.in_maintenance,
    d.switch_state, d.since_human, d.last_error, d.response_ms,
    d.cpu_pct, d.ram_pct, d.temp_c, d.disk_pct, d.uptime,
    d.containers ? d.containers.length : 0,
  ]);
}

function renderDevices(devices){
  const ordered=applyOrder(devices);
  const collapsed=loadCollapsed();
  const list=document.getElementById('device-list');
  const newIds=new Set(ordered.map(d=>d.id));
  // 1) Eliminar filas que ya no existen
  list.querySelectorAll('.device-row').forEach(r=>{ if(!newIds.has(r.dataset.id)) r.remove(); });
  // 2) Para cada device: crear o actualizar in-place
  let prevEl=null;
  ordered.forEach(d=>{
    let row=list.querySelector(`.device-row[data-id="${d.id}"]`);
    const sig=deviceSignature(d);
    if(row){
      // Mover a su posición si el orden cambió
      const correctPos = prevEl ? prevEl.nextElementSibling : list.firstChild;
      if(row!==correctPos) list.insertBefore(row, correctPos);
      // Actualizar solo si cambió la signature
      if(ROW_SIG[d.id]!==sig){
        updateRowInPlace(row, d, collapsed[d.id]);
        ROW_SIG[d.id]=sig;
      }
    } else {
      // Crear nueva fila
      row=createRow(d, collapsed[d.id]);
      ROW_SIG[d.id]=sig;
      if(prevEl) prevEl.after(row);
      else list.appendChild(row);
      initInteractionsFor(row);
      // Cargar datos async solo al crear
      loadUptime(d.id); loadLatency(d.id); loadHeartbeats(d.id);
    }
    prevEl=row;
  });
  loadHaSensors();
}

// ───── Construcción estática de una fila (solo al crear) ─────
function buildHeaderState(d){
  const lc=d.state==='degraded'?'degraded':(d.online?'on':'off');
  const protectionOff=(d.type==='ha_switch' && d.switch_state==='off');
  const isDown = !d.online;
  return {lc, protectionOff, isDown};
}

function createRow(d, collapsedPref){
  const {lc,protectionOff}=buildHeaderState(d);
  const defaultCollapsed=true;
  const isCollapsed=(typeof collapsedPref==='boolean')?collapsedPref:defaultCollapsed;
  const rc=(d.online?'':'is-down')+(isCollapsed?' collapsed':'')+(protectionOff?' protection-off':'');
  const maintBadge=d.in_maintenance?`<span class="badge-maint">Mant.</span>`:'';
  const haSensors=d.id==='home_assistant'?`<div id="ha-sensors" class="ha-sensors"></div>`:'';
  const extraSensors=(d.id==='adguard')?`<div id="adguard-sensors" class="ha-sensors"></div>`:'';

  const row=document.createElement('div');
  row.className=`device-row ${rc}`;
  row.draggable=true;
  row.dataset.id=d.id;
  row.innerHTML=`
    <div class="device-header">
      <div class="led ${lc}"></div>
      <div class="device-name-row">
        <span class="device-name">${d.name}</span>
        ${maintBadge}
      </div>
      <span class="hb-header" id="hb-${d.id}" aria-label="Historial de checks">
        <span class="hb-empty">…</span>
      </span>
      <span class="collapse-btn">${ICONS.chevron}</span>
      <span class="drag-handle">${ICONS.grip}</span>
    </div>
    <div class="device-body"><div class="device-body-inner">
      <div class="device-body-slot-switch"></div>
      <div class="device-body-content">
        <div class="device-left">
          <div class="device-left-slot"></div>
          ${haSensors}
          ${extraSensors}
          ${(d.type!=='system'&&d.type!=='remote_system')?sparklineHtml(d.id):''}
        </div>
        <div class="device-right"></div>
      </div>
      <div class="docker-slot"></div>
      <div class="device-actions">
        <button class="act-btn edit" title="Editar" aria-label="Editar" data-action="edit-device" data-id="${escAttr(d.id)}">${ICONS.edit}</button>
        <button class="act-btn maint ${d.in_maintenance?'active':''}" title="Mantenimiento" aria-label="Mantenimiento" data-action="toggle-maintenance" data-id="${escAttr(d.id)}" data-maintenance="${d.in_maintenance?1:0}">${ICONS.wrench}</button>
        ${d.type==='remote_system'?`<button class="act-btn poweroff" title="Apagar equipo" aria-label="Apagar" data-action="poweroff-device" data-id="${escAttr(d.id)}" data-name="${escAttr(d.name)}">${ICONS.poweroff}</button>`:''}
        <button class="act-btn del" title="Borrar" aria-label="Borrar" data-action="delete-device" data-id="${escAttr(d.id)}" data-name="${escAttr(d.name)}">${ICONS.trash}</button>
      </div>
    </div></div>`;
  updateRowInPlace(row, d, collapsedPref);
  return row;
}

// ───── Actualización in-place (sin reconstruir nada async) ─────
function updateRowInPlace(row, d, collapsedPref){
  const {lc,protectionOff,isDown}=buildHeaderState(d);
  const defaultCollapsed=true;
  const isCollapsed=(typeof collapsedPref==='boolean')?collapsedPref:defaultCollapsed;
  // Classes de la fila
  row.classList.toggle('is-down', isDown);
  row.classList.toggle('collapsed', isCollapsed);
  row.classList.toggle('protection-off', !!protectionOff);
  // LED
  const led=row.querySelector('.device-header > .led');
  if(led){
    led.className=`led ${lc}`;
  }
  // Name + badge (por si cambia nombre o mantenimiento)
  const nameEl=row.querySelector('.device-name');
  if(nameEl && nameEl.textContent!==d.name) nameEl.textContent=d.name;
  const nameRow=row.querySelector('.device-name-row');
  if(nameRow){
    const existingBadge=nameRow.querySelector('.badge-maint');
    if(d.in_maintenance && !existingBadge){
      nameRow.insertAdjacentHTML('beforeend','<span class="badge-maint">Mant.</span>');
    } else if(!d.in_maintenance && existingBadge){
      existingBadge.remove();
    }
  }
  // Maintenance button active state
  const maintBtn=row.querySelector('.act-btn.maint');
  if(maintBtn) maintBtn.classList.toggle('active', !!d.in_maintenance);

  // Cuerpo desplegable
  const switchSlot=row.querySelector('.device-body-slot-switch');
  const leftSlot=row.querySelector('.device-left-slot');
  const rightEl=row.querySelector('.device-right');
  const dockerSlot=row.querySelector('.docker-slot');
  if(switchSlot) switchSlot.innerHTML=buildSwitchRow(d);
  if(leftSlot) leftSlot.innerHTML=buildLeftContent(d);
  if(rightEl) rightEl.innerHTML=buildRightContent(d);
  if(dockerSlot) dockerSlot.innerHTML=buildDockerHtml(d);

  // Mantener datos async que ya teníamos (puntos y uptime) si los hay
  if(HB_CACHE[d.id]) paintHeartbeats(d.id, HB_CACHE[d.id]);
  if(UPTIME_CACHE[d.id]){
    const pctEl=row.querySelector(`#uptime-pct-${d.id}`);
    if(pctEl){pctEl.textContent='↑ '+UPTIME_CACHE[d.id].pct+'%';pctEl.style.color=UPTIME_CACHE[d.id].col;}
  }
  if(sparkCache[d.id]){
    const sp=row.querySelector('.sparkline-wrap');
    if(sp) sp.outerHTML=sparklineHtml(d.id);
  }
}

function buildSwitchRow(d){
  if(d.type!=='ha_switch') return '';
  return `<div class="switch-row">
    <div class="switch-info">
      <span class="switch-title">Bloqueo de anuncios</span>
      <span class="switch-sub">Protección DNS de AdGuard</span>
    </div>
    <div class="switch-control">
      <span class="switch-status-label ${d.switch_state==='on'?'on':'off'}" id="switch-status-${d.id}">
        ${d.switch_state==='on'?'Activado':(d.switch_state==='off'?'Desactivado':'—')}
      </span>
      <label class="ios-toggle">
        <input type="checkbox" id="switch-input-${d.id}" ${d.switch_state==='on'?'checked':''}
          data-action="toggle-switch" data-id="${escAttr(d.id)}">
        <span class="ios-toggle-track"></span>
      </label>
    </div>
  </div>`;
}

function buildLeftContent(d){
  const since=d.online?`${d.since_human} en línea`:`caído hace ${d.since_human}`;
  if((d.type==='system'||d.type==='remote_system') && d.cpu_pct!=null){
    const tempColor = d.temp_c==null?'var(--green)':d.temp_c<70?'var(--green)':d.temp_c<90?'var(--amber)':'var(--red)';
    const cpuColor  = d.cpu_pct==null?'var(--green)':d.cpu_pct<70?'var(--green)':d.cpu_pct<90?'var(--amber)':'var(--red)';
    const ramColor  = d.ram_pct==null?'var(--green)':d.ram_pct<70?'var(--green)':d.ram_pct<90?'var(--amber)':'var(--red)';
    const diskColor = d.disk_pct==null?'var(--green)':d.disk_pct<70?'var(--green)':d.disk_pct<90?'var(--amber)':'var(--red)';
    return `
      <div class="sys-row"><span class="sys-label">CPU</span><div class="sys-bar-wrap"><div class="sys-bar" style="width:${d.cpu_pct??0}%;background:${cpuColor}"></div></div><span class="sys-val">${d.cpu_pct!=null?d.cpu_pct+'%':'—'}</span></div>
      <div class="sys-row"><span class="sys-label">RAM</span><div class="sys-bar-wrap"><div class="sys-bar" style="width:${d.ram_pct??0}%;background:${ramColor}"></div></div><span class="sys-val">${d.ram_pct!=null?d.ram_pct+'%':'—'}</span></div>
      <div class="sys-row"><span class="sys-label">TEMP</span><div class="sys-bar-wrap"><div class="sys-bar" style="width:${d.temp_c!=null?Math.min(d.temp_c,100):0}%;background:${tempColor}"></div></div><span class="sys-val">${d.temp_c!=null?d.temp_c+'°C':'—'}</span></div>
      <div class="sys-row"><span class="sys-label">DISK</span><div class="sys-bar-wrap"><div class="sys-bar" style="width:${d.disk_pct??0}%;background:${diskColor}"></div></div><span class="sys-val">${d.disk_pct!=null?d.disk_pct+'%':'—'}</span></div>
      <div class="since-row" style="margin-top:6px;">Uptime: ${d.uptime||'—'}</div>`;
  }
  return `<div class="since-row">${since}</div>`
    +(d.last_error?`<div class="error-text">${d.last_error.replace(/camera/gi,'cam\u200Bera').replace(/tracker/gi,'trac\u200Bker').replace(/ads?[\s._-]/gi,m=>'a\u200B'+m.slice(1))}</div>`:'');
}

function buildRightContent(d){
  if((d.type==='system'||d.type==='remote_system') && d.uptime){
    return `<div style="font-family:var(--mono);font-size:10.5px;color:var(--text-dim)"><div>Uptime</div><div style="font-weight:600;color:var(--text);margin-top:2px">${d.uptime}</div></div>`;
  }
  return `${latencyHtml(d.response_ms)}<div class="uptime-pct" id="uptime-pct-${d.id}">…</div>`;
}

// Estado "expandido" de los contenedores docker (persiste entre renders).
// Se guarda por nombre de contenedor en localStorage para sobrevivir a refresh.
const DOCKER_EXPANDED_KEY='homem_docker_expanded';
function loadDockerExpanded(){try{return new Set(JSON.parse(localStorage.getItem(DOCKER_EXPANDED_KEY))||[]);}catch{return new Set();}}
function saveDockerExpanded(set){try{localStorage.setItem(DOCKER_EXPANDED_KEY,JSON.stringify([...set]));}catch{}}
// Lee el nombre del data-name del propio elemento.
function toggleDockerContainerFromHeader(headerEl){
  const item=headerEl.closest('.docker-container-item');
  if(!item||!item.dataset.name)return;
  const name=item.dataset.name;
  const set=loadDockerExpanded();
  const willOpen=!set.has(name);
  if(willOpen) set.add(name); else set.delete(name);
  saveDockerExpanded(set);
  item.classList.toggle('expanded', willOpen);
}

function buildDockerHtml(d){
  if(d.type!=='docker' || !d.containers || !d.containers.length) return '';
  const expanded=loadDockerExpanded();
  const items=d.containers.map(c=>{
    const isRunning=c.state==='running';
    const isOpen=expanded.has(c.name);
    return `<div class="docker-container-item${isOpen?' expanded':''}" data-name="${escAttr(c.name)}">
      <div class="docker-container-header" data-action="toggle-docker">
        <div class="led ${isRunning?'on':'off'}" style="width:8px;height:8px;min-width:8px"></div>
        <span style="font-size:12px;font-weight:500;">${escAttr(c.name)}</span>
        <span style="margin-left:auto;font-size:11px;color:var(--text-dim)">${escAttr(c.status||c.state||'')}</span>
        <span class="collapse-btn" style="margin-left:6px">${ICONS.chevron}</span>
      </div>
      ${isRunning?`<div class="docker-container-body">
        <div class="sys-row"><span class="sys-label" style="width:70px">CPU</span><span class="sys-val" style="width:auto;color:var(--text)">${escAttr(c.cpu||'n/a')}</span></div>
        <div class="sys-row"><span class="sys-label" style="width:70px">RAM</span><span class="sys-val" style="width:auto;color:var(--text)">${escAttr(c.mem||'n/a')}</span></div>
        <div class="sys-row"><span class="sys-label" style="width:70px">Red</span><span class="sys-val" style="width:auto;color:var(--text-dim)">${escAttr(c.net||'n/a')}</span></div>
      </div>`:''}
    </div>`;
  }).join('');
  return `<div class="docker-containers">${items}</div>`;
}
function escAttr(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

async function loadUptime(id){
  try{
    const data=await(await fetch(`/api/uptime/${id}`)).json();
    const pct=data.uptime_pct;
    const col=pct>=99?'var(--green)':pct>=90?'var(--amber)':'var(--red)';
    UPTIME_CACHE[id]={pct,col};
    const el=document.getElementById(`uptime-pct-${id}`);
    if(el){el.textContent='↑ '+pct+'%';el.style.color=col;}
  }catch{}
}

async function loadLatency(id){
  try{
    const data=await(await fetch(`/api/latency/${id}`)).json();
    if(data.points&&data.points.length){
      sparkCache[id]=data.points.map(p=>p.ms);
      const row=document.querySelector(`.device-row[data-id="${id}"] .sparkline-wrap`);
      if(row)row.outerHTML=sparklineHtml(id);
    }
  }catch{}
}

// Heartbeat bars (estilo Uptime Kuma · 3 círculos por check) — en la cabecera
async function loadHeartbeats(id){
  try{
    const data=await(await fetch(`/api/heartbeats/${id}`)).json();
    if(!data.points)return;
    HB_CACHE[id]=data.points;
    paintHeartbeats(id, data.points);
  }catch{}
}

function paintHeartbeats(id, points){
  const el=document.getElementById(`hb-${id}`);
  if(!el)return;
  if(!points || !points.length){
    el.innerHTML='<span class="hb-empty">sin datos</span>';
    return;
  }
  // points = buckets de 15 min (ASC). Cada uno: {start, end, state, n}
  const LBL={up:'En línea',down:'Caído',degraded:'Degradado',pending:'Pendiente',maintenance:'Mantenimiento',unknown:'Sin datos'};
  const cols=points.map(p=>{
    const cls=p.state||'unknown';
    const d0=new Date(p.start*1000), d1=new Date(p.end*1000);
    const f=d0.toLocaleDateString('es-ES',{day:'2-digit',month:'short'});
    const h0=d0.toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit'});
    const h1=d1.toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit'});
    const nfo=cls==='unknown'?'':` · ${p.n} checks`;
    return `<div class="hb-col ${cls}" title="${f} ${h0}–${h1} · ${LBL[cls]||cls}${nfo}"><span class="hb-dot"></span><span class="hb-dot"></span><span class="hb-dot"></span></div>`;
  }).join('');
  el.innerHTML=cols;
}
// Modal
function updateFields(){
  const t=document.getElementById('f-type').value;
  ['http','ping','port','ha','dns','tls','docker','heartbeat','http-advanced'].forEach(k=>{
    const el=document.getElementById('fields-'+k);
    if(!el)return;
    el.style.display='none';
  });
  if(t==='http'){document.getElementById('fields-http').style.display='';document.getElementById('fields-http-advanced').style.display='';}
  else if(t==='ping'){document.getElementById('fields-ping').style.display='';}
  else if(t==='port'){document.getElementById('fields-port').style.display='';}
  else if(t==='ha_entity'||t==='ha_switch'){document.getElementById('fields-ha').style.display='';}
  else if(t==='dns'){document.getElementById('fields-dns').style.display='';}
  else if(t==='tls'){document.getElementById('fields-tls').style.display='';}
  else if(t==='docker'){document.getElementById('fields-docker').style.display='';}
  else if(t==='heartbeat'){document.getElementById('fields-heartbeat').style.display='';}
  // 'system' type needs no extra fields
}
function openModal(device){
  editingId=device?device.id:null;
  document.getElementById('modal-title').textContent=device?'Editar dispositivo':'Añadir dispositivo';
  document.getElementById('f-name').value=device?.name||'';
  document.getElementById('f-id').value=device?.id||'';
  document.getElementById('f-id').disabled=!!device;
  document.getElementById('f-type').value=device?.type||'http';
  document.getElementById('f-url').value=device?.url||'';
  document.getElementById('f-host-ping').value=device?.host||'';
  document.getElementById('f-host-port').value=device?.host||'';
  document.getElementById('f-port').value=device?.port||'';
  document.getElementById('f-entity').value=device?.entity_id||'';
  document.getElementById('f-timeout').value=device?.timeout||8;
  document.getElementById('f-check-interval').value=device?.check_interval||60;
  document.getElementById('f-max-retries').value=device?.max_retries||3;
  document.getElementById('f-http-method').value=device?.http_method||'GET';
  document.getElementById('f-expected-status').value=device?.expected_status_codes||'';
  document.getElementById('f-verify-keyword').value=device?.verify_keyword||'';
  document.getElementById('f-follow-redirects').value=device?.follow_redirects!==false?'true':'false';
  document.getElementById('f-tls-host').value=device?.host||'';
  document.getElementById('f-tls-warn-days').value=device?.tls_warn_days||14;
  document.getElementById('f-dns-host').value=device?.host||'';
  document.getElementById('f-dns-expected').value=device?.dns_expected||'';
  document.getElementById('f-docker-container').value=device?.container_name||'';
  document.getElementById('f-heartbeat-interval').value=device?.heartbeat_interval||60;
  updateFields();
  document.getElementById('modal-overlay').classList.add('open');
}
function closeModal(){document.getElementById('modal-overlay').classList.remove('open');}
function closeModalOnBg(e){if(e.target.id==='modal-overlay')closeModal();}
async function saveDevice(){
  const type=document.getElementById('f-type').value;
  const name=document.getElementById('f-name').value.trim();
  const id=editingId||document.getElementById('f-id').value.trim().replace(/\s+/g,'_');
  const timeout=parseInt(document.getElementById('f-timeout').value)||8;
  const check_interval=parseInt(document.getElementById('f-check-interval').value)||60;
  const max_retries=parseInt(document.getElementById('f-max-retries').value)||3;
  if(!name||!id){alert('Nombre e ID son obligatorios');return;}
  const device={id,name,type,timeout,check_interval,max_retries};
  if(type==='http'){
    device.url=document.getElementById('f-url').value.trim();
    device.http_method=document.getElementById('f-http-method').value;
    device.expected_status_codes=document.getElementById('f-expected-status').value.trim();
    device.verify_keyword=document.getElementById('f-verify-keyword').value.trim();
    device.follow_redirects=document.getElementById('f-follow-redirects').value==='true';
  }
  if(type==='ping')device.host=document.getElementById('f-host-ping').value.trim();
  if(type==='port'){device.host=document.getElementById('f-host-port').value.trim();device.port=parseInt(document.getElementById('f-port').value);}
  if(type==='ha_entity'||type==='ha_switch')device.entity_id=document.getElementById('f-entity').value.trim();
  if(type==='dns'){device.host=document.getElementById('f-dns-host').value.trim();device.dns_expected=document.getElementById('f-dns-expected').value.trim();}
  if(type==='tls'){device.host=document.getElementById('f-tls-host').value.trim();device.tls_warn_days=parseInt(document.getElementById('f-tls-warn-days').value)||14;}
  if(type==='docker'){device.container_name=document.getElementById('f-docker-container').value.trim();}
  if(type==='heartbeat'){device.heartbeat_interval=parseInt(document.getElementById('f-heartbeat-interval').value)||60;}
  await fetchApi(editingId?`/api/devices/${editingId}`:'/api/devices',{method:editingId?'PUT':'POST',body:device});
  closeModal();refresh();
}
async function editDevice(id){
  const devs=await(await fetch('/api/devices')).json();
  const d=devs.find(x=>x.id===id);if(d)openModal(d);
}
async function confirmDelete(id,name){
  if(!confirm(`¿Borrar "${name}"? Se eliminará también su historial.`))return;
  await fetchApi(`/api/devices/${id}`,{method:'DELETE'});refresh();
}

async function confirmPoweroff(id, name){
  if(!confirm(`¿Apagar "${name}"? El equipo se apagará inmediatamente.`))return;
  const res = await fetchApi(`/api/devices/${id}/poweroff`,{method:'POST'});
  if(res && res.ok){
    alert(`Orden de apagado enviada a "${name}".`);
  } else {
    alert(`Error al apagar "${name}".`);
  }
}

// Toggle AdGuard
async function toggleSwitch(id, checked){
  const action = checked ? 'turn_on' : 'turn_off';
  const statusEl = document.getElementById(`switch-status-${id}`);
  const input = document.getElementById(`switch-input-${id}`);
  if(statusEl){ statusEl.textContent='...'; statusEl.className='switch-status-label off'; }
  try {
    const res = await fetchApi(`/api/toggle/${id}`, {
      method:'POST',body:{action}
    });
    const data = await res.json();
    if(!data.ok){
      if(input) input.checked = !checked;
      if(statusEl){ statusEl.textContent='Error'; statusEl.className='switch-status-label off'; }
      alert('Error: ' + data.message);
    } else {
      if(statusEl){
        statusEl.textContent = checked ? 'Activado' : 'Desactivado';
        statusEl.className = 'switch-status-label ' + (checked ? 'on' : 'off');
      }
    }
  } catch {
    if(input) input.checked = !checked;
    if(statusEl){ statusEl.textContent='Error'; statusEl.className='switch-status-label off'; }
  }
}

// Drag & Drop
function initInteractions(){
  document.querySelectorAll('.device-row').forEach(row=>{
    if(!row.dataset.intd){
      row.dataset.intd='1';
      initInteractionsFor(row);
    }
  });
}

// Inicializa los listeners de una fila individual (click cabecera + drag desktop + drag touch).
let _touchDragInit=false;
function initInteractionsFor(row){
  const list=document.getElementById('device-list');
  row.querySelector('.device-header').addEventListener('click',e=>{
    if(!e.target.closest('.drag-handle')&&!e.target.closest('.switch-row')&&!e.target.closest('.ios-toggle')&&!e.target.closest('.hb-header'))
      toggleCollapsed(row.dataset.id);
  });
  row.addEventListener('dragstart',e=>{if(!e.target.closest('.drag-handle')&&e.target!==row){e.preventDefault();return;}_draggedRow=row;setTimeout(()=>row.classList.add('dragging'),0);});
  row.addEventListener('dragend',()=>{row.classList.remove('dragging');list.querySelectorAll('.device-row').forEach(r=>r.classList.remove('drag-over'));persistOrder();});
  row.addEventListener('dragover',e=>{e.preventDefault();if(row!==_draggedRow){list.querySelectorAll('.device-row').forEach(r=>r.classList.remove('drag-over'));row.classList.add('drag-over');}});
  row.addEventListener('drop',e=>{e.preventDefault();if(row!==_draggedRow&&_draggedRow){const all=[...list.querySelectorAll('.device-row')];all.indexOf(_draggedRow)<all.indexOf(row)?row.after(_draggedRow):row.before(_draggedRow);}});

  // Touch drag para esta fila (handler global solo se registra una vez)
  row.querySelector('.drag-handle').addEventListener('touchstart',e=>{
    _tdRow=row;const t=e.touches[0],r=row.getBoundingClientRect();_tdOffY=t.clientY-r.top;
    _tdClone=row.cloneNode(true);Object.assign(_tdClone.style,{position:'fixed',zIndex:1000,width:r.width+'px',left:r.left+'px',top:r.top+'px',opacity:.85,pointerEvents:'none',border:'1px solid var(--accent)',borderRadius:'22px',background:'var(--sheet)',transition:'none'});
    document.body.appendChild(_tdClone);row.classList.add('dragging');e.preventDefault();
  },{passive:false});

  if(!_touchDragInit){
    _touchDragInit=true;
    document.addEventListener('touchmove',e=>{
      if(!_tdRow||!_tdClone)return;const t=e.touches[0];_tdClone.style.top=(t.clientY-_tdOffY)+'px';
      _tdClone.style.display='none';const el=document.elementFromPoint(t.clientX,t.clientY);_tdClone.style.display='';
      const tgt=el&&el.closest('.device-row');list.querySelectorAll('.device-row').forEach(r=>r.classList.remove('drag-over'));
      if(tgt&&tgt!==_tdRow)tgt.classList.add('drag-over');e.preventDefault();
    },{passive:false});
    document.addEventListener('touchend',e=>{
      if(!_tdRow)return;const t=e.changedTouches[0];if(_tdClone){_tdClone.remove();_tdClone=null;}_tdRow.classList.remove('dragging');
      list.querySelectorAll('.device-row').forEach(r=>r.classList.remove('drag-over'));
      const el=document.elementFromPoint(t.clientX,t.clientY);const tgt=el&&el.closest('.device-row');
      if(tgt&&tgt!==_tdRow){const all=[...list.querySelectorAll('.device-row')];all.indexOf(_tdRow)<all.indexOf(tgt)?tgt.after(_tdRow):tgt.before(_tdRow);}
      persistOrder();_tdRow=null;
    });
  }
}
let _draggedRow=null,_tdRow=null,_tdClone=null,_tdOffY=0;
function persistOrder(){saveOrder([...document.querySelectorAll('.device-row')].map(r=>r.dataset.id));}

// Fetch
async function loadHaSensors(){
  try{
    const data=await(await fetch('/api/ha-sensors')).json();
    const el=document.getElementById('ha-sensors');
    if(el){
      const cpu=data['sensor.system_monitor_temperatura_del_procesador'];
      const ram=data['sensor.system_monitor_uso_de_memoria_2'];
      el.innerHTML=
        (cpu?`<div class="ha-sensor">CPU <b>${cpu.state}${cpu.unit}</b></div>`:'')
       +(ram?`<div class="ha-sensor">RAM <b>${ram.state}${ram.unit}</b></div>`:'');
    }
    const elAd=document.getElementById('adguard-sensors');
    if(elAd){
      const dns=data['sensor.adguard_home_consultas_dns'];
      const blocked=data['sensor.adguard_home_proporcion_de_consultas_dns_bloqueadas'];
      elAd.innerHTML=
        (dns?`<div class="ha-sensor">Consultas <b>${dns.state}</b></div>`:'')
       +(blocked?`<div class="ha-sensor">Bloqueo <b>${blocked.state}${blocked.unit}</b></div>`:'');
    }
  }catch{}
}

async function toggleMaintenance(id, isActive){
  if(isActive){
    await fetchApi(`/api/devices/${id}/maintenance`,{method:'POST',body:{hours:0}});
  } else {
    const h=prompt('Horas de mantenimiento (ej: 1, 2, 0.5):','1');
    if(!h)return;
    await fetchApi(`/api/devices/${id}/maintenance`,{method:'POST',body:{hours:parseFloat(h)}});
  }
  refresh();
}

async function forceCheck(){
  const btn=document.getElementById('check-btn');
  btn.disabled=true;btn.style.opacity='.5';
  await fetchApi('/api/force-check',{method:'POST'});
  setTimeout(()=>{refresh();btn.disabled=false;btn.style.opacity='';}, 2000);
}

// Filtro Todos/Online/Offline
let currentFilter='all';
function setFilter(f){
  currentFilter=f;
  document.querySelectorAll('#filter-bar .chip').forEach(btn=>{
    btn.style.border=btn.dataset.filter===f?'1px solid var(--accent)':'1px solid var(--card-ring)';
    btn.style.color=btn.dataset.filter===f?'var(--accent)':'var(--text-dim)';
  });
  applyFilter();
}
function applyFilter(){
  document.querySelectorAll('#device-list > .device-row').forEach(row=>{
    const isDown=row.classList.contains('is-down');
    if(currentFilter==='all'){row.style.display='';}
    else if(currentFilter==='online'){row.style.display=isDown?'none':'';}
    else if(currentFilter==='offline'){row.style.display=isDown?'':'none';}
  });
}

// Carga instantánea: cache del último estado en localStorage.
// Al abrir la app se pinta lo cacheado sin esperar al primer fetch.
const STATUS_CACHE_KEY='homem_status_cache';
function paintStatus(data, opts={}){
  renderSummary(data.devices);
  renderDevices(data.devices);
  loadHaSensors();
  applyFilter();
  const note=document.getElementById('footer-note');
  if(opts.stale){
    const age=Math.round((Date.now()/1000 - (data._cached_at||data.server_time))/60);
    note.textContent=`Mostrando datos en caché${age>0?` de hace ${age} min`:''} · actualizando…`;
  } else {
    note.textContent=`Actualizado cada ${REFRESH_MS/1000}s · ${new Date(data.server_time*1000).toLocaleTimeString('es-ES')}`;
  }
}

async function refresh(){
  try{
    const res=await fetch('/api/status');
    if(!res.ok){document.getElementById('footer-note').textContent='Error: '+res.status;return;}
    const data=await res.json();
    if(!data.devices){document.getElementById('footer-note').textContent='Error: no devices in response';return;}
    paintStatus(data);
    // Guardar copia para la próxima carga instantánea
    try{
      data._cached_at=Date.now()/1000;
      localStorage.setItem(STATUS_CACHE_KEY, JSON.stringify(data));
    }catch{}
  }catch(e){document.getElementById('footer-note').textContent='Error: '+e.message;}
}

// 1) Pintar caché antigua de inmediato (si existe) → apertura instantánea.
(function initFromCache(){
  try{
    const raw=localStorage.getItem(STATUS_CACHE_KEY);
    if(raw){
      const data=JSON.parse(raw);
      if(data && data.devices && data.devices.length){
        paintStatus(data, {stale:true});
      }
    }
  }catch{}
})();
// 2) Refresco en segundo plano (fetch real) y cada REFRESH_MS.
refresh();setInterval(refresh,REFRESH_MS);

// Pull-to-refresh
let touchStartY=0;
document.addEventListener('touchstart',e=>{touchStartY=e.touches[0].clientY;},{passive:true});
document.addEventListener('touchend',e=>{
  const diff=e.changedTouches[0].clientY-touchStartY;
  if(diff>120 && window.scrollY===0){refresh();}
},{passive:true});

// Push
let swReg=null;
async function initPush(){
  const btn=document.getElementById('notif-btn');
  if(!('serviceWorker' in navigator)||!('PushManager' in window)){return;}
  swReg=await navigator.serviceWorker.register('/static/sw.js');
  await navigator.serviceWorker.ready;
  const sub=await swReg.pushManager.getSubscription();
  if(sub){btn.classList.add('active');}
}
async function toggleNotifications(){
  const btn=document.getElementById('notif-btn');
  if(!swReg){alert('El service worker aun no esta listo. Espera un momento y vuelve a intentarlo.');return;}
  const sub=await swReg.pushManager.getSubscription();
  if(sub){
    await sub.unsubscribe();
    await fetchApi('/api/unsubscribe',{method:'POST',body:{endpoint:sub.endpoint}});
    btn.classList.remove('active');
    alert('Notificaciones desactivadas.');return;
  }
  const perm=await Notification.requestPermission();
  if(perm!=='granted'){alert('Permiso de notificaciones denegado en el navegador.');return;}
  const{publicKey}=await(await fetch('/api/vapid-key')).json();
  const newSub=await swReg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:urlBase64ToUint8Array(publicKey)});
  await fetchApi('/api/subscribe',{method:'POST',body:newSub.toJSON()});
  btn.classList.add('active');
  alert('Notificaciones activadas correctamente.');
}
function urlBase64ToUint8Array(b64){
  const pad='='.repeat((4-b64.length%4)%4);
  const raw=atob((b64+pad).replace(/-/g,'+').replace(/_/g,'/'));
  return Uint8Array.from([...raw].map(c=>c.charCodeAt(0)));
}
initPush();


// ── Delegación de eventos ──────────────────────────────────────────
// El HTML declara data-action en lugar de onclick, para que el CSP pueda
// prohibir 'unsafe-inline' en script-src. Al vivir el listener en document,
// las filas que se regeneran en cada refresco quedan cubiertas sin re-enlazar.
const CLICK_ACTIONS={
  'toggle-theme':      ()=>toggleTheme(),
  'force-check':       ()=>forceCheck(),
  'open-modal':        ()=>openModal(),
  'close-modal':       ()=>closeModal(),
  'save-device':       ()=>saveDevice(),
  'toggle-notifications':()=>toggleNotifications(),
  'set-filter':        el=>setFilter(el.dataset.filter),
  'edit-device':       el=>editDevice(el.dataset.id),
  'toggle-maintenance':el=>toggleMaintenance(el.dataset.id, el.dataset.maintenance==='1'),
  'delete-device':     el=>confirmDelete(el.dataset.id, el.dataset.name),
  'poweroff-device':   el=>confirmPoweroff(el.dataset.id, el.dataset.name),
  'toggle-docker':     el=>toggleDockerContainerFromHeader(el),
};

document.addEventListener('click',e=>{
  const el=e.target.closest('[data-action]');
  const fn=el&&CLICK_ACTIONS[el.dataset.action];
  if(fn)fn(el);
});

// Los switches de Home Assistant emiten change, no click.
document.addEventListener('change',e=>{
  const el=e.target.closest('[data-action="toggle-switch"]');
  if(el)toggleSwitch(el.dataset.id, el.checked);
});

// closeModalOnBg ya comprueba que el click cayó en el overlay y no en la
// tarjeta, así que el modal interior no necesita parar la propagación.
document.getElementById('modal-overlay').addEventListener('click',closeModalOnBg);
document.getElementById('f-type').addEventListener('change',updateFields);
