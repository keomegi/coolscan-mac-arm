// Film correction in the page: gone from the scanning screen; in Scanned pictures the explanation, Film base
// with "This picture" / "Whole roll", the Film correction switch, and the film mark under the small pictures.
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const path = require('path');
const [scans, shotDir, port] = process.argv.slice(2);
const py = process.env.COOLSCAN_PYTHON || 'python3';
execFileSync(py, ['-c', `
import sys, json, pathlib, numpy as np
sys.path.insert(0, ${JSON.stringify(path.resolve(__dirname, '..', '..'))})
from coolscan_app.imaging.files import write_tiff
from coolscan_app.imaging.colour import invert_negative, convert_planes, frame_colour
EXP = {"red": 900, "green": 1300, "blue": 2700}
MASK = [0.30, 0.62, 1.05]
CROP = [0.08, 0.0, 0.92, 1.0]
def negative(seed, rows=320, cols=480):
    g = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:rows, 0:cols] / np.array([rows, cols])[:, None, None]
    out = []
    for c, k in enumerate(("red", "green", "blue")):
        scene = 0.15 + 0.7 * (0.5 + 0.5 * np.sin(3 * xx + 2 * yy + c + seed))
        d = MASK[c] + 0.65 * (np.log10(np.clip(scene, 0.03, 1)) + 1.6)
        d[:, :30] = MASK[c]; d[:, -30:] = MASK[c]
        d += g.normal(0, 0.004, d.shape)
        out.append(np.clip(10 ** (-d) * EXP[k] / 1000 * 60000, 1, 65535).astype(np.uint16))
    return out
roll = pathlib.Path(sys.argv[1]) / "Roll 007 - Orwocolor"
(roll / "RAW").mkdir(parents=True); (roll / "Positive").mkdir()
frames = {}
for n in range(1, 5):
    stem = f"20261125_{n:03d}_R007"
    neg = negative(n)
    write_tiff(roll / "RAW" / f"{stem}.tif", neg + [np.full_like(neg[0], 60000)], 2900, None,
               meta={"coolscan": {"film": "negative", "dpi": 2900, "exposures": EXP, "turns": 0, "crop": CROP, "roll": roll.name}})
    cols = neg[0].shape[1]
    cut = [p[:, int(round(CROP[0] * cols)):int(round(CROP[2] * cols))] for p in neg]
    pos, icc = convert_planes([(v * 65535 + 0.5).astype(np.uint16) for v in invert_negative(cut, "flat")], "ProPhoto RGB")
    write_tiff(roll / "Positive" / f"{stem}.tif", pos, 2900, icc)
    frames[stem] = {**frame_colour(neg, EXP), "exposures": EXP}
(roll / "FilmColour.json").write_text(json.dumps({"frames": frames}))
`, scans]);
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })).newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  const bad = []; const check = (name, ok, extra) => { if (!ok) bad.push(name + (extra !== undefined ? ' [' + JSON.stringify(extra) + ']' : '')); };
  const base = 'http://127.0.0.1:' + port;
  const marks = () => p.$$eval('#grid .pic .fm', e => e.length);
  const msg = re => p.waitForFunction(r => new RegExp(r).test(document.getElementById('cropmsg').textContent), re, { timeout: 20000 });
  // the scanning screen: no film colour box any more
  await p.goto(base + '/');
  await p.waitForSelector('#g-film');
  check('the scanning screen has no film colour box', !(await p.$('#profilebox')) && !(await p.$('#filmprofile')) && !(await p.$('#b-profilenew')));
  check('its Film help points to Scanned pictures', /Scanned pictures/.test(await p.textContent('#g-film .help')));
  // Scanned pictures: the roll
  await p.goto(base + '/library');
  await p.waitForSelector('#rolllist button');
  await p.click('#rolllist button:has-text("Roll 007")');
  await p.waitForFunction(() => document.querySelectorAll('#grid .pic').length === 4, null, { timeout: 15000 });
  check('the Film correction row and its explanation are there', await p.isVisible('#fcolrow') && await p.isVisible('#fcolhelp summary'));
  await p.click('#fcolhelp summary');
  const help = await p.textContent('#fcolhelp');
  check('the explanation names the steps', /Film base/.test(help) && /clear film/.test(help) && /This picture/.test(help) && /Whole roll/.test(help));
  check('nothing is corrected at first', await marks() === 0 && await p.inputValue('#fcol') === 'frame' && !(await p.isVisible('#b-fcolall')));
  await p.screenshot({ path: path.join(shotDir, 'film-roll-help.png') });
  // one picture: Film base, then This picture
  await p.click('#grid .pic');
  await p.waitForSelector('#viewer:not([hidden])');
  await p.waitForSelector('#b-base:not([hidden])');
  check('the button has no dots', (await p.textContent('#b-base')).trim() === 'Film base');
  check('no Film correction switch before a base is picked', !(await p.isVisible('#b-own')));
  await p.click('#b-base');
  await msg('whole frame as scanned');
  await p.waitForFunction(() => { const i = document.getElementById('big'); return i.complete && i.naturalWidth > 0 && /negative/.test(i.src); }, null, { timeout: 20000 });
  const box = await p.$eval('#big', e => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  await p.mouse.click(box.x + box.w * 0.03, box.y + box.h * 0.5);
  await p.waitForSelector('#baseask:not([hidden])', { timeout: 20000 });
  check('after the click it asks: this picture or the whole roll', await p.isVisible('#b-baseone') && await p.isVisible('#b-baseall') && await p.isVisible('#b-basenone'));
  check('nothing is put on before choosing', !(await p.$eval('#b-own', e => e.getAttribute('aria-pressed') === 'true')));
  await p.screenshot({ path: path.join(shotDir, 'film-base-ask.png') });
  await p.click('#b-baseone');
  await msg('on for this picture only');
  check('the question is gone', !(await p.isVisible('#baseask')));
  await p.waitForFunction(() => document.getElementById('b-own').getAttribute('aria-pressed') === 'true', null, { timeout: 15000 });
  check('Show original is offered', await p.isVisible('#b-orig'));
  await p.click('#b-close');
  check('one small picture carries the film mark', await marks() === 1, await marks());
  check('the mark says film corrected', /film corrected/.test(await p.textContent('#grid .pic .fm')));
  check('the roll row says 1 of 4, and offers the whole roll', /1 of 4/.test(await p.textContent('#fcolmsg')) && await p.isVisible('#b-fcolall'), await p.textContent('#fcolmsg'));
  await p.screenshot({ path: path.join(shotDir, 'film-one-picture.png') });
  // the whole roll, from the roll row
  await p.click('#b-fcolall');
  await p.waitForFunction(() => document.querySelectorAll('#grid .pic .fm').length === 4, null, { timeout: 20000 });
  check('whole roll: the row says so', /whole roll \(4 pictures\)/.test(await p.textContent('#fcolmsg')) && await p.inputValue('#fcol') === 'base' && !(await p.isVisible('#b-fcolall')), await p.textContent('#fcolmsg'));
  await p.screenshot({ path: path.join(shotDir, 'film-whole-roll.png') });
  // off for one picture with the switch
  await p.click('#grid .pic >> nth=1');
  await p.waitForSelector('#viewer:not([hidden])');
  await p.waitForFunction(() => document.getElementById('b-own').getAttribute('aria-pressed') === 'true' && !document.getElementById('b-own').hidden, null, { timeout: 15000 });
  await p.click('#b-own');
  await msg('off for this picture');
  await p.click('#b-own');
  await msg('on for this picture');
  await p.click('#b-own');
  await msg('off for this picture');
  await p.click('#b-close');
  check('three marks left', await marks() === 3, await marks());
  // Film base again, then Whole roll from the question
  await p.click('#grid .pic >> nth=1');
  await p.waitForSelector('#b-base:not([hidden])');
  await p.click('#b-base');
  await msg('whole frame as scanned');
  await p.waitForFunction(() => { const i = document.getElementById('big'); return i.complete && i.naturalWidth > 0 && /negative/.test(i.src); }, null, { timeout: 20000 });
  const box2 = await p.$eval('#big', e => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  await p.mouse.click(box2.x + box2.w * 0.5, box2.y + box2.h * 0.5);
  await msg('not clear film|uneven|thinner');
  check('a click on the picture itself is explained', true);
  if (!(await p.isVisible('#baseask'))) {
    await p.mouse.click(box2.x + box2.w * 0.97, box2.y + box2.h * 0.4);
    await p.waitForSelector('#baseask:not([hidden])', { timeout: 20000 });
  }
  await p.click('#b-baseall');
  await msg('on for the whole roll');
  await p.click('#b-close');
  check('whole roll from the question: every picture, also the one switched off', await marks() === 4, await marks());
  // ticked pictures: Off and On
  await p.click('#grid .pic >> nth=0 >> .tick'); await p.click('#grid .pic >> nth=2 >> .tick');
  await p.waitForSelector('#actions:not([hidden])');
  await p.click('#a-own');
  await p.waitForFunction(() => document.querySelectorAll('#grid .pic .fm').length === 2, null, { timeout: 20000 });
  await p.click('#a-rollcol');
  await p.waitForFunction(() => document.querySelectorAll('#grid .pic .fm').length === 4, null, { timeout: 20000 });
  check('ticked pictures: Off then On', true);
  // off for the whole roll
  await p.selectOption('#fcol', 'frame');
  await p.waitForFunction(() => document.querySelectorAll('#grid .pic .fm').length === 0, null, { timeout: 20000 });
  check('Off in the list takes it off the roll, the base is kept', await p.isVisible('#b-fcolall') && /film base is kept/.test(await p.textContent('#fcolmsg')), await p.textContent('#fcolmsg'));
  await b.close();
  console.log(JSON.stringify({ errs, bad }));
  process.exit(bad.length || errs.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
