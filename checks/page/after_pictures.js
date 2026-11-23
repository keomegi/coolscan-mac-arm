// Takes the "after" pictures of the new screen: node after.js <port> <outDir>
const { chromium } = require('playwright');
(async () => {
  const [port, out] = process.argv.slice(2);
  const url = 'http://127.0.0.1:' + port + '/';
  const b = await chromium.launch();
  const errs = [];
  const open = async (scheme) => {
    const p = await b.newPage({ viewport: { width: 1300, height: 900 }, deviceScaleFactor: 2, colorScheme: scheme });
    p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
    await p.goto(url);
    await p.waitForFunction(() => document.querySelectorAll('.tile img').length >= 6, null, { timeout: 30000 });
    return p;
  };
  const idle = p => p.waitForFunction(() => document.getElementById('busy').hidden && !document.getElementById('b-scan').disabled, null, { timeout: 60000 });
  const p = await open('light');
  await p.selectOption('#s-dpi', '290'); await p.waitForTimeout(300);
  // look at frame 2, give it a preview scan, then scan frames 2 and 3
  await p.click('.tile[data-i="1"] .pic'); await p.waitForTimeout(300);
  await p.click('#b-sharper'); await p.waitForTimeout(400); await idle(p);
  await p.click('.tile[data-i="1"] .tick input'); await p.click('.tile[data-i="2"] .tick input'); await p.waitForTimeout(200);
  await p.click('#b-scan'); await p.waitForTimeout(500); await idle(p);
  await p.click('#b-none'); await p.waitForTimeout(200);
  await p.click('.tile[data-i="3"] .pic'); await p.click('#b-sharper'); await p.waitForTimeout(400); await idle(p);
  await p.click('.tile[data-i="4"] .tick input'); await p.click('.tile[data-i="5"] .tick input');
  await p.click('.tile[data-i="1"] .pic'); await p.waitForTimeout(800);
  await p.screenshot({ path: out + '/after-design-pass.png' });
  console.log(JSON.stringify({ tiles: await p.$$eval('.tile', ts => ts.map(t => t.className + ' ' + (t.querySelector('.badge') || {}).textContent)),
    view: await p.$eval('#vtitle', e => e.textContent) + ' / ' + await p.$eval('#vsrc', e => e.textContent) + ' / ' + await p.$eval('#vinfo', e => e.textContent),
    roll: await p.$eval('#rollfilm', e => e.textContent), scan: await p.$eval('#b-scan', e => e.textContent) }));
  const q = await open('dark');
  await q.click('.tile[data-i="1"] .pic'); await q.waitForTimeout(800);
  await q.screenshot({ path: out + '/after-design-pass-dark.png' });
  console.log(JSON.stringify(errs));
  await b.close();
})();
