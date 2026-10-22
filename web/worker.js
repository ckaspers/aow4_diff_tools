// Runs the mod report in the browser: Pyodide (CPython in WebAssembly) with the same .py files as
// the desktop tool. The page sends the picked files; they are mounted with WORKERFS, which reads a
// file only when Python opens it, so the Python sees /game/... and /mod/... as ordinary folders.
importScripts('https://cdn.jsdelivr.net/pyodide/v0.27.2/full/pyodide.js');

const APP = ['aow4lib.py', 'decode.py', 'differ.py', 'script.py', 'acpread.py', 'tooltips.py',
             'stale.py', 'aow4diff.py', 'template.html', 'schema_db.json'];
const post = (type, data) => self.postMessage({ type, data });
let py = null;
const mounted = new Set();

async function boot() {
  post('log', 'loading Python');
  py = await loadPyodide({ stdout: line => post('log', line), stderr: line => post('log', line) });
  py.FS.mkdirTree('/app/cache');
  post('log', 'loading the tool');
  await Promise.all(APP.map(async f => {
    const r = await fetch(f);
    if (!r.ok) throw new Error(`could not load ${f} (${r.status})`);
    py.FS.writeFile('/app/' + f, new Uint8Array(await r.arrayBuffer()));
  }));
  // the name and id caches (built from the game's packs, never sent anywhere) are kept in this
  // browser's IndexedDB, so a second run on the same game build skips the long scan
  try {
    py.FS.mount(py.FS.filesystems.IDBFS, {}, '/app/cache');
    await sync(true);
  } catch (e) { post('log', 'no browser cache: ' + e); }
  py.runPython("import sys; sys.path.insert(0, '/app'); import aow4diff");
}
const ready = boot();
ready.then(() => post('ready'), e => post('error', 'could not start: ' + (e && e.message || e)));

function sync(populate) {
  return new Promise(ok => py.FS.syncfs(populate, err => { if (err) post('log', 'cache: ' + err); ok(); }));
}

// files: [[path inside the mount, File]]
function mount(point, files) {
  if (mounted.has(point)) { py.FS.unmount(point); mounted.delete(point); }
  py.FS.mkdirTree(point);
  if (!files.length) return;
  py.FS.mount(py.FS.filesystems.WORKERFS, { blobs: files.map(([name, data]) => ({ name, data })) }, point);
  mounted.add(point);
  // a blob gets "now" as its date; the tool needs the real one (the build label is the exe's date,
  // the caches are keyed on size and date)
  for (const [name, f] of files) {
    const node = py.FS.lookupPath(point + '/' + name).node;
    node.timestamp = node.atime = node.mtime = node.ctime = f.lastModified;
  }
}

self.onmessage = async ({ data: m }) => {
  try {
    await ready;
    if (m.type !== 'run') return;
    mount('/game', m.game);
    mount('/mod', m.mod);
    py.globals.set('GAME', '/game/' + m.gameDir);
    py.globals.set('ACP', '/mod/' + m.acp);
    const old = py.runPython(`
import os
exe = os.path.join(GAME, 'ResourceED.exe')
os.path.exists(exe) and not aow4diff.schema_matches({'game': GAME})`);
    if (old) post('note', 'The game was updated after the field names were read from ResourceED.exe. Fields added since then show as field_N.');
    const t0 = Date.now();
    py.runPython(`
import os
os.makedirs('/out', exist_ok=True)
aow4diff.cmd_mod(aow4diff.A(game=GAME, acp=ACP, out='/out/report.html', schema=None, history=[]))`);
    const html = py.FS.readFile('/out/report.html', { encoding: 'utf8' });
    py.FS.unlink('/out/report.html');
    await sync(false);
    post('done', { html, seconds: Math.round((Date.now() - t0) / 1000) });
  } catch (e) {
    post('error', String(e && e.message || e));
  }
};
