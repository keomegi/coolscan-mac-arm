// Changing a crop on the page: drag a side, a corner, or the middle of the box.
const { chromium } = require('playwright');
const [port, shotDir] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })).newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto('http://127.0.0.1:' + port + '/');
  await p.waitForFunction(() => document.querySelectorAll('.tile img').length >= 4, null, { timeout: 30000 });
  const post = (path, body) => p.evaluate(([path, body]) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then(r => r.json()), [path, body]);
  const crop = async () => (await p.evaluate(() => fetch('/api/state').then(r => r.json()))).tiles[1].crop;
  const round = c => c && c.map(v => Math.round(v * 100) / 100);
  await p.click('.tile[data-i="1"] .pic');
  await post('/api/crop', { frame: 1, rect: [0.2, 0.2, 0.6, 0.6] });
  await p.waitForFunction(() => !document.getElementById('cropbox').hidden, null, { timeout: 5000 });
  await p.waitForTimeout(800);
  await p.$eval('#stage', e => e.scrollIntoView({ block: 'center' })); await p.waitForTimeout(200);   // the frame cards can push it below the window
  const big = await p.$eval('#big', e => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  const pt = (fx, fy) => [big.x + fx * big.w, big.y + fy * big.h];
  const drag = async (from, to) => { await p.mouse.move(...from); await p.mouse.down(); await p.mouse.move(...to, { steps: 6 }); await p.mouse.up(); await p.waitForTimeout(700); };
  const out = { start: round(await crop()) };
  out.cursors = await p.$$eval('#cropbox .h', hs => Object.fromEntries(hs.map(h => [h.dataset.h, getComputedStyle(h).cursor])));
  out.boxCursor = await p.$eval('#cropbox', e => getComputedStyle(e).cursor);
  out.hint = await p.textContent('#vsaved');
  await drag(pt(0.6, 0.4), pt(0.8, 0.45));            // right side
  out.right = round(await crop());
  await drag(pt(0.4, 0.2), pt(0.42, 0.1));            // top side
  out.top = round(await crop());
  await drag(pt(0.2, 0.6), pt(0.1, 0.7));             // bottom-left corner
  out.corner = round(await crop());
  await drag(pt(0.45, 0.4), pt(0.55, 0.45));          // the middle: move
  out.moved = round(await crop());
  await drag(pt(0.5, 0.4), pt(1.3, 0.4));             // moved past the edge: stops at it
  out.edge = round(await crop());
  const c = await crop();
  await drag(pt(c[0], 0.4), pt(c[2] + 0.2, 0.4));     // left side past the right side: keeps the smallest width
  out.squeezed = round(await crop());
  await p.screenshot({ path: shotDir + '/crop-handles.png', clip: { x: big.x - 20, y: big.y - 60, width: big.w + 40, height: big.h + 80 } });
  // turned a quarter: the crop on the film follows what is dragged on screen
  await post('/api/crop', { frame: 1, rect: [0.2, 0.2, 0.6, 0.6] });
  await p.keyboard.press(']'); await p.waitForTimeout(1500);
  const big2 = await p.$eval('#big', e => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  const box2 = await p.$eval('#cropbox', e => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  const before = await crop();
  await drag([box2.x + box2.w, box2.y + box2.h / 2], [box2.x + box2.w + big2.w * 0.1, box2.y + box2.h / 2]);
  out.turned = { before: round(before), after: round(await crop()) };
  // while picking a focus point, a click on the crop picks the point and leaves the crop alone
  await p.click('#g-focus summary').catch(() => {});
  out.errs = errs;
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
