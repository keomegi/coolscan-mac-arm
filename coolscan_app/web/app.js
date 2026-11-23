'use strict';
// The page asks the app for its state twice a second and draws it. Two ideas run through it:
//  - the frame you are LOOKING at (cur) is shown big; clicking a frame only looks at it
//  - the frames you TICKED (sel) are what Scan takes; with none ticked, Scan takes the whole strip
let S = null, sel = new Set(), cur = null, anchor = null;
let lastV = -1, lastStrip = null, lastShape = '', shownPic = '', lastPreset = '';
let picking = false, cropping = false, drag = null, descHold = 0;
const $ = id => document.getElementById(id);
const SOURCE = {thumbnail: 'Thumbnail', preview: 'Preview', scan: 'Scan'};

async function api(path, body) {
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  return r.json();
}
function ticked() { return [...sel].sort((a, b) => a - b); }
// what Preview and the turn buttons act on: the ticked frames, or else the one being looked at
function targets() { const t = ticked(); return t.length ? t : (cur !== null ? [cur] : []); }
function clock(s) { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }
// a point is kept the way the scanner sees the frame, and shown the way the frame is turned
function toScreen(p, k) { let [x, y] = p; for (let n = 0; n < k; n++) [x, y] = [y, 1 - x]; return [x, y]; }
function toScanner(p, k) { let [x, y] = p; for (let n = 0; n < k; n++) [x, y] = [1 - y, x]; return [x, y]; }
function tile(i) { return (S && S.tiles && S.tiles[i]) || {}; }

// ---------------------------------------------------------------- settings: every control saves when changed
const keys = ['film', 'format', 'preview_dpi', 'invert_preview', 'rotate180', 'focus_mode', 'focus_position', 'exposure_mode',
  'white_balance', 'dpi', 'samples', 'ir', 'clean', 'negatives', 'archive', 'outdir', 'basename', 'digits', 'start', 'photo_date',
  'colour_space', 'name_date', 'tone', 'stop_on_errors', 'eject_after', 'scan_log', 'auto_thumbnail', 'dust_method', 'grain',
  'restore', 'notify_mac', 'notify_shortcut', 'notify_ntfy', 'multi_exposure'];
keys.forEach(k => { const e = $('s-' + k); e.addEventListener('change', () => api('/api/settings', {[k]: e.type === 'checkbox' ? e.checked : e.value})); });

// ---------------------------------------------------------------- film profiles: the colour of a film, measured from a roll
function profileSay(text) { const m = $('profilemsg'); m.hidden = !text; m.textContent = text || ''; }
$('filmprofile').onchange = async () => {
  const r = await api('/api/film_profile', {action: 'use', name: $('filmprofile').value});
  profileSay(r.ok ? '' : (r.busy ? 'Wait until the scan finishes, then pick it again.' : (r.error || 'That did not work.')));
  if (!r.ok) $('filmprofile').value = S.settings.film_profile || '';
};
$('b-profilenew').onclick = () => {
  const st = S.settings, film = [st.film_brand, st.film_stock, st.film_iso].filter(Boolean).join(' ');
  $('profiledel').hidden = true; $('profilenew').hidden = false; profileSay('');
  $('profilename').value = st.film_profile || film; $('profilename').focus(); $('profilename').select();
};
$('b-profilecancel').onclick = () => { $('profilenew').hidden = true; };
async function profileSave() {
  const r = await api('/api/film_profile', {action: 'save', name: $('profilename').value});
  if (!r.ok) { profileSay(r.error || 'That did not work.'); return; }
  $('profilenew').hidden = true;
  profileSay('Saved "' + r.name + '" from ' + r.frames + ' frames. Pick it above to use it.');
}
$('b-profilesave').onclick = profileSave;
$('profilename').addEventListener('keydown', ev => { if (ev.key === 'Enter') profileSave(); if (ev.key === 'Escape') $('profilenew').hidden = true; });
$('b-profiledel').onclick = () => {
  const name = $('filmprofile').value; if (!name) return;
  $('profilenew').hidden = true; $('profiledel').hidden = false; $('profiledel').dataset.name = name;
  $('profiledelmsg').textContent = 'Delete the film profile "' + name + '"? Pictures already saved stay as they are.';
};
$('b-profiledelno').onclick = () => { $('profiledel').hidden = true; };
$('b-profiledelyes').onclick = async () => {
  const r = await api('/api/film_profile', {action: 'delete', name: $('profiledel').dataset.name}); $('profiledel').hidden = true;
  profileSay(r.ok ? '' : (r.busy ? 'Wait until the scan finishes, then try again.' : (r.error || 'That did not work.')));
};

