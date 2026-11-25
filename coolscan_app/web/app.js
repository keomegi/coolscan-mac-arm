'use strict';
// The page asks the app for its state twice a second and draws it. Two ideas run through it:
//  - the frame you are LOOKING at (cur) is shown big; clicking a frame only looks at it
//  - the frames you TICKED (sel) are what Scan takes; with none ticked, Scan takes the whole strip
let S = null, sel = new Set(), cur = null, anchor = null;
let lastV = -1, lastStrip = null, lastShape = '', shownPic = '', lastPreset = '';
let picking = false, cropping = false, drag = null, descHold = 0;
const $ = id => document.getElementById(id);
const SOURCE = {thumbnail: 'Thumbnail', preview: 'Preview', scan: 'Scan'};
const JOBS = {scan: 'Scanning', preview: 'Preview scan', thumbnail: 'Thumbnail', connect: 'Connecting', load: 'Loading the film', eject: 'Ejecting',
  calibrate: 'Calibrating', autofocus: 'Focusing', 'auto exposure': 'Measuring the exposure'};

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
  'white_balance', 'dpi', 'samples', 'clean', 'negatives', 'archive', 'outdir', 'basename', 'digits', 'start', 'photo_date',
  'colour_space', 'name_date', 'tone', 'stop_on_errors', 'eject_after', 'scan_log', 'auto_thumbnail', 'dust_method', 'grain',
  'restore', 'notify_mac', 'notify_shortcut', 'notify_ntfy', 'multi_exposure', 'crop_scan', 'auto_crop', 'use_roll', 'overwrite', 'frame_fit', 'auto_crop_trim', 'auto_crop_sides', 'notify_on', 'log_days', 'archive_default', 'auto_crop_learn', 'preview_af', 'preview_ae'];
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
// Scan; with "Allow overwriting" and a typed number the app first names the pictures it would replace
let overFrames = [];
$('b-scan').onclick = async () => {
  const frames = ticked(), r = await api('/api/scan', {frames});
  if (!r.confirm_overwrite) return;
  overFrames = frames;
  const n = r.confirm_overwrite.length, ul = $('overwritelist');
  $('overwritemsg').textContent = plural(n, 'picture') + ' in ' + (r.folder || 'the folder') + ' will be replaced by this scan:';
  ul.innerHTML = '';
  r.confirm_overwrite.slice(0, 12).forEach(s => { const li = document.createElement('li'); li.textContent = s; ul.appendChild(li); });
  if (n > 12) { const li = document.createElement('li'); li.textContent = '…and ' + (n - 12) + ' more'; ul.appendChild(li); }
  $('overwritedlg').showModal(); $('b-overcancel').focus();
};
$('b-overcancel').onclick = () => $('overwritedlg').close();
$('b-overok').onclick = () => { $('overwritedlg').close(); api('/api/scan', {frames: overFrames, overwrite_ok: true}); };
$('b-overskip').onclick = async () => {
  $('overwritedlg').close();
  await api('/api/settings', {overwrite: false});  // back to skipping names in use; the typed number stays the start
  api('/api/scan', {frames: overFrames});
};
$('b-choosefolder').onclick = async () => {
  const r = await api('/api/choose_folder'), m = $('foldermsg');
  m.hidden = !r.error; m.textContent = r.error || '';
  if (r.ok && r.path) { $('s-outdir').value = r.path; api('/api/settings', {outdir: r.path}); }
};
$('b-stop').onclick = () => api('/api/stop');

// ---------------------------------------------------------------- the Activity list: its latest line in the bar, the whole list folds out
const BAD = /\bfail(ed|s)?\b|warning|could not|couldn't|went wrong|not ejecting|stopped working/i;
let logOpen = false, logSeen = new Set(), logShown = '';
try { logOpen = localStorage.getItem('coolscan.log') === '1'; } catch (e) { /* private window: closed */ }
function setLog(on) {
  logOpen = on; $('activity').hidden = !on; $('b-log').setAttribute('aria-expanded', String(on));
  try { localStorage.setItem('coolscan.log', on ? '1' : '0'); } catch (e) { /* fine */ }
  if (on) { $('log').scrollTop = 1e9; render(true); }
}
$('b-log').onclick = () => setLog(!logOpen);
function drawLog() {
  const lines = S.log || [], last = lines[lines.length - 1] || '';
  if (logOpen) lines.forEach(l => logSeen.add(l));
  const unseen = lines.filter(l => BAD.test(l) && !logSeen.has(l));
  const w = $('logwarn'); w.hidden = !unseen.length; w.textContent = unseen.length;
  w.title = unseen.length ? plural(unseen.length, 'problem') + ' since you last looked' : '';
  const ll = $('lastline'), showBad = unseen.length ? unseen[unseen.length - 1] : '';
  ll.textContent = showBad || last; ll.classList.toggle('bad', !!showBad); ll.title = showBad || last;
  const want = lines.join('\n');
  if (want !== logShown) {
    logShown = want; const box = $('log'); box.textContent = '';
    lines.forEach((l, k) => {
      const sp = document.createElement('span'); sp.textContent = l + (k < lines.length - 1 ? '\n' : '');
      if (BAD.test(l)) sp.className = 'bad'; box.appendChild(sp);
    });
    box.scrollTop = 1e9;
  }
}
$('lastline').onclick = () => setLog(true);

// ---------------------------------------------------------------- keyboard shortcuts
$('b-keys').onclick = () => $('keysdlg').showModal();
$('b-keysok').onclick = () => $('keysdlg').close();

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
function look(i) {
  const inTiles = $('tiles').contains(document.activeElement);
  cur = i; anchor = i; setCropping(false); render(true);
  if (inTiles) { const t = $('tiles').querySelector('.tile[data-i="' + i + '"]'); if (t) t.focus(); }
}
$('tiles').addEventListener('focusin', ev => { const t = ev.target.closest('.tile'); if (t && +t.dataset.i !== cur) look(+t.dataset.i); });
function tick(i, on) { on ? sel.add(i) : sel.delete(i); anchor = i; render(true); }
$('tiles').addEventListener('click', ev => {
  const t = ev.target.closest('.tile'); if (!t || ev.target.closest('.tick')) return;   // the tick box has its own handler
  if (ev.target.closest('.fsbtn')) { look(+t.dataset.i); openFrameSets(); return; }
  const i = +t.dataset.i;
  if (ev.shiftKey && anchor !== null) { for (let k = Math.min(anchor, i); k <= Math.max(anchor, i); k++) sel.add(k); cur = i; render(true); }
  else if (ev.metaKey || ev.ctrlKey) { cur = i; tick(i, !sel.has(i)); }
  else look(i);
});
$('tiles').addEventListener('change', ev => { const t = ev.target.closest('.tile'); if (t) tick(+t.dataset.i, ev.target.checked); });
$('tiles').addEventListener('dblclick', ev => { const t = ev.target.closest('.tile'); if (t && !ev.target.closest('.tick') && !ev.target.closest('.fsbtn')) { cur = +t.dataset.i; render(true); openZoom(); } });
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
$('imgw').addEventListener('dblclick', () => { if (!cropping && !picking) openZoom(); });  // on the picture or on its crop box

