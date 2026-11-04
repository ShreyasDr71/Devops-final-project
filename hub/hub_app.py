# Cardea Hub - runs on the EC2 server.
# It pairs devices, manages groups, and keeps an audit log.
# It NEVER sees or stores secret values. Secrets travel device -> device.

import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

app = Flask(__name__)
DB_FILE = os.environ.get("HUB_DB", "hub.db")

# Prometheus metrics
REQUESTS = Counter("cardea_hub_requests_total", "Requests handled by the hub", ["endpoint"])
AUDIT_EVENTS = Counter("cardea_audit_events_total", "Audit events recorded", ["action"])
DEVICES = Counter("cardea_devices_registered_total", "Devices registered")


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS devices (code TEXT PRIMARY KEY, name TEXT, ip TEXT, port INTEGER)")
        conn.execute("CREATE TABLE IF NOT EXISTS groups_ (name TEXT PRIMARY KEY, admin TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS members (grp TEXT, code TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS contacts (owner TEXT, peer TEXT)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS requests "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, requester TEXT, owner TEXT, key_name TEXT, status TEXT)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS audit (time TEXT, actor TEXT, target TEXT, key_name TEXT, action TEXT)")


def log_event(actor, target, key_name, action):
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute("INSERT INTO audit VALUES (?, ?, ?, ?, ?)", (now, actor, target, key_name, action))
    AUDIT_EVENTS.labels(action).inc()
    # Printed to stdout so Docker ships it to CloudWatch Logs
    print(f"AUDIT {now} {action} actor={actor} target={target} key={key_name}", flush=True)


@app.before_request
def count_request():
    REQUESTS.labels(request.endpoint or "unknown").inc()


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


# ---------- Devices / pairing ----------

@app.post("/register")
def register():
    data = request.get_json()
    code = secrets.token_hex(3).upper()  # 6 character pairing code
    ip = data.get("ip") or request.remote_addr
    with db() as conn:
        conn.execute("INSERT INTO devices VALUES (?, ?, ?, ?)", (code, data["name"], ip, int(data["port"])))
    DEVICES.inc()
    log_event(data["name"], code, "-", "register")
    return jsonify(code=code, ip=ip)


@app.get("/lookup/<code>")
def lookup(code):
    with db() as conn:
        row = conn.execute("SELECT * FROM devices WHERE code = ?", (code,)).fetchone()
    if row is None:
        return jsonify(error="unknown device"), 404
    return jsonify(dict(row))


# ---------- Pairing with a short-lived PIN ----------
# Device A asks for a PIN, device B types it in, and both become contacts.

PINS = {}  # pin -> (device code, expiry time); memory only
PIN_LIFETIME = 600  # seconds


@app.post("/pair/start")
def pair_start():
    code = request.get_json()["code"]
    pin = f"{secrets.randbelow(1_000_000):06d}"
    PINS[pin] = (code, time.time() + PIN_LIFETIME)
    return jsonify(pin=pin, expires_in=PIN_LIFETIME)


@app.post("/pair/join")
def pair_join():
    data = request.get_json()
    owner, expires = PINS.get(data["pin"], (None, 0))
    if owner is None or time.time() > expires:
        return jsonify(error="This PIN is wrong or has expired. Ask for a new one."), 400
    if owner == data["code"]:
        return jsonify(error="You can't pair a device with itself."), 400
    del PINS[data["pin"]]
    with db() as conn:
        for a, b in ((owner, data["code"]), (data["code"], owner)):
            if not conn.execute("SELECT 1 FROM contacts WHERE owner = ? AND peer = ?", (a, b)).fetchone():
                conn.execute("INSERT INTO contacts VALUES (?, ?)", (a, b))
    log_event(data["code"], owner, "-", "pair")
    return jsonify(ok=True)


@app.get("/contacts/<code>")
def contacts(code):
    with db() as conn:
        rows = conn.execute(
            "SELECT d.code, d.name FROM contacts c JOIN devices d ON d.code = c.peer WHERE c.owner = ?", (code,)
        ).fetchall()
    return jsonify(contacts=[dict(r) for r in rows])


@app.post("/contacts/<code>/remove")
def remove_contact(code):
    peer = request.get_json()["peer"]
    with db() as conn:
        conn.execute("DELETE FROM contacts WHERE (owner = ? AND peer = ?) OR (owner = ? AND peer = ?)",
                     (code, peer, peer, code))
    log_event(code, peer, "-", "unpair")
    return jsonify(ok=True)


