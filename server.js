const ITEMS = [["Sword", "73920481"], ["Unicorn", "58392047"], ["Clickeys", "381562"], ["Lucy The Kitten", "1001"], ["Mc Dolfon", "1002"], ["Pink Bin With Bows", "1003"], ["Unifary Kitty", "1004"], ["Add What U Want", "1005"], ["Happy Sqishmello", "1006"]];
const CSS = "\n*{box-sizing:border-box}body{margin:0;background:#f1f4f8;color:#081b38;\nfont:16px/1.5 system-ui,sans-serif}header{background:#081f40;color:white;\nborder-bottom:8px solid #a6eb10;padding:13px max(5vw,16px);font-size:25px;\nfont-weight:900;letter-spacing:.06em}main{width:min(960px,94%);margin:32px auto}\n.card{background:white;border:1px solid #d3dce6;border-radius:18px;\npadding:clamp(16px,4vw,32px);margin-bottom:18px;box-shadow:0 8px 24px #081f400a}\n.login{max-width:440px;margin:48px auto}h1,h2{margin:0 0 16px;line-height:1.12}\nh1{font-size:clamp(30px,5vw,46px);letter-spacing:-.04em}h2{font-size:22px}\nlabel{display:block;font-weight:700;margin-bottom:12px}input,select,button{font:inherit}\ninput,select{display:block;width:100%;min-width:0;border:1px solid #7890a5;\nborder-radius:9px;padding:10px;margin-top:5px;background:white;color:inherit}\nbutton,.badge{background:#a6eb10;color:#081b38;border:0;padding:10px 15px;\nborder-radius:10px;font-weight:800}button{cursor:pointer}button:hover{background:#bef840}\nbutton.secondary,.badge{background:#e2f9b1}.login button{width:100%}\n.row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}\n.row h1,.row form{margin:0}.table{overflow:auto;border:1px solid #9fafbd;border-radius:12px}\ntable{width:100%;border-collapse:collapse;text-align:left}th,td{padding:9px 13px;\nborder-bottom:1px solid #dce4eb}thead{background:#a6eb10}tr:last-child td{border-bottom:0}\n.formrow{display:flex;align-items:end;gap:10px;flex-wrap:wrap}.formrow label{flex:1;\nmin-width:min(200px,100%);margin:0}.notice{padding:12px 16px;border-radius:9px;\nbackground:#e2f9b1;font-weight:700}.error{background:#fde8e3}\nsmall{color:#425670}video{max-width:100%;max-height:240px;margin-top:10px}\n:focus-visible{outline:3px solid #3568b0;outline-offset:2px}\n";
const CAMERA = "\nconst camera = document.querySelector('#camera');\ncamera.addEventListener('click', async () => {\n  const status = document.querySelector('#scan-status');\n  const video = document.querySelector('video');\n  if (!window.BarcodeDetector || !navigator.mediaDevices?.getUserMedia) {\n    status.textContent = 'Use a USB QR scanner, or type the item code below.';\n    return;\n  }\n  let stream;\n  camera.disabled = true;\n  try {\n    const detector = new BarcodeDetector({formats: ['qr_code']});\n    stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: 'environment'}});\n    video.srcObject = stream;\n    video.hidden = false;\n    await video.play();\n    status.textContent = 'Hold the QR code in front of the camera.';\n    const deadline = Date.now() + 30000;\n    while (Date.now() < deadline) {\n      const [result] = await detector.detect(video);\n      if (result) {\n        document.querySelector('#code').value = result.rawValue.replace(/[\\r\\n]+/g, ' ');\n        status.textContent = 'Code ready. Select Find item to review checkout.';\n        document.querySelector('#find').focus();\n        return;\n      }\n      await new Promise(resolve => setTimeout(resolve, 220));\n    }\n    status.textContent = 'Scan timed out. Try again or enter the code.';\n  } catch {\n    status.textContent = 'Camera unavailable. Use a USB scanner or enter the code.';\n  } finally {\n    stream?.getTracks().forEach(track => track.stop());\n    video.srcObject = null;\n    video.hidden = true;\n    camera.disabled = false;\n  }\n});\n";

const { createServer } = require('node:http');
const { DatabaseSync } = require('node:sqlite');
const { randomBytes, scrypt, timingSafeEqual } = require('node:crypto');
const { promisify } = require('node:util');
const { mkdirSync } = require('node:fs');
const { dirname, join } = require('node:path');