function setCropping(on) { cropping = on; drag = null; if (on) setPicking(false); document.body.classList.toggle('cropping', on); $('b-crop').setAttribute('aria-pressed', String(on)); }
$('b-crop').onclick = () => { setCropping(!cropping); render(true); };
$('b-autocrop').onclick = async () => {
  if (cur === null) return;
  await api('/api/autocrop', {frame: cur});   // what it found, or that it found nothing, goes to the Activity list
};
$('b-cropclear').onclick = async () => { if (cur !== null) { await api('/api/crop', {frame: cur, rect: null}); setCropping(false); } };
function at(ev) { const r = $('big').getBoundingClientRect(), f = v => Math.min(1, Math.max(0, v)); return [f((ev.clientX - r.left) / r.width), f((ev.clientY - r.top) / r.height)]; }
function dragRect() {
  if (drag.edit) return editRect();
  const px = keptShape(Math.abs(drag.b[0] - drag.a[0]) * picAspect() >= Math.abs(drag.b[1] - drag.a[1]));
  if (px) return shapedFrom(drag.a, drag.b, px);
  return [Math.min(drag.a[0], drag.b[0]), Math.min(drag.a[1], drag.b[1]), Math.max(drag.a[0], drag.b[0]), Math.max(drag.a[1], drag.b[1])];
}
// changing a crop that is there: drag.edit is "move" or the side(s) held (n, s, e, w, nw, ...), drag.r the box as it was
const MIN_CROP = 0.05;  // the smallest crop the app keeps, as a fraction of the frame
// The crop's shape. Boxes are kept as fractions of the picture, so a shape of w:h in pixels is
// w/h divided by the picture's own width/height in fractions. "Same as the frame" is the picture's shape.
let cropShape = 'free';
try { cropShape = localStorage.getItem('coolscan.cropshape') || 'free'; } catch (e) { /* fine */ }
$('cropratio').value = cropShape; if ($('cropratio').value !== cropShape) { cropShape = 'free'; $('cropratio').value = 'free'; }
function picAspect() { const r = $('big').getBoundingClientRect(); return r.width && r.height ? r.width / r.height : 1.5; }
// the pixel shape to keep (width / height), or null; wide: whether the box is wider than tall
function keptShape(wide, exact) {
  if (exact) return exact;
  if (cropShape === 'free') return null;
  const R = cropShape === 'frame' ? picAspect() : +cropShape, big = Math.max(R, 1 / R);
  return wide ? big : 1 / big;
}
function pxShape(r) { return (r[2] - r[0]) * picAspect() / (r[3] - r[1]); }
// a box from a fixed corner a towards the point b, kept to the pixel shape `px`, inside the picture
function shapedFrom(a, b, px) {
  const A = picAspect(), q = px / A, sx = b[0] >= a[0] ? 1 : -1, sy = b[1] >= a[1] ? 1 : -1;
  let fw = Math.abs(b[0] - a[0]), fh = Math.abs(b[1] - a[1]);
  if (fh === 0 || fw / fh > q) fh = fw / q; else fw = fh * q;
  const mw = sx > 0 ? 1 - a[0] : a[0], mh = sy > 0 ? 1 - a[1] : a[1];
  if (fw > mw) { fw = mw; fh = fw / q; }
  if (fh > mh) { fh = mh; fw = fh * q; }
  const x2 = a[0] + sx * fw, y2 = a[1] + sy * fh;
  return [Math.min(a[0], x2), Math.min(a[1], y2), Math.max(a[0], x2), Math.max(a[1], y2)];
}
// the largest box of pixel shape px centred on the box r and inside the picture
function shapedAround(r, px) {
  const A = picAspect(), q = px / A, cx = (r[0] + r[2]) / 2, cy = (r[1] + r[3]) / 2;
  let fw = r[2] - r[0], fh = r[3] - r[1];
  if (fw / fh > q) fw = fh * q; else fh = fw / q;
  return [cx - fw / 2, cy - fh / 2, cx + fw / 2, cy + fh / 2];
}
async function saveCrop(r) {
  const k = tile(cur).turns || 0, a = toScanner([r[0], r[1]], k), b = toScanner([r[2], r[3]], k);
  await api('/api/crop', {frame: cur, rect: [a[0], a[1], b[0], b[1]]});
}
function cropNow() { const t = tile(cur); return cur === null || !t.src ? null : (cropOnScreen(t) || (cropping ? [0, 0, 1, 1] : null)); }
$('cropratio').onchange = () => {
  cropShape = $('cropratio').value; try { localStorage.setItem('coolscan.cropshape', cropShape); } catch (e) { /* fine */ }
  const r = cropNow(), px = r && keptShape(pxShape(r) >= 1);
  if (r && px) saveCrop(shapedAround(r, px)); else render(true);
};
$('b-cropswap').onclick = () => {
  const r = cropNow(); if (!r) return;
  const A = picAspect(), cx = (r[0] + r[2]) / 2, cy = (r[1] + r[3]) / 2;
  let fw = (r[3] - r[1]) / A, fh = (r[2] - r[0]) * A;   // the same pixel size, turned
  const s = Math.min(1, 2 * Math.min(cx, 1 - cx) / fw, 2 * Math.min(cy, 1 - cy) / fh); fw *= s; fh *= s;
  saveCrop([cx - fw / 2, cy - fh / 2, cx + fw / 2, cy + fh / 2]);
};
// what the crop comes to in pixels. The size of the whole frame is worked out from the picture's shape
// and a 35 mm frame as the LS-40 scans it (4320 px across at 2900 dpi), so it is an estimate
function showCropSize(r) {
  const t = tile(cur), el = $('cropsize');
  if (!r || !S) { el.textContent = ''; return; }
  const dpi = +S.settings.dpi || 2900, A = picAspect(), long = 4320 / 2900 * dpi;
  const W = A >= 1 ? long : long * A, H = A >= 1 ? long / A : long;
  const w = Math.round((r[2] - r[0]) * W), h = Math.round((r[3] - r[1]) * H), px = w / h;
  const named = [[1.5, '3:2'], [4 / 3, '4:3'], [1.25, '5:4'], [1, 'square'], [16 / 9, '16:9']]
    .find(([v]) => Math.abs(Math.max(px, 1 / px) - v) < 0.01);
  el.textContent = 'About ' + w.toLocaleString() + ' × ' + h.toLocaleString() + ' px at ' + dpi + ' dpi'
    + ' · ' + (named ? named[1] + (named[1] === 'square' ? '' : (px >= 1 ? ' wide' : ' tall')) : (px >= 1 ? px.toFixed(2) + ':1' : '1:' + (1 / px).toFixed(2)))
    + (t.crop ? '' : ' (whole frame)');
}
function editRect() {
  const r = drag.r.slice(), dx = drag.b[0] - drag.a[0], dy = drag.b[1] - drag.a[1], h = drag.edit;
  if (h === 'move') {
    const mx = Math.min(1 - r[2], Math.max(-r[0], dx)), my = Math.min(1 - r[3], Math.max(-r[1], dy));
    return [r[0] + mx, r[1] + my, r[2] + mx, r[3] + my];
  }
  const f = v => Math.min(1, Math.max(0, v));
  // a shape to keep: the one chosen, or with Shift held the one the crop has now
  const px = keptShape(pxShape(drag.r) >= 1, drag.shift ? pxShape(drag.r) : null);
  if (px) {
    const o = drag.r;
    if (h.length === 2) {   // a corner: the opposite corner stays where it is
      const a = [h.includes('w') ? o[2] : o[0], h.includes('n') ? o[3] : o[1]];
      const b = [f((h.includes('w') ? o[0] : o[2]) + dx), f((h.includes('n') ? o[1] : o[3]) + dy)];
      return shapedFrom(a, b, px);
    }
    const A = picAspect();
    if (h === 'e' || h === 'w') {   // a side: the other side stays, the height follows, centred
      const cy = (o[1] + o[3]) / 2, x0 = h === 'w' ? o[2] : o[0];
      let fw = Math.abs(f((h === 'w' ? o[0] : o[2]) + dx) - x0), fh = fw * A / px;
      const mh = 2 * Math.min(cy, 1 - cy); if (fh > mh) { fh = mh; fw = fh * px / A; }
      fw = Math.max(fw, MIN_CROP);
      return h === 'w' ? [x0 - fw, cy - fh / 2, x0, cy + fh / 2] : [x0, cy - fh / 2, x0 + fw, cy + fh / 2];
    }
    const cx = (o[0] + o[2]) / 2, y0 = h === 'n' ? o[3] : o[1];
    let fh = Math.abs(f((h === 'n' ? o[1] : o[3]) + dy) - y0), fw = fh * px / A;
    const mw = 2 * Math.min(cx, 1 - cx); if (fw > mw) { fw = mw; fh = fw * A / px; }
    fh = Math.max(fh, MIN_CROP);
    return h === 'n' ? [cx - fw / 2, y0 - fh, cx + fw / 2, y0] : [cx - fw / 2, y0, cx + fw / 2, y0 + fh];
  }
  if (h.includes('w')) r[0] = Math.min(f(drag.r[0] + dx), r[2] - MIN_CROP);
  if (h.includes('e')) r[2] = Math.max(f(drag.r[2] + dx), r[0] + MIN_CROP);
  if (h.includes('n')) r[1] = Math.min(f(drag.r[1] + dy), r[3] - MIN_CROP);
  if (h.includes('s')) r[3] = Math.max(f(drag.r[3] + dy), r[1] + MIN_CROP);
  return r;
}
function cropOnScreen(t) {
  if (!t.crop) return null;
  const k = t.turns || 0, a = toScreen([t.crop[0], t.crop[1]], k), b = toScreen([t.crop[2], t.crop[3]], k);
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}
// the box on screen: the crop, or while Crop is pressed on a frame without one, the whole frame, ready to drag in
function boxOnScreen(t) { return cropOnScreen(t) || (cropping && t.src ? [0, 0, 1, 1] : null); }
function placeCrop(r) { const box = $('cropbox'); box.hidden = !r; showCropSize(r); if (r) Object.assign(box.style, {left: r[0] * 100 + '%', top: r[1] * 100 + '%', width: (r[2] - r[0]) * 100 + '%', height: (r[3] - r[1]) * 100 + '%'}); }
const iw = $('imgw');
iw.addEventListener('pointerdown', ev => {
  if (cur === null) return;
  if (picking) { setPicking(false); api('/api/settings', {focus_point: toScanner(at(ev), tile(cur).turns || 0)}); return; }
  const box = $('cropbox'), held = ev.target.dataset && ev.target.dataset.h, shown = boxOnScreen(tile(cur));
  const placeholder = !tile(cur).crop;   // the whole-frame box Crop shows before there is a crop: its middle draws a new one
  if (shown && (held || (ev.target === box && !placeholder))) {   // a side or corner of the crop, or its middle: change the crop that is there
    ev.preventDefault(); iw.setPointerCapture(ev.pointerId);
    drag = {edit: held || 'move', r: shown, a: at(ev), b: at(ev), shift: ev.shiftKey};
    document.body.classList.add('cropdrag'); return;
  }
  if (!cropping) return;
  ev.preventDefault(); iw.setPointerCapture(ev.pointerId); drag = {a: at(ev), b: at(ev)}; placeCrop(dragRect());
});
iw.addEventListener('pointermove', ev => { if (drag) { drag.b = at(ev); placeCrop(dragRect()); } });
iw.addEventListener('pointerup', async ev => {
  if (!drag) return;
  drag.b = at(ev); const r = dragRect(), edited = drag.edit, still = drag.a[0] === drag.b[0] && drag.a[1] === drag.b[1];
  drag = null; document.body.classList.remove('cropdrag');
  if (edited && still) { render(true); return; }   // a click on the crop: nothing changed
  if (r[2] - r[0] < MIN_CROP || r[3] - r[1] < MIN_CROP) { render(true); return; }   // a click, not a drag: the crop stays as it was
  await saveCrop(r);
  if (!edited) setCropping(false);
});
iw.addEventListener('pointercancel', () => { drag = null; document.body.classList.remove('cropdrag'); render(true); });