// ---------------------------------------------------------------- the bar: scanner and main actions
$('b-connect').onclick = () => api('/api/connect');
$('b-load').onclick = () => api('/api/load');
$('b-eject').onclick = () => api('/api/eject');
$('b-thumb').onclick = () => api('/api/thumbnail');
$('b-preview').onclick = () => api('/api/preview', {frames: targets()});
$('b-sharper').onclick = () => { if (cur !== null) api('/api/preview', {frames: [cur]}); };
$('b-scan').onclick = () => api('/api/scan', {frames: ticked()});
$('b-stop').onclick = () => api('/api/stop');

// ---------------------------------------------------------------- focus and exposure
const oneFrame = () => cur ?? ticked()[0] ?? 0;
$('b-cal').onclick = () => api('/api/calibrate', {frame: oneFrame()});
$('b-af').onclick = () => api('/api/autofocus', {frame: oneFrame()});
$('b-ae').onclick = () => api('/api/autoexpose', {frame: oneFrame()});
$('b-unlock').onclick = () => api('/api/unlock');
function setPicking(on) { picking = on; if (on) setCropping(false); document.body.classList.toggle('picking', on); }
$('b-fpoint').onclick = () => { if (S.settings.focus_point) { api('/api/settings', {focus_point: null}); return; } setPicking(!picking); render(true); };

// ---------------------------------------------------------------- rolls
const rollKeys = ['film_brand', 'film_stock', 'film_iso', 'film_notes', 'film_description'];
$('rolldesc').addEventListener('change', () => { descHold = Date.now() + 1500; api('/api/describe', {text: $('rolldesc').value}); });
function rollnoDone() { $('rollnodlg').hidden = true; $('b-rollnoyes').hidden = true; }
async function rollno(move) {
  const n = +$('rollno').value, r = await api('/api/rollnumber', {number: n, move_aside: move});
  if (r.ok) { rollnoDone(); return; }
  $('rollnodlg').hidden = false; $('b-rollnoyes').hidden = !r.in_the_way;
  if (r.in_the_way) $('b-rollnoyes').textContent = r.in_the_way.length > 1 ? 'Move them and renumber' : 'Move it and renumber';
  if (r.busy) $('rollnomsg').textContent = 'Wait until the scan finishes, then try again.';
  else if (r.in_the_way) {
    const pad = String(n).padStart(3, '0');
    $('rollnomsg').textContent = 'Nothing was changed yet. To count on from Roll ' + pad + ', ' + (r.in_the_way.length > 1 ? 'these older folders' : 'this older folder')
      + ' would be moved into "' + r.aside + '" (nothing is deleted or renamed inside): '
      + r.in_the_way.map(d => d.name + ' (' + plural(d.files, 'file') + ')').join(', ') + '.';
  } else $('rollnomsg').textContent = r.error || 'That did not work.';
}
$('b-rollno').onclick = () => rollno(false);
$('b-rollnoyes').onclick = () => rollno(true);
$('b-rollnono').onclick = () => { rollnoDone(); $('rollno').value = S.roll_number || ''; };
$('rollpick').onchange = async () => {
  const r = await api('/api/switchroll', {name: $('rollpick').value}), m = $('rollpickmsg');
  m.hidden = !!r.ok;
  if (!r.ok) { m.textContent = r.busy ? 'Wait until the scan finishes, then pick the roll again.' : (r.error || 'That did not work.'); $('rollpick').value = S.settings.roll; }
};
$('b-newroll').onclick = () => { rollKeys.forEach(k => $('r-' + k).value = ''); $('rolldlgmsg').hidden = true; $('rolldlg').showModal(); $('r-film_brand').focus(); };
$('b-rollcancel').onclick = () => $('rolldlg').close();
$('b-rollok').onclick = async () => {
  const b = {}; rollKeys.forEach(k => b[k] = $('r-' + k).value);
  const r = await api('/api/newroll', b);
  if (r.busy) { $('rolldlgmsg').hidden = false; $('rolldlgmsg').textContent = 'Wait until the scan finishes, then try again.'; return; }
  $('rolldlg').close();
};

// ---------------------------------------------------------------- when a strip is done
$('b-notifytest').onclick = () => api('/api/notifytest');
async function shortcutNames() {
  try {
    const r = await (await fetch('/api/shortcuts')).json(), list = $('shortcutlist');
    list.innerHTML = ''; for (const n of r.names) { const o = document.createElement('option'); o.value = n; list.appendChild(o); }
  } catch (e) { /* not a Mac, or Shortcuts is not there: the field still takes a typed name */ }
}
$('s-notify_shortcut').addEventListener('focus', shortcutNames);

