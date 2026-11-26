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
const esig = p => (p.edit ? JSON.stringify(p.edit) : '') + (p.fc ? '~' + p.fc : '');   // fc: the roll's film colour
let picking = false;   // picking the film base on the frame as scanned
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
    if (it.rescan) { const t = el('span', 'rtag', '🎞 ' + it.rescan); t.title = it.rescan + ' tagged for rescan'; nm.appendChild(t); }
    b.appendChild(nm);
    const dates = it.first ? (it.first === it.last ? it.first : it.first + ' – ' + it.last) : '';
    b.appendChild(el('span', 'rsub', it.count + ' picture' + (it.count === 1 ? '' : 's') + (it.raw ? ' · RAW' : '') + (dates ? ' · ' + dates : '')));
    if (it.description) b.appendChild(el('span', 'rdesc', it.description));
    b.onclick = () => { $('search').value = ''; openRoll(it.key); };
    li.appendChild(b); ul.appendChild(li);
  }
  markRoll();
  const rescans = rolls.reduce((n, it) => n + (it.rescan || 0), 0);
  $('rescanlink').textContent = rescans ? '🎞 Rescan list (' + rescans + ')' : '🎞 Rescan list';
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
  drawFilmColour(r.film_colour);
  $('rdate').value = ''; drawRollDate(true);
  $('rcamrow').hidden = false; $('rcam').value = savedCam = r.camera || ''; $('b-rcam').disabled = true; say('rcammsg', '');
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
  $('fcolrow').hidden = true; $('rcamrow').hidden = true;
  drawRollDate();
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
  if (flag === 'rescan' && !p.rescan) return false;
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
    if (p.rescan) rate.appendChild(el('span', 'rs', '🎞 rescan'));
    if (p.edit) { const e = el('span', 'ed', '✂ edited'); e.title = 'Shown with your crop and turn. The file itself is unchanged; Export makes a copy with them.'; rate.appendChild(e); }
    b.appendChild(rate);
    const marks = [];
    if (roll.search) marks.push(p.roll);
    if (p.taken) marks.push(p.taken.slice(0, 10) + (p.date_note ? ' (' + p.date_note + ')' : ''));
    if (p.aperture || p.shutter) marks.push([niceAperture(p.aperture), niceShutter(p.shutter)].filter(Boolean).join(' '));
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
  const s = n > 1 ? 's' : '';
  if ('rescan' in change && Object.keys(change).length === 1)
    say(msgBox, change.rescan ? n + ' picture' + s + ' tagged for rescan. The Rescan list at the top shows them by roll.'
      : 'Rescan tag taken off ' + n + ' picture' + s + '.', 'ok');
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
  drawRollDate();
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
// Export: the dialog (export.js) asks where, what the files are called, what kind, how large and with what metadata
const exportItems = pics => pics.map(p => [p.key, p.file]);
const exportRoll = () => (roll && !roll.search && roll.key != null ? {roll: {key: roll.key, name: roll.name || roll.key || 'Loose pictures', count: roll.pictures.length}} : {});
$('b-exportroll').onclick = () => {   // the whole roll; in search results, the pictures found
  const found = roll && roll.search;
  CoolscanExport.open(found ? exportItems(items) : [], async (text, kind, s) => { if (s && s.saved) await refresh(); say('shown', text, kind); }, found ? {shown: true} : exportRoll());
};
$('a-exportdlg').onclick = () => CoolscanExport.open(exportItems(tickedPics()), async (text, kind, s) => { say('actmsg', text, kind); if (s && s.saved) await refresh(); }, exportRoll());
$('a-exportprev').onclick = () => CoolscanExport.again(exportItems(tickedPics()), async (text, kind, s) => { say('actmsg', text, kind); if (s && s.saved) await refresh(); });
$('a-redonow').onclick = async () => {
  const sel = tickedPics();
  if (!sel.length) return;
  const keys = [...new Set(sel.map(p => p.key))];
  if (keys.length > 1) { say('actmsg', 'Tick pictures of one folder at a time: the Re-develop page works on one RAW folder.', 'warn'); return; }
  const noRaw = sel.filter(p => !p.raw);
  if (noRaw.length === sel.length) {
    say('actmsg', 'None of the ticked pictures has a RAW file (only Archive mode keeps one), so they cannot be developed again. ', 'warn');
    const b = el('button', null, '🎞 Tag ' + (sel.length > 1 ? 'them' : 'it') + ' for rescan');
    b.onclick = () => edit(sel, {rescan: true});
    $('actmsg').appendChild(b);
    return;
  }
  const r = await post('/api/library/to_develop', {key: keys[0], files: sel.map(p => p.file)});
  if (r.ok) location.href = '/develop';
  else say('actmsg', r.error || 'Could not open them.', 'warn');
};
$('a-unredo').onclick = () => edit(tickedPics(), {rescan: false});
$('a-rescan').onclick = () => edit(tickedPics(), {rescan: true});

