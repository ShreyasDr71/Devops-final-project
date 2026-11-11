import os
import tempfile

os.environ["HUB_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import hub_app  # noqa: E402

client = hub_app.app.test_client()


def register(name):
    return client.post("/register", json={"name": name, "ip": "10.0.0.5", "port": 5001}).get_json()["code"]


def test_health():
    assert client.get("/health").get_json() == {"status": "ok", "color": "local", "build": "dev"}


def test_register_and_lookup():
    code = register("laptop")
    device = client.get(f"/lookup/{code}").get_json()
    assert device["name"] == "laptop"
    assert device["ip"] == "10.0.0.5"


def test_lookup_unknown_device():
    assert client.get("/lookup/NOPE00").status_code == 404


def test_only_admin_can_add_members():
    admin, user, stranger = register("admin"), register("user"), register("stranger")
    client.post("/groups", json={"name": "devs", "admin": admin})

    assert client.post("/groups/devs/members", json={"admin": stranger, "member": user}).status_code == 403
    assert client.post("/groups/devs/members", json={"admin": admin, "member": user}).status_code == 200

    members = client.get("/groups/devs/members").get_json()["members"]
    assert admin in members and user in members


def test_key_request_flow():
    client.post("/requests", json={"requester": "AAA", "owner": "BBB", "key_name": "db_pass"})
    pending = client.get("/requests/BBB").get_json()["requests"]
    assert pending[0]["key_name"] == "db_pass"

    client.post(f"/requests/{pending[0]['id']}/close", json={"status": "approved"})
    assert client.get("/requests/BBB").get_json()["requests"] == []


def test_audit_never_contains_secret_values():
    client.post("/audit", json={"actor": "AAA", "target": "BBB", "key_name": "api_key", "action": "share"})
    events = client.get("/audit").get_json()["events"]
    assert any(e["key_name"] == "api_key" for e in events)
    assert all(set(e) == {"time", "actor", "target", "key_name", "action"} for e in events)


def test_metrics():
    assert b"cardea_hub_requests_total" in client.get("/metrics").data


def test_relay_is_delivered_once_then_deleted():
    client.post("/relay/BOB123", json={"sender": "A", "key_name": "k", "payload": "encrypted"})
    assert client.get("/relay/BOB123").get_json()["messages"][0]["payload"] == "encrypted"
    assert client.get("/relay/BOB123").get_json()["messages"] == []


def test_pairing_with_pin():
    a, b = register("phone"), register("laptop")
    pin = client.post("/pair/start", json={"code": a}).get_json()["pin"]
    assert len(pin) == 6

    assert client.post("/pair/join", json={"code": b, "pin": "000000" if pin != "000000" else "111111"}).status_code == 400
    assert client.post("/pair/join", json={"code": b, "pin": pin}).status_code == 200
    assert client.post("/pair/join", json={"code": b, "pin": pin}).status_code == 400  # PIN is single use

    assert client.get(f"/contacts/{a}").get_json()["contacts"] == [{"code": b, "name": "laptop"}]
    assert client.get(f"/contacts/{b}").get_json()["contacts"] == [{"code": a, "name": "phone"}]

    client.post(f"/contacts/{a}/remove", json={"peer": b})
    assert client.get(f"/contacts/{b}").get_json()["contacts"] == []


def test_cannot_pair_with_itself():
    a = register("solo")
    pin = client.post("/pair/start", json={"code": a}).get_json()["pin"]
    assert client.post("/pair/join", json={"code": a, "pin": pin}).status_code == 400


def test_groups_of_device_and_device_audit():
    admin, member = register("boss"), register("dev")
    client.post("/groups", json={"name": "ops", "admin": admin})
    client.post("/groups/ops/members", json={"admin": admin, "member": member})
    assert client.get(f"/groups/of/{member}").get_json()["groups"] == [{"name": "ops", "is_admin": False}]
    assert {"name": "ops", "is_admin": True} in client.get(f"/groups/of/{admin}").get_json()["groups"]

    events = client.get(f"/audit/{member}").get_json()["events"]
    assert events and all(member in (e["actor"], e["target"]) for e in events)


def test_forget_device():
    a = register("old-phone")
    client.post(f"/devices/{a}/remove")
    assert client.get(f"/lookup/{a}").status_code == 404