# ---------- Groups ----------

@app.post("/groups")
def create_group():
    data = request.get_json()
    with db() as conn:
        conn.execute("INSERT INTO groups_ VALUES (?, ?)", (data["name"], data["admin"]))
        conn.execute("INSERT INTO members VALUES (?, ?)", (data["name"], data["admin"]))
    log_event(data["admin"], data["name"], "-", "create_group")
    return jsonify(ok=True)


@app.post("/groups/<name>/members")
def add_member(name):
    data = request.get_json()
    with db() as conn:
        group = conn.execute("SELECT * FROM groups_ WHERE name = ?", (name,)).fetchone()
        if group is None or group["admin"] != data["admin"]:
            return jsonify(error="only the group admin can add members"), 403
        conn.execute("INSERT INTO members VALUES (?, ?)", (name, data["member"]))
    log_event(data["admin"], data["member"], "-", f"add_to_group:{name}")
    return jsonify(ok=True)


@app.get("/groups/of/<code>")
def groups_of(code):
    with db() as conn:
        rows = conn.execute(
            "SELECT g.name, g.admin FROM groups_ g JOIN members m ON m.grp = g.name WHERE m.code = ?", (code,)
        ).fetchall()
    return jsonify(groups=[{"name": r["name"], "is_admin": r["admin"] == code} for r in rows])


@app.get("/groups/<name>/members")
def list_members(name):
    with db() as conn:
        rows = conn.execute("SELECT code FROM members WHERE grp = ?", (name,)).fetchall()
    return jsonify(members=[r["code"] for r in rows])


# ---------- Key requests (user asks the admin for a key) ----------

@app.post("/requests")
def create_request():
    data = request.get_json()
    with db() as conn:
        conn.execute(
            "INSERT INTO requests (requester, owner, key_name, status) VALUES (?, ?, ?, 'pending')",
            (data["requester"], data["owner"], data["key_name"]),
        )
    log_event(data["requester"], data["owner"], data["key_name"], "request_key")
    return jsonify(ok=True)


@app.get("/requests/<owner>")
def pending_requests(owner):
    with db() as conn:
        rows = conn.execute("SELECT * FROM requests WHERE owner = ? AND status = 'pending'", (owner,)).fetchall()
    return jsonify(requests=[dict(r) for r in rows])


@app.post("/requests/<int:req_id>/close")
def close_request(req_id):
    status = request.get_json()["status"]  # "approved" or "denied"
    with db() as conn:
        conn.execute("UPDATE requests SET status = ? WHERE id = ?", (status, req_id))
    return jsonify(ok=True)


# ---------- Relay for devices behind home Wi-Fi / NAT ----------
# If a device can't be reached directly, the sender leaves the ENCRYPTED message here.
# Kept in memory only (never written to disk) and deleted as soon as the device collects it.

INBOX = {}


@app.post("/relay/<code>")
def relay_put(code):
    INBOX.setdefault(code, []).append(request.get_json())
    return jsonify(ok=True)


@app.get("/relay/<code>")
def relay_take(code):
    return jsonify(messages=INBOX.pop(code, []))


# ---------- Audit log ----------

@app.post("/audit")
def add_audit():
    data = request.get_json()
    log_event(data["actor"], data["target"], data["key_name"], data["action"])
    return jsonify(ok=True)


@app.get("/audit/<code>")
def device_audit(code):
    # Only the events this device took part in
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit WHERE actor = ? OR target = ? ORDER BY time DESC LIMIT 100", (code, code)
        ).fetchall()
    return jsonify(events=[dict(r) for r in rows])


@app.post("/devices/<code>/remove")
def remove_device(code):
    # "Forget this device": drop it and its pairings
    with db() as conn:
        conn.execute("DELETE FROM devices WHERE code = ?", (code,))
        conn.execute("DELETE FROM contacts WHERE owner = ? OR peer = ?", (code, code))
        conn.execute("DELETE FROM members WHERE code = ?", (code,))
    INBOX.pop(code, None)
    log_event(code, "-", "-", "forget_device")
    return jsonify(ok=True)


@app.get("/audit")
def get_audit():
    with db() as conn:
        rows = conn.execute("SELECT * FROM audit ORDER BY time DESC LIMIT 100").fetchall()
    return jsonify(events=[dict(r) for r in rows])


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
