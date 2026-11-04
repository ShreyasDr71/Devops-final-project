# Cardea Hub - runs on the EC2 server.
# It pairs devices, manages groups, and keeps an audit log.
# It NEVER sees or stores secret values. Secrets travel device -> device.

import os
import secrets
import sqlite3
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


# ---------- Audit log ----------

@app.post("/audit")
def add_audit():
    data = request.get_json()
    log_event(data["actor"], data["target"], data["key_name"], data["action"])
    return jsonify(ok=True)


@app.get("/audit")
def get_audit():
    with db() as conn:
        rows = conn.execute("SELECT * FROM audit ORDER BY time DESC LIMIT 100").fetchall()
    return jsonify(events=[dict(r) for r in rows])


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
