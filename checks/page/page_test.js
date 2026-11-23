const { chromium } = require('playwright');
const fs = require('fs');
const [base, shotDir, port] = process.argv.slice(2);
const now = new Date(); const D = '' + now.getFullYear() + String(now.getMonth() + 1).padStart(2, '0') + String(now.getDate()).padStart(2, '0');
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(String(e)));
  p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  const url = 'http://127.0.0.1:' + port + '/';
  await p.goto(url);
  await p.waitForFunction(() => document.querySelectorAll('.tile img').length === 6, null, { timeout: 30000 });
  const out = {};
  const logHas = t => p.waitForFunction(x => document.getElementById('log').textContent.includes(x), t, { timeout: 120000 });
  const post = (path, body) => p.evaluate(([path, body]) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then(r => r.json()), [path, body]);
  const state = () => p.evaluate(() => fetch('/api/state').then(r => r.json()));
  const txt = id => p.textContent('#' + id);
  const idle = () => p.waitForFunction(() => document.getElementById('busy').hidden && !document.getElementById('b-scan').disabled, null, { timeout: 120000 });
  const view = async () => [await txt('vtitle'), await p.isVisible('#vsrc') ? await txt('vsrc') : null];

  // 1 what is there at the start
  out.start = { cols: await p.$eval('#tiles', e => getComputedStyle(e).getPropertyValue('--cols').trim()), rows: await p.$$eval('.tile', t => new Set(t.map(e => e.offsetTop)).size),
    view: await view(), scan: await txt('b-scan'), preview: await txt('b-preview'), sum: await txt('framesum'), badges: await p.$$eval('.tile .badge', e => e.length) };
  // 2 looking is not ticking
  await p.click('.tile[data-i="2"] .pic');
  out.look = { view: await view(), scan: await txt('b-scan'), preview: await txt('b-preview'), cur: await p.$$eval('.tile.cur', e => e.map(x => x.dataset.i)), ticked: await p.$$eval('.tile.ticked', e => e.length) };
  // 3 a preview scan of the frame looked at
  await p.click('#b-sharper'); await logHas('frame 3: preview scan'); await idle();
  await p.waitForFunction(() => document.getElementById('vsrc').textContent === 'Preview');
  out.preview = { view: await view(), info: await txt('vinfo'), badge: await p.$eval('.tile[data-i="2"] .badge', e => e.textContent), others: await p.$$eval('.tile .badge', e => e.length) };
  // 4 ticking
  await p.check('.tile[data-i="2"] input'); await p.check('.tile[data-i="3"] input');
  out.tick2 = [await txt('b-scan'), await txt('b-preview'), await txt('framesum')];
  await p.click('#b-none'); out.none = await txt('b-scan');
  await p.click('#framesum'); await p.keyboard.press('Control+a'); out.all = await txt('b-scan');
  await p.keyboard.press('Escape'); out.esc = await txt('b-scan');
  await p.click('.tile[data-i="0"] .pic'); await p.click('.tile[data-i="2"] .pic', { modifiers: ['Shift'] }); out.shift = await txt('b-scan');
  await p.click('.tile[data-i="1"] .pic', { modifiers: ['Control'] }); out.ctrl = [await txt('b-scan'), await p.$$eval('.tile.ticked', e => e.map(x => +x.dataset.i + 1))];
  await p.click('#b-none');
  // 5 crop on frame 3
  await p.click('.tile[data-i="2"] .pic');
  await p.click('#b-crop'); out.cropHint = await txt('vsaved');
  const box = await p.$eval('#big', e => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  await p.mouse.move(box.x + box.w * 0.2, box.y + box.h * 0.2); await p.mouse.down(); await p.mouse.move(box.x + box.w * 0.5, box.y + box.h * 0.5, { steps: 4 });
  await p.mouse.move(box.x + box.w * 0.8, box.y + box.h * 0.7, { steps: 4 }); await p.mouse.up();
  await p.waitForFunction(() => !document.getElementById('b-cropclear').hidden);
  out.crop = { state: (await state()).tiles[2].crop, pressed: await p.getAttribute('#b-crop', 'aria-pressed'), boxShown: await p.isVisible('#cropbox'),
    boxAt: await p.$eval('#cropbox', e => [e.style.left, e.style.top, e.style.width, e.style.height].map(v => Math.round(parseFloat(v)))) };
  await p.screenshot({ path: shotDir + '/crop.png' });
  // 6 scan frame 3 at a low resolution: the big picture becomes the scan
  await p.selectOption('#s-dpi', '145');
  await p.uncheck('#s-crop_scan');  // the whole frame is scanned and cut when saved, so the picture becomes the scan (page_crop_scan.js has the other way)
  await p.check('.tile[data-i="2"] input'); out.scanLabel = await txt('b-scan');
  await p.click('#b-scan');
  await p.waitForFunction(() => !document.getElementById('busy').hidden && document.getElementById('timing').textContent.includes('on this strip'), null, { timeout: 20000 });
  out.busy = [await txt('progtxt'), await txt('timing')];
  await logHas('scan finished: 1 of 1'); await idle();
  await p.waitForFunction(() => document.getElementById('vsrc').textContent === 'Scan');
  out.scanned = { view: await view(), info: await txt('vinfo'), saved: await txt('vsaved'), badge: await p.$eval('.tile[data-i="2"] .badge', e => e.textContent), last: await txt('laststrip') };
  // 7 turn it: the crop mark turns with it
  await p.click('#b-none'); await p.click('#framesum'); await p.keyboard.press(']');
  await p.waitForFunction(() => fetch('/api/state').then(r => r.json()).then(s => s.tiles[2].turns === 3));
  await p.waitForTimeout(700);
  out.turned = { turns: (await state()).tiles[2].turns, boxAt: await p.$eval('#cropbox', e => [e.style.left, e.style.top, e.style.width, e.style.height].map(v => Math.round(parseFloat(v)))) };
  await p.keyboard.press('['); await p.waitForTimeout(700);
  // 8 enlarge and close
  await p.dblclick('#imgw');  // the picture, or the crop box over it out.zoomOpen = await p.$eval('#zoom', d => d.open); out.zoomCap = await txt('zoomcap');
  await p.keyboard.press('Escape'); out.zoomClosed = !(await p.$eval('#zoom', d => d.open));
  await p.keyboard.press(' '); out.zoomSpace = await p.$eval('#zoom', d => d.open); await p.click('#zoom'); out.zoomClick = !(await p.$eval('#zoom', d => d.open));
  // 9 keys
  await p.keyboard.press('ArrowRight'); out.right = await view(); await p.keyboard.press('x'); out.x = await txt('b-scan');
  await p.keyboard.press('ArrowDown'); out.downFrom4 = await txt('vtitle'); await p.keyboard.press('ArrowUp'); await p.keyboard.press('ArrowUp'); out.up = await txt('vtitle');
  await p.keyboard.press('Escape');
  // 10 saved settings
  await p.click('#g-presets > summary');
  out.presetEmpty = await p.$eval('#preset', e => e.options[e.selectedIndex].textContent);
  await p.click('#b-presetnew'); await p.fill('#presetname', 'Quick look'); await p.click('#b-presetsave');
  await p.waitForFunction(() => document.getElementById('preset').value === 'Quick look');
  await p.selectOption('#s-dpi', '2900'); await p.check('#s-archive');
  await p.waitForFunction(() => document.getElementById('preset').value === '');
  out.presetChanged = await p.$eval('#preset', e => e.options[e.selectedIndex].textContent);
  await p.click('#b-presetnew'); out.presetPrefill = await p.inputValue('#presetname'); await p.fill('#presetname', 'Archive 2900'); await p.keyboard.press('Enter');
  await p.waitForFunction(() => document.getElementById('preset').value === 'Archive 2900');
  await p.selectOption('#preset', 'Quick look'); await logHas('settings "Quick look" loaded');
  await p.waitForFunction(() => document.getElementById('s-dpi').value === '145' && !document.getElementById('s-archive').checked);
  out.presetLoaded = [await p.inputValue('#s-dpi'), await p.isChecked('#s-archive'), await p.$$eval('#preset option', o => o.map(e => e.textContent))];
  await p.selectOption('#preset', 'Archive 2900'); await p.waitForFunction(() => document.getElementById('s-dpi').value === '2900');
  await p.click('#b-presetdel'); out.presetAsk = await txt('presetdelmsg'); await p.click('#b-presetdelyes'); await logHas('saved settings "Archive 2900" deleted');
  await p.waitForFunction(() => document.querySelectorAll('#preset option').length === 2);
  out.presetAfterDelete = [await p.$$eval('#preset option', o => o.map(e => e.textContent)), await p.inputValue('#s-dpi')];
  await p.selectOption('#preset', 'Quick look'); await p.waitForFunction(() => document.getElementById('s-dpi').value === '145');
  // 11 cards fold and remember it; 12 help
  out.helpBefore = await p.isVisible('#g-scan .help'); await p.click('#g-scan .q'); out.helpAfter = await p.isVisible('#g-scan .help');
  await p.click('#g-film > summary'); out.filmClosed = !(await p.$eval('#g-film', d => d.open)); out.filmSum = [await p.isVisible('#sum-film'), await txt('sum-film')];
  out.sums = await p.$$eval('.sum', e => e.map(x => x.id.slice(4) + '=' + x.textContent));
  await p.reload(); await p.waitForFunction(() => document.querySelectorAll('.tile img').length === 6);
  out.afterReload = [await p.$eval('#g-film', d => d.open), await p.$eval('#g-presets', d => d.open), await p.$eval('#g-scan', d => d.open)];
  await p.click('#g-film > summary');
  // 13 a new roll through the sheet
  await p.click('#b-newroll'); out.sheet = await p.$eval('#rolldlg', d => d.open);
  await p.fill('#r-film_brand', 'Kodak'); await p.fill('#r-film_stock', 'Gold'); await p.fill('#r-film_description', 'Beach'); await p.click('#b-rollok');
  await logHas('new roll: saving'); await p.waitForFunction(() => document.getElementById('rollpick').value.startsWith('Roll 002'));
  out.roll = [await p.$$eval('#rollpick option', o => o.map(e => e.textContent)), await txt('nameex'), await p.inputValue('#rolldesc'), !(await p.$eval('#rolldlg', d => d.open))];
  await p.screenshot({ path: shotDir + '/after.png' });
  // 14 another strip: ticks and the frame looked at start again
  await p.check('.tile[data-i="4"] input'); await p.check('.tile[data-i="5"] input'); await p.click('.tile[data-i="5"] .pic');
  await post('/api/eject'); await logHas('ejected');
  await p.waitForFunction(() => document.querySelectorAll('.tile').length === 0); out.empty = [await txt('b-scan'), await p.isVisible('#noframes'), await txt('vempty')];
  await post('/api/load');
  await p.waitForFunction(() => document.querySelectorAll('.tile img').length === 4, null, { timeout: 30000 });
  out.strip2 = { cols: await p.$eval('#tiles', e => getComputedStyle(e).getPropertyValue('--cols').trim()), scan: await txt('b-scan'), ticked: await p.$$eval('.tile.ticked', e => e.length), view: await view() };
  await p.click('#stripbox > summary'); out.boxes = await p.$$eval('.box', e => e.length); await p.click('.box >> nth=2'); out.boxLook = await txt('vtitle');
  out.files = fs.readdirSync(base + '/Roll 001').sort();
  out.errs = errs; console.log(JSON.stringify(out, null, 1));
  // dark mode, for a look at the contrast
  const dark = await b.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' }); const q = await dark.newPage();
  await q.goto(url); await q.waitForFunction(() => document.querySelectorAll('.tile img').length === 4); await q.waitForTimeout(500);
  await q.screenshot({ path: shotDir + '/dark.png' });
  await b.close();
})();
