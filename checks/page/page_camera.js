// Camera, aperture and shutter speed in the page: the Camera row of the New roll window, the Camera row of a
// roll in Scanned pictures, and the ƒ/ and Shutter fields beside a picture.
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs'), path = require('path');
const [scans, shotDir, port] = process.argv.slice(2);
const py = process.env.COOLSCAN_PYTHON || 'python3';
execFileSync(py, ['-c', `
import sys, datetime, pathlib, numpy as np
sys.path.insert(0, ${JSON.stringify(path.resolve(__dirname, '..', '..'))})
from coolscan_app.imaging.files import write_tiff
from coolscan_app.imaging.colour import icc_profile
yy, xx = np.mgrid[0:300, 0:450]
for i in (1, 2):
    p = pathlib.Path(sys.argv[1]) / "Roll 001" / f"20261101_{i:03d}_R001.tif"
    p.parent.mkdir(parents=True, exist_ok=True)
    planes = [(20000 + 30000 * np.sin(xx / (20 + 9 * i)) ** 2).astype(np.uint16), (15000 + 35000 * np.cos(yy / (15 + 7 * i)) ** 2).astype(np.uint16), (10000 + 40 * ((xx + yy * i) % 1000)).astype(np.uint16)]
    write_tiff(p, planes, 2900, icc_profile("ProPhoto RGB"), meta={"taken": datetime.datetime(2004, 3, 5, 10, 0, i), "make": "Nikon", "model": "Nikon COOLSCAN IV ED"})
`, scans]);
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })).newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e))); p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  const bad = []; const check = (name, ok, extra) => { if (!ok) bad.push(name + (extra !== undefined ? ' [' + JSON.stringify(extra) + ']' : '')); };
  const base = 'http://127.0.0.1:' + port;
  // Scanned pictures: the roll's camera
  await p.goto(base + '/library');
  await p.waitForSelector('#rolllist button');
  await p.click('#rolllist button:has-text("Roll 001")');
  await p.waitForFunction(() => document.querySelectorAll('#grid .pic').length === 2, null, { timeout: 15000 });
  check('a Camera row under the description, nothing to save yet', await p.isVisible('#rcam') && await p.isDisabled('#b-rcam') && await p.inputValue('#rcam') === '');
  await p.fill('#rcam', 'Nikon F3');
  check('typing lights the Save button', await p.isEnabled('#b-rcam'));
  await p.click('#b-rcam');
  await p.waitForFunction(() => /Camera saved/.test(document.getElementById('rcammsg').textContent), null, { timeout: 15000 });
  check('says where it was saved', /in 2 files/.test(await p.textContent('#rcammsg')), await p.textContent('#rcammsg'));
  await p.screenshot({ path: path.join(shotDir, 'camera-roll.png') });
  // one picture: aperture and shutter speed
  await p.click('#grid .pic');
  await p.waitForSelector('#viewer:not([hidden])');
  await p.waitForSelector('#edit input[aria-label="Aperture (f-number)"]');
  check('the aperture field has the ƒ sign', (await p.textContent('#edit .exposure .fsym')).includes('ƒ'));
  await p.fill('#edit input[aria-label="Aperture (f-number)"]', '5.6');
  await p.keyboard.press('Tab');
  await p.waitForFunction(() => /Saved/.test(document.getElementById('cropmsg').textContent), null, { timeout: 15000 });
  await p.fill('#edit input[aria-label="Shutter speed"]', '125');
  await p.keyboard.press('Tab');
  await p.waitForFunction(() => { const e = document.querySelector('#edit input[aria-label="Shutter speed"]'); return e && e.value === '1/125'; }, null, { timeout: 15000 });
  check('aperture kept in its field', await p.inputValue('#edit input[aria-label="Aperture (f-number)"]') === '5.6');
  await p.waitForFunction(() => /Shutter speed/.test(document.getElementById('meta').textContent), null, { timeout: 15000 });
  const meta = await p.textContent('#meta');
  check('details list: Camera, Aperture ƒ/5.6, Shutter speed 1/125 s, the scanner', /Camera\s*Nikon F3/.test(meta) && meta.includes('ƒ/5.6') && meta.includes('1/125 s') && /Scanner\s*Nikon COOLSCAN IV ED/.test(meta), meta.slice(0, 600));
  await p.screenshot({ path: path.join(shotDir, 'camera-picture.png') });
  await p.fill('#edit input[aria-label="Aperture (f-number)"]', 'wide');
  await p.keyboard.press('Tab');
  await p.waitForFunction(() => /not one/.test(document.getElementById('cropmsg').textContent), null, { timeout: 15000 });
  check('a wrong aperture is explained', true);
  await p.keyboard.press('Escape');
  // New roll: the Camera row
  await p.goto(base + '/');
  await p.waitForSelector('#b-newroll');
  await p.waitForFunction(() => !document.getElementById('b-newroll').disabled, null, { timeout: 30000 });
  await p.click('#b-newroll');
  await p.waitForSelector('#rolldlg[open]');
  check('New roll has a Camera row', await p.isVisible('#r-film_camera'));
  await p.fill('#r-film_brand', 'Kodak'); await p.fill('#r-film_camera', 'Olympus OM-1');
  await p.screenshot({ path: path.join(shotDir, 'camera-newroll.png') });
  await p.click('#b-rollok');
  await p.waitForFunction(() => /Camera: Olympus OM-1/.test(document.getElementById('rollfilm').textContent), null, { timeout: 15000 });
  check('the Roll card names the camera', true);
  await p.click('#b-newroll');
  check('the row is empty for the next roll', await p.inputValue('#r-film_camera') === '');
  await b.close();
  console.log(JSON.stringify({ errs, bad }));
  process.exit(bad.length || errs.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