// ---------------------------------------------------------------- saved settings
function presetSay(text) { const m = $('presetmsg'); m.hidden = !text; m.textContent = text || ''; }
$('preset').onchange = async () => {
  const name = $('preset').value; if (!name) return;
  const r = await api('/api/preset', {action: 'load', name});
  presetSay(r.ok ? '' : (r.busy ? 'Wait until the scan finishes, then pick it again.' : (r.error || 'That did not work.')));
  if (!r.ok) $('preset').value = S.preset || '';
};
$('b-presetnew').onclick = () => { $('presetdel').hidden = true; $('presetnew').hidden = false; $('presetname').value = S.preset || lastPreset || ''; $('presetname').focus(); $('presetname').select(); };
$('b-presetcancel').onclick = () => { $('presetnew').hidden = true; };
async function presetSave() {
  const r = await api('/api/preset', {action: 'save', name: $('presetname').value});
  if (!r.ok) { presetSay(r.error || 'That did not work.'); return; }
  $('presetnew').hidden = true; presetSay('');
}
$('b-presetsave').onclick = presetSave;
$('presetname').addEventListener('keydown', ev => { if (ev.key === 'Enter') presetSave(); if (ev.key === 'Escape') $('presetnew').hidden = true; });
$('b-presetdel').onclick = () => { if (!S.preset) return; $('presetnew').hidden = true; $('presetdel').hidden = false; $('presetdelmsg').textContent = 'Delete "' + S.preset + '"? The settings in use stay as they are.'; };
$('b-presetdelno').onclick = () => { $('presetdel').hidden = true; };
$('b-presetdelyes').onclick = async () => {
  const r = await api('/api/preset', {action: 'delete', name: S.preset}); $('presetdel').hidden = true;
  presetSay(r.ok ? '' : (r.busy ? 'Wait until the scan finishes, then try again.' : (r.error || 'That did not work.')));
};

// ---------------------------------------------------------------- cards fold away, and remember it
let cards = {};
try { cards = JSON.parse(localStorage.getItem('coolscan.cards') || '{}'); } catch (e) { cards = {}; }
document.querySelectorAll('details.card').forEach(d => {
  if (d.id in cards) d.open = !!cards[d.id];
  d.addEventListener('toggle', () => { cards[d.id] = d.open; try { localStorage.setItem('coolscan.cards', JSON.stringify(cards)); } catch (e) { /* private window: fine */ } });
});
document.querySelectorAll('.card .q').forEach(b => {
  b.setAttribute('aria-expanded', 'false'); b.title = 'Explain these settings';
  b.addEventListener('click', ev => { ev.preventDefault(); ev.stopPropagation(); b.setAttribute('aria-expanded', String(b.closest('.card').classList.toggle('showhelp'))); });
});

// ---------------------------------------------------------------- frames: looking and ticking
function look(i) { cur = i; anchor = i; setCropping(false); render(true); }
function tick(i, on) { on ? sel.add(i) : sel.delete(i); anchor = i; render(true); }
$('tiles').addEventListener('click', ev => {
  const t = ev.target.closest('.tile'); if (!t || ev.target.closest('.tick')) return;   // the tick box has its own handler
  const i = +t.dataset.i;
  if (ev.shiftKey && anchor !== null) { for (let k = Math.min(anchor, i); k <= Math.max(anchor, i); k++) sel.add(k); cur = i; render(true); }
  else if (ev.metaKey || ev.ctrlKey) { cur = i; tick(i, !sel.has(i)); }
  else look(i);
});
$('tiles').addEventListener('change', ev => { const t = ev.target.closest('.tile'); if (t) tick(+t.dataset.i, ev.target.checked); });
$('tiles').addEventListener('dblclick', ev => { const t = ev.target.closest('.tile'); if (t && !ev.target.closest('.tick')) { cur = +t.dataset.i; render(true); openZoom(); } });
$('b-all').onclick = () => { S.frames.forEach(i => sel.add(i)); render(true); };
$('b-none').onclick = () => { sel.clear(); render(true); };
const turn = quarter => api('/api/rotate', {frames: targets().length ? targets() : S.frames, quarter});
$('b-rl').onclick = () => turn(1);
$('b-rr').onclick = () => turn(3);

