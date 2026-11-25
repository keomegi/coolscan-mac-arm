// Film profiles on the page: node page6.js <port> <shotDir>
const { chromium } = require('playwright');
(async () => {
  const [port, shots] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1300, height: 950 }, deviceScaleFactor: 1 });
  // the Scan quality and Save as boxes start folded since 2026-11-23: open them, as they were when this script was written
  await p.addInitScript(() => { try { if (!localStorage.getItem('coolscan.cards')) localStorage.setItem('coolscan.cards', JSON.stringify({'g-scan': true, 'g-save': true})); } catch (e) {} });
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto('http://127.0.0.1:' + port + '/');
  await p.waitForFunction(() => document.querySelectorAll('.tile img').length >= 6, null, { timeout: 30000 });
  const idle = () => p.waitForFunction(() => document.getElementById('busy').hidden && !document.getElementById('b-scan').disabled, null, { timeout: 90000 });
  const txt = id => p.$eval('#' + id, e => e.textContent);
  const out = {};
  await p.selectOption('#s-dpi', '145'); await p.waitForTimeout(300);
  out.start = { hint: await txt('profilehint'), saveDisabled: await p.$eval('#b-profilenew', e => e.disabled), delDisabled: await p.$eval('#b-profiledel', e => e.disabled),
    options: await p.$$eval('#filmprofile option', o => o.map(x => x.textContent)) };
  await p.click('#b-scan'); await p.waitForTimeout(500); await idle();
  out.afterScan = { hint: await txt('profilehint'), saveDisabled: await p.$eval('#b-profilenew', e => e.disabled) };
  await p.click('#b-profilenew'); await p.fill('#profilename', 'Kodak Gold 200'); await p.press('#profilename', 'Enter'); await p.waitForTimeout(700);
  out.saved = { msg: await txt('profilemsg'), options: await p.$$eval('#filmprofile option', o => o.map(x => x.textContent)), value: await p.$eval('#filmprofile', e => e.value) };
  await p.click('.tile[data-i="1"] .pic'); await p.waitForTimeout(300);
  const before = await p.$eval('#big', e => e.src);
  await p.selectOption('#filmprofile', 'Kodak Gold 200'); await p.waitForTimeout(900);
  out.used = { value: await p.$eval('#filmprofile', e => e.value), hint: await txt('profilehint'), sum: await txt('sum-film'),
    pictureChanged: (await p.$eval('#big', e => e.src)) !== before, del: await p.$eval('#b-profiledel', e => e.disabled) };
  await p.screenshot({ path: shots + '/profile.png' });
  await p.selectOption('#s-film', 'positive'); await p.waitForTimeout(500);
  out.slide = { hidden: await p.$eval('#profilebox', e => e.hidden), sum: await txt('sum-film') };
  await p.selectOption('#s-film', 'negative'); await p.waitForTimeout(500);
  await p.click('#g-film .q'); out.help = await p.$eval('#g-film', e => e.classList.contains('showhelp'));
  await p.click('#b-profiledel'); out.ask = await txt('profiledelmsg'); await p.click('#b-profiledelyes'); await p.waitForTimeout(700);
  out.deleted = { options: await p.$$eval('#filmprofile option', o => o.map(x => x.textContent)), value: await p.$eval('#filmprofile', e => e.value) };
  out.errs = errs;
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
