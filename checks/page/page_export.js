// The Export dialog on the Scanned pictures page: opens for the ticked pictures, the sections fold, a preset
// fills the form, the example name follows the naming, an export runs and says how it went, Export with
// previous repeats it, and the viewer's Export… opens it for one picture.
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
for i in (1, 2, 3):
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
  const dest = path.join(shotDir, 'exported');
  await p.goto('http://127.0.0.1:' + port + '/library');
  await p.waitForSelector('#rolllist button');
  await p.click('#rolllist button:has-text("Roll 001")');
  await p.waitForFunction(() => document.querySelectorAll('#grid .pic').length === 3, null, { timeout: 15000 });
  await p.click('#b-selall');
  await p.click('#a-exportdlg');
  await p.waitForSelector('#xp:not([hidden])');
  await p.waitForFunction(() => document.getElementById('x-example').textContent.includes('.tif'), null, { timeout: 5000 });
  check('title counts the ticked pictures', (await p.textContent('#x-title')) === 'Export 3 pictures', await p.textContent('#x-title'));
  check('first time: the For Lightroom preset is chosen, TIFF 16-bit', await p.$eval('#xp .plist button[aria-pressed=true]', e => e.dataset.n) === 'For Lightroom' && await p.inputValue('#x-format') === 'tiff16');
  check('six kinds of file, TIFF 16-bit first', (await p.$$eval('#x-format option', o => o.map(x => x.value))).join() === 'tiff16,tiff8,png,jpeg,heic,dng');
  check('What to Export offers the ticked pictures, the whole roll and all rolls', (await p.$$eval('#x-scope option', o => o.map(x => x.value))).join() === 'items,roll,all' && await p.inputValue('#x-scope') === 'items');
  check('finished pictures with crop and roll colour by default', await p.inputValue('#x-source') === 'positive' && await p.isChecked('#x-apply_crop') && await p.isChecked('#x-apply_colour'));
  await p.selectOption('#x-source', 'raw');
  check('RAW scans: the roll colour does not apply', await p.isDisabled('#x-apply_colour'));
  await p.selectOption('#x-source', 'positive');
  // sections fold and say what is in them
  const open = () => p.$$eval('#xp section', s => s.filter(x => x.classList.contains('open')).map(x => x.id.slice(4)));
  const before = await open();
  await p.click('#x-s-size > button');
  check('a section opens with a click on its bar', (await open()).includes('size') !== before.includes('size'));
  if (!(await open()).includes('size')) await p.click('#x-s-size > button');
  await p.check('#x-resizeon'); await p.fill('#x-long', '200');
  await p.click('#x-s-size > button');
  check('folded, it says what is set', (await p.textContent('#x-s-size .sum')) === 'Long edge 200 px', await p.textContent('#x-s-size .sum'));
  check('the chosen preset is marked as edited', (await p.textContent('#xp .plist button[aria-pressed=true]')).includes('(edited)'));
  for (const id of ['where', 'naming', 'file']) if (!(await open()).includes(id)) await p.click('#x-s-' + id + ' > button');
  await p.fill('#x-dest', dest);
  await p.selectOption('#x-naming', 'text_seq'); await p.fill('#x-text', 'Riga'); await p.fill('#x-start', '7');
  await p.waitForFunction(() => document.getElementById('x-example').textContent === 'Riga-007.tif', null, { timeout: 5000 }).catch(() => {});
  check('the example follows the naming', (await p.textContent('#x-example')) === 'Riga-007.tif', await p.textContent('#x-example'));
  await p.selectOption('#x-format', 'png');
  check('PNG: sRGB is chosen with it', (await p.inputValue('#x-space')).startsWith('sRGB'));
  await p.selectOption('#x-format', 'tiff8');
  check('back to TIFF: as scanned again', (await p.inputValue('#x-space')) === 'same');
  await p.uncheck('#x-keep_rolls');
  await p.screenshot({ path: shotDir + '/export-dialog.png' });
  // a preset of one's own
  await p.click('#x-padd'); await p.fill('#x-newname', 'For Ilze'); await p.click('#x-padd2');
  await p.waitForSelector('#xp .plist button[data-n="For Ilze"][aria-pressed=true]');
  check('a saved preset is listed and chosen, not edited', (await p.textContent('#xp .plist button[data-n="For Ilze"]')) === 'For Ilze');
  await p.click('#xp .plist button[data-n="Full size TIFF"]');
  check('a built-in preset sets the file, not the folder or names', await p.inputValue('#x-format') === 'tiff16' && await p.inputValue('#x-dest') === dest && await p.inputValue('#x-naming') === 'text_seq' && !(await p.isChecked('#x-resizeon')));
  check('built-in presets cannot be removed', await p.isDisabled('#x-pdel'));
  await p.click('#xp .plist button[data-n="For Ilze"]');
  check('the saved preset brings everything back', await p.inputValue('#x-format') === 'tiff8' && await p.inputValue('#x-long') === '200' && await p.isChecked('#x-resizeon'));
  // keys stay in the dialog
  await p.focus('#x-text'); await p.keyboard.press('a');
  check('the page\'s keys do nothing while the dialog is up', await p.$eval('#viewer', e => e.hidden));
  await p.fill('#x-text', 'Riga');
  await p.selectOption('#x-after', 'nothing');   // (opening Lightroom is for the Mac)
  await p.click('#x-padd'); await p.click('#x-padd2');   // the preset again, without Lightroom
  await p.click('#x-go');
  await p.waitForSelector('#xp', { state: 'hidden', timeout: 30000 }).catch(async e => { console.log('STUCK', await p.textContent('#x-msg'), await p.isDisabled('#x-go'), await p.textContent('#x-go')); throw e; });
  const said = await p.textContent('#actmsg');
  const files = fs.existsSync(dest) ? fs.readdirSync(dest).sort() : [];
  check('three 8-bit TIFFs with the custom names, in one folder', files.join() === 'Riga-007.tif,Riga-008.tif,Riga-009.tif', files);
  check('the page says how it went', /Exported 3 pictures as TIFF 8-bit/.test(said) && /unchanged/.test(said), said);
  await p.click('#a-exportprev');
  for (let i = 0; i < 100 && fs.readdirSync(dest).length < 6; i++) await p.waitForTimeout(100);
  check('Export with previous: three more, with new names', fs.readdirSync(dest).length === 6 && fs.existsSync(path.join(dest, 'Riga-007 (2).tif')), fs.readdirSync(dest));
  // a folder that is not there is said in the dialog, and Export waits
  await p.click('#a-exportdlg'); await p.waitForSelector('#xp:not([hidden])');
  check('the dialog opens with the settings used last', await p.inputValue('#x-format') === 'tiff8' && await p.inputValue('#x-text') === 'Riga');
  await p.fill('#x-dest', '/no/such/place/at all');
  await p.waitForFunction(() => document.getElementById('x-go').disabled, null, { timeout: 5000 }).catch(() => {});
  check('a folder that cannot be made: said, Export greyed', await p.isDisabled('#x-go') && /no folder/i.test(await p.textContent('#x-msg')), await p.textContent('#x-msg'));
  await p.keyboard.press('Escape');
  check('Escape closes the dialog', await p.$eval('#xp', e => e.hidden));
  // the whole roll, with nothing ticked
  await p.click('#b-selnone');
  await p.click('#b-exportroll'); await p.waitForSelector('#xp:not([hidden])');
  check('Export roll… opens it for the whole roll', (await p.textContent('#x-title')) === 'Export Roll 001' && await p.inputValue('#x-scope') === 'roll', await p.textContent('#x-title'));
  await p.fill('#x-dest', path.join(shotDir, 'roll')); await p.selectOption('#x-naming', 'name'); await p.check('#x-keep_rolls');
  await p.click('#x-go'); await p.waitForSelector('#xp', { state: 'hidden', timeout: 30000 });
  const rollFiles = fs.existsSync(path.join(shotDir, 'roll', 'Roll 001')) ? fs.readdirSync(path.join(shotDir, 'roll', 'Roll 001')) : [];
  check('the whole roll is exported into its folder', rollFiles.length === 3, rollFiles);
  check('and the page says so', /Exported 3 pictures/.test(await p.textContent('#shown')), await p.textContent('#shown'));
  // one picture, from the viewer
  await p.click('#grid .pic >> nth=1');
  await p.waitForSelector('#viewer:not([hidden])');
  await p.click('#b-export'); await p.waitForSelector('#xp:not([hidden])');
  check('the viewer\'s Export… opens it for that picture', (await p.textContent('#x-title')) === 'Export one picture' && (await p.textContent('#x-what')) === '20261101_002_R001.tif', await p.textContent('#x-what'));
  await p.click('#x-cancel');
  check('Cancel closes it', await p.$eval('#xp', e => e.hidden));
  console.log(JSON.stringify({ errs, bad }));
  await b.close();
  process.exit(bad.length ? 1 : 0);
})();