// ---------------------------------------------------------------- the big picture: enlarge, crop, focus point
function openZoom() {
  const t = tile(cur); if (cur === null || !t.src) return;
  $('zoomimg').src = '/frame/' + cur + '.png?v=' + t.v; $('zoomimg').alt = 'Frame ' + (cur + 1) + ', enlarged';
  $('zoomcap').textContent = 'Frame ' + (cur + 1) + ' · ' + SOURCE[t.src] + ' · click or press Esc to close';
  $('zoom').showModal();
}
$('zoom').addEventListener('click', () => $('zoom').close());
$('b-zoom').onclick = openZoom;
$('big').addEventListener('dblclick', () => { if (!cropping && !picking) openZoom(); });

function setCropping(on) { cropping = on; drag = null; if (on) setPicking(false); document.body.classList.toggle('cropping', on); $('b-crop').setAttribute('aria-pressed', String(on)); }
$('b-crop').onclick = () => { setCropping(!cropping); render(true); };
$('b-cropclear').onclick = async () => { if (cur !== null) { await api('/api/crop', {frame: cur, rect: null}); setCropping(false); } };
function at(ev) { const r = $('big').getBoundingClientRect(), f = v => Math.min(1, Math.max(0, v)); return [f((ev.clientX - r.left) / r.width), f((ev.clientY - r.top) / r.height)]; }
function dragRect() { return [Math.min(drag.a[0], drag.b[0]), Math.min(drag.a[1], drag.b[1]), Math.max(drag.a[0], drag.b[0]), Math.max(drag.a[1], drag.b[1])]; }
function cropOnScreen(t) {
  if (!t.crop) return null;
  const k = t.turns || 0, a = toScreen([t.crop[0], t.crop[1]], k), b = toScreen([t.crop[2], t.crop[3]], k);
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}
function placeCrop(r) { const box = $('cropbox'); box.hidden = !r; if (r) Object.assign(box.style, {left: r[0] * 100 + '%', top: r[1] * 100 + '%', width: (r[2] - r[0]) * 100 + '%', height: (r[3] - r[1]) * 100 + '%'}); }
const iw = $('imgw');
iw.addEventListener('pointerdown', ev => {
  if (cur === null) return;
  if (picking) { setPicking(false); api('/api/settings', {focus_point: toScanner(at(ev), tile(cur).turns || 0)}); return; }
  if (!cropping) return;
  ev.preventDefault(); iw.setPointerCapture(ev.pointerId); drag = {a: at(ev), b: at(ev)}; placeCrop(dragRect());
});
iw.addEventListener('pointermove', ev => { if (drag) { drag.b = at(ev); placeCrop(dragRect()); } });
iw.addEventListener('pointerup', async ev => {
  if (!drag) return;
  drag.b = at(ev); const r = dragRect(), k = tile(cur).turns || 0; drag = null;
  if (r[2] - r[0] < 0.05 || r[3] - r[1] < 0.05) { render(true); return; }   // a click, not a drag: the crop stays as it was
  const a = toScanner([r[0], r[1]], k), b = toScanner([r[2], r[3]], k);
  await api('/api/crop', {frame: cur, rect: [a[0], a[1], b[0], b[1]]}); setCropping(false);
});
iw.addEventListener('pointercancel', () => { drag = null; render(true); });

// ---------------------------------------------------------------- keys
document.addEventListener('keydown', ev => {
  if ($('zoom').open) { if (ev.key === ' ' || ev.key === 'Enter') { ev.preventDefault(); $('zoom').close(); } return; }
  if (document.querySelector('dialog[open]') || !S) return;
  const el = document.activeElement, tag = el ? el.tagName : '', typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag);
  const cmd = ev.metaKey || ev.ctrlKey;
  if (cmd && ev.key === 'Enter') { ev.preventDefault(); if (!$('b-scan').disabled) $('b-scan').click(); return; }
  if (typing) return;
  if (cmd && ev.key.toLowerCase() === 'a') { ev.preventDefault(); $('b-all').click(); return; }
  if (cmd || ev.altKey) return;
  const n = S.frames.length, cols = n <= 2 ? Math.max(n, 1) : Math.ceil(n / 2);
  const go = d => { if (!n) return; ev.preventDefault(); look(Math.min(n - 1, Math.max(0, (cur ?? 0) + d))); };
  const press = id => { if (!$(id).disabled && !$(id).hidden) $(id).click(); };
  switch (ev.key) {
    case 'ArrowRight': go(1); break;
    case 'ArrowLeft': go(-1); break;
    case 'ArrowDown': go(cols); break;
    case 'ArrowUp': go(-cols); break;
    case ' ': if (tag !== 'BUTTON' && tag !== 'SUMMARY') { ev.preventDefault(); openZoom(); } break;
    case 'x': case 'X': if (cur !== null) tick(cur, !sel.has(cur)); break;
    case 'Escape': if (cropping || picking) { setCropping(false); setPicking(false); render(true); } else if (sel.size) { sel.clear(); render(true); } break;
    case '[': press('b-rl'); break;
    case ']': press('b-rr'); break;
    case 'c': case 'C': press('b-crop'); break;
    case 'p': case 'P': press('b-preview'); break;
    case 't': case 'T': press('b-thumb'); break;
  }
});

