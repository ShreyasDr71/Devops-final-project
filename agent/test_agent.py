import os
import tempfile
from unittest import mock

os.environ["VAULT_FILE"] = os.path.join(tempfile.mkdtemp(), "vault.json")
os.environ["POLL_SECONDS"] = "0"  # no background thread in tests

import agent_app  # noqa: E402

client = agent_app.app.test_client()


def set_code(code):
    vault = agent_app.load_vault()
    vault["code"] = code
    agent_app.save_vault(vault)


def test_encrypt_decrypt_roundtrip():
    token = agent_app.encrypt("ABC123", "hello")
    assert token != "hello"
    assert agent_app.decrypt("ABC123", token) == "hello"


def test_add_key_stays_local():
    client.post("/keys", data={"name": "db_pass", "value": "s3cret"})
    assert agent_app.load_vault()["keys"]["db_pass"]["value"] == "s3cret"


def test_receive_decrypts_with_my_code():
    set_code("ME1234")
    payload = agent_app.encrypt("ME1234", "from-a-friend")
    client.post("/receive", json={"sender": "FRIEND", "key_name": "shared", "payload": payload})
    assert agent_app.load_vault()["keys"]["shared"]["value"] == "from-a-friend"


def test_share_sends_encrypted_value_directly_to_peer():
    set_code("ME1234")
    client.post("/keys", data={"name": "api_key", "value": "abc"})

    with mock.patch.object(agent_app, "hub") as hub, mock.patch.object(agent_app.requests, "post") as post:
        hub.return_value = {"ip": "1.2.3.4", "port": 5001}
        client.post("/share", data={"key_name": "api_key", "to": "PEER99"})

    url = post.call_args.args[0]
    sent = post.call_args.kwargs["json"]
    assert url == "http://1.2.3.4:5001/receive"
    assert sent["payload"] != "abc"
    assert agent_app.decrypt("PEER99", sent["payload"]) == "abc"
    assert "PEER99" in agent_app.load_vault()["keys"]["api_key"]["shared_with"]


def test_hidden_key_request_is_denied():
    client.post("/keys", data={"name": "root_pass", "value": "x", "hidden": "on"})
    with mock.patch.object(agent_app, "hub") as hub, mock.patch.object(agent_app, "send_secret") as send:
        form = {"id": "1", "requester": "PEER99", "key_name": "root_pass", "decision": "approve"}
        client.post("/answer-request", data=form)
    send.assert_not_called()
    hub.assert_called_with("POST", "/requests/1/close", json={"status": "denied"})


def test_health():
    assert client.get("/health").get_json() == {"status": "ok"}


def test_home_page_works_when_hub_is_offline():
    agent_app.HUB_URL = "http://127.0.0.1:1"
    page = client.get("/")
    assert page.status_code == 200
    assert b"db_pass" in page.data


def test_unreachable_peer_falls_back_to_hub_relay():
    set_code("ME1234")
    client.post("/keys", data={"name": "token", "value": "t0k"})
    with mock.patch.object(agent_app, "hub") as hub, mock.patch.object(agent_app.requests, "post") as post:
        hub.return_value = {"ip": "10.9.9.9", "port": 5001}
        post.side_effect = agent_app.requests.ConnectionError()
        client.post("/share", data={"key_name": "token", "to": "PEER99"})

    relay_call = [c for c in hub.call_args_list if c.args[1] == "/relay/PEER99"][0]
    assert agent_app.decrypt("PEER99", relay_call.kwargs["json"]["payload"]) == "t0k"


def test_check_inbox_stores_relayed_secret():
    set_code("ME1234")
    message = {"sender": "FRIEND", "key_name": "wifi", "payload": agent_app.encrypt("ME1234", "pa55")}
    with mock.patch.object(agent_app, "hub", return_value={"messages": [message]}):
        agent_app.check_inbox()
    assert agent_app.load_vault()["keys"]["wifi"]["value"] == "pa55"


def test_relay_only_peer_skips_direct_send():
    set_code("ME1234")
    client.post("/keys", data={"name": "k2", "value": "v2"})
    with mock.patch.object(agent_app, "hub") as hub, mock.patch.object(agent_app.requests, "post") as post:
        hub.return_value = {"ip": "1.2.3.4", "port": 0}
        client.post("/share", data={"key_name": "k2", "to": "PEER99"})
    post.assert_not_called()
    assert any(c.args[1] == "/relay/PEER99" for c in hub.call_args_list)