const env = process.env;
const railway = Boolean(env.RAILWAY_ENVIRONMENT_ID);
const port = Number(env.PORT || 8765);
const staff = [env.FIRST_STAFF_USER || 'liam.humes', env.SECOND_STAFF_USER || 'delilah.humes']
  .map(name => name.trim().toLowerCase());
const pin = env.STAFF_PIN;
const adminPassword = env.ADMIN_PASSWORD;
if (!pin || !/^\d{4,12}$/.test(pin) || !adminPassword ||
    adminPassword.length < 8 || adminPassword.length > 160) {
  throw new Error('Set STAFF_PIN to 4–12 digits and ADMIN_PASSWORD to 8–160 characters.');
}
if (new Set(staff).size !== 2 || staff.some(name => name === 'admin' || !/^[a-z0-9][a-z0-9._-]{1,39}$/.test(name))) {
  throw new Error('Set two valid, distinct staff usernames.');
}

const dbPath = env.DB_PATH || join(env.RAILWAY_VOLUME_MOUNT_PATH || './data', 'inventory.sqlite3');
if (railway && !env.RAILWAY_VOLUME_MOUNT_PATH) {
  console.warn('Attach a Railway volume to keep inventory across deploys.');
}
mkdirSync(dirname(dbPath), { recursive: true });
const db = new DatabaseSync(dbPath);
db.exec(`
  PRAGMA busy_timeout = 5000;
  CREATE TABLE IF NOT EXISTS stock (
    code TEXT PRIMARY KEY, name TEXT NOT NULL, quantity INTEGER NOT NULL
    CHECK (quantity BETWEEN 0 AND 9999)
  );
  CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY, who TEXT NOT NULL, item TEXT NOT NULL,
    action TEXT NOT NULL, at INTEGER NOT NULL DEFAULT (unixepoch())
  );
  CREATE TABLE IF NOT EXISTS inventory_setup (id INTEGER PRIMARY KEY);
`);

