"""The Item inventory. Start with: python inventory.py"""
import argparse
from contextlib import contextmanager
from getpass import getpass
from hashlib import scrypt
from hmac import compare_digest
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit

ITEMS = [
    ("Sword", "73920481"), ("Unicorn", "58392047"), ("Clickeys", "381562"),
    ("Lucy The Kitten", "1001"), ("Mc Dolfon", "1002"),
    ("Pink Bin With Bows", "1003"), ("Unifary Kitty", "1004"),
    ("Add What U Want", "1005"), ("Happy Sqishmello", "1006"),
]

CSS = """
*{box-sizing:border-box}body{margin:0;background:#f1f4f8;color:#081b38;
font:16px/1.5 system-ui,sans-serif}header{background:#081f40;color:white;
border-bottom:8px solid #a6eb10;padding:13px max(5vw,16px);font-size:25px;
font-weight:900;letter-spacing:.06em}main{width:min(960px,94%);margin:32px auto}
.card{background:white;border:1px solid #d3dce6;border-radius:18px;
padding:clamp(16px,4vw,32px);margin-bottom:18px;box-shadow:0 8px 24px #081f400a}
.login{max-width:440px;margin:48px auto}h1,h2{margin:0 0 16px;line-height:1.12}
h1{font-size:clamp(30px,5vw,46px);letter-spacing:-.04em}h2{font-size:22px}
label{display:block;font-weight:700;margin-bottom:12px}input,select,button{font:inherit}
input,select{display:block;width:100%;min-width:0;border:1px solid #7890a5;
border-radius:9px;padding:10px;margin-top:5px;background:white;color:inherit}
button,.badge{background:#a6eb10;color:#081b38;border:0;padding:10px 15px;
border-radius:10px;font-weight:800}button{cursor:pointer}button:hover{background:#bef840}
button.secondary,.badge{background:#e2f9b1}.login button{width:100%}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.row h1,.row form{margin:0}.table{overflow:auto;border:1px solid #9fafbd;border-radius:12px}
table{width:100%;border-collapse:collapse;text-align:left}th,td{padding:9px 13px;
border-bottom:1px solid #dce4eb}thead{background:#a6eb10}tr:last-child td{border-bottom:0}
.formrow{display:flex;align-items:end;gap:10px;flex-wrap:wrap}.formrow label{flex:1;
min-width:min(200px,100%);margin:0}.notice{padding:12px 16px;border-radius:9px;
background:#e2f9b1;font-weight:700}.error{background:#fde8e3}
small{color:#425670}video{max-width:100%;max-height:240px;margin-top:10px}
:focus-visible{outline:3px solid #3568b0;outline-offset:2px}
"""

CAMERA = """
const camera = document.querySelector('#camera');
camera.addEventListener('click', async () => {
  const status = document.querySelector('#scan-status');
  const video = document.querySelector('video');
  if (!window.BarcodeDetector || !navigator.mediaDevices?.getUserMedia) {
    status.textContent = 'Use a USB QR scanner, or type the item code below.';
    return;
  }
  let stream;
  camera.disabled = true;
  try {
    const detector = new BarcodeDetector({formats: ['qr_code']});
    stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: 'environment'}});
    video.srcObject = stream;
    video.hidden = false;
    await video.play();
    status.textContent = 'Hold the QR code in front of the camera.';
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      const [result] = await detector.detect(video);
      if (result) {
        document.querySelector('#code').value = result.rawValue.replace(/[\\r\\n]+/g, ' ');
        status.textContent = 'Code ready. Select Find item to review checkout.';
        document.querySelector('#find').focus();
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 220));
    }
    status.textContent = 'Scan timed out. Try again or enter the code.';
  } catch {
    status.textContent = 'Camera unavailable. Use a USB scanner or enter the code.';
  } finally {
    stream?.getTracks().forEach(track => track.stop());
    video.srcObject = null;
    video.hidden = true;
    camera.disabled = false;
  }
});
"""

def password_hash(password, salt):
    return scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)