// ---------------------------------------------------------------- pictures to scan again, by roll
let rescanData = null;
function rescanText() {
  const lines = [];
  for (const r of rescanData.rolls) {
    lines.push(r.name + (r.film ? ' (' + r.film + ')' : '') + ': ' + r.pictures.length + ' picture' + (r.pictures.length > 1 ? 's' : ''));
    for (const p of r.pictures) lines.push('  ' + (p.frame != null ? 'Frame ' + p.frame + '  ' : '') + p.name + (p.caption ? '  ' + p.caption : ''));
    lines.push('');
  }
  return lines.join('\n');
}
async function openRescan() {
  $('rescanview').hidden = false;
  say('rsmsg', 'Loading…');
  const r = await getJSON('/api/library/rescan');
  rescanData = r.ok ? r : {rolls: [], total: 0};
  const body = $('rsbody'); body.textContent = '';
  say('rsmsg', rescanData.total ? rescanData.total + ' picture' + (rescanData.total > 1 ? 's' : '') + ' in ' + rescanData.rolls.length + ' roll' + (rescanData.rolls.length > 1 ? 's' : '')
    : 'Nothing is tagged for rescan. Tag pictures with 🎞 Rescan in the picture view (S), or tick them and press 🎞 Tag for rescan.');
  for (const r of rescanData.rolls) {
    const sec = el('section');
    sec.appendChild(el('h3', null, r.name + ' · ' + r.pictures.length + ' picture' + (r.pictures.length > 1 ? 's' : '')));
    sec.appendChild(el('p', 'rsfilm', [r.film, r.description].filter(Boolean).join(' · ') || r.path));
    const grid = el('div', 'rsgrid');
    for (const p of r.pictures) {
      const card = el('div', 'rsp'), ph = el('div', 'ph'), img = el('img');
      img.loading = 'lazy'; img.alt = p.name; img.draggable = false;
      img.src = '/library/small.png?' + q({key: r.key, file: p.file, v: p.v, e: p.edit ? JSON.stringify(p.edit) : ''});
      ph.appendChild(img); ph.title = 'Open in Scanned pictures';
      ph.onclick = () => { $('rescanview').hidden = true; $('search').value = ''; openRoll(r.key, p.file); };
      card.appendChild(ph);
      card.appendChild(el('b', null, p.frame != null ? 'Frame ' + p.frame : p.name));
      const nm = el('span', 'nm', p.name); nm.title = p.name; card.appendChild(nm);
      if (p.caption) card.appendChild(el('span', 'nm', p.caption));
      const un = el('button', null, 'Done, untag');
      un.onclick = async () => { const x = await post('/api/library/edit', {key: r.key, files: [p.file], rescan: false}); if (x.ok) { openRescan(); loadRolls(); } };
      card.appendChild(un);
      grid.appendChild(card);
    }
    sec.appendChild(grid); body.appendChild(sec);
  }
}
function closeRescan() { $('rescanview').hidden = true; if (roll) refresh(); }
$('rescanlink').onclick = openRescan;
$('rs-close').onclick = closeRescan;
$('rs-print').onclick = () => window.print();
$('rs-copy').onclick = async () => {
  const text = rescanText();
  try { await navigator.clipboard.writeText(text); say('rsmsg', 'The list is copied.', 'ok'); }
  catch (e) {   // no clipboard here: show the text to copy by hand
    let ta = $('rstext');
    if (!ta) { ta = el('textarea'); ta.id = 'rstext'; ta.readOnly = true; $('rsbody').prepend(ta); }
    ta.value = text; ta.select(); say('rsmsg', 'Press ⌘C to copy the list.');
  }
};
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
// ---------------------------------------------------------------- the camera the roll's film was in
let savedCam = '';
$('rcam').addEventListener('input', () => { $('b-rcam').disabled = $('rcam').value.trim() === savedCam; });
$('rcam').addEventListener('keydown', ev => { if (ev.key === 'Enter' && !$('b-rcam').disabled) $('b-rcam').click(); });
$('b-rcam').onclick = async () => {
  if (!roll || roll.search) return;
  say('rcammsg', 'Saving…');
  const r = await post('/api/library/camera', {key: roll.key, camera: $('rcam').value});
  if (!r.ok) { say('rcammsg', r.error || 'Could not save.', 'warn'); return; }
  roll.camera = $('rcam').value = savedCam = r.camera; $('b-rcam').disabled = true;
  const viewing = !$('viewer').hidden, inApp = (r.problems || []).length;
  await refresh();
  if (viewing && items[at]) await show(at, alt);
  say('rcammsg', (r.camera ? 'Camera saved' : 'Camera taken out') + ' for this roll, in the app and in ' + r.changed + ' file' + (r.changed === 1 ? '' : 's')
    + (inApp ? '. For ' + inApp + ' it is kept in the app only (RAW files and files Lightroom has written into are not changed); exports get it all the same' : '') + '.', inApp ? 'warn' : 'ok');
};
// aperture and shutter speed as the page shows them
function niceAperture(f) { return f ? 'ƒ/' + f : ''; }
function niceShutter(s) { return s ? s + ' s' : ''; }
// ---------------------------------------------------------------- when the whole roll was taken
function niceDay(d) { const [y, m, n] = d.split('-').map(Number); return n + ' ' + ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][m - 1] + ' ' + y; }
function rollDatePics() { return roll && !roll.search ? roll.pictures.filter(p => !p.file.startsWith('RAW/') || p.only_raw) : []; }
function drawRollDate(fresh) {
  const pics = rollDatePics(), show = pics.length > 0;
  $('rdaterow').hidden = $('rdatehelp').hidden = !show;
  if (!show) return;
  const own = pics.filter(p => p.taken_own).length, rest = pics.filter(p => !p.taken_own);
  const days = [...new Set((rest.length ? rest : pics).map(p => (p.taken || '').slice(0, 10)).filter(Boolean))].sort();
  $('rdateownl').hidden = !own; $('rdateownn').textContent = own + ' picture' + (own === 1 ? '' : 's');
  if (!own) $('rdateown').checked = false;
  $('b-rdate').textContent = pics.length === 1 ? 'Set for the picture' : 'Set for all ' + pics.length + ' pictures';
  if (fresh) say('rdatemsg', !days.length ? 'Now: no date in the files.' : 'Now: ' + (days.length === 1 ? niceDay(days[0]) : niceDay(days[0]) + ' to ' + niceDay(days[days.length - 1]))
    + (own && rest.length ? '; ' + own + ' with a date of their own' : '') + '.');
}
function readRollDate(text) {
  text = text.trim();
  let m;
  if (/^\d{4}$/.test(text)) return {how: 'year', value: text, words: text};
  if ((m = /^(\d{4})[-./ ](\d{1,2})[-./ ](\d{1,2})$/.exec(text))) m = [m[1], m[2], m[3]];
  else if ((m = /^(\d{1,2})[-./ ](\d{1,2})[-./ ](\d{4})$/.exec(text))) m = [m[3], m[2], m[1]];   // day first
  else return null;
  const [y, mo, d] = m.map(Number), day = new Date(Date.UTC(y, mo - 1, d));
  if (y < 1800 || y > 2100 || day.getUTCMonth() !== mo - 1 || day.getUTCDate() !== d) return null;
  const iso = y + '-' + String(mo).padStart(2, '0') + '-' + String(d).padStart(2, '0');
  return {how: 'exact', value: iso + 'T12:00', words: niceDay(iso)};
}
$('b-rdate').onclick = async () => {
  const pics = rollDatePics(), got = readRollDate($('rdate').value);
  if (!got) { say('rdatemsg', 'Type a year (1988) or a day (1988-02-06 or 6.2.1988) first.', 'warn'); $('rdate').focus(); return; }
  say('rdatemsg', 'Saving…');
  const r = await post('/api/library/edit', {key: roll.key, files: pics.map(p => p.file), date: {how: got.how, value: got.value, roll: true, all: $('rdateown').checked}});
  if (!r.ok) { say('rdatemsg', r.error || 'Could not save.', 'warn'); return; }
  const viewing = !$('viewer').hidden;
  await refresh();
  if (viewing && items[at]) await show(at, alt);
  const s = r.changed === 1 ? '' : 's', inApp = (r.problems || []).length;
  say('rdatemsg', 'Photo taken is now ' + got.words + ' for ' + r.changed + ' picture' + s
    + (got.how === 'year' ? ' (month, day and time kept)' : '')
    + (r.left ? '; ' + r.left + ' with a date of their own left as they are' : '')
    + (inApp ? '. For ' + inApp + ' the date is kept in the app only (RAW files and files Lightroom has written into are not changed)' : '') + '.', inApp ? 'warn' : 'ok');
};
$('rdate').addEventListener('keydown', ev => { if (ev.key === 'Enter') $('b-rdate').click(); });

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