// ---------------------------------------------------------------- keys
document.addEventListener('keydown', ev => {
  if ($('zoom').open) { if (ev.key === ' ' || ev.key === 'Enter') { ev.preventDefault(); $('zoom').close(); } return; }
  if (document.querySelector('dialog[open]') || !S) return;
  const el = document.activeElement, tag = el ? el.tagName : '', typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag);
  const cmd = ev.metaKey || ev.ctrlKey;
  if (cmd && ev.key === 'Enter') { ev.preventDefault(); if (!$('b-scan').disabled) $('b-scan').click(); return; }
  if (typing) return;
  if (cmd && ev.key === ',') { ev.preventDefault(); openSettings(); return; }
  if (cmd && ev.key.toLowerCase() === 'a') { ev.preventDefault(); $('b-all').click(); return; }
  if (cmd || ev.altKey) return;
  const n = S.frames.length, order = stripOrder();   // the arrows move along the strip as it is shown
  const go = d => { if (!n) return; ev.preventDefault(); const k = order.indexOf(cur ?? order[0]); look(order[Math.min(n - 1, Math.max(0, k + d))]); };
  const press = id => { if (!$(id).disabled && !$(id).hidden) $(id).click(); };
  switch (ev.key) {
    case 'ArrowRight': go(1); break;
    case 'ArrowLeft': go(-1); break;
    case ' ': if (tag !== 'BUTTON' && tag !== 'SUMMARY') { ev.preventDefault(); openZoom(); } break;
    case 'x': case 'X': if (cur !== null) tick(cur, !sel.has(cur)); break;
    case 'Escape': if (logOpen) { setLog(false); break; } if (cropping || picking) { setCropping(false); setPicking(false); render(true); } else if (sel.size) { sel.clear(); render(true); } break;
    case '[': press('b-rl'); break;
    case ']': press('b-rr'); break;
    case 'c': case 'C': press('b-crop'); break;
    case 'p': case 'P': press('b-preview'); break;
    case 't': case 'T': press('b-thumb'); break;
    case 'a': case 'A': setLog(!logOpen); break;
    case 's': case 'S': openFrameSets(); break;
    case '?': $('keysdlg').showModal(); break;
  }
});

