# Cardea Agent - runs on every user's device.
# Secrets are kept in plaintext ONLY on this device (vault.json).
# The hub is used to find other devices; secrets are sent straight to the other device's IP,
# encrypted with the receiver's pairing code.

import base64
import hashlib
import json
import os
import secrets

import requests
from cryptography.fernet import Fernet
from flask import Flask, jsonify, redirect, render_template, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

app = Flask(__name__)

HUB_URL = os.environ.get("HUB_URL", "http://localhost:5000")
DEVICE_NAME = os.environ.get("DEVICE_NAME", "my-device")
PUBLIC_IP = os.environ.get("PUBLIC_IP", "")  # IP other devices use to reach me (blank = hub detects it)
PUBLIC_PORT = int(os.environ.get("PUBLIC_PORT", "5001"))
VAULT_FILE = os.environ.get("VAULT_FILE", "vault.json")

SENT = Counter("cardea_secrets_sent_total", "Secrets sent to other devices")
RECEIVED = Counter("cardea_secrets_received_total", "Secrets received from other devices")


# ---------- Local vault (plaintext stays here) ----------

def load_vault():
    if not os.path.exists(VAULT_FILE):
        return {"code": "", "keys": {}}
    with open(VAULT_FILE) as f:
        return json.load(f)


def save_vault(vault):
    with open(VAULT_FILE, "w") as f:
        json.dump(vault, f, indent=2)


# ---------- Encryption ----------

def cipher_for(code):
    # Turn a pairing code into a Fernet key
    key = base64.urlsafe_b64encode(hashlib.sha256(code.encode()).digest())
    return Fernet(key)


def encrypt(code, text):
    return cipher_for(code).encrypt(text.encode()).decode()


def decrypt(code, token):
    return cipher_for(code).decrypt(token.encode()).decode()


# ---------- Talking to the hub and to other devices ----------

def hub(method, path, **kwargs):
    resp = requests.request(method, HUB_URL + path, timeout=5, **kwargs)
    resp.raise_for_status()
    return resp.json()


def send_secret(key_name, value, target_code):
    vault = load_vault()
    peer = hub("GET", f"/lookup/{target_code}")
    payload = {"sender": vault["code"], "key_name": key_name, "payload": encrypt(target_code, value)}
    requests.post(f"http://{peer['ip']}:{peer['port']}/receive", json=payload, timeout=5).raise_for_status()
    hub("POST", "/audit", json={"actor": vault["code"], "target": target_code, "key_name": key_name, "action": "share"})
    SENT.inc()


def resolve_targets(target):
    # "group:devs" -> every member of the group, otherwise a single pairing code
    if target.startswith("group:"):
        members = hub("GET", f"/groups/{target[6:]}/members")["members"]
        return [m for m in members if m != load_vault()["code"]]
    return [target]


# ---------- Web pages / API ----------

@app.get("/")
def index():
    vault = load_vault()
    pending, audit = [], []
    try:
        audit = hub("GET", "/audit")["events"]
        if vault["code"]:
            pending = hub("GET", f"/requests/{vault['code']}")["requests"]
    except requests.RequestException:
        pass  # hub offline: still show local secrets
    return render_template("index.html", vault=vault, name=DEVICE_NAME, hub_url=HUB_URL, pending=pending, audit=audit)


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


@app.post("/register")
def register():
    result = hub("POST", "/register", json={"name": DEVICE_NAME, "ip": PUBLIC_IP, "port": PUBLIC_PORT})
    vault = load_vault()
    vault["code"] = result["code"]
    save_vault(vault)
    return redirect("/")


@app.post("/keys")
def add_key():
    vault = load_vault()
    vault["keys"][request.form["name"]] = {
        "value": request.form["value"],
        "hidden": request.form.get("hidden") == "on",
        "shared_with": [],
    }
    save_vault(vault)
    return redirect("/")


@app.post("/share")
def share():
    key_name = request.form["key_name"]
    vault = load_vault()
    key = vault["keys"][key_name]
    for code in resolve_targets(request.form["to"]):
        send_secret(key_name, key["value"], code)
        if code not in key["shared_with"]:
            key["shared_with"].append(code)
    save_vault(vault)
    return redirect("/")


@app.post("/rotate")
def rotate():
    # Admin rotates a key: new random value, pushed again to everyone who had it
    key_name = request.form["key_name"]
    vault = load_vault()
    key = vault["keys"][key_name]
    key["value"] = secrets.token_urlsafe(24)
    save_vault(vault)
    for code in key["shared_with"]:
        send_secret(key_name, key["value"], code)
    hub("POST", "/audit", json={"actor": vault["code"], "target": "-", "key_name": key_name, "action": "rotate"})
    return redirect("/")


@app.post("/groups")
def create_group():
    hub("POST", "/groups", json={"name": request.form["group"], "admin": load_vault()["code"]})
    return redirect("/")


@app.post("/groups/add")
def add_to_group():
    body = {"admin": load_vault()["code"], "member": request.form["member"]}
    hub("POST", f"/groups/{request.form['group']}/members", json=body)
    return redirect("/")


@app.post("/request-key")
def request_key():
    body = {"requester": load_vault()["code"], "owner": request.form["owner"], "key_name": request.form["key_name"]}
    hub("POST", "/requests", json=body)
    return redirect("/")


@app.post("/answer-request")
def answer_request():
    vault = load_vault()
    key = vault["keys"].get(request.form["key_name"])
    # Hidden keys (or keys we don't have) are always denied
    if request.form["decision"] == "approve" and key and not key["hidden"]:
        send_secret(request.form["key_name"], key["value"], request.form["requester"])
        key["shared_with"].append(request.form["requester"])
        save_vault(vault)
        status = "approved"
    else:
        status = "denied"
    hub("POST", f"/requests/{request.form['id']}/close", json={"status": status})
    return redirect("/")


@app.post("/receive")
def receive():
    # Another device sends us a secret directly
    data = request.get_json()
    vault = load_vault()
    value = decrypt(vault["code"], data["payload"])
    vault["keys"][data["key_name"]] = {"value": value, "hidden": False, "shared_with": [], "from": data["sender"]}
    save_vault(vault)
    RECEIVED.inc()
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