// ---------------------------------------------------------------- the roll's colour of negatives (FilmProfile.json in its folder)
function drawFilmColour(fc) {
  const row = $('fcolrow'), sel = $('fcol');
  row.hidden = !fc;
  if (!fc) return;
  sel.textContent = '';
  const add = (v, t) => { const o = el('option', null, t); o.value = v; sel.appendChild(o); };
  add('frame', 'Each frame on its own (as saved)');
  add('roll', "This roll's own frames (" + fc.measured + ' measured)');
  if (fc.mode === 'base' || fc.has_base) add('base', 'Film base picked' + (fc.base_from ? ' on ' + fc.base_from.split('/').pop() : ''));
  for (const n of fc.profiles) add('profile:' + n, 'Film profile: ' + n);
  if (fc.mode === 'profile' && fc.name && !fc.profiles.includes(fc.name)) add('profile:' + fc.name, 'Film profile: ' + fc.name + ' (kept with this roll)');
  sel.value = fc.mode === 'profile' ? 'profile:' + fc.name : fc.mode;
  let words = '';
  if (!fc.raw) words = 'Shown only on pictures with a RAW file (Archive mode): this folder has none.';
  else if (fc.mode === 'frame') words = 'Pictures are shown as they were saved. For an unusual film (Orwocolor…) open a picture and press Film base….';
  else if (fc.mode === 'roll' && !fc.numbers) words = 'Needs ' + fc.least + ' measured frames: shown as saved until then.';
  else words = 'Pictures with a RAW file are shown and exported with this colour. The files are not changed; "Show original" shows them as saved.';
  say('fcolmsg', words);
}
$('fcol').onchange = async () => {
  if (!roll || roll.key == null) return;
  const v = $('fcol').value, mode = v.startsWith('profile:') ? 'profile' : v;
  say('fcolmsg', 'Saving…');
  const r = await post('/api/library/film_colour', {key: roll.key, mode, name: mode === 'profile' ? v.slice(8) : ''});
  if (!r.ok) { say('fcolmsg', r.error || 'That did not work.', 'warn'); drawFilmColour(roll.film_colour); return; }
  stamp++;
  await refresh();
  drawFilmColour(r.film_colour);
  if (!$('viewer').hidden && items[at]) show(at, alt);
};

