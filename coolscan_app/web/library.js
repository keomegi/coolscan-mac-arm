'use strict';
// Library: the scans already made, folder by folder. Look through them, edit a folder's description,
// give pictures stars, flags, tags, people and captions, change when they were taken (one picture or
// a whole roll), search every roll, crop or turn into new files (nothing is overwritten), compare
// versions, and hand pictures to Finder, Lightroom or a small JPEG.
const $ = id => document.getElementById(id);
const FILM_WORDS = {negative: 'Colour negative', positive: 'Slide (positive)', kodachrome: 'Kodachrome',
  mono: 'B&W negative (silver)', chromo: 'B&W chromogenic (C-41)'};
let rolls = [], roll = null, savedDesc = '', items = [], at = -1, alt = null, info = null;
let viewOrig = false, stamp = 0;   // showing the picture without its crop and turn; goes up with every change
const esig = p => p.edit ? JSON.stringify(p.edit) : '';
let cropping = false, rect = null, drag = null, searching = null, comparing = false, lastTick = -1;
const ticked = new Set();     // "key|file" of ticked pictures

async function getJSON(url) {
  const r = await fetch(url);
  const j = await r.json().catch(() => ({}));
  if (!r.ok && !j.error) j.error = 'not found';
  return j;
}
async function post(path, body) {
  const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  return r.json().catch(() => ({ok: false, error: 'The app did not answer.'}));
}
const q = o => Object.entries(o).map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&');
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const id = p => p.key + '|' + p.file;
const starText = n => n ? '★'.repeat(n) : '';
const splitWords = text => text.split(',').map(t => t.trim()).filter(Boolean);
function mb(n) { return n >= 1 << 30 ? (n / (1 << 30)).toFixed(1) + ' GB' : (n / (1 << 20)).toFixed(1) + ' MB'; }
function say(box, text, kind) { $(box).className = 'hint' + (kind ? ' ' + kind : ''); $(box).textContent = text; }
function options(sel, first, values, keep) {
  sel.textContent = '';
  const o0 = el('option', null, first); o0.value = ''; sel.appendChild(o0);
  for (const v of values) { const o = el('option', null, v); o.value = v; sel.appendChild(o); }
  sel.value = keep && values.includes(keep) ? keep : '';
}

// ---------------------------------------------------------------- the place, kept in the address (a reload stays put)
function where() {
  const h = new URLSearchParams(location.hash.slice(1));
  return {roll: h.get('roll'), pic: h.get('pic'), q: h.get('q')};
}
function remember() {
  const h = new URLSearchParams();
  if (searching != null) h.set('q', searching);
  else if (roll) h.set('roll', roll.key);
  if (at >= 0 && items[at] && !$('viewer').hidden) { h.set('pic', items[at].file); if (searching != null) h.set('roll', items[at].key); }
  history.replaceState(null, '', '#' + h.toString());
}

// ---------------------------------------------------------------- folders
async function loadRolls() {
  let r;
  try { r = await getJSON('/api/library/rolls'); }
  catch (e) { $('status').textContent = 'The app has stopped. Open it again.'; return; }
  if (r.error === 'not found') { $('status').textContent = 'The app is older than this page. Quit it and open it again.'; return; }
  rolls = r.rolls || [];
  $('base').textContent = r.base || '';
  $('status').textContent = rolls.length + ' folder' + (rolls.length === 1 ? '' : 's');
  $('norolls').hidden = !!rolls.length;
  $('norolls').textContent = r.missing ? 'The scans folder is not there yet.' : 'No scans in this folder yet.';
  const ul = $('rolllist');
  ul.textContent = '';
  for (const it of rolls) {
    const li = el('li'), b = el('button');
    b.dataset.key = it.key;
    const nm = el('span', 'rname', it.name);
    if (it.current) nm.appendChild(el('span', 'rtag', 'scanning into'));
    if (it.redo) { const t = el('span', 'rtag', '↻ ' + it.redo); t.title = it.redo + ' tagged for re-develop'; nm.appendChild(t); }
    b.appendChild(nm);
    const dates = it.first ? (it.first === it.last ? it.first : it.first + ' – ' + it.last) : '';
    b.appendChild(el('span', 'rsub', it.count + ' picture' + (it.count === 1 ? '' : 's') + (it.raw ? ' · RAW' : '') + (dates ? ' · ' + dates : '')));
    if (it.description) b.appendChild(el('span', 'rdesc', it.description));
    b.onclick = () => { $('search').value = ''; openRoll(it.key); };
    li.appendChild(b); ul.appendChild(li);
  }
  markRoll();
  const tagged = rolls.reduce((n, it) => n + (it.redo || 0), 0);
  $('devlink').textContent = tagged ? 'Re-develop (' + tagged + ' tagged)' : 'Re-develop';
  getJSON('/api/library/tags').then(t => {
    for (const [list, values] of [['alltags', t.tags], ['allpeople', t.people]]) {
      const dl = $(list); dl.textContent = '';
      for (const v of values || []) { const o = el('option'); o.value = v; dl.appendChild(o); }
    }
  });
}
function markRoll() {
  for (const b of $('rolllist').querySelectorAll('button')) b.setAttribute('aria-current', String(searching == null && !!roll && b.dataset.key === roll.key));
}
function showDescription(on) {
  $('desc').hidden = $('b-desc').parentElement.hidden = $('desc').previousElementSibling.hidden = !on;
}

async function openRoll(key, pic, keepTicks) {
  if (roll && roll.key !== key) await saveDesc(true);
  const r = await getJSON('/api/library/roll?' + q({key}));
  if (!r.ok) { $('status').textContent = r.error || 'Could not open that folder.'; return; }
  if ((!roll || roll.key !== key || searching != null) && !keepTicks) ticked.clear();
  searching = null;
  roll = r;
  for (const p of r.pictures) p.key = key;
  $('pickroll').hidden = true; $('rollbody').hidden = false;
  $('rname').textContent = r.name;
  $('rpath').textContent = r.path + ' · ' + r.pictures.length + ' picture' + (r.pictures.length === 1 ? '' : 's');
  showDescription(true);
  $('desc').value = savedDesc = r.description || '';
  say('descmsg', r.current ? 'This is the roll being scanned into: the scanning screen shows the same description.' : '');
  $('b-desc').disabled = true;
  fillFilters(r.pictures);
  drawGrid(); markRoll(); remember();
  if (pic) { const i = items.findIndex(p => p.file === pic); if (i >= 0) show(i); }
}

