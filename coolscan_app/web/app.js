'use strict';
// The page asks the app for its state twice a second and draws it. Two ideas run through it:
//  - the frame you are LOOKING at (cur) is shown big; clicking a frame only looks at it
//  - the frames you TICKED (sel) are what Scan takes; with none ticked, Scan takes the whole strip.
//    A tick only selects: Scan (or, while a strip is scanned, "Add to scan" / "Take out of scan") acts, and unticks them
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

// what a film format is called (the scanner's names are 645, 67, ...)
const FORMAT_NAMES = {'135': '35 mm', half: 'Half frame', IX240: 'APS (IX240)', '16': '16 mm', '645': '6×4.5', '66': '6×6', '67': '6×7', '68': '6×8', '69': '6×9'};
// ---------------------------------------------------------------- settings: every control saves when changed
const keys = ['film', 'format', 'preview_dpi', 'invert_preview', 'rotate180', 'focus_mode', 'focus_position', 'exposure_mode',
  'white_balance', 'dpi', 'samples', 'clean', 'negatives', 'archive', 'outdir', 'basename', 'digits', 'start', 'photo_date',
  'colour_space', 'name_date', 'tone', 'stop_on_errors', 'eject_after', 'scan_log', 'auto_thumbnail', 'dust_method', 'grain',
  'restore', 'notify_mac', 'notify_shortcut', 'notify_ntfy', 'multi_exposure', 'crop_scan', 'auto_crop', 'use_roll', 'overwrite', 'frame_auto', 'show_frame_offset', 'auto_crop_trim', 'auto_crop_sides', 'notify_on', 'log_days', 'archive_default', 'auto_crop_learn', 'crop_learn_delete_after', 'auto_turn', 'auto_turn_learn', 'preview_af', 'preview_ae', 'show_rotate180', 'auto_preview', 'superfine'];
keys.forEach(k => { const e = $('s-' + k); e.addEventListener('change', () => api('/api/settings', {[k]: e.type === 'checkbox' ? e.checked : e.value})); });