def username(value):
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,39}", value):
        raise ValueError("Use 2–40 letters, numbers, periods, hyphens, or underscores.")
    return value


def item_code(value):
    value = value.strip()
    match = re.search(r"\bNumber:\s*(\d+)\b", value, re.IGNORECASE)
    return match[1] if match else value


class Store:
    def __init__(self, path):
        self.path = path
        self.sessions = {}
        self.lock = threading.Lock()
        self.failed_logins = []
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    name TEXT PRIMARY KEY, role TEXT NOT NULL, salt BLOB, password BLOB
                );
                CREATE TABLE IF NOT EXISTS stock (
                    code TEXT PRIMARY KEY, name TEXT NOT NULL, quantity INTEGER NOT NULL
                        CHECK (quantity BETWEEN 0 AND 9999)
                );
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY, who TEXT NOT NULL, item TEXT NOT NULL,
                    action TEXT NOT NULL, at INTEGER NOT NULL DEFAULT (unixepoch())
                );
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def setup(self, staff, pin, admin):
        staff = [username(name) for name in staff]
        if len(set(staff)) != 2 or "admin" in staff:
            raise ValueError("Use two distinct staff usernames other than admin.")
        if not re.fullmatch(r"[0-9]{4,12}", pin):
            raise ValueError("Use a 4–12 digit staff PIN.")
        if len(admin) < 8:
            raise ValueError("Use at least 8 characters for the admin password.")
        accounts = []
        for name, role, password in [("admin", "admin", admin), *[
            (name, "staff", pin) for name in staff
        ]]:
            salt = secrets.token_bytes(16)
            accounts.append((name, role, salt, password_hash(password, salt)))
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise ValueError("Accounts are already set up.")
            db.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", accounts)
            db.executemany("INSERT INTO stock VALUES (?, ?, 1)",
                           [(code, name) for name, code in ITEMS])

    def login(self, name, password):
        with self.lock:
            now = time.monotonic()
            self.failed_logins = [at for at in self.failed_logins if now - at < 60]
            if len(self.failed_logins) >= 8:
                return None, "Too many tries. Wait one minute and try again."
            with self.db() as db:
                user = db.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
            candidate = password_hash(password, user["salt"] if user else bytes(16))
            if user and compare_digest(user["password"], candidate):
                token = secrets.token_urlsafe(32)
                self.sessions = {key: session for key, session in self.sessions.items()
                                 if session[2] > now}
                self.sessions[token] = (name, user["role"], now + 28800)
                return token, ""
            self.failed_logins.append(now)
            return None, "Username or password is incorrect."

    def checkout(self, code, who):
        with self.db() as db:
            changed = db.execute(
                "UPDATE stock SET quantity = quantity - 1 WHERE code = ? AND quantity > 0",
                (code,),
            ).rowcount
            if not changed:
                raise ValueError("This item is out of stock or the code is unknown.")
            item = db.execute("SELECT name FROM stock WHERE code = ?", (code,)).fetchone()
            db.execute("INSERT INTO activity (who, item, action) VALUES (?, ?, ?)",
                       (who, item["name"], "Checked out"))
            return f'{item["name"]} checked out.'

    def update_stock(self, code, name, quantity, who):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", code) or len(name) > 70:
            raise ValueError("Use a short item name and an item code with letters or numbers.")
        if not re.fullmatch(r"[0-9]{1,4}", quantity):
            raise ValueError("Enter a whole stock quantity from 0 to 9999.")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT name FROM stock WHERE code = ?", (code,)).fetchone()
            if name and existing and name != existing["name"]:
                raise ValueError("That item code is already assigned. Use the stock form.")
            if not name and not existing:
                raise ValueError("Enter a name when adding a new item.")
            name = existing["name"] if existing else name
            db.execute(
                "INSERT INTO stock VALUES (?, ?, ?) ON CONFLICT(code)"
                " DO UPDATE SET quantity = excluded.quantity", (code, name, int(quantity))
            )
            db.execute("INSERT INTO activity (who, item, action) VALUES (?, ?, ?)",
                       (who, name, f"Stock set to {int(quantity)}"))
        return f"{name} stock updated."


