// The re-develop page: node page_develop.js <port> <shotDir>   (the app must run with --demo, and the
// roll must already hold RAW files: scan a strip in Archive mode first)
const { chromium } = require('playwright');
(async () => {
  const [port, shots] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1300, height: 1000 }, deviceScaleFactor: 2 });
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  const base = 'http://127.0.0.1:' + port;
  const txt = id => p.$eval('#' + id, e => e.textContent);
  const idle = () => p.waitForFunction(() => document.getElementById('busy').hidden, null, { timeout: 180000 });
  const drawn = () => p.waitForFunction(() => {
    const i = [...document.querySelectorAll('#over img, #pieces img')];
    return i.length >= 5 && i.every(x => x.complete && x.naturalWidth > 0);
  }, null, { timeout: 60000 });
  const out = {};
  await p.goto(base + '/develop');
  await p.waitForTimeout(800);
  out.start = { title: await p.title(), status: await txt('status'), rolls: await p.$$eval('#roll option', o => o.map(x => x.textContent)),
    runDisabled: await p.$eval('#b-run', e => e.disabled), runText: await txt('b-run'), tableHidden: await p.$eval('#filetable', e => e.hidden),
    nolook: await p.$eval('#nolook', e => !e.hidden), spaces: await p.$$eval('#h-colour_space option', o => o.map(x => x.textContent)),
    profiles: await p.$$eval('#h-film_profile option', o => o.map(x => x.textContent)),
    dustMethods: await p.$$eval('#h-dust_method option', o => o.map(x => x.value)) };
  await p.screenshot({ path: shots + '/develop-empty.png' });

  // 1. the files of the roll
  await p.click('#b-look');
  await idle(); await p.waitForTimeout(600);
  out.files = { rows: await p.$$eval('#files tr', r => r.map(x => [...x.cells].slice(1).map(c => c.textContent))),
    current: await p.$$eval('#files tr.cur', r => r.map(x => x.dataset.name)), status: await txt('status'),
    msg: await txt('foldermsg'), runText: await txt('b-run') };
  // the four pictures are made on their own
  await idle(); await drawn();
  out.look = { hint: await txt('lookhint'), views: await p.$$eval('#views button', v => v.map(x => x.textContent + (x.getAttribute('aria-pressed') === 'true' ? ' *' : ''))),
    pieces: await p.$$eval('#pieces figure', f => f.map(x => x.querySelector('figcaption').textContent + ' ' + x.querySelector('img').naturalWidth + 'x' + x.querySelector('img').naturalHeight)),
    whole: await p.$eval('#overimg', i => i.naturalWidth + 'x' + i.naturalHeight), notes: await p.$$eval('#notes p', n => n.map(x => x.textContent)) };
  await p.screenshot({ path: shots + '/develop-look.png', fullPage: true });

  // switching the big picture between the four versions
  const wasSrc = await p.$eval('#overimg', i => i.src);
  await p.click('#views button[data-view="raw"]'); await p.waitForTimeout(700); await drawn();
  out.switched = { pressed: await p.$$eval('#views button', v => v.map(x => x.getAttribute('aria-pressed'))), changed: (await p.$eval('#overimg', i => i.src)) !== wasSrc,
    on: await p.$$eval('#pieces figure.on figcaption', f => f.map(x => x.textContent)) };

  // clicking a spot moves the piece in all four
  const before = await p.$$eval('#pieces img', i => i.map(x => x.src));
  const box = await (await p.$('#overimg')).boundingBox();
  await p.mouse.click(box.x + 0.3 * box.width, box.y + 0.6 * box.height);
  await p.waitForTimeout(900); await drawn();
  out.piece = { moved: (await p.$$eval('#pieces img', i => i.map(x => x.src))).every((s, k) => s !== before[k]),
    mark: await p.$eval('#mark', m => m.style.left + ' ' + m.style.top),
    state: await p.evaluate(() => fetch('/api/state').then(r => r.json()).then(s => s.develop.piece.map(v => +v.toFixed(2)))) };
  await p.click('#pieces img'); await p.waitForTimeout(300);
  out.zoom = { open: await p.$eval('#zoom', d => d.open), cap: await txt('zoomcap') };
  await p.keyboard.press('Escape'); await p.waitForTimeout(200);
  out.zoom.closed = !(await p.$eval('#zoom', d => d.open));

  // another file, and another way of developing
  const second = (await p.$$eval('#files tr', r => r.map(x => x.dataset.name)))[1];
  await p.click(`#files tr[data-name="${second}"] td:nth-child(2)`);
  await idle(); await p.waitForTimeout(500); await drawn();
  out.second = { hint: await txt('lookhint'), current: await p.$$eval('#files tr.cur', r => r.map(x => x.dataset.name)) };
  await p.selectOption('#h-tone', 'nikon'); await p.waitForTimeout(600); await idle(); await drawn();
  const tonePic = await p.$eval('#overimg', i => i.src);
  await p.uncheck('#h-clean'); await p.waitForTimeout(600); await idle(); await drawn();
  out.how = { tone: await p.$eval('#h-tone', e => e.value), clean: await p.$eval('#h-clean', e => e.checked),
    notes: await p.$$eval('#notes p', n => n.map(x => x.textContent)), changed: (await p.$eval('#overimg', i => i.src)) !== tonePic,
    state: await p.evaluate(() => fetch('/api/state').then(r => r.json()).then(s => s.develop.how)) };
  await p.check('#h-clean'); await p.selectOption('#h-tone', 'flat'); await p.waitForTimeout(600); await idle();

  // turning and cropping
  await p.click('#b-left'); await p.waitForTimeout(800); await drawn();
  out.turned = { size: await p.$eval('#overimg', i => i.naturalWidth + 'x' + i.naturalHeight),
    state: await p.evaluate(() => fetch('/api/state').then(r => r.json()).then(s => s.develop.frames)) };
  await p.click('#b-crop');
  const box2 = await (await p.$('#overimg')).boundingBox();
  await p.mouse.move(box2.x + 0.2 * box2.width, box2.y + 0.2 * box2.height);
  await p.mouse.down();
  await p.mouse.move(box2.x + 0.7 * box2.width, box2.y + 0.8 * box2.height, { steps: 8 });
  await p.mouse.up();
  await p.waitForTimeout(800);
  out.cropped = { pressed: await p.$eval('#b-crop', e => e.getAttribute('aria-pressed')), clearShown: await p.$eval('#b-cropclear', e => !e.hidden),
    crop: await p.evaluate(() => fetch('/api/state').then(r => r.json()).then(s => s.develop.frames)) };
  await p.screenshot({ path: shots + '/develop-crop.png', fullPage: true });

  // 4. develop two ticked files
  await p.check('#files tr:nth-child(1) input'); await p.check('#files tr:nth-child(2) input'); await p.waitForTimeout(400);
  await p.check('#extras'); await p.waitForTimeout(400);
  out.ticked = { runText: await txt('b-run') };
  await p.click('#b-run'); await p.waitForTimeout(500);
  out.running = { busy: await p.$eval('#busy', e => !e.hidden), stop: await p.$eval('#b-stop', e => !e.disabled), progress: await txt('progtxt') };
  await idle(); await p.waitForTimeout(600);
  out.saved = { results: await p.$$eval('#results p', r => r.map(x => x.textContent)), log: (await txt('log')).split('\n').slice(-3) };
  await p.screenshot({ path: shots + '/develop-saved.png', fullPage: true });

  // a folder that is not there
  await p.fill('#path', '/nowhere/at/all'); await p.click('#b-look'); await idle(); await p.waitForTimeout(500);
  out.badfolder = { msg: await txt('foldermsg'), stillThere: await p.$$eval('#files tr', r => r.length) };

  out.errs = errs;
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