// ---------------------------------------------------------------- one picture
function current() { return alt || items[at].file; }
const bigURL = (p, file, view) => '/library/big.png?' + q({key: p.key, file, v: p.v, view: view || 'edited', s: stamp, e: file === p.file ? esig(p) : ''});
async function show(i, other) {
  if (i < 0 || i >= items.length) return;
  if (i !== at || (other || null) !== alt) viewOrig = false;
  at = i; alt = other || null;
  stopBase();
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
  fitStage();
  fillCompare(p);
  remember();
  const r = await getJSON('/api/library/info?' + q({key: p.key, file}));
  if (items[at] !== p || current() !== file) return;
  info = r.ok ? r : null;
  const isRaw = file.startsWith('RAW/');
  const s0 = (info && info.scan) || {};
  $('b-base').hidden = !(info && info.raw && (!s0.film || s0.film === 'negative') && !roll.search);
  const fc0 = info && info.film_colour;
  $('b-own').hidden = !(fc0 && info.raw && !isRaw && (fc0.mode !== 'frame' || fc0.own) && (!s0.film || s0.film === 'negative'));
  $('b-own').setAttribute('aria-pressed', String(!!(fc0 && fc0.own)));
  $('b-crop').hidden = $('b-turnl').hidden = $('b-turnr').hidden = isRaw;  // a RAW file is cropped on the Re-develop page
  drawOrig();
  drawInfo(r);
  turnGuess(p, file);
  // the next picture is made ready while this one is looked at
  if (i + 1 < items.length) { const n = new Image(); n.src = bigURL(items[i + 1], items[i + 1].file); }
}
function closeViewer() {
  stopBase();
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
  const mine = info && info.file === current(), e = mine ? info.edit : null, b = $('b-orig'), tag = $('origtag');
  const fc = mine && info.film_colour && info.film_colour.applied;
  b.hidden = !e && !fc;
  $('b-reset').hidden = !e;
  b.textContent = viewOrig ? 'Show edited' : 'Show original';
  b.setAttribute('aria-pressed', String(viewOrig));
  tag.hidden = (!e && !fc) || !viewOrig;
  tag.textContent = 'The original';
  $('sep1').hidden = !e && !fc;
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
$('b-export').onclick = () => {
  const p = items[at];
  CoolscanExport.open([[p.key, current()]], async (text, kind, s) => { say('cropmsg', text, kind); if (s && s.saved) await refresh(); }, exportRoll());
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
  const rs = el('button', null, '🎞 Rescan'); rs.setAttribute('aria-pressed', String(!!p.rescan));
  rs.title = 'Tag to scan again from the film; listed by roll in the Rescan list (S)';
  rs.onclick = () => edit([p], {rescan: !p.rescan}, 'cropmsg');
  flags.appendChild(rs);
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
  // how the photo was exposed: aperture and shutter speed (the camera belongs to the whole roll)
  const r4 = el('div', 'erow exposure');
  const al = el('label', 'hint'); al.appendChild(el('span', 'fsym', 'ƒ/')); al.title = 'Aperture (f-number), e.g. 5.6';
  const ap = el('input'); ap.type = 'text'; ap.inputMode = 'decimal'; ap.placeholder = '5.6'; ap.value = p.aperture || ''; ap.style.width = '64px';
  ap.setAttribute('aria-label', 'Aperture (f-number)');
  ap.onchange = async () => { if (await edit([p], {aperture: ap.value}, 'cropmsg') && items[at]) { const said = $('cropmsg').textContent, kind = $('cropmsg').className; await show(at, alt); $('cropmsg').className = kind; $('cropmsg').textContent = said; } };
  al.appendChild(ap); r4.appendChild(al);
  const sl = el('label', 'hint', 'Shutter '); sl.title = 'Shutter speed: 1/125, or 125 as on the camera\'s dial; 2s for whole seconds';
  const sh = el('input'); sh.type = 'text'; sh.placeholder = '1/125'; sh.value = p.shutter || ''; sh.style.width = '84px';
  sh.setAttribute('aria-label', 'Shutter speed');
  sh.onchange = async () => { if (await edit([p], {shutter: sh.value}, 'cropmsg') && items[at]) { const said = $('cropmsg').textContent, kind = $('cropmsg').className; await show(at, alt); $('cropmsg').className = kind; $('cropmsg').textContent = said; } };
  sl.appendChild(sh); sl.appendChild(el('span', null, ' s')); r4.appendChild(sl);
  box.appendChild(r4);
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
  row(dl, 'Camera', r.camera);
  row(dl, 'Aperture', niceAperture(r.aperture));
  row(dl, 'Shutter speed', niceShutter(r.shutter));
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
  const fcol = r.film_colour;
  if (fcol && (fcol.mode !== 'frame' || fcol.why || fcol.own)) {
    dl = section('Colour of negatives');
    row(dl, 'This roll', fcol.words);
    row(dl, 'This picture', fcol.own && !fcol.applied ? 'its own colour, not the roll\'s' + (fcol.why ? '. ' + fcol.why : '') : fcol.applied && fcol.own ? 'shown with its own colour, not the roll\'s (the file is not changed)' : fcol.applied ? 'shown with it (the file is not changed)' + (fcol.tone ? ', ' + (fcol.tone === 'nikon' ? 'Nikon Scan' : 'flat') + ' tone kept' : '')
      : (fcol.why || 'shown as saved'));
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

// ---------------------------------------------------------------- cropping (into a new file)
function stopCrop(reload) {
  const was = cropping;
  cropping = false; rect = null; drag = null;
  document.body.classList.remove('cropping');
  $('b-crop').setAttribute('aria-pressed', 'false'); $('b-crop').textContent = 'Crop';
  $('shape').hidden = true; $('b-cropsave').hidden = true; $('b-cropsave').disabled = true; $('b-cropclear').hidden = true;
  $('cropbox').hidden = true; $('cropdim').hidden = true;
  $('b-cropadv').hidden = true; $('cropnums').hidden = true; $('loupe').hidden = true;
  document.body.classList.remove('cropadv');
  if (was && reload && items[at]) $('big').src = bigURL(items[at], current(), 'edited');
}
// the picture's size as turned (the crop is marked on the turned picture)
function turnedPixels() { const t = (info && info.edit && info.edit.turns) || 0; return t % 2 ? [info.pixels[1], info.pixels[0]] : info.pixels; }
// Auto crop's guess (learned from your crops, or the built-in rule) as the box to start from; nothing is saved
async function autoBox() {
  const p = items[at], file = current();
  say('cropmsg', 'Finding the edges of the picture…');
  const r = await post('/api/library/autocrop', {key: p.key, file, save: false});
  if (!cropping || items[at] !== p || current() !== file || drag || grab || rect) return;
  const one = r.ok && r.pictures && r.pictures[0];
  if (one && one.found) {
    rect = one.rect.slice(); drawBox();
    say('cropmsg', 'The box is where auto crop puts it' + (r.learned ? ' (learned from your crops)' : '') + '. Drag its sides or corners to change it, then press Done.');
  } else say('cropmsg', 'Auto crop found no border here. Drag on the picture to mark the part to keep, then press Done.');
}
function startCrop() {
  if (!info) return;
  setCompare(false);
  viewOrig = false; drawOrig();
  cropping = true;
  document.body.classList.add('cropping');
  $('b-crop').setAttribute('aria-pressed', 'true'); $('b-crop').textContent = 'Cancel';
  $('shape').hidden = false; $('b-cropsave').hidden = false; $('b-cropadv').hidden = false;
  setAdvanced(advanced);
  rect = (info.edit && info.edit.crop) ? info.edit.crop.slice() : null;
  $('b-cropclear').hidden = !rect;
  if (!rect) autoBox();   // no crop yet: the box starts where auto crop puts it
  const img = $('big');
  img.onload = () => { $('loading').hidden = true; drawBox(); };
  img.src = bigURL(items[at], current(), 'turned');   // the whole picture, so the crop can be made larger again
  say('cropmsg', 'Drag the sides or corners of the box to change it, drag inside it to move it, or drag elsewhere for a new one. Then press Done. The file itself is never changed.');
}
$('b-crop').onclick = () => { if (cropping) { stopCrop(true); say('cropmsg', ''); } else startCrop(); };
$('b-cropclear').onclick = async () => { const p = items[at]; stopCrop(); await changed(await post('/api/library/crop', {key: p.key, file: current(), rect: null}), 'Crop taken off.'); };
function frac(ev) {
  const b = $('big').getBoundingClientRect();
  return [Math.min(1, Math.max(0, (ev.clientX - b.left) / b.width)), Math.min(1, Math.max(0, (ev.clientY - b.top) / b.height))];
}
function drawBox() {
  const box = $('cropbox');
  const dim = $('cropdim');
  if (advanced) drawAdvanced();
  if (!rect) { box.hidden = true; dim.hidden = true; return; }
  const img = $('big'), w = img.clientWidth, h = img.clientHeight;
  box.hidden = false; dim.hidden = false;
  for (const el of [box, dim.firstChild]) {
    el.style.left = rect[0] * w + 'px'; el.style.top = rect[1] * h + 'px';
    el.style.width = (rect[2] - rect[0]) * w + 'px'; el.style.height = (rect[3] - rect[1]) * h + 'px';
  }
  const ok = rect[2] - rect[0] >= 0.05 && rect[3] - rect[1] >= 0.05;
  $('b-cropsave').disabled = !ok;
  if (info && ok) {
    const px = turnedPixels(), cw = Math.round((rect[2] - rect[0]) * px[0]), ch = Math.round((rect[3] - rect[1]) * px[1]);
    say('cropmsg', 'Crop: ' + cw + ' × ' + ch + ' pixels (' + Math.round(100 * cw * ch / (px[0] * px[1])) + '% of the picture). Press Done to keep it.');
  }
}
// What a press on the picture grabs: a side or corner of the crop (its letters: l r t b), the inside
// of the crop (move it), or nothing (draw a new crop). Within 12 screen pixels of a side counts.
let grab = null;
function hitCrop(ev) {
  if (!rect) return '';
  const b = $('big').getBoundingClientRect(), x = ev.clientX - b.left, y = ev.clientY - b.top, tol = 12;
  const L = rect[0] * b.width, R = rect[2] * b.width, T = rect[1] * b.height, B = rect[3] * b.height;
  const inY = y > T - tol && y < B + tol, inX = x > L - tol && x < R + tol;
  let s = '';
  if (inY && Math.abs(x - L) < tol) s += 'l'; else if (inY && Math.abs(x - R) < tol) s += 'r';
  if (inX && Math.abs(y - T) < tol) s += 't'; else if (inX && Math.abs(y - B) < tol) s += 'b';
  if (!s && x > L && x < R && y > T && y < B) s = 'move';
  return s;
}
const CURSORS = {l: 'ew-resize', r: 'ew-resize', t: 'ns-resize', b: 'ns-resize', lt: 'nwse-resize', rb: 'nwse-resize', rt: 'nesw-resize', lb: 'nesw-resize', move: 'move'};
$('imgwrap').addEventListener('pointerdown', ev => {
  if (!cropping || !info) return;
  ev.preventDefault();
  const part = ev.target.closest ? ev.target.closest('#imgwrap [data-hit]') : null;
  let hit = (part && part.dataset.hit) || hitCrop(ev);
  const at0 = frac(ev);
  if (ev.target.id === 'rulx' || ev.target.id === 'ruly') {   // a press on a ruler brings the nearest edge there
    if (!rect) rect = [0, 0, 1, 1];
    hit = ev.target.id === 'rulx' ? (Math.abs(at0[0] - rect[0]) <= Math.abs(at0[0] - rect[2]) ? 'l' : 'r')
      : (Math.abs(at0[1] - rect[1]) <= Math.abs(at0[1] - rect[3]) ? 't' : 'b');
  }
  $('imgwrap').setPointerCapture(ev.pointerId);
  if (hit) {
    grab = {hit, at0, rect0: rect.slice()};
    if (part && part.classList.contains('kc-g')) part.classList.add('on');
    if (ev.target.id === 'rulx' || ev.target.id === 'ruly') moveGrab(ev);
    return;
  }
  grab = null;
  drag = at0;
  rect = [drag[0], drag[1], drag[0], drag[1]];
  drawBox();
});
$('imgwrap').addEventListener('pointermove', ev => {
  if (cropping && !drag && !grab) $('imgwrap').style.cursor = CURSORS[hitCrop(ev)] || 'crosshair';
  if (grab) { moveGrab(ev); return; }
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
$('imgwrap').addEventListener('pointerup', () => {
  drag = null; grab = null; $('loupe').hidden = true;
  for (const g of document.querySelectorAll('#imgwrap .kc-g.on')) g.classList.remove('on');
});
// the picture is kept clear of the bar of buttons under it: as tall as the space above the bar allows
function fitStage() {
  const st = $('stage'), bar = $('pictools');
  if (!st || $('viewer').hidden) return;
  const room = bar.offsetHeight + 22 + (cropping && advanced ? 26 : 0);
  st.style.paddingBottom = room + 'px';
  const h = Math.max(120, st.clientHeight - room - 14) + 'px';
  $('big').style.maxHeight = h; $('big2').style.maxHeight = h;
  if (cropping) drawBox();
}
window.addEventListener('resize', fitStage);
// dragging on the picture never selects or drags it like text or a file
for (const id of ['stage', 'strip']) {
  $(id).addEventListener('dragstart', ev => ev.preventDefault());
  $(id).addEventListener('selectstart', ev => ev.preventDefault());
}
$('stage').addEventListener('mousedown', ev => { if (!ev.target.closest('button, select, input')) ev.preventDefault(); });
if (window.ResizeObserver) { const ro = new ResizeObserver(fitStage); ro.observe($('stage')); ro.observe($('pictools')); }
function moveGrab(ev) {
  const [x, y] = frac(ev), g = grab, r = g.rect0.slice(), min = 0.05;
  if (g.hit === 'move') {
    const w = r[2] - r[0], h = r[3] - r[1];
    const nx = Math.min(1 - w, Math.max(0, r[0] + x - g.at0[0])), ny = Math.min(1 - h, Math.max(0, r[1] + y - g.at0[1]));
    rect = [nx, ny, nx + w, ny + h];
    drawBox(); return;
  }
  if (g.hit.includes('l')) r[0] = Math.min(x, r[2] - min);
  if (g.hit.includes('r')) r[2] = Math.max(x, r[0] + min);
  if (g.hit.includes('t')) r[1] = Math.min(y, r[3] - min);
  if (g.hit.includes('b')) r[3] = Math.max(y, r[1] + min);
  const shape = parseFloat($('shape').value);
  if (shape && g.hit.length === 2) {
    // a corner with a shape chosen: the height follows the width, the way round the crop already is
    const [W, H] = turnedPixels(), wide = (g.rect0[2] - g.rect0[0]) * W >= (g.rect0[3] - g.rect0[1]) * H;
    const hpx = (r[2] - r[0]) * W / (wide ? shape : 1 / shape);
    if (g.hit.includes('t')) r[1] = r[3] - hpx / H; else r[3] = r[1] + hpx / H;
  }
  rect = [Math.max(0, r[0]), Math.max(0, r[1]), Math.min(1, r[2]), Math.min(1, r[3])];
  drawBox();
  if (advanced) drawLoupe(ev, g.hit);
}

// ---------------------------------------------------------------- Advanced crop
let advanced = false;
try { advanced = localStorage.getItem('coolscan-crop-advanced') === '1'; } catch (e) { /* no storage */ }
function setAdvanced(on) {
  advanced = on;
  try { localStorage.setItem('coolscan-crop-advanced', on ? '1' : '0'); } catch (e) { /* no storage */ }
  $('b-cropadv').setAttribute('aria-pressed', String(on));
  document.body.classList.toggle('cropadv', on && cropping);
  $('cropnums').hidden = !(on && cropping);
  fitStage();
  drawBox();
}
$('b-cropadv').onclick = () => setAdvanced(!advanced);
// a step for the ruler's numbers that leaves room for them, in the picture's own pixels
function rulerStep(perPx) {
  for (const s of [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000]) if (s / perPx >= 56) return s;
  return 10000;
}
function drawRuler(cv, along, size, lo, hi) {
  const dpr = window.devicePixelRatio || 1, cs = getComputedStyle(document.body);
  const wide = cv.id === 'rulx', len = wide ? cv.clientWidth : cv.clientHeight, th = 22;
  if (!len) return;
  cv.width = (wide ? len : th) * dpr; cv.height = (wide ? th : len) * dpr;
  const c = cv.getContext('2d');
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!wide) { c.translate(th, 0); c.rotate(Math.PI / 2); }   // draw the side ruler like the top one, turned
  c.fillStyle = cs.getPropertyValue('--panel') || '#fff'; c.fillRect(0, 0, len, th);
  const crop = cs.getPropertyValue('--cropc') || '#22c55e';
  c.globalAlpha = 0.22; c.fillStyle = crop; c.fillRect(lo * len, 0, (hi - lo) * len, th); c.globalAlpha = 1;
  const perPx = size / len, step = rulerStep(perPx), minor = step / 5;
  c.strokeStyle = c.fillStyle = cs.getPropertyValue('--ink2') || '#555';
  c.font = '10px -apple-system, system-ui, sans-serif'; c.textBaseline = 'top'; c.lineWidth = 1;
  c.beginPath();
  for (let v = 0; v <= size + 0.5; v += minor) {
    const x = Math.round(v / perPx) + 0.5, major = Math.abs(v / step - Math.round(v / step)) < 1e-6;
    c.moveTo(x, th); c.lineTo(x, major ? 8 : th - 5);
    if (major && x < len - 20) c.fillText(String(Math.round(v)), x + 2, 1);
  }
  c.stroke();
  c.fillStyle = crop;
  for (const f of [lo, hi]) { const x = f * len; c.beginPath(); c.moveTo(x - 5, th); c.lineTo(x + 5, th); c.lineTo(x, th - 8); c.fill(); }
}
function drawAdvanced() {
  if (!info || !cropping) return;
  const img = $('big'), w = img.clientWidth, h = img.clientHeight, [W, H] = turnedPixels(), r = rect || [0, 0, 1, 1];
  const set = (cls, prop, v) => { $('imgwrap').querySelector(cls).style[prop] = v + 'px'; };
  set('.kc-gl', 'left', r[0] * w); set('.kc-gr', 'left', r[2] * w); set('.kc-gt', 'top', r[1] * h); set('.kc-gb', 'top', r[3] * h);
  drawRuler($('rulx'), 'x', W, r[0], r[2]);
  drawRuler($('ruly'), 'y', H, r[1], r[3]);
  const vals = {l: r[0] * W, r: (1 - r[2]) * W, t: r[1] * H, b: (1 - r[3]) * H}, of = {l: W, r: W, t: H, b: H};
  for (const k of 'lrtb') {
    const inp = $('cn-' + k);
    if (document.activeElement !== inp) inp.value = Math.round(vals[k]);
    inp.max = of[k];
    $('cp-' + k).textContent = (100 * vals[k] / of[k]).toFixed(1) + '%';
  }
}
for (const k of 'lrtb') {
  $('cn-' + k).addEventListener('input', () => {
    const v = parseFloat($('cn-' + k).value);
    if (!isFinite(v) || !info) return;
    const [W, H] = turnedPixels(), r = (rect || [0, 0, 1, 1]).slice(), min = 0.05;
    if (k === 'l') r[0] = Math.max(0, Math.min(v / W, r[2] - min));
    if (k === 'r') r[2] = Math.min(1, Math.max(1 - v / W, r[0] + min));
    if (k === 't') r[1] = Math.max(0, Math.min(v / H, r[3] - min));
    if (k === 'b') r[3] = Math.min(1, Math.max(1 - v / H, r[1] + min));
    rect = r; drawBox();
  });
  $('cn-' + k).addEventListener('change', () => drawAdvanced());
}
// the edge being dragged, four times larger, in a corner away from the pointer
function drawLoupe(ev, hit) {
  const img = $('big'), lp = $('loupe');
  if (!img.naturalWidth || hit === 'move') { lp.hidden = true; return; }
  const b = img.getBoundingClientRect(), st = $('stage').getBoundingClientRect();
  const fx = (hit.includes('l') ? rect[0] : hit.includes('r') ? rect[2] : (ev.clientX - b.left) / b.width);
  const fy = (hit.includes('t') ? rect[1] : hit.includes('b') ? rect[3] : (ev.clientY - b.top) / b.height);
  const zoom = 4, side = 180, srcSide = side / zoom * img.naturalWidth / b.width;
  const c = lp.getContext('2d');
  c.imageSmoothingEnabled = false;
  c.fillStyle = getComputedStyle(document.body).getPropertyValue('--vstage') || '#888';
  c.fillRect(0, 0, side, side);
  c.drawImage(img, fx * img.naturalWidth - srcSide / 2, fy * img.naturalHeight - srcSide / 2, srcSide, srcSide, 0, 0, side, side);
  c.strokeStyle = getComputedStyle(document.body).getPropertyValue('--cropc') || '#22c55e'; c.lineWidth = 1;
  c.beginPath();
  if (/[lr]/.test(hit)) { c.moveTo(side / 2 + 0.5, 0); c.lineTo(side / 2 + 0.5, side); }
  if (/[tb]/.test(hit)) { c.moveTo(0, side / 2 + 0.5); c.lineTo(side, side / 2 + 0.5); }
  c.stroke();
  lp.style.left = (ev.clientX - st.left < st.width / 2 ? st.width - side - 14 : 10) + 'px';
  lp.hidden = false;
}
window.addEventListener('resize', drawBox);
$('b-cropsave').onclick = async () => {
  if (!rect) return;
  const p = items[at], r0 = rect;
  stopCrop();
  await changed(await post('/api/library/crop', {key: p.key, file: current(), rect: r0}), 'Cropped. The file stays as it is; Export makes a copy with the crop.'
    + (learnState && learnState.on ? ' ✓ Auto crop will learn from this crop.' : ''));
  learnShow();
};

// ---------------------------------------------------------------- one picture on its own colour, not the roll's
$('b-own').onclick = async () => {
  const p = items[at], on = !(info && info.film_colour && info.film_colour.own);
  await changed(await post('/api/library/edit', {key: p.key, files: [p.file], colour: on ? 'own' : 'roll'}),
    on ? 'This picture is coloured on its own now, not with the roll\'s colour. The file was not changed.'
       : 'This picture gets the roll\'s colour again. The file was not changed.');
};
$('a-own').onclick = () => edit(tickedPics().filter(p => !p.file.startsWith('RAW/')), {colour: 'own'});
$('a-rollcol').onclick = () => edit(tickedPics().filter(p => !p.file.startsWith('RAW/')), {colour: 'roll'});

// ---------------------------------------------------------------- the film base: picked on the frame as scanned
function stopBase() {
  if (!picking) return;
  picking = false;
  $('stage').classList.remove('picking');
  $('b-base').setAttribute('aria-pressed', 'false');
  $('basemark').hidden = true;
}
$('b-base').onclick = () => {
  const p = items[at];
  if (picking) { stopBase(); $('big').src = bigURL(p, current(), viewOrig ? 'original' : 'edited'); say('cropmsg', ''); return; }
  stopCrop(); setCompare(false);
  picking = true;
  $('stage').classList.add('picking');
  $('b-base').setAttribute('aria-pressed', 'true');
  $('loading').hidden = false; $('loading').textContent = 'Loading the frame as scanned…';
  $('big').onload = () => { $('loading').hidden = true; };
  $('big').src = bigURL(p, current(), 'negative');
  say('cropmsg', 'This is the whole frame as scanned. Click on clear film: the strip between two frames or along the film edge, '
    + 'where nothing was exposed (the lightest, orange or yellow part), not the holder. Esc or Film base… again to stop.');
};
$('big').addEventListener('click', async ev => {
  if (!picking) return;
  const b = $('big').getBoundingClientRect();
  const x = (ev.clientX - b.left) / b.width, y = (ev.clientY - b.top) / b.height;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  const m = $('basemark'), w = $('imgwrap').getBoundingClientRect();
  m.style.left = (ev.clientX - w.left) + 'px'; m.style.top = (ev.clientY - w.top) + 'px'; m.hidden = false;
  say('cropmsg', 'Measuring the film base…');
  const p = items[at];
  const r = await post('/api/library/film_base', {key: p.key, file: current(), x, y});
  if (!r.ok) { say('cropmsg', r.error || 'That did not work.', 'warn'); return; }
  stopBase();
  stamp++;
  await refresh();
  if (roll && !roll.search) drawFilmColour(r.film_colour);
  await show(at, alt);
  say('cropmsg', 'Film base picked: this roll\'s negatives are now coloured from it (the files are not changed; Show original shows them as saved).'
    + (r.warn ? ' ' + r.warn : ''), r.warn ? 'warn' : 'ok');
});

// ---------------------------------------------------------------- keys
document.addEventListener('keydown', ev => {
  if (window.CoolscanExport && CoolscanExport.isOpen()) return;   // the Export dialog has the keys
  if (ev.target.matches('textarea, input, select') || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (!$('rescanview').hidden) { if (ev.key === 'Escape') closeRescan(); return; }
  if (!$('viewer').hidden) {
    const p = items[at], k = ev.key.toLowerCase();
    if (ev.key === 'Escape') { if (picking) { $('b-base').click(); } else if (cropping) { stopCrop(true); say('cropmsg', ''); } else closeViewer(); }
    else if (k === 'b' && !$('b-base').hidden) $('b-base').click();
    else if (ev.key === 'ArrowLeft') $('b-prev').click();
    else if (ev.key === 'ArrowRight' || ev.key === ' ') $('b-next').click();
    else if (k === 'c' && !$('b-crop').hidden) $('b-crop').click();
    else if (k === 'a' && cropping) $('b-cropadv').click();
    else if (k === 'o' && !$('b-orig').hidden) $('b-orig').click();
    else if (k === 'v' && !$('b-compare').hidden) $('b-compare').click();
    else if (/^[0-5]$/.test(ev.key)) edit([p], {stars: +ev.key}, 'cropmsg');
    else if (k === 'p') edit([p], {flag: p.flag === 'pick' ? null : 'pick'}, 'cropmsg');
    else if (k === 'x') edit([p], {flag: p.flag === 'reject' ? null : 'reject'}, 'cropmsg');
    else if (k === 'u') edit([p], {flag: null}, 'cropmsg');
    else if (k === 's') edit([p], {rescan: !p.rescan}, 'cropmsg');
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
    t += '<table class="clr"><tr><th>Picture</th><th>Steps</th><th>Way</th></tr>' + sp.recent.map(x => '<tr><td>' + escL(x.name || '') + '<br><span class="hint">' + escL(x.t.slice(5, 16)) + '</span></td><td>' + x.steps + '</td><td>' + x.kinds.map(k => KW[k] || k).join(' → ') + '</td></tr>').join('') + '</table>';
    rows.push(['Crop steps', t]);
  }
  rows.push(['Kept', r.hand_crops + ' crops from the scanning screen (plus those from Scanned pictures), ' + r.new + ' new since it last learned; ' + r.store_mb + ' MB in <code>' + escL(r.store) + '</code> (never goes to GitHub). What it learned (numbers only, no pictures): <code>' + escL(r.model_file || '') + '</code>, which goes to GitHub with the app.']);
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
  const line = !r.on ? 'Off (Settings, Crop tab): your crops are not learned from.'
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
// the learning auto rotate: what it makes of the picture on show (a suggestion, or "looks right")
async function turnGuess(p, file) {
  const line = $('turnline'), btn = $('b-turnguess');
  btn.hidden = true;
  if (file.startsWith('RAW/')) { line.textContent = 'RAW files are not turned here.'; return; }
  let r = {}, s = {};
  try { [r, s] = await Promise.all([post('/api/turnlearn', {action: 'guess', key: p.key, file}), getJSON('/api/turnlearn')]); } catch (e) { return; }
  if (items[at] !== p || current() !== file) return;
  if (!r.on) { line.textContent = 'Off (Settings in the scanning window): your turns are not learned from.'; return; }
  const g = r.guess;
  if (!g) { line.textContent = s.busy ? 'Learning from your turns…' : (s.text || ''); return; }
  const sure = Math.round(g.p * 100) + '% sure';
  if (!g.quarter) { line.textContent = 'Looks the right way up (' + sure + '). Turning pictures by hand teaches it.'; return; }
  const words = {1: 'a quarter to the left', 2: 'upside down', 3: 'a quarter to the right'}[g.quarter];
  line.textContent = 'Auto rotate thinks this picture should be turned ' + words + ' (' + sure + ').';
  btn.hidden = false;
  btn.onclick = () => turn(g.quarter);
}
$('b-turnlearnnow').onclick = async () => {
  const b = $('b-turnlearnnow'); b.disabled = true; b.textContent = 'Learning…';
  await post('/api/turnlearn', {action: 'learn'});
  const done = async () => {
    let r = {}; try { r = await getJSON('/api/turnlearn'); } catch (e) { /* again */ }
    if (r.busy) return setTimeout(done, 2000);
    b.disabled = false; b.textContent = 'Learn now'; $('turnline').textContent = r.text || '';
  };
  setTimeout(done, 500);
};
$('a-autoturn').onclick = async () => {
  const sel = tickedPics(); if (!sel.length) return;
  const b = $('a-autoturn'); b.disabled = true; say('actmsg', 'Auto rotate: looking at ' + sel.length + ' picture(s)…');
  const byKey = {};
  for (const p of sel) (byKey[p.key] = byKey[p.key] || []).push(p.file);
  let turned = 0, sug = [], same = 0;
  for (const [key, files] of Object.entries(byKey)) {
    const r = await post('/api/turnlearn', {action: 'library', key, files});
    if (r.ok) { turned += r.turned.length; sug = sug.concat(r.suggested); same += r.same; }
  }
  b.disabled = false; stamp++;
  await refresh();
  say('actmsg', 'Auto rotate turned ' + turned + ' picture(s) it was sure of; ' + same + ' look right as they are' +
    (sug.length ? '; not sure enough to turn ' + sug.length + ': ' + sug.slice(0, 8).map(x => x.file.split('/').pop() + ' (' + x.words + ', ' + Math.round(x.p * 100) + '%)').join(', ') + (sug.length > 8 ? '…' : '') + '. Open one to see the suggestion.' : '.'), 'ok');
};
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

// the Settings window is the scanning screen's own, shown here in a frame, so there is one of it and not a copy
(function () {
  const b = document.getElementById('b-settings'); if (!b) return;
  let dlg = null;
  function open() {
    if (dlg && dlg.open) return;
    if (!dlg) { dlg = document.createElement('dialog'); dlg.id = 'setframe'; dlg.setAttribute('aria-label', 'Settings'); document.body.appendChild(dlg);
      dlg.addEventListener('close', () => { dlg.textContent = ''; }); }
    const f = document.createElement('iframe'); f.src = '/?settings=1'; f.title = 'Settings'; dlg.textContent = ''; dlg.appendChild(f); dlg.showModal();
  }
  b.onclick = open;
  window.addEventListener('message', ev => { if (ev.origin === location.origin && ev.data === 'coolscan-settings-closed' && dlg && dlg.open) dlg.close(); });
  document.addEventListener('keydown', ev => { if ((ev.metaKey || ev.ctrlKey) && ev.key === ',') { ev.preventDefault(); open(); } });
})();
