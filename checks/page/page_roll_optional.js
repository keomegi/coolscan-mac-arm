// "Make a folder for each roll" off/on, and the overwrite confirm popup on Scan.
const { chromium } = require('playwright');
const [port, shotDir] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })).newPage();
  await p.addInitScript(() => { try { if (!localStorage.getItem('coolscan.cards')) localStorage.setItem('coolscan.cards', JSON.stringify({'g-save': true, 'g-names': true})); } catch (e) {} });
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto('http://127.0.0.1:' + port + '/');
  await p.waitForFunction(() => document.querySelectorAll('.tile img').length >= 4, null, { timeout: 30000 });
  const state = () => p.evaluate(() => fetch('/api/state').then(r => r.json()));
  const idle = () => p.waitForFunction(() => document.getElementById('busy').hidden && !document.getElementById('b-scan').disabled, null, { timeout: 120000 });
  const out = {};
  // Save as and File names are in the Settings window since 2026-11-25
  await p.evaluate(s => openSettings(s), 'Save as'); await p.waitForTimeout(200); 
  out.on = { box: await p.isChecked('#s-use_roll'), rollcard: await p.isVisible('#g-roll'), where: await p.textContent('#savewhere'), next: await p.textContent('#nameex') };
  await p.uncheck('#s-use_roll'); await p.waitForTimeout(1200);
  out.off = { saved: (await state()).settings.use_roll, rollcard: await p.isVisible('#g-roll'), where: await p.textContent('#savewhere'), next: await p.textContent('#nameex') };
  await p.click('#b-setdone'); await p.waitForTimeout(200);
  await p.click('.tile[data-i="0"] .pic'); await p.keyboard.press('x');
  await p.click('#b-scan'); await p.waitForTimeout(500); await idle(); await p.waitForTimeout(1200);
  out.after_scan = await p.textContent('#nameex');
  // type number 1 again, allow overwriting, scan: the popup must come, Cancel must not scan
  await p.evaluate(s => openSettings(s), 'File names'); await p.waitForTimeout(200); await p.check('#s-overwrite'); await p.fill('#s-start', '1'); await p.press('#s-start', 'Tab'); await p.waitForTimeout(1200); await p.click('#b-setdone'); await p.waitForTimeout(200);
  // the tick went when the scan started: tick frame 1 again. It was scanned with these settings already, so that is asked first
  await p.click('.tile[data-i="0"] .pic'); await p.keyboard.press('x');
  await p.click('#b-scan'); await p.waitForSelector('#againdlg[open]'); out.again = await p.textContent('#againmsg'); await p.click('#b-againok'); await p.waitForTimeout(800);
  out.popup = { open: await p.isVisible('#overwritedlg'), msg: await p.textContent('#overwritemsg'), list: await p.$$eval('#overwritelist li', e => e.map(x => x.textContent)) };
  await p.screenshot({ path: shotDir + '/overwrite-popup.png' });
  await p.click('#b-overcancel'); await p.waitForTimeout(800);
  out.cancel = { open: await p.isVisible('#overwritedlg'), job: (await state()).job };
  await p.click('#b-scan'); await p.waitForSelector('#againdlg[open]'); await p.click('#b-againok'); await p.waitForTimeout(800);
  await p.click('#b-overok'); await p.waitForTimeout(500); await idle(); await p.waitForTimeout(1200);
  out.replaced = (await p.textContent('#log')).split('\n').filter(l => /overwriting|saved /.test(l)).slice(-2);
  await p.evaluate(s => openSettings(s), 'File names'); await p.waitForTimeout(200); await p.uncheck('#s-overwrite'); await p.evaluate(s => openSettings(s), 'Save as'); await p.waitForTimeout(200); await p.check('#s-use_roll'); await p.waitForTimeout(1200); await p.click('#b-setdone'); await p.waitForTimeout(200);
  out.back = { rollcard: await p.isVisible('#g-roll'), next: await p.textContent('#nameex') };
  await p.screenshot({ path: shotDir + '/roll-back-on.png' });
  out.errs = errs;
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