def page(content, javascript=""):
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Item Inventory</title><style>{CSS}</style>
<header>THE ITEM</header><main>{content}</main>
{"<script>" + javascript + "</script>" if javascript else ""}
</html>"""


def notice(message, error=False):
    return (f'<p role="status" class="notice{" error" if error else ""}">'
            f'{escape(message)}</p>') if message else ""


class InventoryHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def respond(self, body="", status=200, **headers):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, target, cookie=None):
        headers = {"Location": target}
        if cookie:
            headers["Set_Cookie"] = cookie
        self.respond(status=303, **headers)

    def session(self):
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookies["item_session"].value
        except (KeyError, ValueError):
            return "", None
        with self.server.store.lock:
            session = self.server.store.sessions.get(token)
        return token, session if session and session[2] > time.monotonic() else None

    def login_page(self, error=""):
        self.respond(page(f"""<section class="card login"><h1>Log in</h1>
{notice(error, True)}<form method="post" action="/login">
<label>Username<input name="username" maxlength="40" autocomplete="username" required autofocus></label>
<label>Password<input name="password" type="password" maxlength="160"
autocomplete="current-password" required></label><button>Log in</button></form></section>"""),
                     401 if error else 200)

    def inventory(self, token, account, message="", error=False, preview=None):
        who, role, _ = account
        admin = role == "admin"
        with self.server.store.db() as db:
            items = list(db.execute("SELECT * FROM stock ORDER BY rowid"))
            activity = list(db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 20"))
        csrf = f'<input type="hidden" name="csrf" value="{escape(token)}">'
        rows = "".join(
            f'<tr><td><strong>{escape(item["name"])}</strong><br><small>{escape(item["code"])}</small>'
            f'</td><td>{item["quantity"]}</td><td>FREE</td></tr>' for item in items
        )
        review = ""
        if preview:
            code, name, quantity = preview
            review = f'<p><strong>{escape(name)}</strong> · {quantity} in stock · FREE</p>'
            if quantity:
                review += f"""<form method="post" action="/checkout">{csrf}
<input type="hidden" name="code" value="{escape(code)}"><button>Check out 1 item</button></form>"""
        controls = ""
        if admin:
            options = "".join(f'<option value="{escape(item["code"])}">{escape(item["name"])}</option>'
                              for item in items)
            controls = f"""<section class="card"><h2>Update Stock</h2>
<form class="formrow" method="post" action="/stock">{csrf}
<label>Item<select name="code">{options}</select></label>
<label>New quantity<input type="number" name="quantity" min="0" max="9999" required></label>
<button>Update stock</button></form></section>
<section class="card"><h2>Add item</h2><form class="formrow" method="post" action="/stock">{csrf}
<label>Item name<input name="name" maxlength="70" required></label>
<label>Item code<input name="code" maxlength="40" required></label>
<label>Starting quantity<input type="number" name="quantity" min="0" max="9999" value="1" required></label>
<button>Add item</button></form></section>"""
        history = "".join(
            f'<tr><td>{escape(log["item"])}</td><td>{escape(log["action"])}</td>'
            f'<td>{escape(log["who"])}</td><td>{time.strftime("%b %d, %I:%M %p", time.localtime(log["at"]))}</td></tr>'
            for log in activity
        )
        self.respond(page(f"""<section class="card"><div class="row"><h1>Inventory Tracker</h1>