// ---------------------------------------------------------------- drawing
function fillSelect(id, vals, curVal, lab) {
  const e = $(id), want = vals.map(String).join();
  if (e.dataset.v !== want) { e.innerHTML = ''; for (const v of vals) { const o = document.createElement('option'); o.value = v; o.textContent = lab ? lab(v) : v; e.appendChild(o); } e.dataset.v = want; }
  e.value = String(curVal);
}
function optionText(id) { const e = $(id), o = e.options[e.selectedIndex]; return o ? o.textContent : ''; }

function drawTiles() {
  const box = $('tiles'), n = S.frames.length;
  const shape = S.strip + '|' + S.frames.map(i => tile(i).v + ':' + tile(i).src).join(',');
  if (shape !== lastShape) {          // other pictures: build the tiles again
    lastShape = shape;
    box.style.setProperty('--cols', n <= 2 ? Math.max(n, 1) : Math.ceil(n / 2));
    box.innerHTML = '';
    for (const i of S.frames) {
      const t = tile(i), d = document.createElement('div');
      d.className = 'tile'; d.dataset.i = i; d.setAttribute('role', 'option'); d.title = 'Frame ' + (i + 1);
      const pic = document.createElement('div'); pic.className = 'pic';
      if (t.src) { const im = document.createElement('img'); im.src = '/frame/' + i + '.png?v=' + t.v; im.alt = 'Frame ' + (i + 1); im.draggable = false; pic.appendChild(im); }
      else { const s = document.createElement('span'); s.className = 'none'; s.textContent = 'No picture yet'; pic.appendChild(s); }
      const lab = document.createElement('label'); lab.className = 'tick'; lab.title = 'Tick to scan this frame';
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.setAttribute('aria-label', 'Scan frame ' + (i + 1));
      const num = document.createElement('span'); num.textContent = i + 1; lab.append(cb, num);
      d.append(pic, lab);
      if (t.src === 'scan' || t.src === 'preview') { const b = document.createElement('span'); b.className = 'badge ' + t.src; b.textContent = t.src === 'scan' ? 'Scanned' : 'Preview'; d.appendChild(b); }
      box.appendChild(d);
    }
  }
  for (const d of box.children) {      // what is looked at and what is ticked: only the marks change
    const i = +d.dataset.i;
    d.classList.toggle('cur', cur === i); d.classList.toggle('ticked', sel.has(i));
    d.setAttribute('aria-selected', String(sel.has(i))); d.querySelector('input').checked = sel.has(i);
  }
  $('noframes').hidden = n > 0;
}

function drawViewer(busy) {
  const t = cur !== null ? tile(cur) : {}, has = !!t.src;
  $('vtitle').textContent = cur !== null ? 'Frame ' + (cur + 1) : 'Preview';
  const chip = $('vsrc'); chip.hidden = !has;
  if (has) { chip.textContent = SOURCE[t.src]; chip.className = 'chip ' + t.src; }
  $('vinfo').textContent = has ? t.info : '';
  $('imgw').hidden = !has; $('vempty').hidden = has;
  $('vempty').textContent = cur === null ? (S.frames.length ? 'Click a frame above to see it here.' : 'Nothing to show yet.')
    : 'No picture of this frame yet. Press Preview scan to look at it.';
  if (has) {
    const pic = '/frame/' + cur + '.png?v=' + t.v;
    if (pic !== shownPic) { $('big').src = pic; shownPic = pic; }
    $('big').alt = 'Frame ' + (cur + 1) + ', ' + SOURCE[t.src].toLowerCase();
  } else shownPic = '';
  if (!drag) placeCrop(has ? cropOnScreen(t) : null);
  const fp = S.settings.focus_point, mark = $('fmark'); mark.hidden = !(has && fp);
  if (has && fp) { const q = toScreen(fp, t.turns || 0); mark.style.left = q[0] * 100 + '%'; mark.style.top = q[1] * 100 + '%'; }
  $('b-cropclear').hidden = !t.crop;
  for (const id of ['b-crop', 'b-zoom']) $(id).disabled = !has;
  $('b-sharper').disabled = busy || cur === null; $('b-rl').disabled = $('b-rr').disabled = busy || !S.frames.length;
  const sv = $('vsaved'), saved = t.saved || [];
  sv.hidden = !saved.length && !cropping && !t.crop;
  sv.textContent = cropping ? 'Drag on the picture to mark the part to save. The crop is used when this frame is scanned.'
    : [saved.length ? 'Saved from this frame: ' + saved.map(s => s.files.join(', ') + ' (' + clock(s.took) + ')').join(' · ') : '',
       t.crop ? 'Cropped: only the marked part is saved (the RAW file of Archive mode keeps the whole frame).' : ''].filter(Boolean).join(' ');
}

