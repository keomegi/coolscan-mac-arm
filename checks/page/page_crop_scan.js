// The "Scan only the cropped part" box on the page: shown, saved, hidden in Archive mode, and the words under the picture.
const { chromium } = require('playwright');
const [port, shotDir] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })).newPage();
  // the Scan quality and Save as boxes start folded since 2026-11-23: open them, as they were when this script was written
  await p.addInitScript(() => { try { if (!localStorage.getItem('coolscan.cards')) localStorage.setItem('coolscan.cards', JSON.stringify({'g-scan': true, 'g-save': true})); } catch (e) {} });
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto('http://127.0.0.1:' + port + '/');
  await p.waitForFunction(() => document.querySelectorAll('.tile img').length >= 4, null, { timeout: 30000 });
  // written when Archive mode started off (it starts on since 2026-11-24): switch it off first
  await p.evaluate(() => fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ archive: false }) }));
  await p.waitForFunction(() => !document.getElementById('s-archive').checked, null, { timeout: 10000 });
  const post = (path, body) => p.evaluate(([path, body]) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then(r => r.json()), [path, body]);
  const state = () => p.evaluate(() => fetch('/api/state').then(r => r.json()));
  const idle = () => p.waitForFunction(() => document.getElementById('busy').hidden && !document.getElementById('b-scan').disabled, null, { timeout: 120000 });
  const out = {};
  await p.evaluate(s => openSettings(s), 'Save as'); await p.waitForTimeout(200); 
  out.box = { visible: await p.isVisible('#s-crop_scan'), checked: await p.isChecked('#s-crop_scan') };
  await p.click('#b-setdone'); await p.waitForTimeout(200);
  await p.click('.tile[data-i="1"] .pic');
  await post('/api/crop', { frame: 1, rect: [0.2, 0.15, 0.7, 0.85] });
  await p.waitForTimeout(1500);
  out.words_on = await p.textContent('#vsaved');
  await p.evaluate(s => openSettings(s), 'Save as'); await p.waitForTimeout(200); await p.uncheck('#s-crop_scan'); await p.waitForTimeout(1200); await p.click('#b-setdone'); await p.waitForTimeout(200);
  out.saved_off = (await state()).settings.crop_scan;
  out.words_off = await p.textContent('#vsaved');
  await p.evaluate(s => openSettings(s), 'Save as'); await p.waitForTimeout(200); await p.check('#s-crop_scan'); await p.waitForTimeout(800); await p.click('#b-setdone'); await p.waitForTimeout(200);
  await p.check('#s-archive', { force: true }); await p.waitForTimeout(1200);
  await p.evaluate(s => openSettings(s), 'Save as'); await p.waitForTimeout(200); 
  out.archive = { box_visible: await p.isVisible('#s-crop_scan'), words: await p.textContent('#vsaved') };
  await p.click('#b-setdone'); await p.waitForTimeout(200);
  await p.uncheck('#s-archive', { force: true }); await p.waitForTimeout(1200);
  await p.click('#b-scan'); await p.waitForTimeout(500); await idle(); await p.waitForTimeout(1200);
  out.badge = await p.$$eval('.tile[data-i="1"] .badge', e => e.map(x => x.textContent));
  out.after = { src: await p.isVisible('#vsrc') ? await p.textContent('#vsrc') : null, info: (await state()).tiles[1].info };
  out.log = (await p.textContent('#log')).split('\n').filter(l => /crop/.test(l)).slice(-4);
  await p.screenshot({ path: shotDir + '/crop-scan.png' });
  out.errs = errs;
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