// ---------------------------------------------------------------- drawing
function fillSelect(id, vals, curVal, lab) {
  const e = $(id), want = vals.map(String).join();
  if (e.dataset.v !== want) { e.innerHTML = ''; for (const v of vals) { const o = document.createElement('option'); o.value = v; o.textContent = lab ? lab(v) : v; e.appendChild(o); } e.dataset.v = want; }
  e.value = String(curVal);
}
function optionText(id) { const e = $(id), o = e.options[e.selectedIndex]; return o ? o.textContent : ''; }

// The frames in the order they lie on the film as the thumbnail pass saw it, left to right (on the
// LS-40 that is the last frame first). Without a thumbnail pass: in frame order.
function stripOrder() {
  const at = {}; for (const b of S.boxes || []) at[b.i] = b.x + b.w / 2;
  return S.frames.slice().sort((a, b) => ((at[a] ?? a) - (at[b] ?? b)) || a - b);
}
function drawTiles() {
  const box = $('tiles'), n = S.frames.length, order = stripOrder();
  const shape = S.strip + '|' + order.join(',') + '|' + S.frames.map(i => tile(i).v + ':' + tile(i).src).join(',');
  if (shape !== lastShape) {          // other pictures: build the tiles again
    lastShape = shape;
    box.innerHTML = '';
    for (const i of order) {
      const t = tile(i), d = document.createElement('div');
      d.className = 'tile'; d.dataset.i = i; d.setAttribute('role', 'option'); d.title = 'Frame ' + (i + 1);
      const pic = document.createElement('div'); pic.className = 'pic';
      if (t.src) { const im = document.createElement('img'); im.src = '/frame/' + i + '.png?v=' + t.v; im.alt = 'Frame ' + (i + 1); im.draggable = false; pic.appendChild(im); }
      else { const s = document.createElement('span'); s.className = 'none'; s.textContent = 'No picture yet'; pic.appendChild(s); }
      const lab = document.createElement('label'); lab.className = 'tick'; lab.title = 'Tick to scan this frame';
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.tabIndex = -1; cb.setAttribute('aria-label', 'Scan frame ' + (i + 1));
      const num = document.createElement('span'); num.textContent = i + 1; lab.append(cb, num);
      // left of the picture: what this frame is scanned with, and whether that is its own or everyone's
      const side = document.createElement('div'); side.className = 'side';
      const own = document.createElement('span'); own.className = 'own';
      const ul = document.createElement('ul'); ul.className = 'fs';
      const ch = document.createElement('button'); ch.className = 'small fsbtn'; ch.tabIndex = -1; ch.textContent = 'Change for this frame…';
      ch.title = 'Give this frame settings of its own (another resolution, more grain reduction, ...)';
      side.append(lab, own, ul, ch);
      d.append(side, pic);
      // a frame scanned as a crop only keeps its thumbnail on screen, but it was scanned all the same
      const kind = t.src === 'thumbnail' && (t.saved || []).length ? 'scan' : t.src;
      if (kind === 'scan' || kind === 'preview') { const b = document.createElement('span'); b.className = 'badge ' + kind; b.textContent = kind === 'scan' ? 'Scanned' : 'Preview'; pic.appendChild(b); }
      const mb = document.createElement('span'); mb.className = 'moved'; mb.hidden = true; mb.title = 'Moved along the strip by hand'; pic.appendChild(mb);
      const sk = document.createElement('span'); sk.className = 'skipped'; pic.appendChild(sk);
      const nb = document.createElement('span'); nb.className = 'badge now'; nb.textContent = 'Scanning…'; nb.hidden = true; pic.appendChild(nb);
      box.appendChild(d);
    }
  }
  // the frame the scanner is on now (scans of a strip only: they are the ones that take long)
  const tm = S.timing || {}, now = S.job === 'scan' && tm.frame ? tm.frame - 1 : null;
  for (const d of box.children) {      // what is looked at, what is ticked and what is scanned now: only the marks change
    const i = +d.dataset.i;
    d.classList.toggle('cur', cur === i); d.classList.toggle('ticked', sel.has(i)); d.classList.toggle('now', now === i);
    d.tabIndex = cur === i || (cur === null && i === 0) ? 0 : -1;
    d.setAttribute('aria-label', 'Frame ' + (i + 1) + (now === i ? ', scanning now' : ''));
    d.querySelector('.badge.now').hidden = now !== i;
    const w = tile(i).window || {}, mv = d.querySelector('.moved');   // a frame moved by hand
    mv.hidden = !w.shift; if (w.shift) mv.textContent = (w.shift * (S.settings.rotate180 ? -1 : 1) > 0 ? '▶ ' : '◀ ') + Math.abs(Math.round(w.moved || 0)) + '%';
    for (const b of d.querySelectorAll('.badge:not(.now)')) b.hidden = now === i;
    d.setAttribute('aria-selected', String(sel.has(i))); d.querySelector('input').checked = sel.has(i);
    drawFrameSide(d, i);
    const t2 = tile(i), sk = d.querySelector('.skipped'), miss = t2.src === 'preview' && t2.preview_skipped ? t2.preview_skipped : {};
    const words = [('af' in miss) && 'AF off', ('ae' in miss) && 'AE off'].filter(Boolean);
    sk.hidden = !words.length || now === i; sk.textContent = words.join(' · ');
    sk.title = words.length ? 'This preview was made with ' + ['af' in miss && 'no autofocus', 'ae' in miss && 'the exposure of the last preview, not metered'].filter(Boolean).join(' and ') + ' (Settings ⚙ › Preview scan)' : '';
  }
  $('noframes').hidden = n > 0;
}

