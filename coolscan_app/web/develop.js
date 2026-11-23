'use strict';
// Re-develop: RAW files made into finished pictures again, with other settings, without the scanner.
let S = null, lastV = -1, shownFiles = '', shownLook = '', filled = '', cropping = false, drag = null, asked = 0;
const ticked = new Set();
const $ = id => document.getElementById(id);
const FILM_WORDS = {negative: 'Colour negative', positive: 'Slide', kodachrome: 'Kodachrome', mono: 'B&W negative'};
const SPACES = ['ProPhoto RGB', 'Adobe RGB (1998)', 'sRGB IEC61966-2.1', 'Apple RGB', 'ColorMatch RGB', 'Bruce RGB', 'Wide Gamut RGB', 'scanner'];

async function api(path, body) {
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  return r.json();
}
function opt(sel, value, text) { const o = document.createElement('option'); o.value = value; o.textContent = text; sel.appendChild(o); }
function dev() { return S.develop; }
function curFrame() { const D = dev(); return (D.current && D.frames[D.current]) || {turns: 0, crop: null}; }
function set(body) { return api('/api/develop/set', body).then(r => { if (r.busy) $('foldermsg').textContent = 'Wait for the files being developed to be done.'; }); }

// ---------------------------------------------------------------- 1. files
$('b-look').onclick = async () => {
  const path = $('path').value.trim();
  const r = await api('/api/develop/folder', path ? {path} : {roll: $('roll').value});
  ticked.clear();
  $('foldermsg').textContent = r.ok ? '' : (r.busy ? 'Something else is running (a scan?). Try again when it is done.' : r.error);
};
$('roll').onchange = () => { $('path').value = ''; $('b-look').click(); };
$('tickall').onchange = () => { ticked.clear(); if ($('tickall').checked) for (const f of dev().files) ticked.add(f.name); shownFiles = ''; render(); };
$('files').addEventListener('click', ev => {
  const tr = ev.target.closest('tr'); if (!tr) return;
  const name = tr.dataset.name;
  if (ev.target.matches('input[type=checkbox]')) { ev.target.checked ? ticked.add(name) : ticked.delete(name); shownFiles = ''; render(); return; }
  set({current: name});
});
function drawFiles() {
  const D = dev(), sig = JSON.stringify(D.files) + D.current + [...ticked].join('|');
  $('filetable').hidden = !D.files.length;
  if (sig === shownFiles) return;
  shownFiles = sig;
  const body = $('files'); body.innerHTML = '';
  for (const f of D.files) {
    const tr = document.createElement('tr'); tr.dataset.name = f.name; if (f.name === D.current) tr.className = 'cur';
    const cell = (text, cls) => { const td = document.createElement('td'); if (cls) td.className = cls; td.textContent = text; tr.appendChild(td); return td; };
    const t = cell('', 'x'), box = document.createElement('input'); box.type = 'checkbox'; box.checked = ticked.has(f.name);
    box.setAttribute('aria-label', 'Develop ' + f.name); t.appendChild(box);
    cell(f.name); cell(f.film ? FILM_WORDS[f.film] : 'not written down');
    cell(f.size[0] + '×' + f.size[1] + (f.dpi ? ' · ' + f.dpi + ' dpi' : ''));
    const notes = [];
    if (!f.ir) notes.push('no infrared: no dust removal');
    if (!f.exposures && (f.film === 'negative' || !f.film)) notes.push('no exposures kept: film profiles cannot be used');
    if (f.crop) notes.push('cropped when scanned');
    cell(notes.join(' · ') || '');
    body.appendChild(tr);
  }
  $('tickall').checked = D.files.length > 0 && ticked.size === D.files.length;
}