<span class="badge">{escape(who)} · {"Admin" if admin else "Staff"}</span>
<form method="post" action="/logout">{csrf}<button class="secondary">Log out</button></form></div>
{notice(message, error)}<p>{sum(item["quantity"] for item in items)} items in stock</p>
<div class="table"><table><thead><tr><th>Item</th><th>In stock</th><th>Price</th></tr></thead>
<tbody>{rows}</tbody></table></div></section>
<section class="card"><div class="row"><h2>Scan QR</h2><button class="secondary" id="camera"
type="button">Use camera</button></div><video hidden muted playsinline></video>
<p id="scan-status" role="status">Scan with a USB QR scanner or enter an item code.</p>
<form class="formrow" method="post" action="/find">{csrf}
<label>Item code<input id="code" name="code" maxlength="240" required></label>
<button id="find">Find item</button></form>{review}</section>
{controls}<section class="card"><h2>Recent activity</h2>
{f'<div class="table"><table><thead><tr><th>Item</th><th>Action</th><th>Account</th><th>Time</th></tr></thead><tbody>{history}</tbody></table></div>' if history else '<p>No activity yet.</p>'}
</section>""", CAMERA))

    def do_GET(self):
        path = urlsplit(self.path).path
        token, account = self.session()
        if path == "/login":
            self.redirect("/inventory") if account else self.login_page()
        elif path in {"/", "/inventory"}:
            if account:
                if path == "/":
                    return self.redirect("/inventory")
                message = parse_qs(urlsplit(self.path).query).get("saved", [""])[0]
                self.inventory(token, account, message)
            else:
                self.redirect("/login")
        else:
            self.respond(status=404)

    def do_POST(self):
        # Browsers send Origin on form POSTs. Reject forms submitted from other sites.
        host = f"127.0.0.1:{self.server.server_port}"
        origin = self.headers.get("Origin")
        if self.headers.get("Host") != host or origin not in {None, f"http://{host}"}:
            return self.respond(status=403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                return self.respond(status=400)
            fields = parse_qs(self.rfile.read(length).decode(), max_num_fields=12)
        except (ValueError, UnicodeError):
            return self.respond(status=400)

        def field(key):
            return fields.get(key, [""])[0].strip() if key != "password" else fields.get(key, [""])[0]

        path = urlsplit(self.path).path
        store = self.server.store
        if path == "/login":
            token, error = store.login(field("username").lower(), field("password"))
            if error:
                return self.login_page(error)
            return self.redirect("/inventory", f"item_session={token}; Path=/; HttpOnly; SameSite=Strict")
        token, account = self.session()
        if not account:
            return self.redirect("/login")
        if not compare_digest(field("csrf").encode(), token.encode()):
            return self.respond(status=403)
        if path == "/logout":
            with store.lock:
                store.sessions.pop(token, None)
            return self.redirect("/login", "item_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        code = item_code(field("code"))
        try:
            if path == "/find":
                with store.db() as db:
                    item = db.execute("SELECT * FROM stock WHERE code = ?", (code,)).fetchone()
                if not item:
                    raise ValueError("No item matches that code.")
                return self.inventory(token, account, preview=tuple(item))
            if path == "/checkout":
                message = store.checkout(code, account[0])
            elif path == "/stock":
                if account[1] != "admin":
                    return self.respond(status=403)
                message = store.update_stock(code, field("name"), field("quantity"), account[0])
            else:
                return self.respond(status=404)
        except ValueError as error:
            return self.inventory(token, account, str(error), True)
        from urllib.parse import urlencode
        self.redirect("/inventory?" + urlencode({"saved": message}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path,
                        default=Path.home() / ".the-item" / "inventory.sqlite3")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--staff", nargs=2, metavar=("FIRST", "SECOND"))
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db)
    with store.db() as db:
        initialized = db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    if not initialized:
        staff = args.staff or [input("First staff username: "), input("Second staff username: ")]
        pin = getpass("Shared staff PIN: ")
        admin = getpass("Create admin password (8+ characters): ")
        if getpass("Retype admin password: ") != admin:
            parser.exit(1, "Admin passwords did not match. Run the command again.\n")
        try:
            store.setup(staff, pin, admin)
        except ValueError as error:
            parser.exit(1, str(error) + "\n")
    with ThreadingHTTPServer(("127.0.0.1", args.port), InventoryHandler) as server:
        server.store = store
        print(f"Open http://127.0.0.1:{server.server_port} in your browser.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