// moving a frame's window along the strip by hand: +1 = later along the strip = to the right on the
// thumbnail as the scanner sends it, so to the left on screen when every frame is turned 180°
async function nudge(screenDir, reset) {
  if (cur === null) return;
  const steps = screenDir * (S.settings.rotate180 ? -1 : 1);
  const r = await api('/api/nudge', reset ? {frame: cur, reset: true} : {frame: cur, steps});
  if (r && r.busy) $('posinfo').textContent = 'Wait until the scan finishes.';
}
$('b-nudgel').onclick = () => nudge(-1); $('b-nudger').onclick = () => nudge(1); $('b-nudge0').onclick = () => nudge(0, true);
function drawFramePos(t, busy) {
  const w = t.window, show = cur !== null && !!w && !!S.thumb;
  $('framepos').hidden = !show; if (!show) return;
  const m = Math.round(w.moved || 0), dir = (w.shift || 0) * (S.settings.rotate180 ? -1 : 1);
  $('posinfo').textContent = [w.fitted ? 'fitted to the gaps' : (S.settings.frame_fit ? 'no clear gap: where the scanner put it' : 'where the scanner put it'),
    w.shift ? 'moved ' + Math.abs(m) + '% ' + (dir > 0 ? 'right' : 'left') : ''].filter(Boolean).join(' · ');
  $('b-nudge0').hidden = !w.shift;
  for (const id of ['b-nudgel', 'b-nudger', 'b-nudge0']) $(id).disabled = busy;
}
function drawViewer(busy) {
  const t = cur !== null ? tile(cur) : {}, has = !!t.src;
  drawFramePos(t, busy);
  $('vtitle').textContent = cur !== null ? 'Frame ' + (cur + 1) : 'Preview';
  $('vown').hidden = cur === null || !Object.keys(frameOwn(cur)).length; $('b-framesets').disabled = cur === null;
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
  if (!drag) placeCrop(has ? boxOnScreen(t) : null);
  const fp = S.settings.focus_point, mark = $('fmark'); mark.hidden = !(has && fp);
  if (has && fp) { const q = toScreen(fp, t.turns || 0); mark.style.left = q[0] * 100 + '%'; mark.style.top = q[1] * 100 + '%'; }
  $('b-cropclear').hidden = !t.crop;
  $('croptools').hidden = !(has && (cropping || t.crop));
  for (const id of ['b-crop', 'b-zoom', 'b-autocrop']) $(id).disabled = !has;
  $('b-sharper').disabled = busy || cur === null; $('b-rl').disabled = $('b-rr').disabled = busy || !S.frames.length;
  const sv = $('vsaved'), saved = t.saved || [];
  sv.hidden = !saved.length && !cropping && !t.crop;
  sv.textContent = cropping ? 'Drag a side or corner of the box to choose the part to save, or its middle to move it. The crop is used when this frame is scanned.'
    : [saved.length ? 'Saved from this frame: ' + saved.map(s => s.files.join(', ') + ' (' + clock(s.took) + ')').join(' · ') : '',
       t.crop ? (S.settings.archive ? 'Cropped: only the marked part is saved in Positive (Archive mode scans the whole frame, and its RAW file keeps it).'
                 : S.settings.crop_scan ? 'Cropped: only the marked part is scanned and saved, which is quicker.'
                 : 'Cropped: only the marked part is saved (the whole frame is scanned).')
         + (t.crop_auto ? ' The crop was found automatically.' : '') + ' Drag its sides or corners to change it.' : ''].filter(Boolean).join(' ');
}


function drawSummaries(st) {
  const on = [];
  $('sum-presets').textContent = S.preset || (S.presets.length ? 'not saved' : '');
  $('sum-film').textContent = optionText('s-film') + (st.film === 'negative' && st.film_profile ? ' · ' + st.film_profile : '');
  $('sum-roll').textContent = (st.roll || '').split(' - ')[0];
  $('sum-scan').textContent = st.dpi + ' dpi' + (st.clean && st.film !== 'mono' ? ' · dust removal' : '') + (st.samples > 1 ? ' · ' + st.samples + 'x' : '') + (+st.multi_exposure ? ' · Multi-Exposure' : '') + (+st.grain ? ' · grain ' + st.grain : '');
  $('sum-save').textContent = (st.archive ? 'RAW + Positive' : optionText('s-negatives')) + ' · ' + (st.colour_space === 'scanner' ? 'Scanner RGB' : st.colour_space.split(' ')[0]);
  $('sum-names').textContent = S.sample_name;
  $('sum-focus').textContent = st.focus_mode === 'same' || st.exposure_mode === 'same' ? 'kept from Calibrate' : 'each frame on its own';
  $('sum-preview').textContent = st.preview_dpi + ' dpi' + (st.auto_thumbnail ? ' · automatic' : '');
  if (st.eject_after) on.push('eject');
  $('sum-done').textContent = on.join(' · ');
  const ways = [st.notify_mac ? 'banner' : '', st.notify_shortcut ? 'shortcut' : '', st.notify_ntfy ? 'ntfy' : ''].filter(Boolean);
  const logs = +st.log_days ? 'logs ' + st.log_days + ' days' : 'logs kept';
  $('archtoggle').classList.toggle('on', !!st.archive);
  $('archsub').textContent = st.archive ? 'RAW + Positive' : 'Off · one finished picture';
  $('sum-extra').textContent = (st.archive_default ? 'starts in Archive mode · ' : '') + (st.auto_crop_learn ? '' : 'crop learning off · ') + (!S.notify ? '' : (st.notify_on ? 'messages: ' + (ways.join(', ') || 'none picked') : 'messages off') + ' · ') + logs;
  const L = S.logs;
  $('loginfo').textContent = !L || !L.on ? 'Logs are written when the app runs with the scanner (not with --demo).'
    : L.files + ' file(s), ' + (L.bytes < 1048576 ? Math.ceil(L.bytes / 1024) + ' KB' : (L.bytes / 1048576).toFixed(1) + ' MB') + ' in ' + L.folder;
}