// ---------------------------------------------------------------- 2. how
function fillForm() {
  const D = dev(), profs = (S.film_profiles || []).map(p => p.name);
  const want = profs.join('\n') + '|' + S.rolls.join('\n') + '|' + D.ice_helper;
  if (want !== filled) {
    filled = want;
    const fp = $('h-film_profile'); fp.innerHTML = ''; opt(fp, '', 'Each frame on its own');
    for (const n of profs) opt(fp, n, 'Film profile: ' + n);
    const cs = $('h-colour_space'); if (!cs.options.length) for (const s of SPACES) opt(cs, s, s === 'scanner' ? 'Scanner RGB (Nikon profile)' : s);
    const roll = $('roll'), keep = roll.value || S.settings.roll; roll.innerHTML = '';
    for (const r of S.rolls) opt(roll, r, r);
    roll.value = S.rolls.includes(keep) ? keep : (S.rolls[S.rolls.length - 1] || '');
    $('h-dust_method').querySelector('option[value=ice]').disabled = !D.ice_helper;
  }
  for (const k of ['film', 'film_profile', 'tone', 'colour_space', 'restore', 'grain', 'dust_method']) {
    const e = $('h-' + k); if (document.activeElement !== e) e.value = String(D.how[k]);
  }
  $('h-clean').checked = D.how.clean;
  $('extras').checked = D.extras;
}
for (const k of ['film', 'film_profile', 'tone', 'colour_space', 'restore', 'grain', 'dust_method'])
  $('h-' + k).onchange = () => set({how: {[k]: ['restore', 'grain'].includes(k) ? +$('h-' + k).value : $('h-' + k).value}});
$('h-clean').onchange = () => set({how: {clean: $('h-clean').checked}});
$('extras').onchange = () => set({extras: $('extras').checked});

// ---------------------------------------------------------------- 3. look
$('views').addEventListener('click', ev => { const b = ev.target.closest('button'); if (b) set({view: b.dataset.view}); });
$('b-left').onclick = () => set({frame: {name: dev().current, turns: (curFrame().turns + 1) % 4}});
$('b-right').onclick = () => set({frame: {name: dev().current, turns: (curFrame().turns + 3) % 4}});
$('b-crop').onclick = () => { cropping = !cropping; document.body.classList.toggle('cropping', cropping); $('b-crop').setAttribute('aria-pressed', String(cropping)); shownLook = ''; render(); };
$('b-cropclear').onclick = () => set({frame: {name: dev().current, crop: null}});
function frac(ev) { const b = $('overimg').getBoundingClientRect(); return [Math.min(1, Math.max(0, (ev.clientX - b.left) / b.width)), Math.min(1, Math.max(0, (ev.clientY - b.top) / b.height))]; }
function placeCrop(r) { const box = $('cropbox'); box.hidden = !r; if (r) Object.assign(box.style, {left: r[0] * 100 + '%', top: r[1] * 100 + '%', width: (r[2] - r[0]) * 100 + '%', height: (r[3] - r[1]) * 100 + '%'}); }
$('over').addEventListener('mousedown', ev => { if (!cropping) return; ev.preventDefault(); drag = frac(ev); });
window.addEventListener('mousemove', ev => { if (!drag) return; const p = frac(ev); placeCrop([Math.min(drag[0], p[0]), Math.min(drag[1], p[1]), Math.max(drag[0], p[0]), Math.max(drag[1], p[1])]); });
window.addEventListener('mouseup', async ev => {
  if (!drag) return;
  const a = drag, b = frac(ev); drag = null;
  const r = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  if (r[2] - r[0] < 0.05 || r[3] - r[1] < 0.05) { shownLook = ''; render(); return; }
  cropping = false; document.body.classList.remove('cropping'); $('b-crop').setAttribute('aria-pressed', 'false');
  await set({frame: {name: dev().current, crop: r}});
});
$('over').addEventListener('click', ev => { if (!cropping) set({piece: frac(ev)}); });
$('pieces').addEventListener('click', ev => {
  const img = ev.target.closest('img'); if (!img) return;
  $('zoomimg').src = img.src; $('zoomimg').className = 'pixels'; $('zoomcap').textContent = img.alt + ' · twice its size · click or press Esc to close'; $('zoom').showModal();
});
$('zoom').addEventListener('click', () => $('zoom').close());