// ---------------------------------------------------------------- search across every roll
let searchTimer = 0;
$('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => runSearch($('search').value), 350); });
$('search').addEventListener('keydown', ev => { if (ev.key === 'Enter') { clearTimeout(searchTimer); runSearch($('search').value); } });
async function runSearch(text, pic, key) {
  text = text.trim();
  if (!text) { if (searching != null) { searching = null; if (roll && roll.key != null) openRoll(roll.key); else if (rolls.length) openRoll(rolls[rolls.length - 1].key); } return; }
  await saveDesc(true);
  const r = await getJSON('/api/library/search?' + q({q: text}));
  if (!r.ok) return;
  if (searching !== text) ticked.clear();
  searching = text;
  roll = {key: null, pictures: r.pictures, search: true};
  $('pickroll').hidden = true; $('rollbody').hidden = false;
  $('rname').textContent = 'Search: ' + text;
  $('rpath').textContent = r.pictures.length + (r.more ? '+' : '') + ' picture' + (r.pictures.length === 1 ? '' : 's') + ' in every roll'
    + (r.rolls.length ? ' · rolls whose name or description match: ' + r.rolls.map(x => x.name).join(', ') : '');
  showDescription(false);
  fillFilters(r.pictures);
  drawGrid(); markRoll(); remember();
  if (pic) { const i = items.findIndex(p => p.file === pic && (key == null || p.key === key)); if (i >= 0) show(i); }
}

// ---------------------------------------------------------------- the grid, filtered
function fillFilters(pics) {
  const tags = new Set(), people = new Set();
  for (const p of pics) { for (const t of p.tags || []) tags.add(t); for (const t of p.people || []) people.add(t); }
  const sort = s => [...s].sort((a, b) => a.localeCompare(b));
  options($('f-tag'), 'Any', sort(tags), $('f-tag').value);
  options($('f-person'), 'Anyone', sort(people), $('f-person').value);
}
function passes(p) {
  const stars = +$('f-stars').value, flag = $('f-flag').value, tag = $('f-tag').value, person = $('f-person').value;
  if ((p.stars || 0) < stars) return false;
  if (flag === 'pick' && p.flag !== 'pick') return false;
  if (flag === 'reject' && p.flag !== 'reject') return false;
  if (flag === 'notreject' && p.flag === 'reject') return false;
  if (flag === 'redo' && !p.redo) return false;
  if (tag && !(p.tags || []).includes(tag)) return false;
  if (person && !(p.people || []).includes(person)) return false;
  return true;
}
for (const f of ['f-stars', 'f-flag', 'f-tag', 'f-person']) $(f).onchange = () => drawGrid();

function drawGrid() {
  const g = $('grid');
  g.textContent = '';
  items = roll.pictures.filter(passes);
  $('nopics').hidden = !!items.length;
  $('nopics').textContent = roll.pictures.length ? 'No pictures match the filter.' : (roll.search ? 'Nothing found.' : 'No pictures in this folder.');
  $('shown').textContent = items.length === roll.pictures.length ? '' : items.length + ' of ' + roll.pictures.length + ' shown';
  items.forEach((p, i) => {
    const b = el('div', 'pic' + (ticked.has(id(p)) ? ' sel' : '') + (p.flag === 'reject' ? ' rejected' : ''));
    b.setAttribute('role', 'listitem'); b.tabIndex = 0;
    b.title = p.name + (p.caption ? '\n' + p.caption : '');
    const tick = el('input', 'tick'); tick.type = 'checkbox'; tick.checked = ticked.has(id(p));
    tick.setAttribute('aria-label', 'Tick ' + p.name);
    tick.onclick = ev => { ev.stopPropagation(); tickRange(i, tick.checked, ev.shiftKey); };
    b.appendChild(tick);
    const ph = el('span', 'ph'), img = el('img');
    img.loading = 'lazy'; img.alt = p.name;
    img.src = '/library/small.png?' + q({key: p.key, file: p.file, v: p.v, e: esig(p)});
    ph.appendChild(img); b.appendChild(ph);
    b.appendChild(el('span', 'cap', p.caption || p.name));
    const rate = el('span', 'rate', starText(p.stars));
    if (p.flag === 'pick') rate.appendChild(el('span', 'fl', '⚑ pick'));
    if (p.flag === 'reject') rate.appendChild(el('span', 'rj', '✕ rejected'));
    if (p.redo) rate.appendChild(el('span', 'rd', '↻ re-develop'));
    if (p.edit) { const e = el('span', 'ed', '✂ edited'); e.title = 'Shown with your crop and turn. The file itself is unchanged; Export makes a copy with them.'; rate.appendChild(e); }
    b.appendChild(rate);
    const marks = [];
    if (roll.search) marks.push(p.roll);
    if (p.taken) marks.push(p.taken.slice(0, 10) + (p.date_note ? ' (' + p.date_note + ')' : ''));
    marks.push(mb(p.bytes));
    if (p.only_raw) marks.push('RAW only'); else if (p.raw) marks.push('RAW');
    if (p.versions.length) marks.push(p.versions.length + ' version' + (p.versions.length > 1 ? 's' : ''));
    b.appendChild(el('span', 'marks', marks.join(' · ')));
    if ((p.people || []).length) b.appendChild(el('span', 'tagline', '👤 ' + p.people.join(', ')));
    if ((p.tags || []).length) b.appendChild(el('span', 'tagline', p.tags.join(', ')));
    b.onclick = ev => { if (ev.metaKey || ev.ctrlKey || ev.shiftKey) tickRange(i, !ticked.has(id(p)), ev.shiftKey); else show(i); };
    b.onkeydown = ev => { if (ev.target !== b) return; if (ev.key === 'Enter') show(i); else if (ev.key === ' ') { ev.preventDefault(); tickRange(i, !ticked.has(id(p)), ev.shiftKey); } };
    g.appendChild(b);
  });
  drawActions();
}
function tickRange(i, on, range) {
  const from = range && lastTick >= 0 ? Math.min(lastTick, i) : i, to = range && lastTick >= 0 ? Math.max(lastTick, i) : i;
  for (let k = from; k <= to; k++) on ? ticked.add(id(items[k])) : ticked.delete(id(items[k]));
  lastTick = i;
  const g = $('grid').children;
  for (let k = from; k <= to; k++) { g[k].classList.toggle('sel', on); g[k].querySelector('.tick').checked = on; }
  drawActions();
}
$('b-selall').onclick = () => { for (const p of items) ticked.add(id(p)); drawGrid(); };
$('b-selnone').onclick = () => { ticked.clear(); drawGrid(); };

// ---------------------------------------------------------------- changing the ticked pictures
function tickedPics() { return roll.pictures.filter(p => ticked.has(id(p))); }
function drawActions() {
  const sel = tickedPics();
  $('actions').hidden = !sel.length;
  $('b-selnone').hidden = !sel.length;
  $('selcount').textContent = sel.length + ' ticked';
  for (const [kind, box, words] of [['tags', 'a-rmtag', 'tag'], ['people', 'a-rmperson', 'person']]) {
    const have = new Set(); for (const p of sel) for (const t of p[kind] || []) have.add(t);
    options($(box), have.size ? 'Take a ' + words + ' off…' : 'No ' + (kind === 'tags' ? 'tags' : 'people') + ' to take off', [...have].sort());
  }
  const box = $('a-stars'); box.textContent = '';
  const common = sel.length && sel.every(p => (p.stars || 0) === (sel[0].stars || 0)) ? sel[0].stars || 0 : -1;
  for (let n = 1; n <= 5; n++) {
    const b = el('button', n <= common ? 'on' : '', '★'); b.title = n + ' star' + (n > 1 ? 's' : '');
    b.onclick = () => edit(sel, {stars: common === n ? 0 : n});
    box.appendChild(b);
  }
}
async function edit(pics, change, msgBox) {
  msgBox = msgBox || 'actmsg';
  if (!pics.length) return false;
  const byKey = new Map();
  for (const p of pics) { if (!byKey.has(p.key)) byKey.set(p.key, []); byKey.get(p.key).push(p.file); }
  say(msgBox, 'Saving…');
  const problems = [];
  let n = 0;
  for (const [key, files] of byKey) {
    const r = await post('/api/library/edit', {key, files, ...change});
    if (!r.ok) { say(msgBox, r.error || 'Could not save.', 'warn'); return false; }
    n += r.changed; problems.push(...(r.problems || []));
  }
  await refresh();
  if ('redo' in change && Object.keys(change).length === 1)
  {
    const noRaw = change.redo ? pics.filter(p => !p.raw).length : 0;
    say(msgBox, change.redo ? n + ' picture' + (n > 1 ? 's' : '') + ' tagged for re-develop. Open the Re-develop page to develop them.'
      + (noRaw ? ' ' + noRaw + ' of them ha' + (noRaw > 1 ? 've' : 's') + ' no RAW file (only Archive mode keeps one), so cannot be developed again.' : '')
      : 'Re-develop tag taken off ' + n + ' picture' + (n > 1 ? 's' : '') + '.', noRaw ? 'warn' : 'ok');
  }
  else if (problems.length) say(msgBox, 'Saved for ' + n + ' picture' + (n > 1 ? 's' : '') + '. In the app\'s list only (not in the file): ' + problems.join('; '), 'warn');
  else say(msgBox, 'Saved for ' + n + ' picture' + (n > 1 ? 's' : '') + ', in the app and in the files.', 'ok');
  return true;
}
async function refresh() {
  const viewing = !$('viewer').hidden && items[at] ? {key: items[at].key, file: items[at].file} : null;
  if (searching != null) {
    const r = await getJSON('/api/library/search?' + q({q: searching}));
    if (r.ok) roll = {key: null, pictures: r.pictures, search: true};
  } else if (roll) {
    const r = await getJSON('/api/library/roll?' + q({key: roll.key}));
    if (r.ok) { for (const p of r.pictures) p.key = roll.key; roll = r; }
  }
  fillFilters(roll.pictures);
  drawGrid();
  loadRolls();
  if (viewing) {
    // the picture on show stays on show, even if a filter would now leave it out
    let i = items.findIndex(p => p.key === viewing.key && p.file === viewing.file);
    if (i < 0) { const p = roll.pictures.find(x => x.key === viewing.key && x.file === viewing.file); if (p) { items.splice(Math.min(at, items.length), 0, p); i = items.indexOf(p); } }
    if (i >= 0) { at = i; drawViewerMarks(); drawOrig(); drawStrip(); }
  }
}
$('a-pick').onclick = () => edit(tickedPics(), {flag: 'pick'});
$('a-reject').onclick = () => edit(tickedPics(), {flag: 'reject'});
$('a-unflag').onclick = () => edit(tickedPics(), {flag: null});
$('a-autocrop').onclick = async () => {
  const sel = tickedPics().filter(p => !p.file.startsWith('RAW/'));
  if (!sel.length) return;
  say('actmsg', 'Auto crop: looking at ' + sel.length + ' picture' + (sel.length > 1 ? 's' : '') + '…');
  const byKey = new Map(); for (const p of sel) { if (!byKey.has(p.key)) byKey.set(p.key, []); byKey.get(p.key).push(p.file); }
  let found = 0;
  for (const [key, files] of byKey) { const r = await post('/api/library/autocrop', {key, files, save: true}); if (r.ok) found += r.found; }
  stamp++; await refresh();
  say('actmsg', 'Auto crop set a crop on ' + found + ' of ' + sel.length + ' picture' + (sel.length > 1 ? 's' : '') + (found < sel.length ? ' (no border found on the rest)' : '') + '. The files are unchanged.', 'ok');
};
$('a-reset').onclick = async () => { stamp++; await edit(tickedPics(), {edit: null}); };
$('a-export').onclick = async () => {
  const sel = tickedPics();
  if (!sel.length) return;
  say('actmsg', 'Exporting…');
  const byKey = new Map(); for (const p of sel) { if (!byKey.has(p.key)) byKey.set(p.key, []); byKey.get(p.key).push(p.file); }
  let n = 0, same = 0;
  for (const [key, files] of byKey) { const r = await post('/api/library/export', {key, files}); if (!r.ok) { say('actmsg', r.error || 'Could not export.', 'warn'); return; } n += r.saved.length; same += r.unchanged.length; }
  await refresh();
  say('actmsg', 'Exported ' + n + ' cop' + (n === 1 ? 'y' : 'ies') + ' with their crop and turn into Edited/.' + (same ? ' ' + same + ' had nothing changed, so were left out.' : '') + ' The pictures themselves are unchanged.', 'ok');
};
$('a-redo').onclick = () => edit(tickedPics(), {redo: true});
$('a-unredo').onclick = () => edit(tickedPics(), {redo: false});
for (const [kind, input, button, rm] of [['tags', 'a-tag', 'a-addtag', 'a-rmtag'], ['people', 'a-person', 'a-addperson', 'a-rmperson']]) {
  $(button).onclick = async () => { const t = splitWords($(input).value); if (t.length && await edit(tickedPics(), {['add_' + kind]: t})) $(input).value = ''; };
  $(input).addEventListener('keydown', ev => { if (ev.key === 'Enter') $(button).click(); });
  $(rm).onchange = () => { const t = $(rm).value; if (t) edit(tickedPics(), {['remove_' + kind]: [t]}); };
}
$('a-setcap').onclick = () => edit(tickedPics(), {caption: $('a-caption').value});
$('a-caption').addEventListener('keydown', ev => { if (ev.key === 'Enter') $('a-setcap').click(); });
$('a-setnote').onclick = () => edit(tickedPics(), {date_note: $('a-datenote').value});
$('a-datenote').addEventListener('keydown', ev => { if (ev.key === 'Enter') $('a-setnote').click(); });
$('a-how').onchange = () => { const h = $('a-how').value; $('a-year').hidden = h !== 'year'; $('a-date').hidden = h !== 'date'; $('a-exact').hidden = h !== 'exact'; };
$('a-setdate').onclick = () => {
  const how = $('a-how').value, value = $(how === 'year' ? 'a-year' : how === 'date' ? 'a-date' : 'a-exact').value;
  if (!value) { say('actmsg', 'Type the ' + (how === 'year' ? 'year' : 'date') + ' first.', 'warn'); return; }
  edit(tickedPics(), {date: {how, value}});
};
async function filesDo(path, pics, box) {
  const byKey = new Map();
  for (const p of pics) { if (!byKey.has(p.key)) byKey.set(p.key, []); byKey.get(p.key).push(p.file); }
  for (const [key, files] of byKey) {
    const r = await post(path, {key, files});
    if (!r.ok) { say(box, r.error || 'That did not work.', 'warn'); return; }
  }
  say(box, path.endsWith('lightroom') ? 'Handed to Lightroom.' : 'Shown in Finder.', 'ok');
}
$('a-finder').onclick = () => filesDo('/api/library/reveal', tickedPics(), 'actmsg');
$('a-lr').onclick = () => filesDo('/api/library/lightroom', tickedPics(), 'actmsg');
async function jpegs(pics, side, box) {
  let done = 0, folder = '';
  for (const p of pics) {
    say(box, 'Making JPEG ' + (done + 1) + ' of ' + pics.length + '…');
    const r = await post('/api/library/jpeg', {key: p.key, file: p.file, side});
    if (!r.ok) { say(box, r.error || 'Could not make the JPEG.', 'warn'); return; }
    done++; folder = r.folder;
  }
  say(box, done + ' JPEG' + (done > 1 ? 's' : '') + ' saved in ' + folder + '.', 'ok');
}
$('a-jpeg').onclick = () => jpegs(tickedPics(), +$('a-side').value, 'actmsg');

// ---------------------------------------------------------------- description
$('desc').addEventListener('input', () => { $('b-desc').disabled = $('desc').value === savedDesc; say('descmsg', ''); });
$('b-desc').onclick = () => saveDesc(false);
async function saveDesc(quiet) {
  if (!roll || roll.search || $('desc').value === savedDesc) return;
  const text = $('desc').value;
  const r = await post('/api/library/describe', {key: roll.key, text});
  if (r.ok) {
    savedDesc = text; $('b-desc').disabled = true;
    if (!quiet) say('descmsg', 'Saved.', 'ok');
    loadRolls();
  } else say('descmsg', r.error || 'Could not save.', 'warn');
}
$('desc').addEventListener('keydown', ev => { if ((ev.metaKey || ev.ctrlKey) && ev.key === 's') { ev.preventDefault(); saveDesc(false); } });

// ---------------------------------------------------------------- one picture
function current() { return alt || items[at].file; }
const bigURL = (p, file, view) => '/library/big.png?' + q({key: p.key, file, v: p.v, view: view || 'edited', s: stamp, e: file === p.file ? esig(p) : ''});
async function show(i, other) {
  if (i < 0 || i >= items.length) return;
  if (i !== at || (other || null) !== alt) viewOrig = false;
  at = i; alt = other || null;
  stopCrop();
  $('viewer').hidden = false;
  const p = items[i], file = current();
  $('vtitle').textContent = (alt ? alt : p.name) + '  ·  ' + (i + 1) + ' of ' + items.length + (roll.search ? '  ·  ' + p.roll : '');
  $('b-prev').disabled = i === 0;
  $('b-next').disabled = i === items.length - 1;
  $('loading').hidden = false; $('loading').textContent = 'Loading…';
  const img = $('big');
  img.onload = () => { if (items[at] === p && current() === file) $('loading').hidden = true; };
  img.onerror = () => { if (current() === file) $('loading').textContent = 'Could not show this file (only uncompressed TIFFs can be shown).'; };
  img.src = bigURL(p, file, viewOrig ? 'original' : 'edited');
  $('cap1').textContent = file;
  say('cropmsg', '');
  $('meta').textContent = '';
  drawViewerMarks();
  drawOrig();
  drawStrip();
  fillCompare(p);
  remember();
  const r = await getJSON('/api/library/info?' + q({key: p.key, file}));
  if (items[at] !== p || current() !== file) return;
  info = r.ok ? r : null;
  $('b-raw').hidden = !(info && info.raw);
  const isRaw = file.startsWith('RAW/');
  $('b-crop').hidden = $('b-turnl').hidden = $('b-turnr').hidden = $('b-autocrop').hidden = isRaw;  // a RAW file is cropped on the Re-develop page
  drawOrig();
  drawInfo(r);
  // the next picture is made ready while this one is looked at
  if (i + 1 < items.length) { const n = new Image(); n.src = bigURL(items[i + 1], items[i + 1].file); }
}
function closeViewer() {
  stopCrop(); setCompare(false);
  $('viewer').hidden = true; alt = null;
  drawGrid(); remember();
  const b = $('grid').children[at]; if (b) b.focus();
}
$('b-close').onclick = closeViewer;

// ---------------------------------------------------------------- the folder's pictures along the bottom
let stripSig = '';
function drawStrip() {
  const box = $('strip'), sig = items.map(p => p.key + '/' + p.file + ':' + p.v + esig(p)).join('|');
  if (sig !== stripSig) {
    stripSig = sig; box.textContent = '';
    items.forEach((p, i) => {
      const b = el('button'); b.setAttribute('role', 'listitem'); b.title = p.caption || p.name; b.setAttribute('aria-label', 'Show ' + p.name);
      const img = el('img'); img.loading = 'lazy'; img.alt = '';
      img.src = '/library/small.png?' + q({key: p.key, file: p.file, v: p.v, e: esig(p)});
      b.appendChild(img); b.appendChild(el('span', 'n', String(i + 1)));
      b.onclick = () => show(i);
      box.appendChild(b);
    });
  }
  [...box.children].forEach((b, i) => b.setAttribute('aria-current', String(i === at)));
  const cur = box.children[at];
  if (cur) cur.scrollIntoView({block: 'nearest', inline: 'center'});
}

// ---------------------------------------------------------------- the edited copy or the original
function drawOrig() {
  const e = info && info.file === current() ? info.edit : null, b = $('b-orig'), tag = $('origtag');
  b.hidden = $('b-reset').hidden = $('b-export').hidden = !e;
  b.textContent = viewOrig ? 'Show edited' : 'Show original';
  b.setAttribute('aria-pressed', String(viewOrig));
  tag.hidden = !e || !viewOrig;
  tag.textContent = 'The original';
  $('sep1').hidden = !e;
}
$('b-orig').onclick = () => { viewOrig = !viewOrig; stopCrop(); $('big').src = bigURL(items[at], current(), viewOrig ? 'original' : 'edited'); drawOrig(); };
async function changed(r, words) {
  if (!r.ok) { say('cropmsg', r.error || 'Could not save.', 'warn'); return false; }
  stamp++; viewOrig = false;
  await refresh();
  await show(at, alt);
  say('cropmsg', words, 'ok');
  return true;
}
$('b-reset').onclick = async () => {
  const p = items[at];
  await changed(await post('/api/library/edit', {key: p.key, files: [current()], edit: null}), 'Crop and turn taken off. The file was never changed.');
};
$('b-export').onclick = async () => {
  const p = items[at];
  say('cropmsg', 'Exporting…');
  const r = await post('/api/library/export', {key: p.key, file: current()});
  if (!r.ok) { say('cropmsg', r.error || 'Could not export.', 'warn'); return; }
  await refresh();
  say('cropmsg', r.saved.length ? 'Exported as ' + r.saved.join(', ') + ' with the crop and turn. The picture itself is unchanged.' : 'Nothing to export: this picture has no crop or turn.', 'ok');
};
$('b-prev').onclick = () => show(at - 1);
$('b-next').onclick = () => show(at + 1);

function chips(box, p, kind, label) {
  const row = el('div', 'erow chips');
  row.appendChild(el('span', 'hint', label));
  for (const t of p[kind] || []) {
    const c = el('span', 'chip', t), x = el('button', null, '×'); x.setAttribute('aria-label', 'Take off ' + t);
    x.onclick = () => edit([p], {['remove_' + kind]: [t]}, 'cropmsg');
    c.appendChild(x); row.appendChild(c);
  }
  const add = el('input'); add.type = 'text'; add.placeholder = kind === 'people' ? 'Add a person…' : 'Add a tag…';
  add.setAttribute('list', kind === 'people' ? 'allpeople' : 'alltags'); add.setAttribute('aria-label', add.placeholder);
  add.onkeydown = ev => { if (ev.key === 'Enter' && add.value.trim()) edit([p], {['add_' + kind]: splitWords(add.value)}, 'cropmsg'); };
  row.appendChild(add); box.appendChild(row);
}
// stars, flag, caption, people, tags and date of the picture on show (they belong to the picture, not to a version)
function drawViewerMarks() {
  const p = items[at], box = $('edit');
  box.textContent = '';
  const r1 = el('div', 'erow'), stars = el('span', 'stars');
  for (let n = 1; n <= 5; n++) {
    const b = el('button', n <= (p.stars || 0) ? 'on' : '', '★'); b.title = n + ' star' + (n > 1 ? 's' : '') + ' (key ' + n + ')';
    b.onclick = () => edit([p], {stars: p.stars === n ? 0 : n}, 'cropmsg');
    stars.appendChild(b);
  }
  r1.appendChild(stars);
  const flags = el('span', 'flags');
  for (const [f, words, key] of [['pick', '⚑ Pick', 'P'], ['reject', '✕ Reject', 'X']]) {
    const b = el('button', null, words); b.setAttribute('aria-pressed', String(p.flag === f)); b.title = words.slice(2) + ' (' + key + ')';
    b.onclick = () => edit([p], {flag: p.flag === f ? null : f}, 'cropmsg');
    flags.appendChild(b);
  }
  const rd = el('button', null, '↻ Re-develop'); rd.setAttribute('aria-pressed', String(!!p.redo));
  rd.title = 'Tag to develop again from its RAW file, on the Re-develop page (R)';
  rd.onclick = () => edit([p], {redo: !p.redo}, 'cropmsg');
  flags.appendChild(rd);
  r1.appendChild(flags); box.appendChild(r1);
  const cap = el('textarea'); cap.placeholder = 'Caption: who, where, what is happening'; cap.value = p.caption || ''; cap.setAttribute('aria-label', 'Caption');
  cap.onchange = () => edit([p], {caption: cap.value}, 'cropmsg');
  box.appendChild(cap);
  chips(box, p, 'people', 'People ');
  chips(box, p, 'tags', 'Tags ');
  const r3 = el('div', 'erow');
  r3.appendChild(el('span', 'hint', 'Photo taken '));
  const d = el('input'); d.type = 'datetime-local'; d.step = 1; d.value = (p.taken || '').replace(' ', 'T');
  d.setAttribute('aria-label', 'Photo taken');
  d.onchange = () => { if (d.value) edit([p], {date: {how: 'exact', value: d.value}}, 'cropmsg'); };
  r3.appendChild(d);
  const note = el('input'); note.type = 'text'; note.placeholder = 'Date note (about 1988?)'; note.value = p.date_note || '';
  note.setAttribute('aria-label', 'Date note'); note.style.marginTop = '4px';
  note.onchange = () => edit([p], {date_note: note.value}, 'cropmsg');
  r3.appendChild(note);
  box.appendChild(r3);
}

function row(dl, label, value) {
  if (value == null || value === '' || (Array.isArray(value) && !value.length)) return;
  dl.appendChild(el('dt', null, label));
  const dd = el('dd');
  if (value instanceof Node) dd.appendChild(value); else dd.textContent = value;
  dl.appendChild(dd);
}
function section(title) {
  const box = $('meta');
  box.appendChild(el('h3', null, title));
  const dl = el('dl'); box.appendChild(dl); return dl;
}
function drawInfo(r) {
  const box = $('meta');
  box.textContent = '';
  if (!r.ok) { box.appendChild(el('p', 'hint warn', r.error || 'Could not read this file.')); return; }
  let dl = section('File');
  row(dl, 'Name', r.name);
  row(dl, 'Folder', r.folder);
  row(dl, 'Size', r.size_words);
  row(dl, 'Changed', r.modified);
  dl = section('Picture');
  row(dl, 'Pixels', r.pixels[0] + ' × ' + r.pixels[1] + ' (' + r.megapixels + ' MP)');
  row(dl, 'Channels', r.channels === 4 ? '4 (colour + infrared)' : r.channels === 1 ? '1 (grey)' : String(r.channels));
  row(dl, 'Resolution', r.dpi ? r.dpi + ' dpi' : null);
  row(dl, 'On the film', r.film_mm ? r.film_mm[0] + ' × ' + r.film_mm[1] + ' mm' : null);
  row(dl, 'Print at 300 dpi', r.print_cm_300 ? r.print_cm_300[0] + ' × ' + r.print_cm_300[1] + ' cm' : null);
  row(dl, 'Colour profile', r.profile);
  dl = section('Photo');
  row(dl, 'Photo taken', r.taken);
  row(dl, 'Film speed', r.iso ? 'ISO ' + r.iso : null);
  row(dl, 'Scanner', r.scanner);
  row(dl, 'Note in file', r.description);
  const s = r.scan || {};
  if (Object.keys(s).length) {
    dl = section(r.raw && r.file !== r.raw ? 'How it was scanned (from the RAW file)' : 'How it was scanned');
    row(dl, 'Film', s.film ? (FILM_WORDS[s.film] || s.film) : null);
    row(dl, 'Scanned', s.scanned);
    row(dl, 'Roll', s.roll);
    row(dl, 'Scan dpi', s.dpi);
    if (s.exposures && Object.keys(s.exposures).length)
      row(dl, 'Exposures', Object.entries(s.exposures).map(([k, v]) => k + ' ' + v).join(', ') + (s.from_roll_file ? ' (from the roll\'s FilmColour.json)' : ''));
    row(dl, 'Focus position', s.focus_position);
    row(dl, 'Turned', s.turns ? s.turns + ' quarter turn' + (s.turns > 1 ? 's' : '') + ' left' : null);
    row(dl, 'Crop asked for', s.crop ? s.crop.map(v => Math.round(v * 1000) / 10 + '%').join(', ') : null);
  }
  const p = items[at];
  const files = versionsOf(p, r.raw);
  if (files.length > 1) {
    dl = section('Versions');
    const list = el('div');
    for (const [f, words] of files) {
      const a = el('button', 'vlink', words);
      a.setAttribute('aria-current', String(f === current()));
      a.onclick = () => show(at, f);
      list.appendChild(a);
    }
    row(dl, '', list);
  }
  if (r.raw) box.appendChild(el('p', 'hint', 'Crop from RAW opens the whole frame on the Re-develop page: the crop can be made larger there too, and the result is saved in Re-developed/ inside this folder.'));
}
function versionsOf(p, raw) {
  const out = [[p.file, p.file + ' (the picture)']];
  for (const v of p.versions || []) out.push([v, v]);
  raw = raw || p.raw;
  if (raw && raw !== p.file) out.push([raw, raw + ' (RAW, as scanned)']);
  return out;
}

// ---------------------------------------------------------------- compare with another version, side by side
function fillCompare(p) {
  const vs = versionsOf(p).filter(([f]) => f !== current());
  const sel = $('cmpwith'), keep = sel.value;
  sel.textContent = '';
  for (const [f, words] of vs) { const o = el('option', null, words); o.value = f; sel.appendChild(o); }
  if (vs.some(([f]) => f === keep)) sel.value = keep;
  $('b-compare').hidden = !vs.length;
  if (!vs.length) setCompare(false); else if (comparing) drawCompare();
}
function setCompare(on) {
  comparing = on;
  $('b-compare').setAttribute('aria-pressed', String(on));
  $('cmpwith').hidden = !on; $('imgwrap2').hidden = !on;
  $('stage').classList.toggle('two', on);
  if (on) { stopCrop(); drawCompare(); }
}
function drawCompare() { const f = $('cmpwith').value; if (f) { $('big2').src = bigURL(items[at], f); $('cap2').textContent = f; } }
$('b-compare').onclick = () => setCompare(!comparing);
$('cmpwith').onchange = drawCompare;

// ---------------------------------------------------------------- new files from the picture on show: turned, JPEG; Finder, Lightroom
async function turn(k) {
  const p = items[at];
  stopCrop();
  say('cropmsg', 'Turning…');
  await changed(await post('/api/library/turn', {key: p.key, file: current(), quarter: k}), 'Turned. The file stays as it is; Export makes a copy with the change.');
}
$('b-turnl').onclick = () => turn(1);
$('b-turnr').onclick = () => turn(3);
$('b-finder').onclick = () => filesDo('/api/library/reveal', [{key: items[at].key, file: current()}], 'cropmsg');
$('b-lr').onclick = () => filesDo('/api/library/lightroom', [{key: items[at].key, file: current()}], 'cropmsg');
$('b-jpeg').onclick = () => jpegs([{key: items[at].key, file: current()}], 2048, 'cropmsg');

// ---------------------------------------------------------------- cropping (into a new file)
function stopCrop(reload) {
  const was = cropping;
  cropping = false; rect = null; drag = null;
  document.body.classList.remove('cropping');
  $('b-crop').setAttribute('aria-pressed', 'false'); $('b-crop').textContent = 'Crop…';
  $('shape').hidden = true; $('b-cropsave').hidden = true; $('b-cropsave').disabled = true; $('b-cropclear').hidden = true;
  $('cropbox').hidden = true;
  if (was && reload && items[at]) $('big').src = bigURL(items[at], current(), 'edited');
}
// the picture's size as turned (the crop is marked on the turned picture)
function turnedPixels() { const t = (info && info.edit && info.edit.turns) || 0; return t % 2 ? [info.pixels[1], info.pixels[0]] : info.pixels; }
function startCrop() {
  if (!info) return;
  setCompare(false);
  viewOrig = false; drawOrig();
  cropping = true;
  document.body.classList.add('cropping');
  $('b-crop').setAttribute('aria-pressed', 'true'); $('b-crop').textContent = 'Cancel';
  $('shape').hidden = false; $('b-cropsave').hidden = false;
  rect = (info.edit && info.edit.crop) ? info.edit.crop.slice() : null;
  $('b-cropclear').hidden = !rect;
  const img = $('big');
  img.onload = () => { $('loading').hidden = true; drawBox(); };
  img.src = bigURL(items[at], current(), 'turned');   // the whole picture, so the crop can be made larger again
  say('cropmsg', 'Drag on the picture to mark the part to keep, then press Done. The file itself is never changed.'
    + (info.raw ? ' For more than the scan kept, use Crop from RAW.' : ''));
}
$('b-crop').onclick = () => { if (cropping) { stopCrop(true); say('cropmsg', ''); } else startCrop(); };
$('b-cropclear').onclick = async () => { const p = items[at]; stopCrop(); await changed(await post('/api/library/crop', {key: p.key, file: current(), rect: null}), 'Crop taken off.'); };
$('b-autocrop').onclick = async () => {
  const p = items[at], file = current();
  stopCrop();
  $('b-autocrop').disabled = true;
  say('cropmsg', 'Looking for the edges of the picture…');
  const r = await post('/api/library/autocrop', {key: p.key, file, save: true});
  $('b-autocrop').disabled = false;
  if (items[at] !== p || current() !== file) return;
  if (!r.ok) { say('cropmsg', r.error || 'Auto crop did not work.', 'warn'); return; }
  if (!r.found) { say('cropmsg', 'Auto crop found no border to cut on this picture; it is as it was.'); return; }
  await changed(r, (r.learned ? 'Auto crop applied, the way it learned from your crops. ' : 'Auto crop applied (built-in rule). ') + 'Crop… to change it, Show original to compare, Reset to take it off. The file is unchanged.');
};
function frac(ev) {
  const b = $('big').getBoundingClientRect();
  return [Math.min(1, Math.max(0, (ev.clientX - b.left) / b.width)), Math.min(1, Math.max(0, (ev.clientY - b.top) / b.height))];
}
function drawBox() {
  const box = $('cropbox');
  if (!rect) { box.hidden = true; return; }
  const img = $('big'), w = img.clientWidth, h = img.clientHeight;
  box.hidden = false;
  box.style.left = rect[0] * w + 'px'; box.style.top = rect[1] * h + 'px';
  box.style.width = (rect[2] - rect[0]) * w + 'px'; box.style.height = (rect[3] - rect[1]) * h + 'px';
  const ok = rect[2] - rect[0] >= 0.05 && rect[3] - rect[1] >= 0.05;
  $('b-cropsave').disabled = !ok;
  if (info && ok) {
    const px = turnedPixels(), cw = Math.round((rect[2] - rect[0]) * px[0]), ch = Math.round((rect[3] - rect[1]) * px[1]);
    say('cropmsg', 'Crop: ' + cw + ' × ' + ch + ' pixels (' + Math.round(100 * cw * ch / (px[0] * px[1])) + '% of the picture). Press Done to keep it.');
  }
}
$('imgwrap').addEventListener('pointerdown', ev => {
  if (!cropping || !info) return;
  ev.preventDefault();
  drag = frac(ev);
  rect = [drag[0], drag[1], drag[0], drag[1]];
  $('imgwrap').setPointerCapture(ev.pointerId);
  drawBox();
});
$('imgwrap').addEventListener('pointermove', ev => {
  if (!drag) return;
  let [x, y] = frac(ev);
  const shape = parseFloat($('shape').value);
  if (shape && info) {
    // keep the shape in pixels, either way round (wide when the drag is wider than tall)
    const [W, H] = turnedPixels();
    let dw = Math.abs(x - drag[0]) * W, dh = Math.abs(y - drag[1]) * H;
    const ratio = dw >= dh ? shape : 1 / shape;
    if (dw / Math.max(dh, 1) > ratio) dw = dh * ratio; else dh = dw / ratio;
    x = drag[0] + Math.sign(x - drag[0]) * dw / W; y = drag[1] + Math.sign(y - drag[1]) * dh / H;
    x = Math.min(1, Math.max(0, x)); y = Math.min(1, Math.max(0, y));
  }
  rect = [Math.min(drag[0], x), Math.min(drag[1], y), Math.max(drag[0], x), Math.max(drag[1], y)];
  drawBox();
});
$('imgwrap').addEventListener('pointerup', () => { drag = null; });
window.addEventListener('resize', drawBox);
$('b-cropsave').onclick = async () => {
  if (!rect) return;
  const p = items[at], r0 = rect;
  stopCrop();
  await changed(await post('/api/library/crop', {key: p.key, file: current(), rect: r0}), 'Cropped. The file stays as it is; Export makes a copy with the crop.'
    + (learnState && learnState.on ? ' ✓ Auto crop will learn from this crop.' : ''));
  learnShow();
};
$('b-raw').onclick = async () => {
  say('cropmsg', 'Opening the RAW file on the Re-develop page…');
  const p = items[at];
  const r = await post('/api/library/redevelop', {key: p.key, file: p.file});
  if (r.ok) location.href = '/develop';
  else say('cropmsg', r.error || 'Could not open it.', 'warn');
};

// ---------------------------------------------------------------- keys
document.addEventListener('keydown', ev => {
  if (ev.target.matches('textarea, input, select') || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (!$('viewer').hidden) {
    const p = items[at], k = ev.key.toLowerCase();
    if (ev.key === 'Escape') { if (cropping) { stopCrop(true); say('cropmsg', ''); } else closeViewer(); }
    else if (ev.key === 'ArrowLeft') $('b-prev').click();
    else if (ev.key === 'ArrowRight' || ev.key === ' ') $('b-next').click();
    else if (k === 'c' && !$('b-crop').hidden) $('b-crop').click();
    else if (k === 'a' && !$('b-autocrop').hidden) $('b-autocrop').click();
    else if (k === 'o' && !$('b-orig').hidden) $('b-orig').click();
    else if (k === 'v' && !$('b-compare').hidden) $('b-compare').click();
    else if (/^[0-5]$/.test(ev.key)) edit([p], {stars: +ev.key}, 'cropmsg');
    else if (k === 'p') edit([p], {flag: p.flag === 'pick' ? null : 'pick'}, 'cropmsg');
    else if (k === 'x') edit([p], {flag: p.flag === 'reject' ? null : 'reject'}, 'cropmsg');
    else if (k === 'u') edit([p], {flag: null}, 'cropmsg');
    else if (k === 'r') edit([p], {redo: !p.redo}, 'cropmsg');
    else return;
    ev.preventDefault();
  } else if (ev.key === 'r' || ev.key === 'R') loadRolls();
  else if ((ev.key === 'a' || ev.key === 'A') && roll) $('b-selall').click();
  else if (ev.key === 'Escape' && ticked.size) $('b-selnone').click();
});
$('b-reload').onclick = async () => { await loadRolls(); if (searching != null) runSearch(searching); else if (roll) openRoll(roll.key, null, true); };
window.addEventListener('beforeunload', () => { saveDesc(true); });

(async () => {
  await loadRolls();
  const w = where();
  if (w.q) { $('search').value = w.q; return runSearch(w.q, w.pic, w.roll); }
  const key = w.roll != null && rolls.some(r => r.key === w.roll) ? w.roll : (rolls.find(r => r.current) || rolls[rolls.length - 1] || {}).key;
  if (key != null) openRoll(key, w.pic);
})();

// ---------------------------------------------------------------- the learning auto crop, beside the picture
const SHORT = {simple: 'simple', net: 'net', cnn: 'CNN'};
const KIND = {simple: 'Simple model', net: 'Small neural network', cnn: 'Picture network (CNN)', rule: 'Built-in rule'};
function pc(v) { return v === null || v === undefined ? '–' : (v * 100).toFixed(1) + '%'; }
function escL(t) { return String(t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c])); }
function learnReport(r) {
  const m = r.model, rows = [];
  if (m) {
    const e = m.errors || {}, w = m.within_1pc || {};
    rows.push(['Learned from', m.pictures + ' crops (' + m.sides + ' sides), ' + escL(m.made.slice(0, 16)) + ', ' + escL(m.why || '')]);
    rows.push(['In use', r.use ? KIND[r.running_kind] || r.running_kind : 'Built-in rule (nothing learned does better yet)']);
    let t = '<table class="clr"><tr><th></th><th>Off by</th><th>Within 1%</th></tr>';
    for (const k of ['simple', 'net', 'cnn']) if (e[k] !== undefined && e[k] !== null) t += '<tr><td>' + KIND[k] + (k === m.kind ? ' ✓' : '') + '</td><td>' + pc(e[k]) + '</td><td>' + pc(w[k]) + '</td></tr>';
    t += '<tr><td>' + KIND.rule + '</td><td>' + pc(m.error_rule) + '</td><td>' + pc(w.rule) + '</td></tr></table>';
    rows.push(['How close to your crops', t + '<p class="hint">Edges tried on crops each model was not trained on (5 rounds), as a share of the frame.' + (m.cnn_tried ? '' : ' The picture network is tried from 12 crops' + (r.cnn_available ? '' : ' and needs MLX') + '.') + '</p>']);
    const u = m.used || {};
    rows.push(['Last learning took', u.seconds + ' s (processor time ' + u.cpu_seconds + ' s) on the ' + escL(u.device || '') +
      '<br>App memory at most ' + u.memory_mb + ' MB' + (u.memory_added_mb ? ' (+' + u.memory_added_mb + ' MB while learning)' : '') +
      (u.gpu_memory_mb !== null && u.gpu_memory_mb !== undefined ? '<br>GPU memory at most ' + u.gpu_memory_mb + ' MB' : '')]);
  }
  rows.push(['Cropping with it', r.crops_made ? r.crops_made + ' frames since the app started, ' + r.ms_per_crop + ' ms each' : 'not yet since the app started']);
  rows.push(['Kept', r.hand_crops + ' crops from the scanning screen (plus the Library\'s), ' + r.new + ' new since it last learned; ' + r.store_mb + ' MB in <code>' + escL(r.store) + '</code>']);
  const h = r.history || [];
  if (h.length > 1) {
    let t = '<table class="clr"><tr><th>When</th><th>Crops</th><th>Used</th><th>Off by</th><th>Rule</th></tr>';
    for (const x of h.slice(-10).reverse()) {
      const best = (x.errors || {})[x.kind];
      t += '<tr><td>' + escL((x.made || '').slice(5, 16)) + '</td><td>' + x.pictures + '</td><td>' + (x.use ? (SHORT[x.kind] || x.kind) : 'rule') + '</td><td>' + pc(best) + '</td><td>' + pc(x.error_rule) + '</td></tr>';
    }
    rows.push(['Earlier runs', t + '</table>']);
  }
  return rows.map(([k, v]) => '<div class="clrow"><b>' + k + '</b><div>' + v + '</div></div>').join('');
}
let learnState = null, learnTimer = null;
async function learnShow() {
  let r; try { r = await (await fetch('/api/croplearn')).json(); } catch (e) { return; }
  learnState = r;
  const line = !r.on ? 'Off (Additional settings in the scanning window): your crops are not learned from.'
    : r.busy ? 'Learning from your crops…'
    : (r.use ? 'Auto crop crops your way: ' : 'Auto crop uses the built-in rule for now. ') + (r.model ? 'learned from ' + r.model.pictures + ' crops' + (r.use ? ' with the ' + (KIND[r.running_kind] || '').toLowerCase() : '') + '. ' : '')
      + (r.new ? r.new + ' new crop' + (r.new === 1 ? '' : 's') + ' since it last learned; it learns again by itself at 10.' : '')
      + (r.model ? '' : ' Crop at least ' + r.least + ' pictures, then press Learn now.');
  $('learnline').textContent = line;
  $('b-learnnow').disabled = !!r.busy || !r.on;
  $('b-learnnow').textContent = r.busy ? 'Learning…' : 'Learn now';
  clearTimeout(learnTimer);
  if (r.busy) learnTimer = setTimeout(learnShow, 2000);
  if (!$('learnrep').hidden) $('learnrep').innerHTML = learnReport(r);
}
$('b-learnnow').onclick = async () => { $('b-learnnow').disabled = true; await post('/api/croplearn', {action: 'learn'}); setTimeout(learnShow, 300); };
$('b-learnrep').onclick = () => { const b = $('learnrep'); b.hidden = !b.hidden; $('b-learnrep').setAttribute('aria-expanded', String(!b.hidden)); learnShow(); };
learnShow();

// the learning auto crop: learn now from the crops made so far (what it learned shows in the Activity list
// and in the scanning window's Additional settings › Learning report)
$('a-croplearn').onclick = async () => {
  const b = $('a-croplearn'); b.disabled = true; b.textContent = 'Learning…'; say('actmsg', 'Learning from your crops…');
  await post('/api/croplearn', {action: 'learn'});
  const done = async () => {
    let r = {}; try { r = await (await fetch('/api/croplearn')).json(); } catch (e) { /* try again */ }
    if (r.busy) return setTimeout(done, 2000);
    b.disabled = false; b.textContent = 'Learn from my crops'; b.title = r.text || b.title;
    if (r.text) say('actmsg', r.text, 'ok');
    learnShow();
  };
  setTimeout(done, 500);
};