// the line at the top: what the next scan will use, like Nikon Scan's status line. While a scan runs it
// keeps what that scan started with, even if a setting is changed meanwhile (the change is for the next one)
let setFrozen = null;
// the words a select on the left shows for a value (not the value it shows now: a frame can have its own)
function textOf(k, v) {
  if (k === 'clean') return v ? 'on' : 'off';
  if (k === 'dpi') return v + ' dpi';
  const e = $('s-' + k); if (!e || !e.options) return String(v);
  for (const o of e.options) if (o.value === String(v)) return o.textContent;
  return String(v);
}
// dust removal in the same words everywhere: "on, built in" or "off"
function dustWords(clean, method, film) {
  if (film === 'mono') return 'off (B&W film)';
  return clean ? 'on, ' + textOf('dust_method', method).replace(/ \(.*\)$/, '').toLowerCase() : 'off';
}
function scanSettings(st) {
  const film = textOf('film', st.film), dust = st.clean && st.film !== 'mono' ? textOf('dust_method', st.dust_method) : '';
  const slide = st.film === 'positive' || st.film === 'kodachrome';
  return [
    film + (st.film === 'negative' && st.film_profile ? ' (' + st.film_profile + ')' : ''),
    st.dpi + ' dpi',
    'dust removal: ' + dustWords(st.clean, st.dust_method, st.film),
    st.samples > 1 ? st.samples + 'x multi-sample' : '',
    +st.multi_exposure ? st.multi_exposure + 'x Multi-Exposure' : '',
    +st.grain ? 'grain ' + st.grain : '', +st.restore ? 'colour restore ' + textOf('restore', st.restore).toLowerCase() : '',
    'focus: ' + textOf('focus_mode', st.focus_mode).toLowerCase(),
    'exposure: ' + (st.exposure_mode === 'same' ? 'kept' : 'auto'),
    st.white_balance !== 'film' ? 'white balance: ' + textOf('white_balance', st.white_balance).toLowerCase() : '',
    st.archive ? 'Archive (RAW + Positive)' : slide ? '' : 'negatives as ' + textOf('negatives', st.negatives).toLowerCase(),
    st.film === 'negative' && st.tone !== 'flat' ? 'tone: ' + textOf('tone', st.tone).toLowerCase() : '',
    st.colour_space === 'scanner' ? 'scanner RGB' : st.colour_space.split(' ')[0],
    st.auto_crop ? 'auto crop' : '',
  ].filter(Boolean);
}
function frameOwn(i) { return (S.frame_settings || {})[i] || {}; }
function drawSetLine(st) {
  const scanning = S.job === 'scan', fs = S.frame_settings || {};
  if (scanning && !setFrozen) setFrozen = JSON.parse(JSON.stringify(st));
  if (!scanning) setFrozen = null;
  const tm = S.timing || {}, now = scanning && tm.frame ? tm.frame - 1 : null;
  const base = setFrozen || st, shared = scanSettings(base);
  const items = (now !== null ? scanSettings({...base, ...frameOwn(now)}) : shared).map(t => [t, !shared.includes(t)]);
  const owners = Object.keys(fs).map(Number).sort((a, b) => a - b);
  if (!scanning && owners.length) items.push([(owners.length > 1 ? 'frames ' : 'frame ') + owners.map(i => i + 1).join(', ') + ': own settings', true]);
  $('setlbl').textContent = scanning ? 'This scan' + (now !== null ? ', frame ' + (now + 1) : '') : 'Next scan';
  const box = $('setchips'), want = JSON.stringify(items);
  if (box.dataset.v !== want) {
    box.dataset.v = want; box.textContent = '';
    for (const [t, own] of items) { const c = document.createElement('span'); c.className = 'setchip' + (own ? ' own' : ''); c.textContent = t; if (own) c.title = 'This frame\'s own setting'; box.appendChild(c); }
  }
}

// ---------------------------------------------------------------- settings of one frame
const FRAME_KEYS = ['dpi', 'samples', 'multi_exposure', 'clean', 'dust_method', 'grain', 'restore',
  'focus_mode', 'exposure_mode', 'white_balance', 'negatives', 'tone', 'colour_space'];
