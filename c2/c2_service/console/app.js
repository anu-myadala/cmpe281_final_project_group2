// SCICP C2 device console: the IoT engineer's Vehicles, Devices, Provisioning and Network pages and
// the per-car Data tab (Section 5 of the C2 design). Plain JavaScript, served by C2 at /console/ and
// reached through nginx at http://<host>:8080/c2/console/. It signs in with C1 (/api/v1/auth/login)
// and calls C2 at /c2/api/v1 with the same JWT. It refreshes every 3 s.
'use strict';

const UNDER_NGINX = location.pathname.startsWith('/c2/');
const C2 = UNDER_NGINX ? '/c2/api/v1' : '/api/v1';
const C1 = '/api/v1';
const STAFF = ['IOT_ENGINEER', 'SERVICE_STAFF', 'SYS_ADMIN'];
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const state = { token: null, user: null, tenantId: null, tenants: [], vehicles: [], vehicle: null, devices: [],
  tab: 'devices', editor: null, netRows: [], timer: null };

// -- HTTP ----------------------------------------------------------------------------------------------
async function call(method, path, body, base = C2) {
  const url = new URL(base + path, location.origin);
  if (state.tenantId && base === C2 && STAFF.includes(state.user?.role)) url.searchParams.set('tenantId', state.tenantId);
  const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json', ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 401 && state.token) { logout(); throw new Error('Session expired'); }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const e = data?.error || {};
    const err = new Error(e.message || res.statusText);
    err.rule = e.details?.rule; err.status = res.status;
    throw err;
  }
  return data;
}
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add('hidden'), 3500); }
function fail(e) { toast(e.rule ? `${e.message} (${e.rule})` : e.message); }

// -- formatting -----------------------------------------------------------------------------------------
const CHIP = { ONLINE: 'good', ACTIVE: 'good', CONFIRMED: 'good', APPLIED: 'good', STORED: 'good', OFFLINE: 'warn', PENDING: 'warn', SENT: 'warn',
  PROVISIONED: 'blue', TIMED_OUT: 'bad', REJECTED: 'bad', FAULT: 'bad', FAILED: 'bad', NEVER: 'grey', PROVISIONING: 'grey', REGISTERED: 'grey',
  DISABLED: 'grey', RETIRED: 'grey', SUPERSEDED: 'grey', DELETED: 'grey', EXPIRED: 'grey' };
