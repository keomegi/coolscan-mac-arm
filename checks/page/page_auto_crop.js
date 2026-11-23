// Auto crop on the page: the button, "Crop each frame automatically", and Crop showing its box at once.
// Start the app with COOLSCAN_DEMO_BORDERS=1 so the pretend film has edges to find.
const { chromium } = require('playwright');
const [port, shotDir] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })).newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto('http://127.0.0.1:' + port + '/');
  await p.waitForFunction(() => document.querySelectorAll('.tile img').length >= 4, null, { timeout: 30000 });
  const state = () => p.evaluate(() => fetch('/api/state').then(r => r.json()));
  const box = () => p.$eval('#cropbox', e => e.hidden ? null : [e.style.left, e.style.top, e.style.width, e.style.height].map(v => Math.round(parseFloat(v))));
  const out = {};
  await p.click('.tile[data-i="1"] .pic'); await p.waitForTimeout(600);
  out.before = { box: await box(), autoBtn: await p.isVisible('#b-autocrop'), setting: await p.isChecked('#s-auto_crop') };
  // 1 Crop on a frame without a crop: the box is there at once, over the whole frame, with its handles
  await p.click('#b-crop'); await p.waitForTimeout(500);
  out.cropMode = { box: await box(), handles: await p.$$eval('#cropbox .h', h => h.filter(x => x.getBoundingClientRect().width > 0).length), words: await p.textContent('#vsaved') };
  await p.screenshot({ path: shotDir + '/crop-box-at-once.png', clip: await p.$eval('#stage', e => { const q = e.getBoundingClientRect(); return { x: q.left, y: q.top - 50, width: q.width, height: q.height + 90 }; }) });
  // drag its left side in: that makes the crop
  const big = await p.$eval('#big', e => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  await p.mouse.move(big.x + 4, big.y + big.h / 2); await p.mouse.down(); await p.mouse.move(big.x + big.w * 0.2, big.y + big.h / 2, { steps: 6 }); await p.mouse.up();
  await p.waitForTimeout(800);
  out.afterSideDrag = { crop: (await state()).tiles[1].crop, cropMode: await p.$eval('#b-crop', e => e.getAttribute('aria-pressed')) };
  await p.keyboard.press('Escape'); await p.click('#b-cropclear'); await p.waitForTimeout(600);
  // 2 the Auto crop button
  await p.click('#b-autocrop'); await p.waitForTimeout(900);
  const s1 = await state();
  out.button = { crop: s1.tiles[1].crop, auto: s1.tiles[1].crop_auto, box: await box(), words: await p.textContent('#vsaved'),
    log: (await p.textContent('#log')).split('\n').filter(l => /auto crop/.test(l)).slice(-1) };
  await p.screenshot({ path: shotDir + '/auto-crop.png', clip: await p.$eval('#stage', e => { const q = e.getBoundingClientRect(); return { x: q.left, y: q.top - 50, width: q.width, height: q.height + 90 }; }) });
  // 3 the setting: every frame gets a crop; off again: they go
  await p.check('#s-auto_crop'); await p.waitForTimeout(1200);
  const s2 = await state();
  out.settingOn = Object.values(s2.tiles).map(t => !!t.crop && t.crop_auto);
  await p.uncheck('#s-auto_crop'); await p.waitForTimeout(1200);
  out.settingOff = Object.values((await state()).tiles).map(t => !!t.crop);
  out.errs = errs;
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