function rowLabel(k) { const e = $('s-' + k), l = e && e.closest('label'), sp = l && l.querySelector('span'); return sp ? sp.textContent : k; }
// what a card shows: a few settings everybody looks at, then whatever else the frame has of its own
function frameLines(i) {
  const st = S.settings, own = frameOwn(i), v = k => (k in own ? own[k] : st[k]);
  const clean = v('clean') && st.film !== 'mono';
  const lines = [
    [['dpi'], v('dpi') + ' dpi' + (v('samples') > 1 ? ', ' + v('samples') + 'x samples' : ''), ['samples']],
    [['clean', 'dust_method'], 'Dust removal: ' + dustWords(clean, v('dust_method'), st.film)],
    [['focus_mode'], 'Focus: ' + textOf('focus_mode', v('focus_mode')).toLowerCase()],
    [['exposure_mode'], 'Exposure: ' + (v('exposure_mode') === 'same' ? 'kept' : 'auto')],
  ].map(([ks, t, more]) => [t, [...ks, ...(more || [])].some(k => k in own)]);
  const shown = ['dpi', 'samples', 'clean', 'dust_method', 'focus_mode', 'exposure_mode'];
  for (const k of FRAME_KEYS) if (k in own && !shown.includes(k)) lines.push([rowLabel(k) + ': ' + textOf(k, own[k]).toLowerCase(), true]);
  return lines;
}
function drawFrameSide(d, i) {
  const own = Object.keys(frameOwn(i)).length > 0, lines = frameLines(i), want = JSON.stringify(lines);
  d.classList.toggle('hasown', own);
  const o = d.querySelector('.own'); o.textContent = own ? 'Own settings' : 'Same as all frames';
  o.title = own ? 'This frame has settings of its own; the highlighted ones differ from the settings on the left' : 'Scanned with the settings on the left';
  const ul = d.querySelector('ul.fs');
  if (ul.dataset.v !== want) {
    ul.dataset.v = want; ul.textContent = '';
    for (const [t, mine] of lines) { const li = document.createElement('li'); li.textContent = t; if (mine) li.className = 'mine'; ul.appendChild(li); }
  }
}
function frameSetsTargets() {
  const also = $('fs-also').checked && sel.has(cur);
  return also ? [...sel].sort((a, b) => a - b) : [cur];
}
function drawFrameSets() {
  if (cur === null || !$('framedlg').open) return;
  const own = frameOwn(cur), st = S.settings, rows = $('fsrows');
  $('framedlgtitle').textContent = 'Settings for frame ' + (cur + 1);
  const others = [...sel].filter(i => i !== cur).length;
  $('fs-alsorow').hidden = !(others && sel.has(cur));
  $('fs-alsotext').textContent = 'Also for the other ' + (others === 1 ? 'ticked frame' : others + ' ticked frames');
  if (!rows.children.length) {
    for (const k of FRAME_KEYS) {
      if (k === 'dust_method') continue;   // one row for dust removal: off, or on and how
      const l = document.createElement('label'); l.className = 'row';
      const sp = document.createElement('span'); sp.textContent = k === 'clean' ? 'Dust removal' : rowLabel(k);
      const s = document.createElement('select'); s.id = 'fs-' + k; s.dataset.k = k;
      s.onchange = () => {
        const val = s.value === '' ? null : k === 'clean' ? s.value === 'on' : s.value;
        const changes = k !== 'clean' ? {[k]: val} : s.value === '' ? {clean: null, dust_method: null}
          : s.value === 'off' ? {clean: false, dust_method: null} : {clean: true, dust_method: s.value};
        api('/api/framesettings', {frames: frameSetsTargets(), changes});
      };
      l.append(sp, s); rows.appendChild(l);
    }
  }
  for (const s of rows.querySelectorAll('select')) {
    const k = s.dataset.k, src = $('s-' + k);
    const methods = [...$('s-dust_method').options].filter(o => !o.disabled).map(o => [o.value, 'On, ' + o.textContent.toLowerCase()]);
    const vals = k === 'clean' ? [['off', 'Off'], ...methods] : [...src.options].map(o => [o.value, o.textContent]);
    const shared = k === 'clean' ? dustWords(st.clean, st.dust_method, st.film) : textOf(k, st[k]);
    const want = JSON.stringify([shared, vals]);
    if (s.dataset.v !== want) {
      s.dataset.v = want; s.textContent = '';
      const first = document.createElement('option'); first.value = ''; first.textContent = 'Same as all frames (' + shared + ')'; s.appendChild(first);
      for (const [v, t] of vals) { const o = document.createElement('option'); o.value = v; o.textContent = t; s.appendChild(o); }
    }
    const mine = k === 'clean' ? 'clean' in own || 'dust_method' in own : k in own;
    if (document.activeElement !== s) s.value = !mine ? '' : k !== 'clean' ? String(own[k])
      : (('clean' in own ? own.clean : st.clean) ? ('dust_method' in own ? own.dust_method : st.dust_method) : 'off');
    s.closest('label').classList.toggle('mine', mine);
  }
  $('fs-reset').disabled = !Object.keys(own).length;
}
function openFrameSets() { if (cur === null || !S.frames.length) return; $('fs-also').checked = false; $('framedlg').showModal(); drawFrameSets(); }
$('b-framesets').onclick = openFrameSets; $('vown').onclick = openFrameSets;
$('fs-done').onclick = () => $('framedlg').close();
$('fs-reset').onclick = () => api('/api/framesettings', {frames: frameSetsTargets(), reset: true});