const chip = (s, label) => `<span class="chip ${CHIP[s] || 'grey'}">${esc(label || s)}</span>`;
function ago(iso) {
  if (!iso) return '–';
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`;
}
const when = (iso) => iso ? new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '–';
const day = (iso) => iso ? new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' }) : '–';
const isEngineer = () => state.user?.role === 'IOT_ENGINEER';
const canWriteData = () => ['OWNER', 'FLEET_MANAGER', 'IOT_ENGINEER'].includes(state.user?.role);

// -- login ------------------------------------------------------------------------------------------------
async function login(ev) {
  ev.preventDefault();
  $('#loginError').textContent = '';
  try {
    const r = await call('POST', '/auth/login', { email: $('#email').value, password: $('#password').value }, C1);
    state.token = r.accessToken; state.user = r.user;
    sessionStorage.setItem('c2console', JSON.stringify({ token: state.token, user: state.user }));
    await start();
  } catch (e) { $('#loginError').textContent = UNDER_NGINX ? e.message : 'Open the console through nginx: http://localhost:8080/c2/console/'; }
}
function logout() { sessionStorage.removeItem('c2console'); state.token = null; clearInterval(state.timer); $('#app').classList.add('hidden'); $('#login').classList.remove('hidden'); }

async function start() {
  $('#login').classList.add('hidden'); $('#app').classList.remove('hidden');
  $('#who').innerHTML = `${esc(state.user.name)} · <b>${esc(state.user.role)}</b>`;
  document.querySelectorAll('.engineer').forEach((el) => el.classList.toggle('hidden', !isEngineer()));
  document.querySelectorAll('.writer').forEach((el) => el.classList.toggle('hidden', !canWriteData()));
  if (STAFF.includes(state.user.role)) {
    state.tenants = await call('GET', '/admin/tenants?size=100', undefined, C1).then((r) => r.items || r).catch(() => []);
    state.tenantId = state.tenantId || state.tenants[0]?.id || state.tenants[0]?.tenantId || 1;
    $('#tenantBox').innerHTML = `<select id="tenantSel">${state.tenants.map((t) => `<option value="${t.id ?? t.tenantId}">${esc(t.name)} (${esc(t.code)})</option>`).join('')}</select>`;
    $('#tenantSel').value = state.tenantId;
    $('#tenantSel').onchange = (e) => { state.tenantId = e.target.value; state.vehicle = null; refresh(); };
  } else { state.tenantId = null; $('#tenantBox').textContent = ''; }
  if (!['OWNER', 'FLEET_MANAGER', 'IOT_ENGINEER', 'SERVICE_STAFF', 'SYS_ADMIN'].includes(state.user.role)) {
    document.querySelector('[data-tab="data"]').classList.add('hidden');
  }
  if (!STAFF.includes(state.user.role)) { document.querySelector('[data-tab="network"]').classList.add('hidden'); selectTab('data'); }
  await refresh();
  clearInterval(state.timer);
  state.timer = setInterval(refresh, 3000);
}

// -- vehicles ---------------------------------------------------------------------------------------------
async function refresh() {
  try {
    const r = await call('GET', '/vehicles?size=100');
    state.vehicles = r.items;
    $('#live').className = 'chip good'; $('#live').textContent = '● live';
    renderVehicles();
    if (state.vehicle) await loadVehicle(state.vehicle.vehicleId, true);
  } catch (e) { $('#live').className = 'chip bad'; $('#live').textContent = 'C2 unreachable'; }
}
function renderVehicles() {
  const q = $('#search').value.toLowerCase();
  const tenant = state.tenants.find((t) => String(t.id ?? t.tenantId) === String(state.tenantId));
  $('#planCount').textContent = tenant ? `${state.vehicles.length} of ${tenant.maxVehicles}` : '';
  $('#vehicleTable tbody').innerHTML = state.vehicles.filter((v) => !q || v.vehicleId.toLowerCase().includes(q) || (v.vin || '').toLowerCase().includes(q))
    .map((v) => `<tr class="click ${state.vehicle?.vehicleId === v.vehicleId ? 'sel' : ''}" data-id="${esc(v.vehicleId)}"><td><b>${esc(v.vehicleId)}</b><br><span class="muted small">${esc(v.label || v.model)}</span></td>
      <td>${chip(v.status)}</td><td>${chip(v.connection)}</td></tr>`).join('');
  document.querySelectorAll('#vehicleTable tbody tr').forEach((tr) => tr.onclick = () => loadVehicle(tr.dataset.id));
}

async function loadVehicle(id, quiet) {
  try {
    const v = await call('GET', `/vehicles/${id}`);
    const before = new Map(state.devices.map((d) => [d.deviceId, d.status + (d.config?.state || '')]));
    state.vehicle = v; state.devices = v.devices;
    $('#empty').classList.add('hidden'); $('#vehicle').classList.remove('hidden');
    $('#vTitle').innerHTML = `${esc(v.vehicleId)} · ${esc(v.label || '')} ${chip(v.status)} ${chip(v.connection, v.connection === 'NEVER' ? 'never connected' : v.connection)}`;
    $('#vMeta').textContent = `${v.make} ${v.model} ${v.year} · VIN ${v.vin} · MQTT ${v.provisioning?.mqttUsername || '–'} (credential v${v.provisioning?.credentialVersion ?? 0}) · retention ${v.retentionDays} days`;
    $('#provision').textContent = v.provisioning?.provisionedAt ? 'Rotate credentials' : 'Provision agent';
    $('#provision').disabled = $('#retire').disabled = v.status === 'RETIRED';
    renderDevices(before);
    if (!quiet) { renderVehicles(); if (state.tab === 'network') loadNetwork(); if (state.tab === 'data') loadClips(); $('#retention').value = v.retentionDays; }
    if (state.editor) renderEditor();
  } catch (e) { if (!quiet) fail(e); }
}

function renderDevices(before) {
  $('#deviceTable tbody').innerHTML = state.devices.map((d) => {
    const cfg = d.config ? (d.config.state === 'CONFIRMED' ? `v${d.config.version} ✓` : chip(d.config.state, `v${d.config.version} ${d.config.state}`)) : '–';
    const changed = before && before.has(d.deviceId) && before.get(d.deviceId) !== d.status + (d.config?.state || '');
    return `<tr class="${changed ? 'flash' : ''} ${state.editor === d.deviceId ? 'sel' : ''}"><td>${esc(d.deviceId)}</td><td>${esc(d.type)}</td><td>${esc(d.position)}</td>
      <td>${chip(d.status)} ${d.muted ? chip('DISABLED', 'muted') : ''} ${d.faultReason ? `<span class="muted small">${esc(d.faultReason)}</span>` : ''}</td>
      <td>${ago(d.lastSeenAt)}</td><td>${cfg}</td><td><button class="ghost" data-edit="${esc(d.deviceId)}">Edit</button></td></tr>`;
  }).join('');
  document.querySelectorAll('[data-edit]').forEach((b) => b.onclick = () => { state.editor = b.dataset.edit; renderEditor(true); renderDevices(); });
}

// -- device editor ------------------------------------------------------------------------------------------
async function renderEditor(fresh) {
  const d = state.devices.find((x) => x.deviceId === state.editor);
  if (!d) { $('#editor').classList.add('hidden'); return; }
  $('#editor').classList.remove('hidden');
  $('#edTitle').textContent = `Edit device · ${d.deviceId} (${d.type}, ${d.position})`;
  const mic = !!d.config;
  ['#edGain', '#edThr', '#edApply', '#edMute'].forEach((s) => { $(s).disabled = !mic || d.status === 'DISABLED'; });
  if (fresh && mic) { const v = d.config.pending || d.config.values || {}; $('#edGain').value = v.micGainDb ?? 6; $('#edThr').value = v.detectionThreshold ?? 0.4; }
  $('#edMute').textContent = d.muted ? 'Unmute' : 'Mute';
  $('#edToggle').textContent = d.status === 'DISABLED' ? 'Enable' : 'Disable';
  $('#edDelete').disabled = d.type === 'EDGE_GATEWAY' && state.vehicle.status !== 'RETIRED';
  if (mic) {
    const c = d.config, v = c.values || {};
    const confirmed = `Confirmed: v${c.state === 'CONFIRMED' ? c.version : c.version - 1} (${v.micGainDb > 0 ? '+' : ''}${v.micGainDb} dB, ${Number(v.detectionThreshold).toFixed(2)})`;
    const pending = c.state === 'CONFIRMED' ? chip('CONFIRMED', `v${c.version} confirmed by the car`) :
      c.state === 'PENDING' ? chip('PENDING', `v${c.version} sent, waiting for the car (30 s)`) :
      c.state === 'TIMED_OUT' ? chip('TIMED_OUT', `v${c.version} not confirmed (timed out); it is sent again when the car reconnects`) :
      chip('REJECTED', `v${c.version} rejected by the car`);
    $('#edState').innerHTML = `<p>${pending} <span class="muted small">${esc(confirmed)}</span></p>`;
  } else $('#edState').innerHTML = '<p class="muted small">No settings for this device type.</p>';
  if (STAFF.includes(state.user.role)) {
    const cmds = await call('GET', `/devices/${d.deviceId}/commands?limit=6`).catch(() => []);
    $('#cmdTable tbody').innerHTML = cmds.map((c) => `<tr><td>${c.commandId}</td><td>${esc(c.command)}</td><td>${chip(c.status)}</td><td>${when(c.createdAt)}</td><td>${when(c.ackedAt)}</td></tr>`).join('');
  }
}
async function applyConfig() {
  try {
    const r = await call('PUT', `/devices/${state.editor}/config`, { micGainDb: Number($('#edGain').value), detectionThreshold: Number($('#edThr').value) });
    toast(r.state === 'SENT' ? `v${r.configVersion} sent to the car` : `v${r.configVersion} pending: the car is offline`);
    await loadVehicle(state.vehicle.vehicleId, true);
  } catch (e) { fail(e); }
}
async function toggleMute() {
  const d = state.devices.find((x) => x.deviceId === state.editor);
  try { await call('POST', `/devices/${d.deviceId}/commands`, { command: d.muted ? 'UNMUTE' : 'MUTE' }); toast(`${d.muted ? 'UNMUTE' : 'MUTE'} sent`); } catch (e) { fail(e); }
}
async function toggleEnabled() {
  const d = state.devices.find((x) => x.deviceId === state.editor);
  try { await call('PUT', `/devices/${d.deviceId}`, { enabled: d.status === 'DISABLED' }); await loadVehicle(state.vehicle.vehicleId, true); } catch (e) { fail(e); }
}
async function deleteDevice() {
  if (!confirm(`Delete ${state.editor}? Its history is kept.`)) return;
  try { await call('DELETE', `/devices/${state.editor}`); state.editor = null; $('#editor').classList.add('hidden'); await loadVehicle(state.vehicle.vehicleId); } catch (e) { fail(e); }
}

// -- dialogs ----------------------------------------------------------------------------------------------
function dialog(title, html, onOk, okLabel = 'OK') {
  $('#dlgTitle').textContent = title; $('#dlgBody').innerHTML = html; $('#dlgError').textContent = ''; $('#dlgOk').textContent = okLabel;
  $('#dlgOk').classList.toggle('hidden', !onOk);
  const dlg = $('#dlg');
  $('#dlgOk').onclick = async (ev) => {
    if (!onOk) return;
    ev.preventDefault();
    try { await onOk(); dlg.close(); } catch (e) { $('#dlgError').textContent = e.rule ? `${e.message} (${e.rule})` : e.message; }
  };
  dlg.showModal();
}
function addVehicle() {
  dialog('Add vehicle', `<label>Vehicle code <input id="nvCode" placeholder="AV-02"></label><label>VIN <input id="nvVin" maxlength="17"></label>
    <label>Make <input id="nvMake" value="Tesla"></label><label>Model <input id="nvModel" value="Model Y"></label>
    <label>Year <input id="nvYear" type="number" value="2023"></label><label>Label <input id="nvLabel"></label>`, async () => {
    const v = await call('POST', '/vehicles', { vehicleId: $('#nvCode').value.trim(), vin: $('#nvVin').value.trim(), make: $('#nvMake').value,
      model: $('#nvModel').value, year: Number($('#nvYear').value), label: $('#nvLabel').value || null });
    toast(`${v.vehicleId} added`); await refresh(); await loadVehicle(v.vehicleId);
  }, 'Save');
}
function addDevice() {
  dialog(`Add device to ${state.vehicle.vehicleId}`, `<label>Type <select id="ndType"><option>MIC_EXTERIOR</option><option>MIC_INTERIOR</option><option>CAMERA</option><option>EDGE_GATEWAY</option></select></label>
    <label>Position <input id="ndPos" placeholder="front bumper"></label><label>Serial number <input id="ndSerial"></label>`, async () => {
    const d = await call('POST', `/vehicles/${state.vehicle.vehicleId}/devices`, { type: $('#ndType').value, position: $('#ndPos').value || null, serialNo: $('#ndSerial').value.trim() });
    toast(`${d.deviceId} added`); await loadVehicle(state.vehicle.vehicleId);
  }, 'Add');
}
async function provision() {
  const rotate = !!state.vehicle.provisioning?.provisionedAt;
  if (rotate && !confirm(`Rotate the credentials of ${state.vehicle.vehicleId}? The running agent stops working until it gets the new agent.yaml.`)) return;
  try {
    const r = await call('POST', `/vehicles/${state.vehicle.vehicleId}/provision`, { rotate });
    const blob = URL.createObjectURL(new Blob([r.agentYaml], { type: 'text/yaml' }));
    dialog(`Provision agent · ${r.vehicleId}`, `<p class="muted small">Shown once. The password is not stored by the platform; Rotate issues a new one and revokes this one.${r.brokerAuth === 'none' ? ' (Broker login checks are off in the course stack.)' : ''}</p>
      <pre>${esc(r.agentYaml)}</pre><p><a class="link" download="agent.yaml" href="${blob}">Download agent.yaml</a> · run <code>python agent.py --config agent.yaml --mic</code></p>`, null);
    await loadVehicle(state.vehicle.vehicleId);
  } catch (e) { fail(e); }
}
async function retire() {
  if (!confirm(`Retire ${state.vehicle.vehicleId}? Its login stops working now and it cannot be undone.`)) return;
  try { await call('DELETE', `/vehicles/${state.vehicle.vehicleId}?confirm=true`); toast('Retired'); state.vehicle = null; $('#vehicle').classList.add('hidden'); $('#empty').classList.remove('hidden'); await refresh(); } catch (e) { fail(e); }
}

// -- tabs: network and data ---------------------------------------------------------------------------------
function selectTab(tab) {
  state.tab = tab;
  document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  ['devices', 'network', 'data'].forEach((t) => $(`#tab-${t}`).classList.toggle('hidden', t !== tab));
  if (state.vehicle && tab === 'network') loadNetwork();
  if (state.vehicle && tab === 'data') loadClips();
}
async function loadNetwork() {
  try {
    const n = await call('GET', `/vehicles/${state.vehicle.vehicleId}/network`);
    state.netRows = n.profiles.map((p) => ({ ...p, password: '' }));
    renderNet();
    const cur = n.current ? `${n.current.type}${n.current.ssid ? ' ' + n.current.ssid : ''}${n.current.rssiDbm ? ', ' + n.current.rssiDbm + ' dBm' : ''}` : 'unknown';
    $('#netInfo').innerHTML = `Last applied: ${n.lastApplied ? chip(n.lastApplied.status) + ' command ' + n.lastApplied.commandId + ' at ' + when(n.lastApplied.at) : 'never'} · Current link: ${esc(cur)} · Passwords are write-only (BR-C2-25).`;
  } catch (e) { fail(e); }
}
function renderNet() {
  const eng = isEngineer();
  $('#netTable tbody').innerHTML = state.netRows.map((p, i) => `<tr><td><input data-i="${i}" data-k="priority" type="number" min="1" max="3" value="${p.priority}" style="width:50px" ${eng ? '' : 'disabled'}></td>
    <td><input data-i="${i}" data-k="ssid" value="${esc(p.ssid)}" ${eng ? '' : 'disabled'}></td>
    <td><select data-i="${i}" data-k="securityType" ${eng ? '' : 'disabled'}>${['WPA2_PSK', 'WPA3_SAE', 'OPEN'].map((s) => `<option ${s === p.securityType ? 'selected' : ''}>${s}</option>`).join('')}</select></td>
    <td><input data-i="${i}" data-k="password" type="password" placeholder="${p.passwordSet ? 'set (leave empty to keep)' : ''}" ${eng ? '' : 'disabled'}></td>
    <td>${eng ? `<button class="ghost" data-del="${i}">Remove</button>` : ''}</td></tr>`).join('');
  document.querySelectorAll('#netTable input, #netTable select').forEach((el) => el.onchange = () => {
    const row = state.netRows[el.dataset.i]; row[el.dataset.k] = el.dataset.k === 'priority' ? Number(el.value) : el.value; });
  document.querySelectorAll('[data-del]').forEach((b) => b.onclick = () => { state.netRows.splice(Number(b.dataset.del), 1); renderNet(); });
}
async function saveNet() {
  try {
    const profiles = state.netRows.map((p) => ({ priority: p.priority, ssid: p.ssid, securityType: p.securityType, ...(p.password ? { password: p.password } : {}) }));
    const r = await call('PUT', `/vehicles/${state.vehicle.vehicleId}/network`, { profiles });
    toast(r.state === 'SENT' ? 'Network sent to the car' : 'Saved; the car is offline, it gets it when it reconnects');
    setTimeout(loadNetwork, 1500);
  } catch (e) { fail(e); }
}
async function loadClips() {
  const days = Number($('#dataWindow').value);
  const from = new Date(Date.now() - days * 86400000).toISOString();
  try {
    const r = await call('GET', `/vehicles/${state.vehicle.vehicleId}/clips?from=${encodeURIComponent(from)}&size=100${$('#alertOnly').checked ? '&alertOnly=true' : ''}`);
    $('#clipTable tbody').innerHTML = r.items.map((c) => `<tr><td>${when(c.ts)}</td><td>${esc(c.deviceId)}</td><td>${esc(c.channel)}</td>
      <td>${esc(c.topClass || 'pending')}</td><td>${c.confidence != null ? Number(c.confidence).toFixed(2) : '–'}</td>
      <td>${c.alert ? chip('FAULT', `${c.alert.type} ${c.alert.alertId}`) : '–'}</td>
      <td>${c.state === 'STORED' ? `<button data-play="${c.clipId}">▶ Play</button>` : `<span class="muted small">${c.state.toLowerCase()}</span>`}</td>
      <td>${c.state === 'STORED' ? day(c.expireAt) : '–'}</td><td>${c.state === 'STORED' && canWriteData() ? `<button class="ghost" data-delclip="${c.clipId}">Delete</button>` : ''}</td></tr>`).join('')
      || '<tr><td colspan="9" class="muted">No clips in this period.</td></tr>';
    document.querySelectorAll('[data-play]').forEach((b) => b.onclick = () => play(b.dataset.play));
    document.querySelectorAll('[data-delclip]').forEach((b) => b.onclick = () => deleteClip(b.dataset.delclip));
  } catch (e) { fail(e); }
}
async function play(id) {
  try { const r = await call('GET', `/clips/${id}/url`); const p = $('#player'); p.src = r.url; p.classList.remove('hidden'); p.play(); } catch (e) { fail(e); }
}
async function deleteClip(id) {
  if (!confirm(`Delete clip ${id}? The audio is removed now.`)) return;
  try { await call('DELETE', `/clips/${id}`); toast('Clip deleted'); loadClips(); } catch (e) { fail(e); }
}
async function saveRetention() {
  try { const r = await call('PUT', `/vehicles/${state.vehicle.vehicleId}/retention`, { retentionDays: Number($('#retention').value) }); toast(`Retention ${r.retentionDays} days from now on`); } catch (e) { fail(e); }
}

