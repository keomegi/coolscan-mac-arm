// The Export dialog of Scanned pictures ("Scanned pictures"), laid out like Lightroom's: presets down the left,
// sections that fold on the right, Cancel and Export at the bottom. It brings its own markup and styles, so
// the page only loads this file and calls CoolscanExport.open(items, done, ctx) or CoolscanExport.again(items, say).
//   items: [[folder key, file inside the folder], ...]     done(text, kind): told how the export went
//   ctx: {roll: {key, name, count}} lets the dialog offer the whole roll (and all rolls) as well as the items
// The work is done by the app (exporting.py); this file only draws the dialog and asks.
(() => {
'use strict';

const CSS = `
#xp{position:fixed;inset:0;z-index:60;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;padding:18px}
#xp[hidden]{display:none}
#xp .win{width:min(900px,100%);max-height:100%;display:grid;grid-template-rows:auto minmax(0,1fr) auto;background:var(--panel);color:var(--ink);
  border:1px solid var(--line);border-radius:10px;box-shadow:0 18px 60px rgba(0,0,0,.5);overflow:hidden;font-size:13px}
#xp .top{padding:10px 16px;border-bottom:1px solid var(--line);display:flex;align-items:baseline;gap:12px}
#xp .top h2{font-size:14px;margin:0;font-weight:600}
#xp .top span{color:var(--ink2);font-size:12px}
#xp .mid{display:grid;grid-template-columns:210px minmax(0,1fr);min-height:0}
#xp .left{border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0;background:color-mix(in srgb,var(--ink) 4%,var(--panel))}
#xp .left h3{font-size:11px;font-weight:600;color:var(--ink2);margin:10px 12px 4px;letter-spacing:.02em}
#xp .plist{flex:1;overflow:auto;padding:0 6px 6px}
#xp .pgroup{font-size:11px;color:var(--ink2);padding:8px 6px 2px;text-transform:uppercase;letter-spacing:.05em}
#xp .plist button{all:unset;box-sizing:border-box;display:block;width:100%;padding:4px 8px 4px 16px;border-radius:5px;cursor:default;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#xp .plist button:hover{background:color-mix(in srgb,var(--acc) 10%,transparent)}
#xp .plist button[aria-pressed=true]{background:var(--acc);color:#fff}
#xp .plist button:focus-visible{outline:2px solid var(--acc);outline-offset:-2px}
#xp .plist .none{padding:4px 16px;color:var(--ink2);font-size:12px}
#xp .pbtns{border-top:1px solid var(--line);padding:8px;display:flex;flex-wrap:wrap;gap:6px}
#xp .pbtns button{flex:1 1 auto;height:24px;padding:0 8px;font-size:12px}
#xp .pname{display:flex;gap:6px;padding:0 8px 8px}
#xp .pname[hidden]{display:none}
#xp .pname input{flex:1;min-width:0}
#xp .right{overflow:auto;padding:10px 12px;display:flex;flex-direction:column;gap:6px}
#xp section{border:1px solid var(--line);border-radius:6px;overflow:hidden;flex:0 0 auto;padding:0;margin:0;background:none}
#xp section>button{all:unset;box-sizing:border-box;width:100%;display:flex;align-items:center;gap:8px;padding:5px 10px;cursor:default;
  background:color-mix(in srgb,var(--ink) 11%,var(--panel));font-weight:600}
#xp section>button:focus-visible{outline:2px solid var(--acc);outline-offset:-2px}
#xp section>button .tri{display:inline-block;width:10px;font-size:10px;color:var(--ink2);transition:transform .12s}
#xp section.open>button .tri{transform:rotate(90deg)}
#xp section>button .sum{margin-left:auto;font-weight:400;color:var(--ink2);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:62%}
#xp section.open>button .sum{visibility:hidden}
#xp section .body{display:none;padding:10px 12px 12px;border-top:1px solid var(--line)}
#xp section.open .body{display:grid;grid-template-columns:132px minmax(0,1fr);gap:7px 10px;align-items:center}
#xp .body>label,#xp .body>.lab{text-align:right;color:var(--ink2);white-space:nowrap}
#xp .body .row{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-start;gap:6px 10px;min-width:0;min-height:0;margin:0;padding:0}
#xp .body>label,#xp .body>.lab,#xp .body .chk{margin:0;padding:0}
#xp #x-dpi{width:110px}
#xp .body .row[hidden],#xp .body label[hidden],#xp .body .lab[hidden]{display:none}
#xp .body input[type=text]{flex:1 1 160px;min-width:0}
#xp .body input[type=number]{width:84px}
#xp .body input,#xp .body select,#xp .pname input{height:24px;font:inherit;font-size:12px}
#xp .body input[type=checkbox]{height:auto;margin:0 4px 0 0}
#xp .body input[type=range]{flex:0 1 240px}
#xp .body select{width:auto;max-width:100%}
#xp .body .chk{display:inline-flex;align-items:center;color:var(--ink)}
#xp .body .note{grid-column:2;color:var(--ink2);font-size:12px;line-height:1.35}
#xp .body .note[hidden]{display:none}
#xp .body .note.warn,#xp .foot .msg.warn{color:#c2410c}
#xp .body code{font-size:12px;background:color-mix(in srgb,var(--ink) 8%,transparent);padding:0 3px;border-radius:3px}
#xp .foot{border-top:1px solid var(--line);padding:10px 14px;display:flex;align-items:center;gap:10px}
#xp .foot .msg{flex:1;min-width:0;color:var(--ink2);font-size:12px;max-height:5.4em;overflow:auto}
#xp .foot .msg.ok{color:var(--ink)}
#xp .foot progress{flex:0 0 220px;height:8px}
#xp .foot progress[hidden]{display:none}
#xp .foot button{min-width:84px;height:28px}
#xp #x-go{background:var(--acc);color:#fff;border-color:var(--acc);font-weight:600}
#xp #x-go:disabled{opacity:.5}
@media (max-width:640px){#xp .mid{grid-template-columns:1fr}#xp .left{display:none}}
`;

const sec = (id, title, body) => `<section id="x-s-${id}"><button type="button" aria-expanded="false"><span class="tri">▶</span>${title}<span class="sum"></span></button><div class="body">${body}</div></section>`;
const HTML = `
<div class="win" role="dialog" aria-modal="true" aria-labelledby="x-title">
<div class="top"><h2 id="x-title">Export</h2><span id="x-what"></span></div>
<div class="mid">
<div class="left"><h3>Preset</h3><div class="plist" id="x-plist"></div>
<div class="pname" id="x-pname" hidden><input type="text" id="x-newname" placeholder="Name of the preset" aria-label="Name of the new preset" maxlength="60"><button type="button" id="x-padd2">Save</button></div>
<div class="pbtns"><button type="button" id="x-padd" title="Keep these settings under a name">Add</button><button type="button" id="x-pupd" title="Put these settings into the chosen preset" disabled>Update</button><button type="button" id="x-pdel" title="Delete the chosen preset" disabled>Remove</button></div></div>
<div class="right">
${sec('what', 'What to Export', `
<label for="x-scope">Pictures</label><div class="row"><select id="x-scope"></select></div>
<label for="x-source">Export</label><div class="row"><select id="x-source"><option value="positive">Finished pictures (positives)</option><option value="raw">RAW scans (as scanned, with infrared)</option><option value="both">Both</option></select></div>
<span class="lab"></span><div class="row"><label class="chk"><input type="checkbox" id="x-apply_crop">With my crop and turn</label></div>
<span class="lab"></span><div class="row"><label class="chk"><input type="checkbox" id="x-apply_colour">With the roll's colour of negatives</label></div>
<div class="note" id="x-whatnote"></div>`)}
${sec('where', 'Export Location', `
<label for="x-where">Export to</label><div class="row"><select id="x-where"><option value="folder">Chosen folder</option><option value="edited">The roll's Edited folder</option></select></div>
<label for="x-dest" data-for="folder">Folder</label><div class="row" data-for="folder"><input type="text" id="x-dest" placeholder="~/Pictures/Coolscan export"><button type="button" id="x-choose">Choose</button></div>
<span class="lab" data-for="folder"></span><div class="row" data-for="folder"><label class="chk"><input type="checkbox" id="x-subon">Put in subfolder</label><input type="text" id="x-subfolder" aria-label="Subfolder"></div>
<span class="lab" data-for="folder"></span><div class="row" data-for="folder"><label class="chk"><input type="checkbox" id="x-keep_rolls">Keep the roll folders (Roll 005/…)</label></div>
<label for="x-existing">Existing files</label><div class="row"><select id="x-existing"><option value="rename">Choose a new name for the exported file</option><option value="skip">Skip</option><option value="replace">Replace without asking</option></select></div>
<div class="note" id="x-wherenote"></div>`)}
${sec('naming', 'File Naming', `
<label for="x-naming">Rename to</label><div class="row"><select id="x-naming"><option value="name">Original name</option><option value="text_seq">Custom name - number</option><option value="date_seq">Date taken - number</option><option value="roll_frame">Roll - frame number</option><option value="text_name">Custom text - original name</option><option value="custom">Edit…</option></select></div>
<label for="x-template" id="x-tpllab">Template</label><div class="row" id="x-tplrow"><input type="text" id="x-template" spellcheck="false"></div>
<div class="note" id="x-tplnote">Use <code>{name}</code> <code>{text}</code> <code>{seq}</code> <code>{date}</code> <code>{roll}</code> <code>{frame}</code> and any words between them.</div>
<label for="x-text" id="x-textlab">Custom text</label><div class="row" id="x-textrow"><input type="text" id="x-text"></div>
<label for="x-start" id="x-startlab">Start number</label><div class="row" id="x-startrow"><input type="number" id="x-start" min="0" max="999999" step="1"></div>
<span class="lab">Example</span><div class="row"><b id="x-example">…</b></div>`)}
${sec('file', 'File Settings', `
<label for="x-format">Image format</label><div class="row"><select id="x-format"></select></div>
<label for="x-quality" id="x-qlab">Quality</label><div class="row" id="x-qrow"><input type="range" id="x-quality" min="10" max="100" step="1"><span id="x-qnum"></span></div>
<label for="x-space">Colour space</label><div class="row"><select id="x-space"></select></div>
<div class="note" id="x-filenote"></div>`)}
${sec('size', 'Image Sizing', `
<span class="lab"></span><div class="row"><label class="chk"><input type="checkbox" id="x-resizeon">Resize to fit</label><select id="x-resize" aria-label="How to resize"><option value="long">Long edge</option><option value="wh">Width &amp; height</option><option value="mp">Megapixels</option><option value="percent">Percentage</option></select>
<label class="chk"><input type="checkbox" id="x-noenlarge">Don't enlarge</label></div>
<span class="lab" id="x-sizelab"></span><div class="row" id="x-sizerow">
<span data-r="long"><input type="number" id="x-long" min="16" max="30000" aria-label="Long edge"> pixels</span>
<span data-r="wh">W <input type="number" id="x-width" min="16" max="30000" aria-label="Width"> H <input type="number" id="x-height" min="16" max="30000" aria-label="Height"> pixels</span>
<span data-r="mp"><input type="number" id="x-mp" min="0.1" max="400" step="0.1" aria-label="Megapixels"> megapixels</span>
<span data-r="percent"><input type="number" id="x-percent" min="1" max="400" aria-label="Percent"> %</span></div>
<label for="x-dpi">Resolution</label><div class="row"><input type="number" id="x-dpi" min="0" max="20000" placeholder="as scanned"> pixels per inch</div>`)}
${sec('meta', 'Metadata', `
<label for="x-metadata">Include</label><div class="row"><select id="x-metadata"><option value="all">All metadata</option><option value="nopeople">All except people's names</option><option value="basic">Date taken and caption only</option><option value="none">None</option></select></div>
<label for="x-creator">Creator</label><div class="row"><input type="text" id="x-creator"></div>
<label for="x-copyright">Copyright</label><div class="row"><input type="text" id="x-copyright"></div>
<div class="note">The crop, turn, colour of the roll and date taken are always put into the copies.</div>`)}
${sec('after', 'After Export', `
<label for="x-after">After export</label><div class="row"><select id="x-after"><option value="nothing">Do nothing</option><option value="finder">Show in Finder</option><option value="lightroom">Open in Lightroom</option></select></div>
<div class="note" id="x-afternote" hidden></div>`)}
</div></div>
<div class="foot"><div class="msg" id="x-msg" role="status"></div><progress id="x-bar" hidden max="1" value="0"></progress>
<button type="button" id="x-cancel">Cancel</button><button type="button" id="x-go">Export</button></div>
</div>`;

const WORDS = {existing: {rename: 'new name if there', skip: 'skip if there', replace: 'replace if there'},
  resize: {long: 'Long edge', wh: 'Fit', mp: '', percent: ''},
  metadata: {all: 'All metadata', nopeople: "All except people's names", basic: 'Date taken and caption only', none: 'None'},
  after: {nothing: 'Do nothing', finder: 'Show in Finder', lightroom: 'Open in Lightroom'}};
const TEXTS = ['dest', 'subfolder', 'template', 'text', 'creator', 'copyright'];
const SELECTS = ['source', 'where', 'existing', 'naming', 'format', 'space', 'metadata', 'after'];
const NUMBERS = ['start', 'quality', 'long', 'width', 'height', 'mp', 'percent'];

let root = null, opts = null, items = [], ctx = {}, done = null, chosen = '', chosenBuilt = false, clean = null;
let busy = false, finished = false, nameTimer = 0, lastFocus = null;
const q = id => root.querySelector('#x-' + id);

async function post(path, body) {
  try {
    const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
    return await r.json();
  } catch (e) { return {ok: false, error: 'The app did not answer. Is it still running?'}; }
}
function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { /* no storage */ } return null; }
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c])); }
function plural(n, w) { return n + ' ' + w + (n === 1 ? '' : 's'); }