function render(force) {
  if (!S) return;
  $('notifyopts').hidden = !S.notify; $('notifymissing').hidden = !!S.notify; $('notifyways').hidden = !S.settings.notify_on;
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
  $('negrow').hidden = !!st.archive; $('cropscanrow').hidden = !!st.archive; $('dustrow').hidden = !st.clean || st.film === 'mono';
  $('cleanrow').hidden = st.film === 'mono'; $('monohint').hidden = st.film !== 'mono'; $('kchint').hidden = st.film !== 'kodachrome';
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
  document.body.classList.toggle('noroll', !st.use_roll);
  $('savewhere').textContent = 'Saving in ' + (S.roll_path || st.outdir) + (st.archive ? ' (RAW/ and Positive/)' : '');
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
  $('b-af').closest('.act').hidden = !af; $('posrow').hidden = st.focus_mode !== 'manual';
  // Calibrate (or choosing it by hand) keeps one frame's focus or exposure for all: say so, and light up Reset
  const kept = [st.focus_mode === 'same' ? 'focus' : '', st.exposure_mode === 'same' ? 'exposure' : ''].filter(Boolean);
  $('b-unlock').classList.toggle('attn', kept.length > 0);
  $('overnote').hidden = !kept.length;
  $('overnote').textContent = kept.length ? 'Overridden: ' + kept.join(' and ') + ' kept from one frame for every frame. Reset goes back to each frame on its own.' : '';
  if (c && c.focus_range) { $('s-focus_position').min = c.focus_range[0]; $('s-focus_position').max = c.focus_range[1]; }
  $('fpoint').textContent = picking ? 'click the big picture…' : (st.focus_point ? st.focus_point.map(v => v.toFixed(2)).join(', ') : 'centre');
  $('b-fpoint').textContent = st.focus_point ? 'Clear' : (picking ? 'Cancel' : 'Pick');
  $('calinfo').textContent = [fl ? 'Focus: ' + fl.result + ' at lens position ' + fl.position + (fl.frame != null ? ' (frame ' + (fl.frame + 1) + ')' : '') + '.'
      : (st.focus_mode === 'frame' ? 'Focus: each frame is focused on its first preview or scan.' : 'Focus: not set yet.'),
    el ? 'Exposure: ' + Object.entries(el.exposures).map(([k, v]) => k[0].toUpperCase() + ' ' + v).join(', ') + (el.infrared ? ' (with IR)' : '') + (el.frame != null ? ' from frame ' + (el.frame + 1) : '') + '.'
      : 'Exposure: not set yet.'].join(' ');

  drawTiles(); drawViewer(busy); drawSummaries(st); drawSetLine(st); drawFrameSets();
  if (force || S.version !== lastV) lastV = S.version;
  drawLog();

  // what is happening now, and how long it takes
  const p = S.progress, tm = S.timing || {}, prog = $('prog');
  $('busy').hidden = !busy;
  if (busy) {
    if (p) { prog.max = p.total || 1; prog.value = p.done; } else prog.removeAttribute('value');
    const pct = p ? (p.total ? Math.round(100 * p.done / p.total) : 0) + '%' : '', job = S.job;
    const phase = p && p.phase && !['scan', 'preview', 'thumbnail'].includes(p.phase) && p.phase !== job ? p.phase + ' ' : '';
    $('progtxt').textContent = job === 'scan' && tm.frame
      ? 'Scanning frame ' + tm.frame + ' (' + Math.min(tm.done + 1, tm.of) + ' of ' + tm.of + ')' + (p ? ' · ' + phase + pct : '')
      : (JOBS[job] || (job[0].toUpperCase() + job.slice(1))) + (p ? ' · ' + phase + pct : '…');
    let tt = '';
    if (tm.strip_started) {
      const left = tm.average ? tm.average * (tm.of - tm.done) - (tm.frame_started ? Math.min(tm.average, tm.now - tm.frame_started) : 0) : null;
      tt = clock(tm.now - (tm.frame_started || tm.now)) + ' on this frame · '
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
setLog(logOpen);
poll();

// ---------------------------------------------------------------- logs and cache (Additional settings)
$('b-openlogs').onclick = () => api('/api/open_logs');
$('b-clearcache').onclick = async () => {
  const r = await api('/api/clear_cache'), m = $('cachemsg');
  m.hidden = false;
  m.textContent = r.ok ? (r.cleared && r.cleared.length ? 'Cleared: ' + r.cleared.join(', ') + '.' : 'There was nothing to clear.')
    : (r.error || 'That did not work.');
};

// ---------------------------------------------------------------- auto crop learning (Additional settings)
const SHORT = {simple: 'simple', net: 'net', cnn: 'CNN'};
const KIND = {simple: 'Simple model', net: 'Small neural network', cnn: 'Picture network (CNN)', rule: 'Built-in rule'};
function pc(v) { return v === null || v === undefined ? '–' : (v * 100).toFixed(1) + '%'; }
function esc(t) { return String(t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c])); }
let learnTimer = null;
async function croplearnShow() {
  let r;
  try { r = await (await fetch('/api/croplearn')).json(); } catch (e) { return; }
  $('croplearnmsg').textContent = (r.on ? '' : 'Learning is off. ') + (r.text || '');
  $('b-croplearn').disabled = !!r.busy;
  $('b-croplearn').textContent = r.busy ? 'Learning…' : 'Learn from my crops';
  clearTimeout(learnTimer);
  if (r.busy) learnTimer = setTimeout(croplearnShow, 2000);
  if ($('croplearnreport').hidden) return;
  const m = r.model, rows = [];
  if (m) {
    const e = m.errors || {}, w = m.within_1pc || {};
    rows.push(['Learned from', m.pictures + ' crops (' + m.sides + ' sides), ' + esc(m.made.slice(0, 16)) + ', ' + esc(m.why || '')]);
    rows.push(['In use', r.use ? KIND[r.running_kind] || r.running_kind : 'Built-in rule (nothing learned does better yet)']);
    let t = '<table class="clr"><tr><th></th><th>Off by</th><th>Within 1%</th></tr>';
    for (const k of ['simple', 'net', 'cnn']) if (e[k] !== undefined && e[k] !== null) t += '<tr><td>' + KIND[k] + (k === m.kind ? ' ✓' : '') + '</td><td>' + pc(e[k]) + '</td><td>' + pc(w[k]) + '</td></tr>';
    t += '<tr><td>' + KIND.rule + '</td><td>' + pc(m.error_rule) + '</td><td>' + pc(w.rule) + '</td></tr></table>';
    rows.push(['How close to your crops', t + '<p class="hint">Edges tried on crops each model was not trained on (5 rounds), as a share of the frame.' + (m.cnn_tried ? '' : ' The picture network is tried from 12 crops' + (r.cnn_available ? '' : ' and needs MLX') + '.') + '</p>']);
    const u = m.used || {};
    rows.push(['Last learning took', u.seconds + ' s (processor time ' + u.cpu_seconds + ' s) on the ' + esc(u.device || '') +
      '<br>App memory at most ' + u.memory_mb + ' MB' + (u.memory_added_mb ? ' (+' + u.memory_added_mb + ' MB while learning)' : '') +
      (u.gpu_memory_mb !== null && u.gpu_memory_mb !== undefined ? '<br>GPU memory at most ' + u.gpu_memory_mb + ' MB' : '')]);
  }
  rows.push(['Cropping with it', r.crops_made ? r.crops_made + ' frames since the app started, ' + r.ms_per_crop + ' ms each' : 'not yet since the app started']);
  rows.push(['Kept', r.hand_crops + ' crops from the scanning screen (plus the Library\'s), ' + r.new + ' new since it last learned; ' + r.store_mb + ' MB in <code>' + esc(r.store) + '</code>']);
  const h = r.history || [];
  if (h.length > 1) {
    let t = '<table class="clr"><tr><th>When</th><th>Crops</th><th>Used</th><th>Off by</th><th>Rule</th></tr>';
    for (const x of h.slice(-10).reverse()) {
      const best = (x.errors || {})[x.kind];
      t += '<tr><td>' + esc((x.made || '').slice(5, 16)) + '</td><td>' + x.pictures + '</td><td>' + (x.use ? (SHORT[x.kind] || x.kind) : 'rule') + '</td><td>' + pc(best) + '</td><td>' + pc(x.error_rule) + '</td></tr>';
    }
    rows.push(['Earlier runs', t + '</table>']);
  }
  $('croplearnreport').innerHTML = rows.map(([k, v]) => '<div class="clrow"><b>' + k + '</b><div>' + v + '</div></div>').join('');
}
$('b-croplearn').onclick = async () => {
  $('b-croplearn').disabled = true;
  const r = await api('/api/croplearn', {action: 'learn'});
  if (!r.ok) $('croplearnmsg').textContent = r.error || 'That did not work.';
  setTimeout(croplearnShow, 300);
};
$('b-croplearnreport').onclick = () => {
  const box = $('croplearnreport'); box.hidden = !box.hidden;
  $('b-croplearnreport').setAttribute('aria-expanded', String(!box.hidden));
  croplearnShow();
};
// the Settings window (⚙ in the top bar, or ⌘,): what used to be the last two cards on the left
function openSettings() { if (!$('setdlg').open) { $('setdlg').showModal(); croplearnShow(); } }
$('b-settings').onclick = openSettings;
$('b-setdone').onclick = () => $('setdlg').close();