// ---------------------------------------------------------------- film profiles: the colour of a film, measured from a roll
function profileSay(text) { const m = $('profilemsg'); m.hidden = !text; m.textContent = text || ''; }
$('filmprofile').onchange = async () => {
  const r = await api('/api/film_profile', {action: 'use', name: $('filmprofile').value});
  profileSay(r.ok ? '' : (r.busy ? 'Wait until the scan finishes, then pick it again.' : (r.error || 'That did not work.')));
  if (!r.ok) { const rc = S.roll_colour || {}; $('filmprofile').value = rc.mode === 'roll' ? '@roll' : rc.mode === 'base' ? '@base' : rc.mode === 'profile' ? rc.name : ''; }
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
// Scan. Two things are asked first when they apply: frames scanned already with nothing changed since
// (scan them again?), and, with "Allow overwriting" and a typed number, the pictures it would replace.
// Once the scan has started the ticks are taken off: they have done their work.
let overFrames = [], overFlags = {}, againAsk = null;
function unTick() { sel.clear(); anchor = null; render(true); }
function frameNames(list) {
  const n = list.map(i => i + 1);
  return n.length === 1 ? 'Frame ' + n[0] : 'Frames ' + n.slice(0, -1).join(', ') + ' and ' + n[n.length - 1];
}
function askAgain(same, frames, go) {   // go(frames) carries on with the scan, the question answered
  const all = frames.length ? frames : S.frames, others = all.filter(i => !same.includes(i));
  againAsk = {go, all, others};
  $('againmsg').textContent = frameNames(same) + (same.length === 1 ? ' was' : ' were') + ' already scanned with exactly these settings. Scan '
    + (same.length === 1 ? 'it' : 'them') + ' again?';
  $('b-againothers').hidden = !others.length;
  $('b-againothers').textContent = others.length === 1 ? 'Scan only frame ' + (others[0] + 1) : 'Scan only the other ' + others.length;
  $('againdlg').showModal(); $('b-againcancel').focus();
}
$('b-againcancel').onclick = () => $('againdlg').close();
$('b-againok').onclick = () => { $('againdlg').close(); againAsk.go(againAsk.all); };
$('b-againothers').onclick = () => { $('againdlg').close(); againAsk.go(againAsk.others); };
async function startScan(frames, flags) {
  const r = await api('/api/scan', {frames, ...flags});
  if (r.confirm_again) { askAgain(r.confirm_again, frames, f => startScan(f, {...flags, again_ok: true})); return; }
  if (!r.confirm_overwrite) { if (r.ok) unTick(); return; }
  overFrames = frames; overFlags = flags || {};
  const n = r.confirm_overwrite.length, ul = $('overwritelist');
  $('overwritemsg').textContent = plural(n, 'picture') + ' in ' + (r.folder || 'the folder') + ' will be replaced by this scan:';
  ul.innerHTML = '';
  r.confirm_overwrite.slice(0, 12).forEach(s => { const li = document.createElement('li'); li.textContent = s; ul.appendChild(li); });
  if (n > 12) { const li = document.createElement('li'); li.textContent = '…and ' + (n - 12) + ' more'; ul.appendChild(li); }
  $('overwritedlg').showModal(); $('b-overcancel').focus();
}
$('b-scan').onclick = () => startScan(ticked(), {});
$('b-overcancel').onclick = () => $('overwritedlg').close();
$('b-overok').onclick = () => { $('overwritedlg').close(); startScan(overFrames, {...overFlags, overwrite_ok: true}); };
$('b-overskip').onclick = async () => {
  $('overwritedlg').close();
  await api('/api/settings', {overwrite: false});  // back to skipping names in use; the typed number stays the start
  startScan(overFrames, overFlags);
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
$('b-archinfo').onclick = () => $('archdlg').showModal();
$('b-archok').onclick = () => $('archdlg').close();
// the cards on the left in the order of the work: the roll, the film, how to scan, how to save
for (const id of ['g-roll', 'g-film', 'g-scan', 'g-focus', 'g-presets']) { const c = $(id); if (c) $('side').appendChild(c); }
// "Photo taken" belongs to the roll: it sits in the Roll card, above the description
{ const row = $('s-photo_date').closest('.row'), before = document.querySelector('#g-roll label[for=rolldesc]'); if (row && before) before.before(row); }
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
  if (inTiles) { const t = $('tiles').querySelector('.tile[data-i="' + i + '"]'); if (t) t.focus(longStrip() ? {preventScroll: true} : undefined); }
  if (longStrip()) showTile(i);
}
// A long strip (an SA-30 roll, a holder with several strips: more than 6 frames, so never the LS-40's
// SA-21) is one row of bigger frames that scrolls sideways, with ◀ ▶ under it.
// wide window: the frames are a column beside the big picture (style.css, 1080 px and up)
const COLUMN = matchMedia('(min-width:1080px)');
function inColumn() { return document.body.classList.contains('main') && COLUMN.matches; }
COLUMN.addEventListener('change', () => { if (S) render(true); });
function longStrip() { return !!S && S.frames.length > 6; }
function showTile(i) { const t = $('tiles').querySelector('.tile[data-i="' + i + '"]'); if (t) t.scrollIntoView({block: 'nearest', inline: 'nearest'}); }
function longPos() {
  const box = $('tiles'), tiles = [...box.children]; if (!tiles.length) return;
  const q = box.getBoundingClientRect(), l = box.scrollLeft, r = l + box.clientWidth;
  const seen = tiles.filter(t => { const m = t.getBoundingClientRect(); return m.left >= q.left - 4 && m.right <= q.right + 4; }).map(t => tiles.indexOf(t) + 1);
  $('longpos').textContent = seen.length ? 'Showing ' + seen[0] + (seen.length > 1 ? '–' + seen[seen.length - 1] : '') + ' of ' + tiles.length + ' along the strip' : '';
  $('b-longl').disabled = l <= 0; $('b-longr').disabled = r >= box.scrollWidth - 1;
}
$('tiles').addEventListener('scroll', () => { if (longStrip()) longPos(); }, {passive: true});
$('b-longl').onclick = () => $('tiles').scrollBy({left: -$('tiles').clientWidth * 0.9, behavior: 'smooth'});
$('b-longr').onclick = () => $('tiles').scrollBy({left: $('tiles').clientWidth * 0.9, behavior: 'smooth'});
$('tiles').addEventListener('focusin', ev => { const t = ev.target.closest('.tile'); if (t && +t.dataset.i !== cur) look(+t.dataset.i); });
// A tick only selects. While a strip is scanned, the two buttons above the frames put the ticked frames
// into the running scan (at the end; one scanned already is scanned again) or take the waiting ones out.
function inQueue(i) { return (S.queue || []).some(q => q.i === i && (q.state === 'waiting' || q.state === 'scanning')); }
function isWaiting(i) { return (S.queue || []).some(q => q.i === i && q.state === 'waiting'); }
function tick(i, on) { on ? sel.add(i) : sel.delete(i); anchor = i; render(true); }
async function queueAdd(frames, flags) {
  const r = await api('/api/queue', {action: 'add', frames, ...flags});
  if (r.confirm_again) { askAgain(r.confirm_again, frames, f => queueAdd(f, {again_ok: true})); return; }
  if (r.error) say(r.error); else unTick();
}
$('b-qadd').onclick = () => queueAdd(ticked().filter(i => !inQueue(i)), {});
$('b-qout').onclick = async () => {
  const r = await api('/api/queue', {action: 'remove', frames: ticked().filter(isWaiting)});
  if (r.error) say(r.error); else unTick();
};
function say(text) { const l = $('lastline'); if (l) { l.textContent = text; l.classList.add('bad'); setTimeout(() => l.classList.remove('bad'), 4000); } }
// The queue while a strip is scanned shows on the frame cards themselves (Scanning…, Next, 2nd in line, Done):
// there is no separate row of it.
function drawQueue() { $('queue').hidden = true; }
const ORD = n => n + (n % 10 === 1 && n % 100 !== 11 ? 'st' : n % 10 === 2 && n % 100 !== 12 ? 'nd' : n % 10 === 3 && n % 100 !== 13 ? 'rd' : 'th');
function queuePlace(i) {   // {text, cls, sooner} for frame i while a strip is scanned, or null
  if (!S || S.job !== 'scan' || !(S.queue || []).length) return null;
  const q = S.queue, waiting = q.filter(it => it.state === 'waiting'), k = waiting.findIndex(it => it.i === i);
  if (q.some(it => it.i === i && it.state === 'scanning')) return {text: 'Scanning now', cls: 'scanning'};
  if (k === 0) return {text: 'Next', cls: 'waiting', sooner: false};
  if (k > 0) return {text: ORD(k + 1) + ' in line', cls: 'waiting', sooner: true};
  const last = [...q].reverse().find(it => it.i === i);
  if (last && last.state === 'failed') return {text: 'Failed', cls: 'failed'};
  if (last && last.state === 'done') return {text: 'Done', cls: 'done'};
  return null;
}
function drawQueuePlace(d, i) {
  let box = d.querySelector('.qplace');
  if (!box) {
    box = document.createElement('span'); box.className = 'qplace';
    const t = document.createElement('span'); t.className = 'qtext';
    const up = document.createElement('button'); up.className = 'qsoon'; up.type = 'button'; up.tabIndex = -1; up.textContent = '↑ Sooner';
    up.onclick = ev => { ev.stopPropagation(); api('/api/queue', {action: 'earlier', frame: +d.dataset.i}); };
    box.append(t, up); d.querySelector('.side').appendChild(box);
  }
  const p = queuePlace(i);
  box.hidden = !p || p.cls === 'scanning' || p.cls === 'done'; if (box.hidden) return;   // those two have their badge on the picture
  box.className = 'qplace ' + p.cls; box.querySelector('.qtext').textContent = p.text;
  const up = box.querySelector('.qsoon'); up.hidden = !p.sooner; up.title = 'Scan frame ' + (i + 1) + ' sooner';
}
$('tiles').addEventListener('click', ev => {
  const t = ev.target.closest('.tile'); if (!t || ev.target.closest('.tick')) return;   // the tick box has its own handler
  if (ev.target.closest('.fsbtn')) { look(+t.dataset.i); openFrameSets(); return; }
  const v = ev.target.closest('.atbtn');
  if (v) { api('/api/turnlearn', {action: v.dataset.a, frames: [+t.dataset.i]}); return; }
  const i = +t.dataset.i;
  if (ev.shiftKey && anchor !== null) { for (let k = Math.min(anchor, i); k <= Math.max(anchor, i); k++) sel.add(k); cur = i; render(true); }
  else if (ev.metaKey || ev.ctrlKey) { cur = i; tick(i, !sel.has(i)); }
  else look(i);
});
$('tiles').addEventListener('change', ev => { const t = ev.target.closest('.tile'); if (t) tick(+t.dataset.i, ev.target.checked); });
$('tiles').addEventListener('dblclick', ev => { const t = ev.target.closest('.tile'); if (t && !ev.target.closest('.tick') && !ev.target.closest('.fsbtn') && !ev.target.closest('.autoturn')) { cur = +t.dataset.i; render(true); openZoom(); } });
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

// Crop works as on the Scanned pictures page: Crop opens the box (where auto crop puts it when the frame has no
// crop yet), the box is changed on screen only, Done keeps it, Cancel (or Esc) leaves the crop as it was.
let pend = null;   // the box while Crop is open, as fractions of the picture on screen
function setCropping(on) {
  cropping = on; drag = null; if (!on) pend = null; if (on) setPicking(false);
  document.body.classList.toggle('cropping', on); $('b-crop').setAttribute('aria-pressed', String(on));
  $('b-crop').textContent = on ? 'Cancel' : 'Crop'; $('b-cropdone').hidden = !on;
}
const WHOLE = [0, 0, 1, 1];
function isWhole(r) { return !r || (r[0] <= 0 && r[1] <= 0 && r[2] >= 1 && r[3] >= 1); }
async function startCrop() {
  if (cur === null || !tile(cur).src) return;
  const i = cur, t = tile(i);
  let r = cropOnScreen(t);
  if (!r) {   // no crop yet: the box starts where auto crop puts it
    try { const a = await api('/api/autocrop', {frame: i, save: false}); if (a && a.rect) r = cropOnScreen({crop: a.rect, turns: t.turns}); } catch (e) { /* the whole frame then */ }
    if (cur !== i) return;
  }
  setCropping(true); pend = r || WHOLE.slice(); render(true);
}
$('b-crop').onclick = () => { if (cropping) { setCropping(false); render(true); } else startCrop(); };
$('b-cropdone').onclick = async () => {
  if (!cropping || cur === null) return;
  const r = pend, had = !!tile(cur).crop;
  if (isWhole(r)) { if (had) await api('/api/crop', {frame: cur, rect: null}); }
  else await saveCrop(r);
  setCropping(false); render(true);
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
function cropNow() { const t = tile(cur); return cur === null || !t.src ? null : boxOnScreen(t); }
// a new box: while Crop is open it is only shown (Done keeps it)
function setBox(r) { if (cropping) { pend = r; render(true); } else saveCrop(r); }
$('cropratio').onchange = () => {
  cropShape = $('cropratio').value; try { localStorage.setItem('coolscan.cropshape', cropShape); } catch (e) { /* fine */ }
  const r = cropNow(), px = r && keptShape(pxShape(r) >= 1);
  if (r && px) setBox(shapedAround(r, px)); else render(true);
};
$('b-cropswap').onclick = () => {
  const r = cropNow(); if (!r) return;
  const A = picAspect(), cx = (r[0] + r[2]) / 2, cy = (r[1] + r[3]) / 2;
  let fw = (r[3] - r[1]) / A, fh = (r[2] - r[0]) * A;   // the same pixel size, turned
  const s = Math.min(1, 2 * Math.min(cx, 1 - cx) / fw, 2 * Math.min(cy, 1 - cy) / fh); fw *= s; fh *= s;
  setBox([cx - fw / 2, cy - fh / 2, cx + fw / 2, cy + fh / 2]);
};
// what the crop comes to in pixels. The size of the whole frame is worked out from the picture's shape
// and a 35 mm frame as the LS-40 scans it (4320 px across at 2900 dpi), so it is an estimate.
// 120 film and the small formats send their own frame length (caps.frame_long_in)
function showCropSize(r) {
  const t = tile(cur), el = $('cropsize');
  if (!r || !S) { el.textContent = ''; return; }
  const dpi = +S.settings.dpi || 2900, A = picAspect(), long = (S.caps && S.caps.frame_long_in ? S.caps.frame_long_in : 4320 / 2900) * dpi;
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
function boxOnScreen(t) { return cropping && t.src ? (pend || WHOLE.slice()) : cropOnScreen(t); }
function placeCrop(r) { const box = $('cropbox'); box.hidden = !r; showCropSize(r); if (r) Object.assign(box.style, {left: r[0] * 100 + '%', top: r[1] * 100 + '%', width: (r[2] - r[0]) * 100 + '%', height: (r[3] - r[1]) * 100 + '%'}); }
const iw = $('imgw');
iw.addEventListener('pointerdown', ev => {
  if (cur === null) return;
  if (picking) { setPicking(false); api('/api/settings', {focus_point: toScanner(at(ev), tile(cur).turns || 0)}); return; }
  const box = $('cropbox'), held = ev.target.dataset && ev.target.dataset.h, shown = boxOnScreen(tile(cur));
  if (!cropping) return;   // the box is changed only while Crop is open
  const placeholder = isWhole(shown);   // the whole-frame box: its middle draws a new one
  if (shown && (held || (ev.target === box && !placeholder))) {   // a side or corner of the crop, or its middle: change the crop that is there
    ev.preventDefault(); iw.setPointerCapture(ev.pointerId);
    drag = {edit: held || 'move', r: shown, a: at(ev), b: at(ev), shift: ev.shiftKey};
    document.body.classList.add('cropdrag'); return;
  }
  ev.preventDefault(); iw.setPointerCapture(ev.pointerId); drag = {a: at(ev), b: at(ev)}; placeCrop(dragRect());
});
iw.addEventListener('pointermove', ev => { if (drag) { drag.b = at(ev); placeCrop(dragRect()); } });
iw.addEventListener('pointerup', async ev => {
  if (!drag) return;
  drag.b = at(ev); const r = dragRect(), edited = drag.edit, still = drag.a[0] === drag.b[0] && drag.a[1] === drag.b[1];
  drag = null; document.body.classList.remove('cropdrag');
  if (edited && still) { render(true); return; }   // a click on the crop: nothing changed
  if (r[2] - r[0] < MIN_CROP || r[3] - r[1] < MIN_CROP) { render(true); return; }   // a click, not a drag: the crop stays as it was
  pend = r; render(true);   // kept with Done
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
    case 'Enter': if (cropping && tag !== 'BUTTON') { ev.preventDefault(); press('b-cropdone'); } break;
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

// The order the frame cards are shown in: frame 1 first. (They used to follow the strip as the
// thumbnail pass saw it, which on the LS-40 puts the last frame first; D found that backwards.)
function stripOrder() {
  return S.frames.slice().sort((a, b) => a - b);
}
// the strip's frame direction (every frame turned 180° or not), found by auto rotate before each frame's own turn
function drawStripTurn() {
  const st = S.strip_turn, bar = $('stripturn');
  $('row-rotate180').hidden = !S.settings.show_rotate180;
  bar.hidden = !st || !S.frames.length;
  if (bar.hidden) return;
  $('stripturntext').textContent = '↻ Frame direction found: ' + (st.turned ? 'every frame turned 180°'
    : 'frames not turned 180°') + ' (' + st.agree + ' of ' + st.frames + ' frames agree). Is this right?';
  $('b-stripok').disabled = $('b-stripno').disabled = !!S.job;
}
// a strip that looks like another film than the roll it is being scanned into
function drawFilmWarn() {
  const w = S.film_warning, bar = $('filmwarn');
  bar.hidden = !w;
  if (!w) return;
  $('filmwarntext').textContent = '⚠ This strip looks like a different film than the rest of ' + (w.roll || 'this folder')
    + ': its film base is another colour (' + w.frames + (w.frames === 1 ? ' frame' : ' frames') + ' against ' + w.compared + ' earlier ones). '
    + (w.had_colour ? 'Its frames are coloured each on their own for now, not with the roll\'s colour. ' : '')
    + 'Is it another film?';
}
$('b-filmnew').onclick = () => $('b-newroll').click();
$('b-filmsame').onclick = () => api('/api/film_warning', {action: 'same'});
$('b-filmown').onclick = () => api('/api/film_warning', {action: 'own'});
$('b-stripok').onclick = () => api('/api/turnlearn', {action: 'strip_accept'});
$('b-stripno').onclick = () => api('/api/turnlearn', {action: 'strip_reject'});

let lastNow = null;
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
      const lab = document.createElement('label'); lab.className = 'tick'; lab.title = 'Tick to select this frame';
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.tabIndex = -1; cb.setAttribute('aria-label', 'Select frame ' + (i + 1));
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
      // a turn auto rotate made by itself: marked until the person keeps it or turns it back
      const at = document.createElement('div'); at.className = 'autoturn'; at.hidden = true;
      const atw = document.createElement('span'); atw.className = 'atwords'; atw.textContent = '↻ Auto rotated';
      const ok = document.createElement('button'); ok.className = 'small atbtn ok'; ok.dataset.a = 'accept'; ok.tabIndex = -1;
      ok.textContent = '✓ Accept'; ok.title = 'Keep this turn (auto rotate learns it guessed right)';
      const no = document.createElement('button'); no.className = 'small atbtn no'; no.dataset.a = 'reject'; no.tabIndex = -1;
      no.textContent = '✕ Reject'; no.title = 'Turn it back (auto rotate learns it guessed wrong)';
      at.append(atw, ok, no); pic.appendChild(at);
      const nb = document.createElement('span'); nb.className = 'badge now'; nb.textContent = 'Scanning…'; nb.hidden = true; pic.appendChild(nb);
      box.appendChild(d);
    }
  }
  // the frame the scanner is on now (scans of a strip only: they are the ones that take long)
  const tm = S.timing || {}, now = S.job === 'scan' && tm.frame ? tm.frame - 1 : null;
  const long = n > 6; box.classList.toggle('long', long);
  $('longnav').hidden = !long || inColumn();   // in the column beside the big picture a long strip simply scrolls down
  if (long) { if (now !== null && now !== lastNow) showTile(now); requestAnimationFrame(longPos); }
  lastNow = now;
  for (const d of box.children) {      // what is looked at, what is ticked and what is scanned now: only the marks change
    const i = +d.dataset.i;
    const on = sel.has(i);
    d.classList.toggle('cur', cur === i); d.classList.toggle('ticked', on); d.classList.toggle('now', now === i);
    d.tabIndex = cur === i || (cur === null && i === 0) ? 0 : -1;
    d.setAttribute('aria-label', 'Frame ' + (i + 1) + (now === i ? ', scanning now' : ''));
    d.querySelector('.badge.now').hidden = now !== i;
    const w = tile(i).window || {}, mv = d.querySelector('.moved');   // a frame moved by hand
    mv.hidden = !w.shift; if (w.shift) mv.textContent = (w.shift * (S.settings.rotate180 ? -1 : 1) > 0 ? '▶ ' : '◀ ') + Math.abs(Math.round(w.moved || 0)) + '%';
    for (const b of d.querySelectorAll('.badge:not(.now)')) b.hidden = now === i;
    d.setAttribute('aria-selected', String(on)); d.querySelector('input').checked = on;
    drawFrameSide(d, i); drawQueuePlace(d, i);
    const t2 = tile(i), sk = d.querySelector('.skipped'), miss = t2.src === 'preview' && t2.preview_skipped ? t2.preview_skipped : {};
    const words = [('af' in miss) && 'AF off', ('ae' in miss) && 'AE off'].filter(Boolean);
    sk.hidden = !words.length || now === i; sk.textContent = words.join(' · ');
    const au = !!t2.turn_auto && !!t2.src, at = d.querySelector('.autoturn');
    d.classList.toggle('auto', au); at.hidden = !au || now === i;
    if (au) at.title = 'Auto rotate turned this frame by itself' + (t2.turn_hint && t2.turn_hint.p ? ' (' + Math.round(t2.turn_hint.p * 100) + '% sure)' : '');
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
// every frame on the strip together (the frames were all put a bit too far one way); Shift = 5 presses
async function nudgeStrip(screenDir, reset, ev) {
  const steps = screenDir * (S.settings.rotate180 ? -1 : 1) * (ev && ev.shiftKey ? 5 : 1);
  const r = await api('/api/nudge', reset ? {strip: true, reset: true} : {strip: true, steps});
  if (r && r.busy) $('posinfo').textContent = 'Wait until the scan finishes.';
}
$('b-stripl').onclick = (e) => nudgeStrip(-1, false, e); $('b-stripr').onclick = (e) => nudgeStrip(1, false, e);
$('b-strip0').onclick = () => nudgeStrip(0, true);
function drawStripOffset(busy) {   // by the frames: every frame on the strip together, like Nikon Scan's Strip Film Offset
  const w0 = tile(0).window, on = !!S.settings.show_frame_offset && !!w0 && !!S.thumb;
  $('stripoff').hidden = !on; if (!on) return;
  const v = Math.round(w0.strip_moved || 0) * (S.settings.rotate180 ? -1 : 1);
  $('stripoffval').textContent = (v > 0 ? '+' : '') + v + '%';
  $('b-strip0').hidden = !w0.strip_shift;
  for (const id of ['b-stripl', 'b-stripr', 'b-strip0']) $(id).disabled = busy;
}
function drawFramePos(t, busy) {
  drawStripOffset(busy);
  const w = t.window, show = !!S.settings.show_frame_offset && cur !== null && !!w && !!S.thumb;
  $('framepos').hidden = !show; if (!show) return;
  const flip = S.settings.rotate180 ? -1 : 1;
  const m = Math.round(w.moved || 0), dir = (w.shift || 0) * flip;
  $('posinfo').textContent = [w.fitted ? 'placed on the gaps' : w.realigned ? 'moved off the gap onto its own picture'
      : (S.settings.frame_auto ? 'no clear gap: where the scanner put it' : 'where the scanner put it'),
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
    : 'No picture of this frame yet. Press Preview to look at it.';
  if (has) {
    const pic = '/frame/' + cur + '.png?v=' + t.v;
    if (pic !== shownPic) { $('big').src = pic; shownPic = pic; }
    $('big').alt = 'Frame ' + (cur + 1) + ', ' + SOURCE[t.src].toLowerCase();
  } else shownPic = '';
  if (!drag) placeCrop(has ? boxOnScreen(t) : null);
  const fp = S.settings.focus_point, mark = $('fmark'); mark.hidden = !(has && fp);
  if (has && fp) { const q = toScreen(fp, t.turns || 0); mark.style.left = q[0] * 100 + '%'; mark.style.top = q[1] * 100 + '%'; }
  $('b-cropclear').hidden = !(cropping && t.crop);
  $('croptools').hidden = !(has && cropping);
  for (const id of ['b-crop', 'b-zoom']) $(id).disabled = !has;
  $('b-sharper').disabled = busy || cur === null; $('b-rl').disabled = $('b-rr').disabled = busy || !S.frames.length || cropping;
  const th = t.turn_hint, vt = $('vturn');
  vt.hidden = !(th && cur !== null && (th.applied ? th.turn : th.quarter));
  if (!vt.hidden) {
    $('vturntext').textContent = th.applied ? 'Auto rotate turned this frame ' + th.words + ' (' + Math.round(th.p * 100) + '% sure). Accept keeps it, Reject turns it back; it learns from either.'
      : 'Auto rotate thinks this frame goes ' + th.words + ' (' + Math.round(th.p * 100) + '% sure).' + (th.small ? ' A thumbnail is too small for it to turn by itself: it looks again at the scan, before the files are saved.' : '');
    $('b-turnhint').hidden = !!th.applied;
    $('b-turnok').hidden = $('b-turnno').hidden = !(th.applied && t.turn_auto);
    $('b-turnok').onclick = () => api('/api/turnlearn', {action: 'accept', frames: [cur]});
    $('b-turnno').onclick = () => api('/api/turnlearn', {action: 'reject', frames: [cur]});
    $('b-turnhint').onclick = () => api('/api/rotate', {frames: [cur], quarter: th.quarter});
  }
  const sv = $('vsaved'), saved = t.saved || [];
  sv.hidden = !saved.length && !cropping && !t.crop;
  sv.textContent = cropping ? 'Drag a side or corner of the box to choose the part to save, or its middle to move it, then press Done. The crop is used when this frame is scanned.'
    : [saved.length ? 'Saved from this frame: ' + saved.map(s => s.files.join(', ') + ' (' + clock(s.took) + ')').join(' · ')
         + '. Crop and turn it here and the change is kept for the saved picture, as in Scanned pictures: the file stays as it is. Press Scan to scan it again with the new crop.' : '',
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
  $('sum-extra').textContent = (st.archive_default ? 'starts in Archive mode · ' : '') + (st.auto_crop_learn ? '' : 'crop learning off · ') + (!S.notify ? '' : (st.notify_on ? 'notifications: ' + (ways.join(', ') || 'none picked') : 'notifications off') + ' · ') + logs;
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
    st.superfine && S.caps && S.caps.multi_line ? 'superfine' : '',
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
  const qp = queuePlace(cur), when = $('fs-when');   // while a strip is scanned: what a change here does
  when.hidden = !qp && !(S.job === 'scan');
  when.textContent = !qp ? 'A scan is running. This frame is not in it: tick the frame and press “Add to scan”, and it is scanned with what you set here.'
    : qp.cls === 'waiting' ? 'This frame is waiting (' + qp.text.toLowerCase() + '): it will be scanned with what you set here.'
    : qp.cls === 'scanning' ? 'This frame is being scanned right now, so a change here comes too late for this pass. When it is done, tick it and press “Add to scan” to scan it once more with the new settings.'
    : 'This frame is finished in this scan. Tick it and press “Add to scan” to scan it once more with what you set here.';
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
  fillSelect('s-format', [''].concat(fmts), st.format, v => v ? (FORMAT_NAMES[v] || v) : 'From holder');
  $('superfinerow').hidden = !(c && c.multi_line);   // only units whose sensor reads its lines at once (the LS-5000)
  if (c && c.model && c.model !== 'LS-40') {   // the timing was measured on an LS-40: say so on other models (the LS-40's text is left as it is)
    const pv = $('pvhelp'); if (pv.textContent.includes('your LS-40')) pv.innerHTML = pv.innerHTML.replace('your LS-40', 'an LS-40 (yours is an ' + c.model + ')');
  }
  $('negrow').hidden = !!st.archive; $('cropscanrow').hidden = !!st.archive; $('dustrow').hidden = !st.clean || st.film === 'mono';
  $('cleanrow').hidden = st.film === 'mono'; $('monohint').hidden = st.film !== 'mono'; $('kchint').hidden = st.film !== 'kodachrome';
  const iceopt = $('s-dust_method').querySelector('option[value=ice]'); iceopt.hidden = iceopt.disabled = !S.ice_helper;

  for (const id of ['b-load', 'b-eject', 'b-thumb', 'b-preview', 'b-cal', 'b-af', 'b-ae', 'b-unlock', 'b-scan', 'b-connect', 'b-newroll', 'b-rollok',
    'b-rollno', 'b-rollnoyes', 'rollpick', 'preset', 'b-presetdelyes']) $(id).disabled = busy;
  $('b-stop').disabled = !busy;
  const nf = S.frames.length, ns = sel.size;
  // the button says what it will do; while a strip is scanned, how far it is
  const sq = S.job === 'scan' ? (S.queue || []) : [], sdone = sq.filter(it => it.state === 'done' || it.state === 'failed').length;
  $('b-scan').textContent = sq.length ? 'Scanning ' + Math.min(sdone + 1, sq.length) + ' of ' + sq.length
    : ns ? 'Scan ' + ns + ' selected' : (nf ? 'Scan whole strip (' + plural(nf, 'frame') + ')' : 'Scan whole strip');
  $('b-preview').textContent = ns ? 'Preview ' + ns + ' selected' : (cur !== null ? 'Preview frame ' + (cur + 1) : 'Preview');
  // while a strip is scanned: the ticked frames go into the scan, or out of it, with these two buttons
  const canAdd = sq.length ? ticked().filter(i => !inQueue(i)).length : 0, canOut = sq.length ? ticked().filter(isWaiting).length : 0;
  $('qbtns').hidden = !sq.length;
  $('b-qadd').disabled = !canAdd; $('b-qout').disabled = !canOut;
  $('b-qadd').textContent = canAdd ? 'Add ' + canAdd + ' to scan' : 'Add to scan';
  $('b-qout').textContent = canOut ? 'Take ' + canOut + ' out of scan' : 'Take out of scan';
  $('framesum').textContent = !nf ? '' : sq.length ? (ns ? ns + ' of ' + nf + ' ticked' : 'Tick frames, then add them to this scan or take waiting ones out with the buttons here.')
    : ns ? ns + ' of ' + nf + ' ticked for scanning' : plural(nf, 'frame') + ' · none ticked, so Scan takes the whole strip';
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
  const fp = $('filmprofile'), profs = S.film_profiles || [], rc = S.roll_colour || {mode: 'frame', name: ''};
  const n = S.roll_measured || 0, least = S.profile_least || 3;
  // the roll's own colour (kept in the roll folder's FilmProfile.json, shared with Scanned pictures)
  const rcValue = rc.mode === 'roll' ? '@roll' : rc.mode === 'base' ? '@base' : rc.mode === 'profile' ? rc.name : '';
  if (document.activeElement !== fp) {
    const kept = rc.mode === 'profile' && rc.name && !profs.some(p => p.name === rc.name) ? rc.name : '';
    const want = profs.map(p => p.name + '/' + p.frames).join('\n') + '|' + n + '|' + rc.mode + '|' + kept;
    if (fp.dataset.v !== want) {
      fp.innerHTML = '';
      const add = (v, t) => { const o = document.createElement('option'); o.value = v; o.textContent = t; fp.appendChild(o); };
      add('', 'Each frame on its own');
      add('@roll', "This roll's frames (" + n + ')');
      if (rc.mode === 'base') add('@base', 'Film base picked in Scanned pictures');
      for (const p of profs) add(p.name, p.name);
      if (kept) add(kept, kept + ' (kept with this roll)');
      fp.dataset.v = want;
    }
    fp.value = rcValue;
  }
  const used = profs.find(p => p.name === st.film_profile);
  $('profilehint').textContent = (used ? 'Measured from ' + used.frames + ' frames of ' + (used.roll || 'a roll') + (used.made ? ', ' + used.made : '') + '. ' : '')
    + 'This roll: ' + n + (n === 1 ? ' frame' : ' frames') + ' measured' + (n < least ? ' (a profile needs ' + least + ' or more; frames are measured when scanned).' : '.');
  fp.disabled = busy; $('b-profiledelyes').disabled = busy;
  $('b-profilenew').disabled = n < least; $('b-profiledel').disabled = busy || !fp.value || fp.value[0] === '@' || !profs.some(p => p.name === fp.value);

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

  drawTiles(); drawStripTurn(); drawFilmWarn(); drawQueue(); drawViewer(busy); drawSummaries(st); drawSetLine(st); drawFrameSets();
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

// ---------------------------------------------------------------- logs and cache (Settings, Logs)
$('b-openlogs').onclick = () => api('/api/open_logs');
$('b-clearcache').onclick = async () => {
  const r = await api('/api/clear_cache'), m = $('cachemsg');
  m.hidden = false;
  m.textContent = r.ok ? (r.cleared && r.cleared.length ? 'Cleared: ' + r.cleared.join(', ') + '.' : 'There was nothing to clear.')
    : (r.error || 'That did not work.');
};

// ---------------------------------------------------------------- auto crop learning (Settings, Crop)
const SHORT = {simple: 'simple', net: 'net', cnn: 'CNN'};
const KIND = {simple: 'Simple model', net: 'Small neural network', cnn: 'Picture network (CNN)', rule: 'Built-in rule'};
function pc(v) { return v === null || v === undefined ? '–' : (v * 100).toFixed(1) + '%'; }
function esc(t) { return String(t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c])); }
function reportHtml(rows, more) {
  const h = list => list.map(([k, v]) => '<div class="clrow"><b>' + k + '</b><div>' + v + '</div></div>').join('');
  return h(rows) + (more.length ? '<details class="clmore"><summary>More detail</summary>' + h(more) + '</details>' : '');
}
let learnTimer = null;
async function croplearnShow() {
  let r;
  try { r = await (await fetch('/api/croplearn')).json(); } catch (e) { return; }
  $('croplearnmsg').textContent = (r.on ? '' : 'Learning is off. ') + (r.text || '');
  $('b-croplearn').disabled = !!r.busy;
  $('b-croplearn').textContent = r.busy ? 'Learning…' : 'Learn from my crops';
  clearTimeout(learnTimer);
  if (r.busy) learnTimer = setTimeout(croplearnShow, 2000);
  const pk = r.pictures_kept || {};
  $('b-croplearndelete').disabled = !pk.files;
  $('b-croplearndelete').textContent = pk.files ? 'Delete learning pictures (' + pk.files + ', ' + pk.mb + ' MB)' : 'No learning pictures kept';
  if ($('croplearnreport').hidden) return;
  const m = r.model, rows = [], more = [];
  if (r.role && r.role.text) more.push(['Whose model', esc(r.role.text)]);
  if (m) {
    const e = m.errors || {}, w = m.within_1pc || {};
    rows.push(['Learned from', m.pictures + ' crops (' + m.sides + ' sides), ' + esc(m.made.slice(0, 16)) + ', ' + esc(m.why || '')]);
    const kk = r.use ? m.kind : null;
    rows.push(['In use', (r.use ? KIND[r.running_kind] || r.running_kind : 'Built-in rule (nothing learned does better yet)') +
      (kk && e[kk] !== undefined && e[kk] !== null ? ': its edges land ' + pc(e[kk]) + ' of the frame from yours on average (built-in rule: ' + pc(m.error_rule) + ')' : '')]);
    let t = '<table class="clr"><tr><th></th><th>Off by</th><th>Within 1%</th></tr>';
    for (const k of ['simple', 'net', 'cnn']) if (e[k] !== undefined && e[k] !== null) t += '<tr><td>' + KIND[k] + (k === m.kind ? ' ✓' : '') + '</td><td>' + pc(e[k]) + '</td><td>' + pc(w[k]) + '</td></tr>';
    if (e.shipped !== undefined && e.shipped !== null) t += '<tr><td>The app\'s model as it came</td><td>' + pc(e.shipped) + '</td><td>' + pc(w.shipped) + '</td></tr>';
    t += '<tr><td>' + KIND.rule + '</td><td>' + pc(m.error_rule) + '</td><td>' + pc(w.rule) + '</td></tr></table>';
    more.push(['How close to your crops', t + '<p class="hint">Edges tried on crops each model was not trained on (5 rounds), as a share of the frame.' + (m.cnn_tried ? '' : ' The picture network is tried from 12 crops' + (r.cnn_available ? '' : ' and needs MLX') + '.') + '</p>']);
    const u = m.used || {};
    more.push(['Last learning took', u.seconds + ' s (processor time ' + u.cpu_seconds + ' s) on the ' + esc(u.device || '') +
      '<br>App memory at most ' + u.memory_mb + ' MB' + (u.memory_added_mb ? ' (+' + u.memory_added_mb + ' MB while learning)' : '') +
      (u.gpu_memory_mb !== null && u.gpu_memory_mb !== undefined ? '<br>GPU memory at most ' + u.gpu_memory_mb + ' MB' : '')]);
  }
  more.push(['Cropping with it', r.crops_made ? r.crops_made + ' frames since the app started, ' + r.ms_per_crop + ' ms each' : 'not yet since the app started']);
  const sp = r.steps;
  if (sp && sp.pictures) {
    const SIDEW = {left: 'left', top: 'top', right: 'right', bottom: 'bottom'};
    let t = sp.pictures + ' picture' + (sp.pictures === 1 ? '' : 's') + ', ' + sp.steps + ' crop steps; ' + sp.several + ' took more than one step. Earlier steps you moved away from are learned as "not here".';
    if (sp.from_auto) {
      const mv = Object.keys(SIDEW).filter(k => sp.auto_moved[k] !== null && Math.abs(sp.auto_moved[k]) >= 0.001)
        .map(k => SIDEW[k] + ' ' + (sp.auto_moved[k] > 0 ? 'in ' : 'out ') + Math.abs(sp.auto_moved[k] * 100).toFixed(1) + '%');
      t += '<br>From auto crop\'s box to your final crop (' + sp.from_auto + ' pictures), on average: ' + (mv.length ? mv.join(', ') : 'kept as it was') + '.';
    }
    const KW = {auto: 'auto', saved: 'saved', hand: 'drawn'};
    t += '<table class="clr"><tr><th>Picture</th><th>Steps</th><th>Way</th></tr>' + sp.recent.map(x => '<tr><td>' + esc(x.name || '') + '<br><span class="hint">' + esc(x.t.slice(5, 16)) + '</span></td><td>' + x.steps + '</td><td>' + x.kinds.map(k => KW[k] || k).join(' → ') + '</td></tr>').join('') + '</table>';
    more.push(['Crop steps', t]);
  }
  rows.push(['Kept', r.hand_crops + ' crops from the scanning screen (plus those from Scanned pictures), ' + r.new + ' new since it last learned; ' + r.store_mb + ' MB in <code>' + esc(r.store) + '</code> (never goes to GitHub). What it learned (numbers only, no pictures): <code>' + esc(r.model_file || '') + '</code>, which goes to GitHub with the app.']);
  const h = r.history || [];
  if (h.length > 1) {
    let t = '<table class="clr"><tr><th>When</th><th>Crops</th><th>Used</th><th>Off by</th><th>Rule</th></tr>';
    for (const x of h.slice(-10).reverse()) {
      const best = (x.errors || {})[x.kind];
      t += '<tr><td>' + esc((x.made || '').slice(5, 16)) + '</td><td>' + x.pictures + '</td><td>' + (x.use ? (SHORT[x.kind] || x.kind) : 'rule') + '</td><td>' + pc(best) + '</td><td>' + pc(x.error_rule) + '</td></tr>';
    }
    more.push(['Earlier runs', t + '</table>']);
  }
  $('croplearnreport').innerHTML = reportHtml(rows, more);
}
$('b-croplearn').onclick = async () => {
  $('b-croplearn').disabled = true;
  const r = await api('/api/croplearn', {action: 'learn'});
  if (!r.ok) $('croplearnmsg').textContent = r.error || 'That did not work.';
  setTimeout(croplearnShow, 300);
};
let turnTimer = null;
async function turnlearnShow() {
  let r;
  try { r = await (await fetch('/api/turnlearn')).json(); } catch (e) { return; }
  $('turnlearnmsg').textContent = (r.on ? '' : 'Learning is off. ') + (r.text || '');
  $('b-turnlearn').disabled = !!r.busy;
  $('b-turnlearn').textContent = r.busy ? 'Learning…' : 'Learn from my turns';
  const al = r.all;
  $('b-turnlearnall').disabled = !!r.busy || !!(al && al.busy);
  $('b-turnlearnall').textContent = al && al.busy ? (al.learning ? 'Learning from Scanned pictures…' : 'Reading Scanned pictures… ' + (al.done || 0) + ' of ' + (al.of || '?'))
    : 'Learn from all scanned pictures';
  if (al && !al.busy) $('turnlearnmsg').textContent += (al.error ? ' All scanned pictures: ' + al.error : ' All scanned pictures: learned from ' + al.added + ' pictures and ' + (al.raws || 0) + ' views of their RAW files' + (al.failed ? ' (' + al.failed + ' could not be read)' : '') + '.');
  clearTimeout(turnTimer);
  if (r.busy || (r.all && r.all.busy)) turnTimer = setTimeout(turnlearnShow, 2000);
  if ($('turnlearnreport').hidden) return;
  const m = r.model, rows = [], more = [], TK = {simple: 'Simple model', net: 'Small neural network', cnn: 'Picture network (CNN)', shipped: 'The app\'s model as it came'};
  const c = r.counts || {};
  if (r.base) rows.push(['Ready-made model', esc(r.base.text) + (r.base.state === 'ready' ? ' It turns a picture by itself when it is ' + Math.round(r.base_sure * 100) + '% sure and the picture is at least ' + r.base_small + ' pixels on its longer side (a preview scan, a scan, a scanned picture); on thumbnails it only suggests. What was learned from your own turns, below, is used only without it.' : '')]);
  rows.push(['Kept to learn from', (c.scan || 0) + ' frames from the scanning screen, ' + (c.library || 0) + ' scanned pictures; ' + r.new + ' new since it last learned']);
  if (m) {
    const bt = m.by_turn || [];
    rows.push(['Learned from', m.pictures + ' pictures (' + esc(m.made.slice(0, 16)) + ', ' + esc(m.why || '') + '): ' + (bt[0] || 0) + ' as they were, ' + (bt[1] || 0) + ' turned left, ' + (bt[3] || 0) + ' turned right, ' + (bt[2] || 0) + ' upside down' + (m.left_as_they_were ? '; ' + m.left_as_they_were + ' of them only scanned or opened and left as they were, which count less' : '') + '. Turns are counted from how the frame was shown, so with "turn every frame 180°" on, a frame left alone counts as right that way up.']);
    rows.push(['In use', r.use ? esc(r.running || '') + (r.auto ? '; turns by itself when ' + Math.round(r.sure * 100) + '% sure' : '; suggests only') : 'Nothing yet (not better than leaving frames as they are)']);
    let t = '<table class="clr"><tr><th></th><th>Right way up</th><th>When sure</th></tr>';
    for (const k of ['simple', 'net', 'cnn', 'shipped']) {
      const a = (m.right || {})[k]; if (a === undefined || a === null) continue;
      const s = (m.sure || {})[k] || {};
      t += '<tr><td>' + TK[k] + (k === m.kind ? ' ✓' : '') + '</td><td>' + Math.round(a * 100) + '%</td><td>' + (s.n ? Math.round(s.right * 100) + '% of ' + s.n : '–') + '</td></tr>';
    }
    t += '<tr><td>Leave as it is</td><td>' + Math.round((m.right_keep || 0) * 100) + '%</td><td></td></tr></table>';
    more.push(['How often right', t + '<p class="hint">On pictures each model was not trained on (5 rounds). "When sure": how often it was right when it was ' + Math.round(r.sure * 100) + '% sure or more; it turns by itself only when that is 95% or better.' + (m.cnn_tried ? '' : ' The picture network is tried from 24 pictures and needs MLX.') + '</p>']);
    const u = m.used || {};
    more.push(['Last learning took', u.seconds + ' s (processor time ' + u.cpu_seconds + ' s) on the ' + esc(u.device || '') + '<br>App memory at most ' + u.memory_mb + ' MB' + (u.gpu_memory_mb !== null && u.gpu_memory_mb !== undefined ? '<br>GPU memory at most ' + u.gpu_memory_mb + ' MB' : '')]);
  }
  more.push(['Since the app started', r.frames_looked ? r.frames_looked + ' frames looked at (' + r.ms_per_frame + ' ms each, ' + esc(r.device) + '), ' + r.turned_by_itself + ' turned by itself, ' + r.suggested + ' suggestions' : 'no frames looked at yet']);
  rows.push(['Kept', (r.pictures_kept || {}).files + ' small greyscale copies (' + (r.pictures_kept || {}).mb + ' MB) in <code>' + esc(r.store) + '</code> (never goes to GitHub). What it learned (numbers only): <code>' + esc(r.model_file || '') + '</code>.']);
  if (r.role && r.role.text) more.push(['Whose model', esc(r.role.text)]);
  $('turnlearnreport').innerHTML = reportHtml(rows, more);
}
$('b-turnlearn').onclick = async () => {
  $('b-turnlearn').disabled = true;
  const r = await api('/api/turnlearn', {action: 'learn'});
  if (!r.ok) $('turnlearnmsg').textContent = r.error || 'That did not work.';
  setTimeout(turnlearnShow, 300);
};
$('b-turnlearnall').onclick = async () => {
  $('b-turnlearnall').disabled = true;
  const r = await api('/api/turnlearn', {action: 'library_all'});
  if (!r.ok) $('turnlearnmsg').textContent = r.error || 'That did not work.';
  setTimeout(turnlearnShow, 300);
};
$('b-turnlearnreport').onclick = () => {
  const box = $('turnlearnreport'); box.hidden = !box.hidden;
  $('b-turnlearnreport').setAttribute('aria-expanded', String(!box.hidden));
  turnlearnShow();
};
let delArmed = 0;
$('b-croplearndelete').onclick = async () => {
  // asked twice (a second click within 5 s), not with a pop-up: the Mac app's window shows none
  if (Date.now() - delArmed > 5000) {
    delArmed = Date.now();
    $('b-croplearndelete').textContent = 'Click again to delete them';
    $('croplearndelmsg').textContent = 'What it learned stays. Crops from the scanning screen cannot be learned from again.';
    setTimeout(() => { if (delArmed && Date.now() - delArmed >= 5000) croplearnShow(); }, 5100);
    return;
  }
  delArmed = 0;
  const r = await api('/api/croplearn', {action: 'delete_pictures'});
  $('croplearndelmsg').textContent = r.ok ? 'Deleted ' + r.deleted + ' learning pictures (' + r.mb + ' MB).' : (r.error || 'That did not work.');
  croplearnShow();
};
function resetButton(id, msg, url, show) {
  let armed = 0;
  $(id).onclick = async () => {
    if (!armed || Date.now() - armed > 5000) {
      armed = Date.now(); $(id).textContent = 'Click again to reset';
      setTimeout(() => { if (armed && Date.now() - armed >= 5000) { armed = 0; $(id).textContent = 'Reset to default'; } }, 5100);
      return;
    }
    armed = 0; $(id).textContent = 'Reset to default';
    const r = await api(url, {action: 'reset'});
    await show();
    $(msg).textContent = r.ok ? r.text : (r.error || 'That did not work.');
  };
}
resetButton('b-croplearnreset', 'croplearnresetmsg', '/api/croplearn', croplearnShow);
resetButton('b-turnlearnreset', 'turnlearnresetmsg', '/api/turnlearn', turnlearnShow);
$('b-croplearnreport').onclick = () => {
  const box = $('croplearnreport'); box.hidden = !box.hidden;
  $('b-croplearnreport').setAttribute('aria-expanded', String(!box.hidden));
  croplearnShow();
};
// The Settings window (⚙ in the top bar, or ⌘,) has tabs along the top and shows one at a time. A tab is a
// <section class="settab" data-tab="Name"> in index.html; inside it every heading (.sub) with what follows
// is a group. A group opens with a short line that is always shown (p.what); the longer explanations
// are folded away behind the group's "?". The set-once cards of the left column (Save as, File names)
// are moved into the tab that has a <div data-card="their id">.
// Names from before the tabs still open the right place (page checks and other pages use them).
const SET_ALIASES = {'Thumbnail & preview': 'Thumbnail', 'Preview scan': 'Preview', 'Messages when a strip is done': 'Notifications',
  'Auto crop learning': 'Learning from your crops', 'Auto rotate learning': 'Learning from your turns', 'Logs and cache': 'Logs'};
function buildSettings() {
  const dlg = $('setdlg');
  const nav = document.createElement('div'); nav.id = 'setnav'; nav.setAttribute('role', 'tablist'); nav.setAttribute('aria-label', 'Settings');
  const pane = document.createElement('div'); pane.id = 'setpane';
  const body = document.createElement('div'); body.id = 'setbody'; body.append(nav, pane);
  [...dlg.querySelectorAll('section.settab')].forEach((tab, n) => {
    tab.dataset.name = tab.dataset.tab; tab.id = 'settab' + n; tab.setAttribute('role', 'tabpanel');
    for (const slot of [...tab.querySelectorAll('[data-card]')]) { const card = $(slot.dataset.card); if (card) { card.open = true; slot.replaceWith(card); } else slot.remove(); }
    for (const sub of [...tab.children].filter(e => e.classList.contains('sub'))) {
      const grp = document.createElement('div'); grp.className = 'setgrp'; grp.dataset.name = sub.textContent.trim();
      const rest = []; for (let x = sub.nextSibling; x && !(x.nodeType === 1 && x.classList.contains('sub')); x = x.nextSibling) rest.push(x);
      sub.before(grp); grp.append(sub, ...rest);
      if (grp.querySelector('p.help, p.hint:not([id]), ul.how')) {
        const q = document.createElement('button'); q.type = 'button'; q.className = 'q'; q.textContent = '?';
        q.setAttribute('aria-label', 'Explain ' + grp.dataset.name); q.title = 'Explain'; q.setAttribute('aria-expanded', 'false');
        q.onclick = () => q.setAttribute('aria-expanded', String(grp.classList.toggle('showhelp'))); sub.appendChild(q);
      }
    }
    const b = document.createElement('button'); b.type = 'button'; b.textContent = tab.dataset.name; b.setAttribute('role', 'tab'); b.setAttribute('aria-controls', tab.id);
    b.onclick = () => showSetSec(tab.dataset.name);
    b.onkeydown = ev => { const d = ev.key === 'ArrowRight' ? 1 : ev.key === 'ArrowLeft' ? -1 : 0; if (!d) return;
      const all = [...nav.children], to = all[(all.indexOf(b) + d + all.length) % all.length]; ev.preventDefault(); to.click(); to.focus(); };
    nav.appendChild(b); pane.appendChild(tab);
  });
  dlg.appendChild(body);
}
// name: a tab, or a group inside one (then its tab is shown and the group scrolled to)
function showSetSec(name) {
  const tabs = [...document.querySelectorAll('#setpane .settab')]; if (!tabs.length) return;
  name = SET_ALIASES[name] || name;
  let tab = tabs.find(t => t.dataset.name === name), grp = null;
  if (!tab) { grp = [...document.querySelectorAll('#setpane .setgrp')].find(g => g.dataset.name === name); tab = grp ? grp.closest('.settab') : tabs[0]; }
  tabs.forEach(t => { t.hidden = t !== tab; });
  [...$('setnav').children].forEach(b => { const on = b.textContent === tab.dataset.name; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; });
  $('setpane').scrollTop = grp ? Math.max(0, grp.offsetTop - $('setpane').offsetTop - 8) : 0;
  try { localStorage.setItem('coolscan.setsec', tab.dataset.name); } catch (e) { /* fine */ }
}
buildSettings();
{ let last = ''; try { last = localStorage.getItem('coolscan.setsec') || ''; } catch (e) { /* fine */ } showSetSec(last); }
function openSettings(name) { if (typeof name === 'string') showSetSec(name); if (!$('setdlg').open) { $('setdlg').showModal(); croplearnShow(); turnlearnShow(); } }
$('b-settings').onclick = () => openSettings();
$('b-setdone').onclick = () => $('setdlg').close();
// /?settings=1: only the Settings window, for the other pages to show in a frame (the same window, not a copy)
if (new URLSearchParams(location.search).has('settings')) {
  document.body.classList.add('setonly');
  $('setdlg').addEventListener('close', () => { try { parent.postMessage('coolscan-settings-closed', location.origin); } catch (e) { /* alone */ } });
  const wait = setInterval(() => { if (S) { clearInterval(wait); openSettings(); } }, 100);
}