function build() {
  const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);
  root = document.createElement('div'); root.id = 'xp'; root.hidden = true; root.innerHTML = HTML; document.body.appendChild(root);
  let open = {what: 1, where: 1, naming: 1, file: 1, size: 0, meta: 0, after: 1};
  try { open = Object.assign(open, JSON.parse(store('coolscan-export-open') || '{}')); } catch (e) { /* as it was */ }
  for (const s of root.querySelectorAll('section')) {
    const id = s.id.slice(4), head = s.firstElementChild;
    const set = on => { s.classList.toggle('open', !!on); head.setAttribute('aria-expanded', on ? 'true' : 'false'); open[id] = on ? 1 : 0; };
    set(open[id]);
    head.onclick = () => { set(!s.classList.contains('open')); store('coolscan-export-open', JSON.stringify(open)); };
  }
  root.addEventListener('input', changed);
  root.addEventListener('change', changed);
  // the page's own keys (arrows, letters for stars and crop) must not act while the dialog is up
  for (const ev of ['keydown', 'keyup', 'keypress']) root.addEventListener(ev, e => {
    e.stopPropagation();
    if (ev !== 'keydown') return;
    if (e.key === 'Escape') { e.preventDefault(); q('cancel').click(); }
    else if (e.key === 'Enter' && e.target.tagName !== 'BUTTON' && e.target.id !== 'x-newname') { e.preventDefault(); q('go').click(); }
    else if (e.key === 'Tab') trap(e);
  });
  root.addEventListener('mousedown', e => { if (e.target === root) e.preventDefault(); });
  q('cancel').onclick = cancel;
  q('go').onclick = go;
  q('choose').onclick = async () => {
    const r = await post('/api/choose_folder', {for: 'export', start: q('dest').value.trim() || '~'});
    if (r.ok) { q('dest').value = r.path; changed(); } else if (!r.cancelled) msg(r.error || 'The folder picker did not open. Type the folder instead.', 'warn');
  };
  q('format').addEventListener('change', () => {   // 8-bit files for sharing: sRGB is what other screens expect
    const f = q('format').value, sp = q('space');
    if (['png', 'jpeg', 'heic'].includes(f) && sp.value === 'same') { sp.value = 'sRGB IEC61966-2.1'; sp.dataset.auto = '1'; }
    else if (['tiff16', 'tiff8', 'dng'].includes(f) && sp.dataset.auto) { sp.value = 'same'; delete sp.dataset.auto; }
  });
  q('space').addEventListener('change', e => { if (e.isTrusted) delete q('space').dataset.auto; });
  q('padd').onclick = () => { q('pname').hidden = !q('pname').hidden; if (!q('pname').hidden) { q('newname').value = chosen && !chosenBuilt ? chosen : ''; q('newname').focus(); } };
  q('padd2').onclick = () => savePreset(q('newname').value);
  q('newname').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); savePreset(q('newname').value); } });
  q('pupd').onclick = () => savePreset(chosen);
  q('pdel').onclick = async () => {
    const r = await post('/api/library/export_preset', {action: 'delete', name: chosen});
    if (!r.ok) { msg(r.error || 'Could not delete the preset.', 'warn'); return; }
    msg('Preset "' + chosen + '" deleted.'); chosen = ''; await loadOptions(); drawPresets();
  };
}

