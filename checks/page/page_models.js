// Other Coolscan models on the page: node page_models.js <port> <shotDir> <model>
// The app runs with the pretend scanner as that model (--demo-model / COOLSCAN_DEMO_MODEL). Checks the
// controls the model should have, the long-strip row (more than 6 frames), and scans two frames.
// Prints a JSON report; "problems" lists what is wrong (empty when all is well).
const { chromium } = require('playwright');
(async () => {
  const [port, shots, model] = process.argv.slice(2);
  const base = 'http://127.0.0.1:' + port;
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: +(process.env.PAGE_WIDTH || 1300), height: 950 }, deviceScaleFactor: 1 });
  // the left cards open one at a time: a control in a closed card is reached by opening its card first
  for (const m of ['click', 'selectOption', 'fill', 'check', 'uncheck']) { const f = p[m].bind(p); p[m] = async (sel, ...a) => {
    if (typeof sel === 'string') await p.evaluate(s => { let e = null; try { e = document.querySelector(s); } catch (x) { return; } const d = e && e.closest('#side > details.card'); if (d && !d.open && (!e.closest('summary') || e.matches('.q'))) d.open = true; }, sel);
    return f(sel, ...a); }; }
  await p.addInitScript(() => { try { localStorage.setItem('coolscan.cards', JSON.stringify({'g-scan': true, 'g-film': true})); } catch (e) {} });
  const errs = [], problems = [];
  p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  const want = (ok, what) => { if (!ok) problems.push(what); };
  const state = async () => (await p.request.get(base + '/api/state')).json();
  const idle = () => p.waitForFunction(() => document.getElementById('busy').hidden && !document.getElementById('b-scan').disabled, null, { timeout: 180000 });
  await p.goto(base + '/');
  await p.waitForFunction(() => typeof S !== 'undefined' && S && S.frames && S.frames.length > 0 && document.querySelectorAll('.tile').length === S.frames.length, null, { timeout: 60000 });
  await p.waitForTimeout(500); await idle();
  const st = await state(), c = st.caps, n = st.frames.length;
  const out = { model, product: c.product, holder: c.holder, frames: n };
  out.status = await p.$eval('#status', e => e.textContent);
  out.dpis = await p.$$eval('#s-dpi option', o => o.map(x => +x.value));
  out.samples = await p.$$eval('#s-samples option', o => o.map(x => x.textContent));
  out.superfine = !(await p.$eval('#superfinerow', e => e.hidden));
  out.formats = (await p.$eval('#fmtrow', e => e.hidden)) ? [] : await p.$$eval('#s-format option', o => o.map(x => x.textContent));
  out.long = await p.$eval('#tiles', e => e.classList.contains('long'));
  out.longnav = (await p.$eval('#longnav', e => e.hidden)) ? null : await p.$eval('#longpos', e => e.textContent);
  out.picHeight = await p.$eval('.tile .pic', e => Math.round(e.getBoundingClientRect().height));
  out.tilesHeight = await p.$eval('#tiles', e => Math.round(e.getBoundingClientRect().height));
  await p.screenshot({ path: shots + '/' + model.replace(':', '-') + '-strip.png' });

  // what this model should show
  want(out.dpis[0] === c.optical_dpi, 'highest dpi is the optical ' + c.optical_dpi);
  want(out.superfine === !!c.multi_line, 'Superfine shown only where the sensor reads lines at once');
  want(!!out.long === n > 6, 'long-strip row only for more than 6 frames');
  out.column = await p.evaluate(() => innerWidth >= 1080);   // wide window: the frames are a column beside the big picture
  if (out.column) {
    want(out.longnav === null, 'no sideways row in the column');
    want(out.picHeight === 104, 'column frames are 104 px');
    if (n > 6) want(await p.$eval('#frames', e => e.scrollHeight > e.clientHeight), 'a long strip scrolls down the column');
  } else if (n > 6) {
    want(out.picHeight >= 200, 'bigger frames on a long strip');
    want(out.tilesHeight < 400, 'a long strip stays one row');
    want(/^Showing 1/.test(out.longnav || ''), 'the row says which frames it shows');
    await p.click('#b-longr'); await p.waitForTimeout(900);
    out.afterScroll = await p.$eval('#longpos', e => e.textContent);
    want(out.afterScroll !== out.longnav && (await p.$eval('#tiles', e => e.scrollLeft)) > 0, '▶ scrolls the row');
    // the arrow keys move along the strip and keep the frame looked at in view
    await p.click('.tile[data-i="' + (await p.$eval('#tiles .tile', e => e.dataset.i)) + '"] .pic');
    for (let k = 0; k < n; k++) await p.keyboard.press('ArrowRight');
    await p.waitForTimeout(2500);
    const inView = await p.$eval('#tiles', box => { const t = box.querySelector('.tile.cur'), r = t.getBoundingClientRect(), q = box.getBoundingClientRect(); return r.left >= q.left - 2 && r.right <= q.right + 2; });
    if (!inView) out.inViewDebug = await p.evaluate(() => { const box = document.getElementById('tiles'), t = box.querySelector('.tile.cur'); return { cur: t && t.dataset.i, left: t && t.offsetLeft, sl: box.scrollLeft, w: box.clientWidth, sw: box.scrollWidth, active: document.activeElement && document.activeElement.className }; });
    want(inView, 'the frame looked at is scrolled into view');
  }
  if (c.film_formats && c.film_formats.length) {
    /* the left cards open one at a time */ await p.evaluate(s => { const d = document.querySelector(s).closest('details.card'); if (d && !d.open) d.open = true; }, '#s-format'); await p.waitForTimeout(150); await p.selectOption('#s-format', c.film_formats.includes('67') ? '67' : c.film_formats[0]);
    await p.waitForTimeout(800);
    out.frameLongIn = (await state()).caps.frame_long_in;
    want(out.frameLongIn > 1.5, 'a 120 format sends its frame length');
  }

  // scan two frames (the last two on a long strip, to see the row follow the scan), at a low resolution
  await p.selectOption('#s-dpi', String(out.dpis.find(d => d <= 500) || out.dpis[out.dpis.length - 1])); await p.waitForTimeout(300);
  if (out.superfine) { await p.check('#s-superfine'); await p.waitForTimeout(500); out.chips = await p.$eval('#setchips', e => e.textContent); want(/superfine/.test(out.chips), 'Superfine shows in the Next scan line'); }
  const pick = n > 1 ? [n - 2, n - 1] : [];
  for (const i of pick) await p.click('.tile[data-i="' + i + '"] .tick');
  await p.click('#b-scan');
  let followed = n <= 6;
  const end = Date.now() + 180000;
  await p.waitForFunction(() => S && S.job === 'scan', null, { timeout: 30000 });   // the scan has started
  while (Date.now() < end) {
    const s = await p.evaluate(() => ({ busy: !document.getElementById('busy').hidden,
      seen: (() => { const box = document.getElementById('tiles'), t = box.querySelector('.tile.now'); if (!t) return null; const r = t.getBoundingClientRect(), q = box.getBoundingClientRect(), f = document.getElementById('frames').getBoundingClientRect();
        return r.left >= q.left - 2 && r.right <= q.right + 2 && r.top >= Math.max(0, f.top) - 2 && r.bottom <= Math.min(innerHeight, f.bottom) + 2; })() }));
    if (s.seen) followed = true;
    if (!s.busy) break;
    await p.waitForTimeout(300);
  }
  await idle();
  want(followed, 'the frame being scanned is scrolled into view');
  const after = await state();
  out.saved = (pick.length ? pick : [0]).map(i => (after.tiles[i].saved || []).reduce((k, x) => k + (x.files || []).length, 0));
  want(out.saved.every(k => k > 0), 'each scanned frame saved its files');
  out.log = after.log.filter(l => /nkscan has run|error|failed/i.test(l)).slice(-3);
  want(!after.log.some(l => /error|failed/i.test(l) && !/nkscan has run/.test(l)), 'no errors in the Activity list');
  await p.screenshot({ path: shots + '/' + model.replace(':', '-') + '-scanned.png' });
  out.errs = errs; out.problems = problems.concat(errs.length ? ['page errors'] : []);
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