function drawLook() {
  const D = dev(), has = D.have.length > 0 && !!D.current, f = curFrame();
  $('nolook').hidden = has; $('over').hidden = !has; $('overhint').hidden = !has; $('pieces').hidden = !has;
  for (const id of ['b-left', 'b-right', 'b-crop']) $(id).disabled = !D.current || (S.job === 're-develop');
  $('b-cropclear').hidden = !f.crop;
  $('lookhint').textContent = D.current ? D.current + (D.stale || S.job === 're-develop preview' ? ' · making the pictures…' : '') : '';
  const notes = $('notes'); const nsig = D.notes.join('|');
  if (notes.dataset.sig !== nsig) { notes.dataset.sig = nsig; notes.innerHTML = ''; for (const n of D.notes) { const p = document.createElement('p'); p.className = 'hint warn'; p.textContent = n; notes.appendChild(p); } }
  const sig = [D.current, D.v, D.view, D.have.join(), f.turns, JSON.stringify(f.crop), cropping, D.piece.join()].join('|');
  if (sig === shownLook) return;
  shownLook = sig;
  const vbox = $('views'); vbox.innerHTML = '';
  for (const v of D.views) {
    const b = document.createElement('button'); b.dataset.view = v; b.textContent = D.view_words[v];
    b.setAttribute('role', 'tab'); b.setAttribute('aria-pressed', String(v === D.view)); b.disabled = !has; vbox.appendChild(b);
  }
  if (!has) return;
  const stamp = encodeURIComponent(D.current) + '-' + D.v + '-' + f.turns;
  $('overimg').src = '/develop/view/' + D.view + '.png?s=' + stamp;
  $('overimg').alt = D.view_words[D.view] + ', ' + D.current;
  const m = $('mark'); m.style.left = 100 * D.piece[0] + '%'; m.style.top = 100 * D.piece[1] + '%'; m.hidden = cropping;
  if (!drag) placeCrop(f.crop);
  const box = $('pieces'); box.innerHTML = '';
  for (const v of D.views) {
    const fig = document.createElement('figure'); if (v === D.view) fig.className = 'on';
    const img = document.createElement('img'); img.alt = D.view_words[v] + ', a piece at full size';
    img.src = '/develop/piece/' + v + '.png?s=' + stamp + '-' + D.piece.join('-');
    const cap = document.createElement('figcaption'); cap.textContent = D.view_words[v] + ' · full size';
    fig.appendChild(img); fig.appendChild(cap); box.appendChild(fig);
  }
}

// ---------------------------------------------------------------- 4. save
$('b-run').onclick = async () => {
  const r = await api('/api/develop/run', {names: [...ticked]});
  if (!r.ok) $('foldermsg').textContent = 'Something else is running. Wait for it to finish, then try again.';
};
$('b-stop').onclick = () => api('/api/stop');
function drawResults() {
  const res = dev().results, box = $('results');
  const sig = JSON.stringify(res);
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig; box.innerHTML = '';
  if (!res) return;
  const p = document.createElement('p'); p.className = 'hint';
  p.textContent = (res.running ? 'Saving into ' : 'Saved into ') + res.folder + ': ' + res.saved.length + ' file(s)'
    + (res.took ? ' in ' + res.took : '') + (res.failed.length ? '. Failed: ' + res.failed.join(', ') + ' (see the Activity list)' : '');
  box.appendChild(p);
  if (res.saved.length) { const q = document.createElement('p'); q.className = 'hint'; q.textContent = res.saved.join(', '); box.appendChild(q); }
}

// ---------------------------------------------------------------- drawing
function render() {
  if (!S) return;
  const D = dev(), busy = !!S.job;
  $('status').textContent = (S.demo ? 'DEMO · ' : '') + (D.folder ? D.folder : 'no folder picked yet');
  const n = ticked.size;
  $('b-run').textContent = n ? 'Develop ' + n + ' ticked file' + (n > 1 ? 's' : '') : 'Develop all files';
  $('b-run').disabled = busy || !D.files.length;
  $('b-look').disabled = S.job === 're-develop';
  $('b-stop').disabled = !(S.job === 're-develop' || S.job === 're-develop preview');
  $('busy').hidden = !busy;
  if (busy) {
    const p = S.progress, prog = $('prog');
    if (p && p.total) { prog.max = p.total; prog.value = p.done; } else prog.removeAttribute('value');
    $('progtxt').textContent = S.job + (p ? ': ' + p.phase + (p.total ? ' ' + Math.round(100 * p.done / p.total) + '%' : '') : '…');
  }
  fillForm(); drawFiles(); drawLook(); drawResults();
  if (S.version !== lastV) { $('log').textContent = S.log.join('\n'); $('log').scrollTop = 1e9; lastV = S.version; }
  // the pictures are made again whenever they no longer match (not while a scan is running)
  if (D.stale && !busy && Date.now() - asked > 1500) { asked = Date.now(); api('/api/develop/look'); }
}

async function poll() {
  let next = null;
  try { next = await (await fetch('/api/state')).json(); }
  catch (e) { $('status').textContent = 'The app has stopped. Start it again in Terminal: python coolscan.py'; }
  if (next && !next.develop) { $('status').textContent = 'This app is older than this page. Stop it in Terminal (Ctrl-C) and start it again.'; next = null; }
  try { if (next) { S = next; render(); } }
  finally { setTimeout(poll, 500); }
}
poll();