function trap(e) {   // Tab stays inside the dialog
  const all = [...root.querySelectorAll('button,input,select')].filter(el => !el.disabled && el.offsetParent !== null);
  if (!all.length) return;
  const first = all[0], last = all[all.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

function msg(text, kind) { const m = q('msg'); m.className = 'msg' + (kind ? ' ' + kind : ''); m.textContent = text || ''; }

// ---- the form
function fill(o) {
  for (const k of TEXTS) if (k in o) q(k).value = o[k];
  for (const k of SELECTS) if (k in o) q(k).value = o[k];
  for (const k of NUMBERS) if (k in o) q(k).value = o[k];
  if ('subfolder' in o) q('subon').checked = !!o.subfolder;
  if ('keep_rolls' in o) q('keep_rolls').checked = !!o.keep_rolls;
  if ('apply_crop' in o) q('apply_crop').checked = !!o.apply_crop;
  if ('apply_colour' in o) q('apply_colour').checked = !!o.apply_colour;
  if ('resize' in o) { q('resizeon').checked = o.resize !== 'none'; if (o.resize !== 'none') q('resize').value = o.resize; }
  if ('enlarge' in o) q('noenlarge').checked = !o.enlarge;
  if ('dpi' in o) q('dpi').value = o.dpi || '';
  delete q('space').dataset.auto;
}
function read() {
  const o = {};
  for (const k of TEXTS) o[k] = q(k).value.trim();
  for (const k of SELECTS) o[k] = q(k).value;
  for (const k of NUMBERS) o[k] = Number(q(k).value) || 0;
  if (!q('subon').checked) o.subfolder = '';
  o.keep_rolls = q('keep_rolls').checked;
  o.apply_crop = q('apply_crop').checked;
  o.apply_colour = q('apply_colour').checked;
  o.resize = q('resizeon').checked ? q('resize').value : 'none';
  o.enlarge = !q('noenlarge').checked;
  o.dpi = Number(q('dpi').value) || 0;
  if (o.naming !== 'custom') o.template = {name: '{name}', text_seq: '{text}-{seq}', date_seq: '{date}_{seq}', roll_frame: '{roll}_{frame}', text_name: '{text}_{name}'}[o.naming];
  return o;
}
// which pictures: the ones handed over, the whole roll, or every roll
function which() {
  const v = q('scope').value;
  if (v === 'roll') return {scope: {rolls: [ctx.roll.key]}};
  if (v === 'all') return {scope: {all: true}};
  return {items};
}
function label(list, v) { const f = (list || []).find(x => x[0] === v); return f ? f[1] : v; }

function changed() {
  if (!opts) return;
  const o = read(), folder = o.where === 'folder', sc = q('scope').value;
  q('title').textContent = sc === 'roll' ? 'Export ' + ctx.roll.name : sc === 'all' ? 'Export all rolls' : 'Export ' + (items.length === 1 ? 'one picture' : items.length + ' pictures');
  q('what').textContent = sc === 'items' && items.length === 1 ? items[0][1].split('/').pop() : sc === 'roll' ? plural(ctx.roll.count, 'picture') : '';
  q('apply_colour').disabled = o.source === 'raw';
  q('whatnote').textContent = o.source === 'positive' ? 'The pictures themselves are never changed; the copies get what is ticked here.'
    : 'RAW scans are the untouched scans Archive mode keeps (the negative as scanned, with the infrared channel). '
      + (o.apply_crop ? 'They are framed like their pictures.' : 'The whole frame, as scanned.') + (o.source === 'both' ? ' They go into a RAW folder beside the pictures.' : '');
  for (const el of root.querySelectorAll('[data-for=folder]')) el.hidden = !folder;
  q('subfolder').disabled = !q('subon').checked;
  q('wherenote').textContent = folder ? (o.keep_rolls ? 'Each roll gets its own folder inside this one.' : 'All pictures go into this one folder.')
    : 'Copies go into each roll\'s ' + opts.edited + ' folder and show up in this app. TIFF 16-bit only.';
  const custom = o.naming === 'custom';
  q('tpllab').hidden = q('tplrow').hidden = q('tplnote').hidden = !custom;
  if (!custom) q('template').value = o.template;
  const usesText = /\{text\}/i.test(o.template), usesSeq = /\{seq\}/i.test(o.template) || /\{frame\}/i.test(o.template) && custom;
  q('textlab').hidden = q('textrow').hidden = !usesText;
  q('startlab').hidden = q('startrow').hidden = !usesSeq;
  const lossy = o.format === 'jpeg' || o.format === 'heic';
  q('qlab').hidden = q('qrow').hidden = !lossy;
  q('qnum').textContent = o.quality;
  let note = '', warn = false;
  if (lossy && !opts.can_convert) { note = label(opts.formats, o.format) + ' is made by the Mac\'s own converter, which is not on this computer.'; warn = true; }
  else if (!folder && o.format !== 'tiff16') { note = 'The ' + opts.edited + ' folder takes TIFF 16-bit only. Choose a folder for other kinds of file.'; warn = true; }
  else if (o.format === 'dng') note = 'A linear DNG of the finished picture, for Lightroom. It is not the scanner\'s unprocessed data, and Lightroom may show it a little differently from the TIFF.';
  else if (o.format === 'tiff8' || o.format === 'png') note = '8 bits a channel: smaller files, less room for later editing.';
  else if (lossy) note = 'Made from the full picture by the Mac\'s own converter.';
  q('filenote').textContent = note; q('filenote').className = 'note' + (warn ? ' warn' : ''); q('filenote').hidden = !note;
  q('resize').disabled = q('noenlarge').disabled = !q('resizeon').checked;
  q('sizelab').hidden = q('sizerow').hidden = !q('resizeon').checked;
  for (const el of q('sizerow').children) el.hidden = el.dataset.r !== q('resize').value;
  const an = o.after === 'lightroom' && !opts.lightroom ? 'Lightroom was not found in Applications on this computer.' : (o.after !== 'nothing' && !opts.mac ? 'Works on the Mac only.' : '');
  q('afternote').textContent = an; q('afternote').hidden = !an; q('afternote').className = 'note' + (an ? ' warn' : '');
  // what each section says when folded
  const sum = (id, t) => { root.querySelector('#x-s-' + id + ' .sum').textContent = t; };
  sum('what', q('scope').selectedOptions[0].textContent + ' · ' + {positive: 'pictures', raw: 'RAW scans', both: 'pictures and RAW scans'}[o.source]
    + (o.apply_crop ? '' : ' · no crop') + (o.apply_colour || o.source === 'raw' ? '' : ' · no roll colour'));
  sum('where', (folder ? (o.dest || 'no folder chosen') + (o.subfolder ? '/' + o.subfolder : '') : 'Each roll\'s ' + opts.edited + ' folder') + ' · ' + WORDS.existing[o.existing]);
  sum('file', label(opts.formats, o.format) + (lossy ? ' ' + o.quality : '') + ' · ' + label(opts.spaces, o.space).replace(/ \(.*/, ''));
  sum('size', o.resize === 'none' ? 'Full size' : o.resize === 'long' ? 'Long edge ' + o.long + ' px' : o.resize === 'wh' ? 'Fit ' + o.width + ' × ' + o.height + ' px'
    : o.resize === 'mp' ? o.mp + ' megapixels' : o.percent + ' %');
  sum('meta', WORDS.metadata[o.metadata] + (o.copyright ? ' · ' + o.copyright : ''));
  sum('after', WORDS.after[o.after]);
  drawPresetState();
  clearTimeout(nameTimer);
  nameTimer = setTimeout(example, 180);
}

async function example() {
  if (!root || root.hidden || busy) return;
  const o = read();
  const w = which();
  const r = await post('/api/library/export_names', Object.assign(w.items ? {items: w.items.slice(0, 40)} : w, {options: o}));
  if (!r.ok || root.hidden) return;
  const name = (r.names || [])[0] || '';
  q('example').textContent = name || '…';
  root.querySelector('#x-s-naming .sum').textContent = name;
  if (!busy && !finished) { msg(r.problem || '', r.problem ? 'warn' : ''); q('go').disabled = !!r.problem; }
}

// ---- presets
async function loadOptions() {
  const r = await post('/api/library/export_options', {});
  if (r.ok) opts = r;
  return r;
}
function presetOf(name, built) { return built ? opts.built_in[name] : opts.presets[name]; }
function same(a, b) { return Object.keys(a).every(k => String(a[k]) === String(b[k])); }
function drawPresets() {
  const row = (name, built) => '<button type="button" data-n="' + esc(name) + '"' + (built ? ' data-b="1"' : '') + ' aria-pressed="false">' + esc(name) + '</button>';
  const own = Object.keys(opts.presets);
  q('plist').innerHTML = '<div class="pgroup">Built in</div>' + Object.keys(opts.built_in).map(n => row(n, true)).join('')
    + '<div class="pgroup">Your presets</div>' + (own.length ? own.map(n => row(n, false)).join('') : '<div class="none">None yet. Set things up, then Add.</div>');
  for (const b of q('plist').querySelectorAll('button')) b.onclick = () => {
    chosen = b.dataset.n; chosenBuilt = !!b.dataset.b;
    fill(presetOf(chosen, chosenBuilt)); q('pname').hidden = true; changed();
  };
  drawPresetState();
}
function drawPresetState() {
  if (!opts) return;
  const now = read(), p = chosen ? presetOf(chosen, chosenBuilt) : null, edited = p && !same(p, now);
  for (const b of q('plist').querySelectorAll('button')) {
    const on = b.dataset.n === chosen && !!b.dataset.b === chosenBuilt;
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.textContent = b.dataset.n + (on && edited ? ' (edited)' : '');
  }
  q('pupd').disabled = !(p && !chosenBuilt && edited);
  q('pdel').disabled = !(p && !chosenBuilt);
}
async function savePreset(name) {
  const r = await post('/api/library/export_preset', {action: 'save', name, options: read()});
  if (!r.ok) { msg(r.error || 'Could not save the preset.', 'warn'); return; }
  chosen = r.name; chosenBuilt = false; q('pname').hidden = true;
  await loadOptions(); drawPresets(); msg('Preset "' + r.name + '" saved.');
}

// ---- exporting
function summary(s) {
  if (s.error) return [s.error, 'warn'];
  const n = s.saved || 0;
  let t = (s.cancelled ? 'Stopped. ' : '') + 'Exported ' + plural(n, 'picture') + (s.format ? ' as ' + s.format : '') + (s.folder ? ' to ' + s.folder : '') + '.';
  if (s.skipped) t += ' ' + s.skipped + ' skipped (already there).';
  if ((s.problems || []).length) t += ' Not exported: ' + s.problems.join('; ') + '.';
  if (s.after) t += ' ' + s.after;
  if (n) t += ' The pictures themselves are unchanged.';
  return [t, (s.problems || []).length || s.after ? 'warn' : 'ok'];
}
async function follow(tick) {
  for (;;) {
    await new Promise(r => setTimeout(r, 250));
    const s = await post('/api/library/export_status', {});
    if (s.ok === false && !('running' in s)) return {error: s.error || 'The app did not answer.'};
    if (!s.running) return s;
    tick(s);
  }
}
async function go() {
  if (busy) return;
  if (finished) { close(); return; }
  const r = await post('/api/library/export_start', Object.assign(which(), {options: read()}));
  if (!r.ok) { msg(r.error || 'Could not start the export.', 'warn'); return; }
  busy = true; q('go').disabled = true; q('cancel').textContent = 'Stop';
  q('bar').hidden = false; q('bar').max = r.total; q('bar').value = 0;
  msg('Exporting ' + plural(r.total, 'picture') + '…');
  const s = await follow(st => { q('bar').max = st.total; q('bar').value = st.done; msg('Exporting ' + Math.min(st.done + 1, st.total) + ' of ' + st.total + (st.current ? ': ' + st.current.split('/').pop() : '') + '…'); });
  busy = false; q('bar').hidden = true; q('cancel').textContent = 'Cancel'; q('go').disabled = false;
  const [text, kind] = summary(s);
  if (kind === 'ok') { close(); if (done) done(text, kind, s); return; }
  finished = true; q('go').textContent = 'Close'; q('cancel').hidden = true; msg(text, kind);   // something to read first
  if (done) done(text, kind, s);
}
async function cancel() {
  if (busy) { msg('Stopping after the picture in hand…'); await post('/api/library/export_cancel', {}); return; }
  close();
}
function close() {
  if (!root) return;
  root.hidden = true; finished = false; q('go').textContent = 'Export'; q('cancel').hidden = false;
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}

async function open(list, whenDone, context) {
  if (!root) build();
  if (busy) { root.hidden = false; return; }
  items = (list || []).map(i => [i[0], i[1]]);
  ctx = context || {};
  done = whenDone || null;
  if (!items.length && !ctx.roll) { if (done) done('Tick the pictures to export first.', 'warn'); return; }
  const r = await loadOptions();
  if (!r.ok) { if (done) done(r.error || 'The export dialog could not be opened.', 'warn'); return; }
  q('format').innerHTML = opts.formats.map(f => '<option value="' + f[0] + '">' + esc(f[1]) + '</option>').join('');
  q('space').innerHTML = opts.spaces.map(f => '<option value="' + esc(f[0]) + '">' + esc(f[1]) + '</option>').join('');
  q('scope').innerHTML = (items.length ? '<option value="items">' + (items.length === 1 ? 'This picture' : 'The ' + items.length + (ctx.shown ? ' pictures shown' : ' ticked pictures')) + '</option>' : '')
    + (ctx.roll ? '<option value="roll">The whole roll: ' + esc(ctx.roll.name) + ' (' + ctx.roll.count + ')</option>' : '') + '<option value="all">All rolls</option>';
  q('scope').value = items.length ? 'items' : 'roll';
  fill(opts.last);
  finished = false; q('go').textContent = 'Export'; q('go').disabled = false; q('cancel').hidden = false; q('bar').hidden = true; q('pname').hidden = true; msg('');
  if (!opts.used && !chosen) { chosen = Object.keys(opts.built_in)[0]; chosenBuilt = true; fill(presetOf(chosen, true)); }
  else if (chosen && !presetOf(chosen, chosenBuilt)) chosen = '';
  drawPresets();
  lastFocus = document.activeElement;
  root.hidden = false;
  changed();
  q('go').focus();
}

// Export with previous: the options used last time, without the dialog. say(text, kind) is told how it goes.
async function again(list, say) {
  say = say || (() => {});
  if (busy) { say('An export is running already.', 'warn'); return; }
  const its = (list || []).map(i => [i[0], i[1]]);
  if (!its.length) { say('Tick the pictures to export first.', 'warn'); return; }
  const r = await post('/api/library/export_again', {items: its});
  if (!r.ok) { say(r.error || 'Could not start the export.', 'warn'); return; }
  busy = true;
  say('Exporting ' + plural(r.total, 'picture') + ' with the settings used last time…');
  const s = await follow(st => say('Exporting ' + Math.min(st.done + 1, st.total) + ' of ' + st.total + '…'));
  busy = false;
  const [text, kind] = summary(s);
  say(text, kind, s);
}

window.CoolscanExport = {open, again, isOpen: () => !!root && !root.hidden};
})();