function drawStrip() {
  $('stripbox').hidden = !S.thumb;
  if (!S.thumb) return;
  const src = '/thumb.png?v=' + S.thumb_v; if ($('thumb').dataset.v !== src) { $('thumb').src = src; $('thumb').dataset.v = src; }
  const inner = document.querySelector('#thumbwrap .inner'); inner.querySelectorAll('.box').forEach(b => b.remove());
  for (const b of S.boxes) {
    const d = document.createElement('div'); d.className = 'box' + (cur === b.i ? ' cur' : '');
    Object.assign(d.style, {left: b.x * 100 + '%', top: b.y * 100 + '%', width: b.w * 100 + '%', height: b.h * 100 + '%'});
    const s = document.createElement('span'); s.textContent = b.i + 1; d.appendChild(s); d.onclick = () => look(b.i); inner.appendChild(d);
  }
}

function drawSummaries(st) {
  const on = [];
  $('sum-presets').textContent = S.preset || (S.presets.length ? 'not saved' : '');
  $('sum-film').textContent = optionText('s-film') + (st.film === 'negative' && st.film_profile ? ' · ' + st.film_profile : '');
  $('sum-roll').textContent = (st.roll || '').split(' - ')[0];
  $('sum-scan').textContent = st.dpi + ' dpi' + (st.clean ? ' · dust removal' : '') + (st.samples > 1 ? ' · ' + st.samples + 'x' : '') + (+st.multi_exposure ? ' · Multi-Exposure' : '') + (+st.grain ? ' · grain ' + st.grain : '');
  $('sum-save').textContent = (st.archive ? 'RAW + Positive' : optionText('s-negatives')) + ' · ' + (st.colour_space === 'scanner' ? 'Scanner RGB' : st.colour_space.split(' ')[0]);
  $('sum-names').textContent = S.sample_name;
  $('sum-focus').textContent = st.focus_mode === 'same' || st.exposure_mode === 'same' ? 'kept from Calibrate' : 'each frame on its own';
  $('sum-preview').textContent = st.preview_dpi + ' dpi' + (st.auto_thumbnail ? ' · automatic' : '');
  if (st.eject_after) on.push('eject'); if (st.notify_mac) on.push('banner'); if (st.notify_shortcut) on.push('shortcut'); if (st.notify_ntfy) on.push('ntfy');
  $('sum-done').textContent = on.join(' · ');
}

