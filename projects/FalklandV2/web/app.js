async function load() {
  const r = await fetch('/api/status');
  const j = await r.json();

  // HUD + ship
  document.getElementById('hud_line').textContent = j.hud;
  document.getElementById('ship_cell').textContent = j.ship.cell;
  document.getElementById('ship_course').textContent = j.ship.course_deg;
  document.getElementById('ship_speed').textContent = j.ship.speed_kts;
  document.getElementById('pauseBtn').textContent = j.paused ? 'Resume' : 'Pause';
  document.getElementById('radar_line').textContent = j.radar.status_line;

  // Weapons
  document.getElementById('weapons_ship').textContent = j.weapons.ship_name;
  document.getElementById('weapons_line').textContent = j.weapons.status_line;

  const wt = document.getElementById('weapons_table');
  wt.innerHTML = '';
  for (const row of (j.weapons.table || [])) {
    const badge = (row.ready === true) ? '<span class="badge ready">READY</span>'
                : (row.ready === false) ? '<span class="badge notready">OUT</span>'
                : '<span class="badge">N/A</span>';
    const allow = ((row.key === 'corvus_chaff') || (row.ready === true)) && (!row.cooldown_s || row.cooldown_s <= 0);
    const cd = row.cooldown_s ? `${row.cooldown_s.toFixed(1)}s` : '—';
    const reason = row.reason ? ` <span class="pill">${row.reason}</span>` : '';
    let buttons = '';
    if (row.key === 'gun_4_5in') {
      const dis = allow ? '' : 'disabled';
      buttons = `<button class="small" ${dis} onclick="fire('${row.key}','he')">Fire HE</button>
                 <button class="small" ${dis} onclick="fire('${row.key}','illum')">Fire ILLUM</button>`;
    } else {
      const label = (row.key === 'corvus_chaff') ? 'Deploy' : 'Fire';
      const dis = allow ? '' : 'disabled';
      buttons = `<button class="small" ${dis} onclick="fire('${row.key}')">${label}</button>`;
    }
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${row.name}</td>
                    <td class="mono">${row.ammo}</td>
                    <td class="mono">${row.range}</td>
                    <td>${badge}${reason}</td>
                    <td class="mono">${cd}</td>
                    <td>${buttons}</td>`;
    wt.appendChild(tr);
  }
  const hint = (j.radar.locked_contact_id && j.radar.locked_range_nm !== null)
      ? `Locked target ${j.radar.locked_contact_id} at ${j.radar.locked_cell} (${j.radar.locked_range_nm.toFixed(2)} nm)`
      : `Lock a target to evaluate weapon ranges.`;
  document.getElementById('weapons_hint').textContent = hint;

  // Contacts
  const tb = document.getElementById('contacts_body');
  tb.innerHTML = '';
  if ((j.contacts || []).length === 0) {
    tb.innerHTML = '<tr><td colspan="7">No contacts.</td></tr>';
  } else {
    for (const c of j.contacts) {
      const tr = document.createElement('tr');
      tr.className = 'clickable';
      tr.onclick = () => lockById(c.id);
      tr.innerHTML = `
        <td class="mono">${c.cell}</td>
        <td>${c.type}</td>
        <td>${c.name} <span class="pill">${c.allegiance}</span></td>
        <td class="mono">${c.range_nm.toFixed(1)} nm</td>
        <td class="mono">${c.course_deg}°</td>
        <td class="mono">${c.speed_kts}</td>
        <td class="mono">#${String(c.id).padStart(2,'0')}</td>`;
      tb.appendChild(tr);
    }
  }

  // Log
  const log = document.getElementById('eng_log');
  log.innerHTML = (j.engagements || []).map(line => `<li>${line}</li>`).join('') || '<li>—</li>';

  // In-flight
  const infl = document.getElementById('inflight_list');
  infl.innerHTML = (j.inflight || []).map(e => `<li>#${e.target_id} @ ${e.cell} — ${e.weapon} (ETA ${e.eta_s}s)</li>`).join('') || '<li>—</li>';

  // Sounds (server clears queue each poll)
  for (const url of (j.sfx || [])) {
    try { new Audio(url).play().catch(()=>{}); } catch (e) {}
  }
}

// --- Controls
async function scan()   { await fetch('/api/scan',   {method:'POST'}); await load(); }
async function unlock() { await fetch('/api/unlock', {method:'POST'}); await load(); }
async function lock()   {
  const id = parseInt(document.getElementById('lock_id').value);
  if (!id) return;
  await fetch('/api/lock', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
  await load();
}
async function lockById(id) {
  await fetch('/api/lock', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
  await load();
}
async function helm() {
  const c = document.getElementById('set_course').value;
  const s = document.getElementById('set_speed').value;
  const payload = {};
  if (c !== '') payload.course_deg = parseFloat(c);
  if (s !== '') payload.speed_kts = parseFloat(s);
  if (Object.keys(payload).length === 0) return;
  await fetch('/api/helm', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  await load();
}
async function resetGame() {
  const ok = confirm('Reset game to fresh state? Contacts will be cleared.');
  if (!ok) return;
  const r = await fetch('/api/reset', {method:'POST'});
  const j = await r.json();
  if (!j.ok) { alert('Reset failed: ' + (j.error || 'unknown error')); }
  await load();
}
async function togglePause() {
  const r = await fetch('/api/pause', {method:'POST'});
  const j = await r.json();
  document.getElementById('pauseBtn').textContent = j.paused ? 'Resume' : 'Pause';
}
async function refit() {
  const r = await fetch('/api/refit', {method:'POST'});
  const j = await r.json();
  if (!j.ok) alert(j.error || 'Refit failed');
  await load();
}

// Immediate, in-gesture audio test + queue playback on next poll
async function sfxTest() {
  const r = await fetch('/api/sfx_test', {method:'POST'});
  const j = await r.json();
  if (j.url) {
    try { await new Audio(j.url).play(); } catch(e) {}
  }
  await load();
}

load();
setInterval(load, 1000);