// -- wiring ----------------------------------------------------------------------------------------------
$('#loginForm').onsubmit = login;
$('#logout').onclick = logout;
$('#search').oninput = renderVehicles;
$('#addVehicle').onclick = addVehicle;
$('#addDevice').onclick = addDevice;
$('#provision').onclick = provision;
$('#retire').onclick = retire;
$('#edClose').onclick = () => { state.editor = null; $('#editor').classList.add('hidden'); renderDevices(); };
$('#edApply').onclick = applyConfig;
$('#edMute').onclick = toggleMute;
$('#edToggle').onclick = toggleEnabled;
$('#edDelete').onclick = deleteDevice;
$('#addNet').onclick = () => { state.netRows.push({ priority: state.netRows.length + 1, ssid: '', securityType: 'WPA2_PSK', password: '' }); renderNet(); };
$('#saveNet').onclick = saveNet;
$('#dataWindow').onchange = loadClips;
$('#alertOnly').onchange = loadClips;
$('#saveRetention').onclick = saveRetention;
document.querySelectorAll('.tab').forEach((b) => b.onclick = () => selectTab(b.dataset.tab));

(async () => {
  const saved = JSON.parse(sessionStorage.getItem('c2console') || 'null');
  if (saved?.token) {
    state.token = saved.token; state.user = saved.user;
    try { await start(); return; } catch (e) { /* fall through to the login */ }
  }
  $('#login').classList.remove('hidden');
})();
