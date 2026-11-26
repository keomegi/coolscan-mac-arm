// Ticks only select. Scan takes the ticks off once it has started; while a strip is scanned the ticked frames go
// into the scan, or out of it, with the two buttons above the frames; and a frame scanned already with nothing
// changed since is asked about first.
const { chromium } = require('playwright');
const [port, shotDir] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })).newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto('http://127.0.0.1:' + port + '/');
  await p.waitForFunction(() => document.querySelectorAll('.tile img').length >= 4, null, { timeout: 30000 });
  const post = (path, body) => p.evaluate(([path, body]) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then(r => r.json()), [path, body]);
  const state = () => p.evaluate(() => fetch('/api/state').then(r => r.json()));
  const txt = id => p.textContent('#' + id);
  const idle = () => p.waitForFunction(() => document.getElementById('busy').hidden && !document.getElementById('b-scan').disabled, null, { timeout: 180000 });
  const tickedNow = () => p.$$eval('.tile.ticked', e => e.map(x => +x.dataset.i + 1).sort());
  const queue = async () => (await state()).queue.map(q => (q.i + 1) + ':' + q.state);
  const scanning = () => p.waitForFunction(() => /^Scanning/.test(document.getElementById('b-scan').textContent), null, { timeout: 30000 });
  const out = {}, bad = [];
  const check = (name, ok, extra) => { if (!ok) bad.push(name + (extra !== undefined ? ' [' + JSON.stringify(extra) + ']' : '')); };
  await post('/api/settings', { archive: false, eject_after: false });
  await p.waitForTimeout(700);

  // 1 before a scan: the two buttons are not there
  out.start = { add: await p.isVisible('#b-qadd'), out: await p.isVisible('#b-qout') };
  check('no queue buttons before a scan', !out.start.add && !out.start.out);

  // 2 tick two frames, Scan: the ticks go once it has started
  await p.check('.tile[data-i="0"] input'); await p.check('.tile[data-i="1"] input');
  out.before = [await txt('b-scan'), await tickedNow()];
  await p.click('#b-scan'); await scanning();
  out.after = { ticked: await tickedNow(), queue: await queue(), add: await p.isVisible('#b-qadd'), addOff: await p.isDisabled('#b-qadd'), outOff: await p.isDisabled('#b-qout'), sum: await txt('framesum') };
  check('ticks taken off when Scan is pressed', out.after.ticked.length === 0, out.after.ticked);
  check('the queue holds the two frames', out.after.queue.length === 2, out.after.queue);
  check('queue buttons shown, nothing ticked so both off', out.after.add && out.after.addOff && out.after.outOff);

  // 3 while it scans: a tick alone changes nothing
  await p.check('.tile[data-i="3"] input'); await p.check('.tile[data-i="4"] input'); await p.waitForTimeout(700);
  out.tickOnly = { queue: await queue(), add: await txt('b-qadd'), outOff: await p.isDisabled('#b-qout') };
  check('a tick does not add to the scan', out.tickOnly.queue.length === 2, out.tickOnly.queue);
  check('the button says what it adds', out.tickOnly.add === 'Add 2 to scan', out.tickOnly.add);
  await p.screenshot({ path: shotDir + '/queue-buttons.png' });
  // 4 Add to scan
  await p.click('#b-qadd'); await p.waitForTimeout(900);
  out.added = { queue: await queue(), ticked: await tickedNow() };
  check('Add to scan puts both at the end', out.added.queue.slice(-2).join() === '4:waiting,5:waiting', out.added.queue);
  check('ticks taken off after Add', out.added.ticked.length === 0);
  // 5 Take out of scan
  await p.check('.tile[data-i="4"] input'); await p.waitForTimeout(400);
  out.outText = await txt('b-qout');
  check('the button says what it takes out', out.outText === 'Take 1 out of scan', out.outText);
  await p.click('#b-qout'); await p.waitForTimeout(900);
  out.removed = { queue: await queue(), ticked: await tickedNow() };
  check('frame 5 taken out', !out.removed.queue.some(q => q.startsWith('5:')), out.removed.queue);
  check('ticks taken off after Take out', out.removed.ticked.length === 0);
  await idle(); await p.waitForTimeout(800);
  out.done = { saved: Object.entries((await state()).tiles).filter(([, t]) => (t.saved || []).length).map(([i]) => +i + 1), add: await p.isVisible('#b-qadd') };
  check('frames 1, 2 and 4 were scanned', out.done.saved.join() === '1,2,4', out.done.saved);
  check('queue buttons gone after the scan', !out.done.add);

  // 6 the same frames again, nothing changed: asked first
  await p.check('.tile[data-i="0"] input'); await p.check('.tile[data-i="2"] input');
  await p.click('#b-scan'); await p.waitForSelector('#againdlg[open]', { timeout: 5000 });
  out.ask = { msg: await txt('againmsg'), others: await txt('b-againothers'), job: (await state()).job };
  check('asked about frame 1 only', /^Frame 1 was already scanned/.test(out.ask.msg), out.ask.msg);
  check('offers to scan only the other one', out.ask.others === 'Scan only frame 3', out.ask.others);
  check('nothing scans while it asks', !out.ask.job);
  await p.screenshot({ path: shotDir + '/scan-again.png' });
  await p.click('#b-againcancel'); await p.waitForTimeout(500);
  out.cancel = { job: (await state()).job, ticked: await tickedNow() };
  check('Cancel: no scan, ticks kept', !out.cancel.job && out.cancel.ticked.join() === '1,3', out.cancel);
  await p.click('#b-scan'); await p.waitForSelector('#againdlg[open]'); await p.click('#b-againothers'); await scanning();
  out.others = { queue: await queue(), ticked: await tickedNow() };
  check('Scan only the other: frame 3 alone', out.others.queue.length === 1 && out.others.queue[0].startsWith('3:'), out.others.queue);
  // 7 adding a scanned frame while scanning: asked too
  await p.check('.tile[data-i="1"] input'); await p.click('#b-qadd'); await p.waitForSelector('#againdlg[open]', { timeout: 5000 });
  out.askAdd = { msg: await txt('againmsg'), othersShown: await p.isVisible('#b-againothers') };
  check('Add to scan asks about frame 2', /^Frame 2 was already scanned/.test(out.askAdd.msg) && !out.askAdd.othersShown, out.askAdd);
  await p.click('#b-againok'); await p.waitForTimeout(900);
  out.addAgain = await queue();
  check('Scan again: frame 2 added', out.addAgain.some(q => q.startsWith('2:')), out.addAgain);
  await idle(); await p.waitForTimeout(600);
  // 8 a setting of its own on frame 1: not asked
  await post('/api/framesettings', { frames: [0], changes: { dpi: 1450 } }); await p.waitForTimeout(700);
  await p.check('.tile[data-i="0"] input'); await p.click('#b-scan'); await p.waitForTimeout(800);
  out.changed = { asked: await p.isVisible('#againdlg'), job: (await state()).job };
  check('a changed setting: scanned without asking', !out.changed.asked && out.changed.job === 'scan', out.changed);
  await idle();
  // 9 whole strip with frames scanned already: asked, Scan again does them all
  await post('/api/framesettings', { frames: [0], reset: true }); await p.waitForTimeout(500);
  await p.click('#b-scan'); await p.waitForSelector('#againdlg[open]', { timeout: 5000 });
  out.whole = { msg: await txt('againmsg'), others: await txt('b-againothers') };
  check('whole strip: names the frames scanned the same', /^Frames 2, 3 and 4 were/.test(out.whole.msg), out.whole.msg);
  await p.click('#b-againcancel');
  out.bad = bad; out.errs = errs;
  console.log(JSON.stringify(out, null, 1));
  await b.close();
  if (bad.length) process.exit(1);
})();