function transaction(action) {
  db.exec('BEGIN IMMEDIATE');
  try {
    const result = action();
    db.exec('COMMIT');
    return result;
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
}

transaction(() => {
  if (!db.prepare('INSERT OR IGNORE INTO inventory_setup VALUES (1)').run().changes) return;
  const insert = db.prepare('INSERT OR IGNORE INTO stock VALUES (?, ?, 1)');
  for (const [name, code] of ITEMS) insert.run(code, name);
});

const derive = promisify(scrypt);
const salt = randomBytes(32);
const sessions = new Map();
const attempts = new Map();
const token = () => randomBytes(32).toString('base64url');
const html = value => String(value).replace(/[&<>"']/g, char =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const lookup = code => db.prepare('SELECT * FROM stock WHERE code = ?').get(code);
const log = (who, item, action) =>
  db.prepare('INSERT INTO activity (who, item, action) VALUES (?, ?, ?)').run(who, item, action);
const cookie = (value, maxAge) =>
  `item_session=${value}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${maxAge}${railway ? '; Secure' : ''}`;
const timeFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: env.STORE_TIME_ZONE || 'America/Detroit', month: 'short', day: 'numeric',
  hour: 'numeric', minute: '2-digit'
});

function respond(res, body = '', status = 200, headers = {}) {
  res.writeHead(status, {
    'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
    ...headers
  });
  res.end(body);
}

function redirect(res, location, sessionCookie) {
  respond(res, '', 303, {
    Location: location, ...(sessionCookie ? { 'Set-Cookie': sessionCookie } : {})
  });
}

function page(content, script = '') {
  return `<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Item Inventory</title><style>${CSS}</style>
<header>THE ITEM</header><main>${content}</main>
${script ? `<script>${script}</script>` : ''}</html>`;
}

function notice(message, error = false) {
  return message ? `<p role="status" class="notice${error ? ' error' : ''}">${html(message)}</p>` : '';
}

function loginPage(res, error = '', status = 200) {
  respond(res, page(`<section class="card login"><h1>Log in</h1>${notice(error, true)}
<form method="post" action="/login"><label>Username
<input name="username" autocomplete="username" maxlength="40" required autofocus></label>
<label>Password<input name="password" type="password" autocomplete="current-password"
maxlength="160" required></label><button>Log in</button></form></section>`), status);
}

function inventory(res, session, { message, error, preview } = {}) {
  const items = db.prepare('SELECT * FROM stock ORDER BY rowid').all();
  const activity = db.prepare('SELECT * FROM activity ORDER BY id DESC LIMIT 20').all();
  const csrf = `<input type="hidden" name="csrf" value="${session.csrf}">`;
  const rows = items.map(({ code, name, quantity }) =>
    `<tr><td><strong>${html(name)}</strong><br><small>${html(code)}</small></td>
<td>${quantity}</td><td>FREE</td></tr>`).join('');
  const review = preview ? `<p><strong>${html(preview.name)}</strong> · ${preview.quantity} in stock · FREE</p>
${preview.quantity ? `<form method="post" action="/checkout">${csrf}<input type="hidden"
name="code" value="${html(preview.code)}"><button>Check out 1 item</button></form>` : ''}` : '';
  const controls = session.admin ? `<section class="card"><h2>Update Stock</h2>
<form class="formrow" method="post" action="/stock">${csrf}<label>Item<select name="code">${items.map(
    item => `<option value="${html(item.code)}">${html(item.name)}</option>`).join('')}</select></label>
<label>New quantity<input type="number" name="quantity" min="0" max="9999" required></label>
<button>Update stock</button></form></section>
<section class="card"><h2>Add item</h2><form class="formrow" method="post" action="/stock">${csrf}
<label>Item name<input name="name" maxlength="70" required></label>
<label>Item code<input name="code" maxlength="40" required></label>
<label>Starting quantity<input type="number" name="quantity" min="0" max="9999" value="1" required></label>
<button>Add item</button></form></section>` : '';
  const history = activity.map(entry => `<tr><td>${html(entry.item)}</td><td>${html(entry.action)}</td>
<td>${html(entry.who)}</td><td>${html(timeFormat.format(entry.at * 1000))}</td></tr>`).join('');
  respond(res, page(`<section class="card"><div class="row"><h1>Inventory Tracker</h1>
<span class="badge">${html(session.name)} · ${session.admin ? 'Admin' : 'Staff'}</span>
<form method="post" action="/logout">${csrf}<button class="secondary">Log out</button></form></div>
${notice(message, error)}<p>${items.reduce((sum, item) => sum + item.quantity, 0)} items in stock</p>
<div class="table"><table><thead><tr><th>Item</th><th>In stock</th><th>Price</th></tr></thead>
<tbody>${rows}</tbody></table></div></section>
<section class="card"><div class="row"><h2>Scan QR</h2><button class="secondary" id="camera"
type="button">Use camera</button></div><video hidden muted playsinline></video>
<p id="scan-status" role="status">Scan with a USB QR scanner or enter an item code.</p>
<form class="formrow" method="post" action="/find">${csrf}
<label>Item code<input id="code" name="code" maxlength="240" required></label>
<button id="find">Find item</button></form>${review}</section>
${controls}<section class="card"><h2>Recent activity</h2>
${history ? `<div class="table"><table><thead><tr><th>Item</th><th>Action</th><th>Account</th>
<th>Time</th></tr></thead><tbody>${history}</tbody></table></div>` : '<p>No activity yet.</p>'}
</section>`, CAMERA));
}

class InputError extends Error {}

function checkout(code, name) {
  return transaction(() => {
    const item = lookup(code);
    if (!item || !item.quantity) throw new InputError('This item is out of stock or the code is unknown.');
    db.prepare('UPDATE stock SET quantity = quantity - 1 WHERE code = ?').run(code);
    log(name, item.name, 'Checked out');
    return `${item.name} checked out.`;
  });
}

function setStock(code, name, quantity, who) {
  if (!/^[A-Za-z0-9_-]{1,40}$/.test(code) || name.length > 70 || !/^\d{1,4}$/.test(quantity)) {
    throw new InputError('Enter a valid item code, name, and a quantity from 0 to 9999.');
  }
  return transaction(() => {
    const item = lookup(code);
    if (name && item) throw new InputError('That item code already exists. Use Update Stock.');
    if (!name && !item) throw new InputError('Choose an existing item or enter a name to add it.');
    if (item) db.prepare('UPDATE stock SET quantity = ? WHERE code = ?').run(Number(quantity), code);
    else db.prepare('INSERT INTO stock VALUES (?, ?, ?)').run(code, name, Number(quantity));
    const itemName = item ? item.name : name;
    log(who, itemName, `Stock set to ${Number(quantity)}`);
    return `${itemName} stock updated.`;
  });
}

async function readForm(req) {
  if (!req.headers['content-type']?.startsWith('application/x-www-form-urlencoded')) return null;
  if (Number(req.headers['content-length']) > 8192) return null;
  const chunks = [];
  let size = 0;
  for await (const chunk of req.iterator({ destroyOnReturn: false })) {
    size += chunk.length;
    if (size > 8192) return null;
    chunks.push(chunk);
  }
  return new URLSearchParams(Buffer.concat(chunks).toString());
}

let inFlight = 0;
async function tryLogin(res, form) {
  const name = (form.get('username') || '').trim().toLowerCase();
  const password = form.get('password') || '';
  const key = name === 'admin' || staff.includes(name) ? name : 'unknown';
  const now = Date.now();
  const recent = (attempts.get(key) || []).filter(at => now - at < 15 * 60_000);
  if (recent.length >= 8 || inFlight >= 4) {
    return loginPage(res, 'Too many tries. Wait a few minutes and try again.', 429);
  }
  if (password.length > 160) return loginPage(res, 'Username or password is incorrect.', 401);
  recent.push(now);
  attempts.set(key, recent);
  inFlight++;
  try {
    const expected = name === 'admin' ? adminPassword : pin;
    const providedHash = await derive(password, salt, 32);
    const expectedHash = await derive(expected, salt, 32);
    if (!timingSafeEqual(providedHash, expectedHash) || key === 'unknown') {
      return loginPage(res, 'Username or password is incorrect.', 401);
    }
  } finally {
    inFlight--;
  }
  attempts.delete(key);
  for (const [id, session] of sessions) if (session.until <= now) sessions.delete(id);
  const id = token();
  sessions.set(id, { name, admin: name === 'admin', csrf: token(), until: now + 28_800_000 });
  redirect(res, '/inventory', cookie(id, 28800));
}

async function handle(req, res) {
  const url = new URL(req.url, 'http://localhost');
  const path = url.pathname;
  const id = req.headers.cookie?.match(/(?:^|;\s*)item_session=([A-Za-z0-9_-]{43})(?:;|$)/)?.[1];
  const record = sessions.get(id);
  const session = record && record.until > Date.now() ? record : null;
  if (req.method === 'GET') {
    if (path === '/health') return respond(res, 'OK');
    if (path === '/') return redirect(res, session ? '/inventory' : '/login');
    if (path === '/login') return session ? redirect(res, '/inventory') : loginPage(res);
    if (path === '/inventory') {
      if (!session) return redirect(res, '/login');
      const message = session.message;
      delete session.message;
      return inventory(res, session, { message });
    }
    return respond(res, 'Not found', 404);
  }
  if (req.method !== 'POST') return respond(res, 'Method not allowed', 405);
  const host = req.headers.host;
  const origin = req.headers.origin;
  if (!host || (origin && origin !== `${railway ? 'https' : 'http'}://${host}`) ||
      (req.headers['sec-fetch-site'] && req.headers['sec-fetch-site'] !== 'same-origin')) {
    return respond(res, 'Request blocked', 403);
  }
  const form = await readForm(req);
  if (!form) return respond(res, 'Invalid form', 400);
  if (path === '/login') return tryLogin(res, form);
  if (!session) return redirect(res, '/login');
  if (form.get('csrf') !== session.csrf) return respond(res, 'Request blocked', 403);
  if (path === '/logout') {
    sessions.delete(id);
    return redirect(res, '/login', cookie('', 0));
  }
  const scanned = (form.get('code') || '').trim();
  const code = scanned.match(/\bNumber:\s*(\d+)\b/i)?.[1] || scanned;
  try {
    if (path === '/find') {
      const item = lookup(code);
      if (!item) throw new InputError('No item matches that code.');
      return inventory(res, session, { preview: item });
    }
    if (path === '/checkout') session.message = checkout(code, session.name);
    else if (path === '/stock') {
      if (!session.admin) return respond(res, 'Admin access required', 403);
      session.message = setStock(code, (form.get('name') || '').trim(),
        (form.get('quantity') || '').trim(), session.name);
    } else return respond(res, 'Not found', 404);
    return redirect(res, '/inventory');
  } catch (error) {
    if (!(error instanceof InputError)) throw error;
    return inventory(res, session, { message: error.message, error: true });
  }
}

createServer((req, res) => {
  handle(req, res).catch(error => {
    console.error(error);
    if (!res.headersSent) respond(res, 'Something went wrong.', 500);
    else res.end();
  });
}).listen(port, '0.0.0.0', () => console.log(`The Item listening on port ${port}`));