function render(force) {
  if (!S) return;
  const st = S.settings, c = S.caps, busy = !!S.job;
  // ticks and the frame looked at belong to one strip: another strip, or fewer frames, and they go
  if (S.strip !== lastStrip) { if (lastStrip !== null) { sel.clear(); anchor = null; cur = null; setCropping(false); setPicking(false); } lastStrip = S.strip; }
  for (const i of [...sel]) if (i >= S.frames.length) sel.delete(i);
  if (cur !== null && cur >= S.frames.length) cur = null;
  if (cur === null && S.frames.length) cur = 0;

  $('status').title = c && c.revision ? 'Firmware ' + c.revision : '';
  $('status').textContent = (S.demo ? 'DEMO · ' : '') + (c ? (c.product + ' · ' + c.optical_dpi + ' dpi' + (c.holder ? ' · ' + c.holder : '') + (S.film_in === false ? ' · no film' : '')) : 'not connected');

  keys.forEach(k => {
    const e = $('s-' + k); if (document.activeElement === e) return;
    if (e.type === 'checkbox') e.checked = !!st[k];
    else if (k === 'start') e.value = S.next_number;
    else if (!['dpi', 'preview_dpi', 'samples', 'format'].includes(k)) e.value = st[k] ?? '';
  });
  const opt = c ? c.optical_dpi : 2900, lo = c && c.x_dpi_range ? c.x_dpi_range[0] : 73, dpis = [];
  for (let n = 1; n <= 40; n++) { const d = Math.round(opt / n); if (d >= lo) dpis.push(d); }
  fillSelect('s-dpi', dpis, st.dpi); fillSelect('s-preview_dpi', dpis.filter(d => d <= 1000), st.preview_dpi);
  const ms = c ? (c.max_samples || 1) : 1;
  fillSelect('s-samples', [1, 2, 4, 8, 16].filter(n => n <= Math.max(ms, 4)), st.samples, v => v == 1 ? 'Off' : (v <= ms ? v + 'x (one pass)' : v + 'x (' + v + ' passes)'));
  $('mshint').hidden = !(st.samples > ms);
  $('mshint').textContent = 'Averages ' + Math.min(st.samples, 4) + ' passes, so each frame takes that many times longer.';
  $('mehint').hidden = !+st.multi_exposure;
  $('mehint').textContent = st.samples > ms ? 'Left out while multi-sample averages passes: choose one of the two.' : 'A second pass exposed ' + st.multi_exposure + ' times longer: each frame takes at least twice as long. Not tried on the real scanner yet.';
  const fmts = c && c.film_formats ? c.film_formats : []; $('fmtrow').hidden = !fmts.length;
  fillSelect('s-format', [''].concat(fmts), st.format, v => v || 'From holder');
  $('negrow').hidden = !!st.archive; $('dustrow').hidden = !st.clean;
  const iceopt = $('s-dust_method').querySelector('option[value=ice]'); iceopt.hidden = iceopt.disabled = !S.ice_helper;

  for (const id of ['b-load', 'b-eject', 'b-thumb', 'b-preview', 'b-cal', 'b-af', 'b-ae', 'b-unlock', 'b-scan', 'b-connect', 'b-newroll', 'b-rollok',
    'b-rollno', 'b-rollnoyes', 'rollpick', 'preset', 'b-presetdelyes']) $(id).disabled = busy;
  $('b-stop').disabled = !busy;
  const nf = S.frames.length, ns = sel.size;
  $('b-scan').textContent = ns ? 'Scan ' + plural(ns, 'selected frame') : (nf ? 'Scan all ' + plural(nf, 'frame') : 'Scan all frames');
  $('b-preview').textContent = ns ? 'Preview ' + ns + ' selected' : (cur !== null ? 'Preview frame ' + (cur + 1) : 'Preview');
  $('framesum').textContent = nf ? (ns ? ns + ' of ' + nf + ' ticked for scanning' : plural(nf, 'frame') + ' · none ticked, so Scan takes the whole strip') : '';
  $('b-all').disabled = $('b-none').disabled = !nf;

  // rolls
  if (document.activeElement !== $('rollpick')) {
    const rp = $('rollpick'), want = S.rolls.join('\n');
    if (rp.dataset.v !== want) { rp.innerHTML = ''; for (const n of S.rolls) { const o = document.createElement('option'); o.value = n; o.textContent = n; rp.appendChild(o); } rp.dataset.v = want; }
    rp.value = st.roll || '';
  }
  $('rollfilm').textContent = (S.film_text ? ('Film: ' + S.film_text) : 'No film details') + ' · saving in ' + (S.roll_path || '') + (st.archive ? ' (RAW/ and Positive/)' : '');
  if (document.activeElement !== $('rollno') && $('rollnodlg').hidden) $('rollno').value = S.roll_number || '';
  const dsc = $('rolldesc'); if (document.activeElement !== dsc && Date.now() > descHold) dsc.value = st.film_description || '';
  $('nameex').textContent = 'Next file: ' + (st.archive ? 'RAW/ and Positive/' : '') + S.sample_name;

  // saved settings
  if (S.preset) lastPreset = S.preset;
  const pr = $('preset');
  if (document.activeElement !== pr) {
    const want = S.presets.join('\n') + '|' + (S.preset || '');
    if (pr.dataset.v !== want) {
      pr.innerHTML = '';
      if (!S.preset) { const o = document.createElement('option'); o.value = ''; o.textContent = S.presets.length ? 'Not saved' : 'Nothing saved yet'; pr.appendChild(o); }
      for (const n of S.presets) { const o = document.createElement('option'); o.value = n; o.textContent = n; pr.appendChild(o); }
      pr.dataset.v = want;
    }
    pr.value = S.preset || '';
  }
  $('b-presetdel').disabled = busy || !S.preset;

  // film profiles
  $('profilebox').hidden = st.film !== 'negative';
  const fp = $('filmprofile'), profs = S.film_profiles || [];
  if (document.activeElement !== fp) {
    const want = profs.map(p => p.name + '/' + p.frames).join('\n');
    if (fp.dataset.v !== want) {
      fp.innerHTML = '';
      const o = document.createElement('option'); o.value = ''; o.textContent = 'Each frame on its own'; fp.appendChild(o);
      for (const p of profs) { const e = document.createElement('option'); e.value = p.name; e.textContent = p.name; fp.appendChild(e); }
      fp.dataset.v = want;
    }
    fp.value = st.film_profile || '';
  }
  const used = profs.find(p => p.name === st.film_profile), n = S.roll_measured || 0, least = S.profile_least || 3;
  $('profilehint').textContent = (used ? 'Measured from ' + used.frames + ' frames of ' + (used.roll || 'a roll') + (used.made ? ', ' + used.made : '') + '. ' : '')
    + 'This roll: ' + n + (n === 1 ? ' frame' : ' frames') + ' measured' + (n < least ? ' (a profile needs ' + least + ' or more; frames are measured when scanned).' : '.');
  fp.disabled = busy; $('b-profiledelyes').disabled = busy;
  $('b-profilenew').disabled = n < least; $('b-profiledel').disabled = busy || !fp.value;

  // focus and exposure
  const fl = S.focus_lock, el = S.exposure_lock, af = !c || c.autofocus;
  $('b-af').hidden = !af; $('posrow').hidden = st.focus_mode !== 'manual';
  if (c && c.focus_range) { $('s-focus_position').min = c.focus_range[0]; $('s-focus_position').max = c.focus_range[1]; }
  $('fpoint').textContent = picking ? 'click the big picture…' : (st.focus_point ? st.focus_point.map(v => v.toFixed(2)).join(', ') : 'centre');
  $('b-fpoint').textContent = st.focus_point ? 'Clear' : (picking ? 'Cancel' : 'Pick');
  $('calinfo').textContent = [fl ? 'Focus: ' + fl.result + ' at lens position ' + fl.position + (fl.frame != null ? ' (frame ' + (fl.frame + 1) + ')' : '') + '.'
      : (st.focus_mode === 'frame' ? 'Focus: each frame is focused on its first preview or scan.' : 'Focus: not set yet.'),
    el ? 'Exposure: ' + Object.entries(el.exposures).map(([k, v]) => k[0].toUpperCase() + ' ' + v).join(', ') + (el.infrared ? ' (with IR)' : '') + (el.frame != null ? ' from frame ' + (el.frame + 1) : '') + '.'
      : 'Exposure: not set yet.'].join(' ');

  drawTiles(); drawViewer(busy); drawSummaries(st);
  if (force || S.version !== lastV) { drawStrip(); $('log').textContent = S.log.join('\n'); $('log').scrollTop = 1e9; lastV = S.version; }

  // what is happening now, and how long it takes
  const p = S.progress, tm = S.timing || {}, prog = $('prog');
  $('busy').hidden = !busy;
  if (busy) {
    if (p) { prog.max = p.total || 1; prog.value = p.done; } else prog.removeAttribute('value');
    $('progtxt').textContent = S.job + (p ? ': ' + p.phase + ' ' + (p.total ? Math.round(100 * p.done / p.total) : 0) + '%' : '…');
    let tt = '';
    if (tm.strip_started) {
      const left = tm.average ? tm.average * (tm.of - tm.done) - (tm.frame_started ? Math.min(tm.average, tm.now - tm.frame_started) : 0) : null;
      tt = 'Frame ' + (tm.frame || '') + ' (' + Math.min(tm.done + 1, tm.of) + ' of ' + tm.of + ') · ' + clock(tm.now - (tm.frame_started || tm.now)) + ' on this frame · '
        + clock(tm.now - tm.strip_started) + ' on this strip' + (left !== null ? ' · about ' + clock(left) + ' left' : '') + (tm.last_frame ? ' · last frame took ' + clock(tm.last_frame) : '');
    }
    $('timing').textContent = tt;
  }
  const ls = tm.last_strip;
  $('laststrip').textContent = !busy && ls ? 'Last strip: ' + ls.frames + ' of ' + plural(ls.of, 'frame') + ' in ' + clock(ls.seconds) + (ls.average ? ' (' + clock(ls.average) + ' per frame)' : '') + ', finished ' + ls.ended : '';
}

async function poll() {
  let next = null;
  try { next = await (await fetch('/api/state')).json(); }
  catch (e) { $('status').textContent = 'The app has stopped. Start it again in Terminal: python coolscan.py'; }
  // the page files are read fresh on every reload, the rest of the app only when it starts: a new page
  // on an app that was started before an update must not try to draw what that app does not send
  if (next && !('tiles' in next)) {
    $('status').textContent = 'The app was updated while it was running. When no scan is going, stop it in Terminal (Ctrl-C) and start it again: python coolscan.py';
    next = null;
  }
  try { if (next) { S = next; render(false); } }
  finally { setTimeout(poll, 500); }
}
poll();
