"""Pinned profile, authentication and exact public-wire validation proofs."""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from persistent_lad_support import CHILD, RUN, capabilities, observation, receipt, setup

from millrace_plus import workflow_os_client as client
from millrace_plus.subworkflow_records import SubworkflowError, canonical, envelope


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    return setup(tmp_path, monkeypatch)


def reference(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.mark.parametrize(
    "change",
    [
        {"format": "wrong"},
        {"extra": True},
        {"os_home": None},
        {"os_home": "a/b"},
        {"os_origin": "https://localhost:8731"},
        {"os_origin": "http://example.com"},
        {"os_origin": "http://localhost:0"},
        {"os_origin": "http://localhost/"},
        {"os_origin": "http://user@localhost"},
        {"os_origin": "http://localhost?x=1"},
        {"os_credential_file": "relative"},
    ],
)
def test_profile_schema_and_hash_refusals(fixture, change):
    file = fixture["profile"]
    data = json.loads(file.read_bytes())
    file.write_bytes(canonical({**data, **change}))
    with pytest.raises(SubworkflowError):
        client.load_profile(reference(file), home="local")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"format":1,"format":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b"\xff",
        b" " * 65537,
    ],
)
def test_profile_strict_json(fixture, raw):
    file = fixture["profile"]
    file.write_bytes(raw)
    with pytest.raises(SubworkflowError):
        client.load_profile(reference(file), home="local")


def test_profile_hash_is_exact_and_does_not_open_credential(fixture):
    ref = reference(fixture["profile"])
    fixture["credential"].unlink()
    assert client.load_profile(ref, home="local")["os_home"] == "local"
    fixture["profile"].write_bytes(fixture["profile"].read_bytes() + b"\n")
    with pytest.raises(SubworkflowError, match="os_profile_changed"):
        client.load_profile(ref, home="local")


@pytest.mark.parametrize(
    "contracts",
    [
        ["millrace-os-router-call/v2"],
        ["millrace-os-router-call/v2", "millrace-os-router-call/v1"],
        ["future/v3", "millrace-os-router-call/v2"],
    ],
)
def test_capability_membership_not_list_order(contracts):
    value = capabilities()
    value["observation_contracts"] = contracts
    assert client.validate_capabilities(value, "local") == value


@pytest.mark.parametrize(
    "change",
    [
        {"home": "other"},
        {"holder_joined_supported": False},
        {"holder_joined_supported": 1},
        {"observation_contracts": "millrace-os-router-call/v2"},
        {"observation_contracts": [None, "millrace-os-router-call/v2"]},
        {"observation_contracts": ["", "millrace-os-router-call/v2"]},
        {"observation_contracts": ["millrace-os-router-call/v1"]},
        {"extra": True},
    ],
)
def test_capability_refusal(change):
    with pytest.raises(SubworkflowError):
        client.validate_capabilities({**capabilities(), **change}, "local")


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self, values):
        self.values = iter(values)
        self.requests = []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return Response(value)


def test_credentials_are_owner_only_and_read_per_request(fixture, monkeypatch):
    opener = Opener([canonical(capabilities()), canonical(capabilities())])
    monkeypatch.setattr(client, "build_opener", lambda *handlers: opener)
    connection = client.WorkflowOSClient(reference(fixture["profile"]), "local")
    connection.capabilities(deadline=time.monotonic() + 1)
    fixture["credential"].write_bytes(canonical({"token": "b" * 32}))
    connection.capabilities(deadline=time.monotonic() + 1)
    headers = [r.get_header("Authorization") for r, _ in opener.requests]
    assert headers == ["Bearer " + "a" * 32, "Bearer " + "b" * 32]
    assert "a" * 32 not in repr(vars(connection))
    assert "b" * 32 not in repr(vars(connection))


@pytest.mark.parametrize("token", ["x" * 31, "x" * 8193, "x" * 32 + "\n", None, 12])
def test_invalid_credentials_never_send(fixture, monkeypatch, token):
    fixture["credential"].write_bytes(canonical({"token": token}))
    opener = Opener([])
    monkeypatch.setattr(client, "build_opener", lambda *handlers: opener)
    connection = client.WorkflowOSClient(reference(fixture["profile"]), "local")
    with pytest.raises(SubworkflowError):
        connection.capabilities(deadline=time.monotonic() + 1)
    assert not opener.requests


def test_credential_modes_owner_and_no_follow(fixture, monkeypatch):
    file = fixture["credential"]
    file.chmod(0o644)
    with pytest.raises(SubworkflowError):
        client._credential(str(file))
    file.chmod(0o600)
    actual = os.getuid()
    monkeypatch.setattr(client.os, "getuid", lambda: actual + 10000)
    with pytest.raises(SubworkflowError):
        client._credential(str(file))
    monkeypatch.undo()
    linked = file.with_name("linked.json")
    linked.symlink_to(file)
    with pytest.raises(SubworkflowError):
        client._credential(str(linked))


def test_http_bounds_and_sanitized_refusals(fixture, monkeypatch):
    secret = "private server body"
    opener = Opener(
        [
            HTTPError("http://localhost", 503, secret, {}, io.BytesIO(secret.encode())),
            b" " * 2097153,
        ]
    )
    handlers = []

    def build(*values):
        handlers.extend(values)
        return opener

    monkeypatch.setattr(client, "build_opener", build)
    connection = client.WorkflowOSClient(reference(fixture["profile"]), "local")
    with pytest.raises(SubworkflowError, match="os_http_503") as caught:
        connection.capabilities(deadline=time.monotonic() + 1)
    assert secret not in str(caught.value)
    with pytest.raises(SubworkflowError, match="record_too_large"):
        connection.capabilities(deadline=time.monotonic() + 1)
    assert any(isinstance(h, client.ProxyHandler) and h.proxies == {} for h in handlers)
    assert any(isinstance(h, client._NoRedirect) for h in handlers)
    assert all(0 < timeout <= 2 for _, timeout in opener.requests)
    with pytest.raises(SubworkflowError, match="os_redirect_refused"):
        client._NoRedirect().redirect_request(
            None, None, 302, "", {}, "http://elsewhere"
        )


def test_capability_negotiation_precedes_post(fixture, monkeypatch):
    request = envelope("start_run", fixture["request"], instance_id=CHILD, expected=1)
    core = receipt(request, run=RUN)
    opener = Opener(
        [canonical(capabilities()), canonical(observation(request, core=core))]
    )
    monkeypatch.setattr(client, "build_opener", lambda *handlers: opener)
    connection = client.WorkflowOSClient(reference(fixture["profile"]), "local")
    connection.submit(request, deadline=time.monotonic() + 1)
    assert [r.get_method() for r, _ in opener.requests] == ["OPTIONS", "POST"]
    post = opener.requests[1][0]
    assert post.get_header("Accept") == "application/vnd.millrace.router-call.v2+json"
    assert parse_qs(urlsplit(opener.requests[0][0].full_url).query) == {
        "home": ["local"]
    }
    assert post.data == canonical({"home": "local", "request": request})


def test_submit_requires_valid_capabilities_before_post(fixture, monkeypatch):
    sent = envelope("start_run", fixture["request"], instance_id=CHILD, expected=1)
    opener = Opener([canonical({**capabilities(), "holder_joined_supported": False})])
    monkeypatch.setattr(client, "build_opener", lambda *handlers: opener)
    connection = client.WorkflowOSClient(reference(fixture["profile"]), "local")
    with pytest.raises(SubworkflowError):
        connection.submit(sent, deadline=time.monotonic() + 1)
    assert [request.get_method() for request, _ in opener.requests] == ["OPTIONS"]


@pytest.mark.parametrize("fault", ["credential", "profile", "response"])
def test_prevalidated_submit_keeps_post_boundary_checks(fixture, monkeypatch, fault):
    sent = envelope("start_run", fixture["request"], instance_id=CHILD, expected=1)
    value = observation(sent, core=receipt(sent, run=RUN))
    if fault == "response":
        value["request_id"] = "f" * 32
    opener = Opener([canonical(capabilities()), canonical(value)])
    monkeypatch.setattr(client, "build_opener", lambda *handlers: opener)
    connection = client.WorkflowOSClient(reference(fixture["profile"]), "local")
    connection.capabilities(deadline=time.monotonic() + 1)
    if fault == "credential":
        fixture["credential"].write_bytes(canonical({"token": None}))
    elif fault == "profile":
        fixture["profile"].write_bytes(fixture["profile"].read_bytes() + b" ")
    with pytest.raises(SubworkflowError):
        connection._submit_prevalidated(sent, deadline=time.monotonic() + 1)
    assert [request.get_method() for request, _ in opener.requests] == (
        ["OPTIONS", "POST"] if fault == "response" else ["OPTIONS"]
    )


def test_start_get_uses_original_envelope_target(fixture, monkeypatch):
    sent = envelope("start_run", fixture["request"], instance_id=CHILD, expected=1)
    opener = Opener([canonical(observation(sent, core=receipt(sent, run=RUN)))])
    monkeypatch.setattr(client, "build_opener", lambda *handlers: opener)
    connection = client.WorkflowOSClient(reference(fixture["profile"]), "local")
    value = connection.observe(sent, deadline=time.monotonic() + 1)
    query = parse_qs(urlsplit(opener.requests[0][0].full_url).query)
    assert query == {"home": ["local"], "instance_id": [CHILD]}
    assert value["run_id"] == RUN
    assert sent["run_id"] is None


def test_exact_request_body_limit(fixture):
    sent = envelope("create_instance", fixture["request"], instance_id=None)
    sent["payload"] = {"padding": ""}
    room = 4096 - len(client.request_body("local", sent))
    sent["payload"]["padding"] = "x" * room
    assert len(client.request_body("local", sent)) == 4096
    sent["payload"]["padding"] += "x"
    with pytest.raises(SubworkflowError, match="os_request_limit"):
        client.request_body("local", sent)


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"contract": "millrace-os-router-call/v1"},
        {"request_digest": "0" * 64},
        {"home": "other"},
        {"instance_id": "f" * 32},
        {"holder_joined": 1},
        {"observed_at": "x" * 41},
        {"run_id": "not-a-core-id"},
        {"source": "core_receipt", "holder_joined": True},
    ],
)
def test_strict_call_refusals(fixture, change):
    sent = envelope("start_run", fixture["request"], instance_id=CHILD)
    row = observation(sent)
    with pytest.raises(SubworkflowError):
        client.validate_call({**row, **change}, "local", sent)


def test_receipt_digest_and_read_identity(fixture):
    sent = envelope("start_run", fixture["request"], instance_id=CHILD)
    wrong = receipt(sent, run=RUN)
    wrong["request_digest"] = "f" * 64
    with pytest.raises(SubworkflowError, match="invalid_core_receipt"):
        client.validate_call(observation(sent, core=wrong), "local", sent)
    read = {
        "contract": "millrace-os-router-read/v1",
        "home": "local",
        "source_provider": "core",
        "authority": "core-state-home",
        "observed_at": "now",
        "data": {"id": RUN, "instance_id": CHILD},
    }
    assert client.validate_read(read, "local", CHILD, RUN) == read["data"]
    for change in ({"extra": 1}, {"observed_at": None}, {"home": "other"}):
        with pytest.raises(SubworkflowError):
            client.validate_read({**read, **change}, "local", CHILD, RUN)


def test_slow_header_cannot_extend_total_window(fixture):
    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            try:
                for byte in b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n":
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        path = fixture["profile"]
        data = json.loads(path.read_bytes())
        data["os_origin"] = f"http://127.0.0.1:{server.server_port}"
        path.write_bytes(canonical(data))
        connection = client.WorkflowOSClient(reference(path), "local")
        started = time.monotonic()
        with pytest.raises(SubworkflowError, match="observation_window_expired"):
            connection.capabilities(deadline=started + 0.12)
        assert time.monotonic() >= started + 0.12
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.parametrize("window_seconds", [0.15, 10])
def test_real_socket_timeout_distinguishes_window_from_transport_limit(
    fixture, window_seconds
):
    release = threading.Event()
    entered = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            entered.set()
            release.wait(15)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        path = fixture["profile"]
        data = json.loads(path.read_bytes())
        data["os_origin"] = f"http://127.0.0.1:{server.server_port}"
        path.write_bytes(canonical(data))
        connection = client.WorkflowOSClient(reference(path), "local")
        deadline = time.monotonic() + window_seconds
        expected = (
            "observation_window_expired" if window_seconds < 2 else "os_unavailable"
        )
        with pytest.raises(SubworkflowError, match=expected):
            connection.capabilities(deadline=deadline)
        assert entered.is_set()
        assert (time.monotonic() >= deadline) == (window_seconds < 2)
    finally:
        release.set()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("wrapped", [False, True])
def test_early_timeout_does_not_claim_window_expired(fixture, monkeypatch, wrapped):
    error = TimeoutError()
    opener = Opener([URLError(error) if wrapped else error])
    monkeypatch.setattr(client, "build_opener", lambda *handlers: opener)
    connection = client.WorkflowOSClient(reference(fixture["profile"]), "local")
    with pytest.raises(SubworkflowError, match="os_unavailable"):
        connection.capabilities(deadline=time.monotonic() + 1)


@pytest.mark.parametrize(
    "encoding",
    [
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "utf-32",
        "utf-32-le",
        "utf-32-be",
        "utf-8-sig",
    ],
)
@pytest.mark.parametrize("boundary", ["profile", "credential", "http"])
def test_non_utf8_and_bom_json_refuse_before_http_mutation(
    fixture,
    monkeypatch,
    encoding,
    boundary,
):
    opener = Opener([])
    monkeypatch.setattr(client, "build_opener", lambda *handlers: opener)
    if boundary == "profile":
        path = fixture["profile"]
        path.write_bytes(path.read_bytes().decode("utf-8").encode(encoding))
        with pytest.raises(SubworkflowError, match="invalid_json"):
            client.WorkflowOSClient(reference(path), "local")
        assert opener.requests == []
        return
    connection = client.WorkflowOSClient(reference(fixture["profile"]), "local")
    if boundary == "credential":
        path = fixture["credential"]
        path.write_bytes(path.read_bytes().decode("utf-8").encode(encoding))
        with pytest.raises(SubworkflowError, match="invalid_json"):
            connection.capabilities(deadline=time.monotonic() + 1)
        assert opener.requests == []
    else:
        opener.values = iter(
            [canonical(capabilities()).decode("utf-8").encode(encoding)]
        )
        sent = envelope("start_run", fixture["request"], instance_id=CHILD)
        with pytest.raises(SubworkflowError, match="invalid_json"):
            connection.submit(sent, deadline=time.monotonic() + 1)
        assert [request.get_method() for request, _ in opener.requests] == ["OPTIONS"]


def plane_call(fixture, plane):
    sent = envelope(
        "resume_plane" if plane is not None else "resume_run",
        fixture["request"],
        instance_id=CHILD,
        run_id=RUN,
        expected=8,
        explicit_id="d" * 32,
    )
    sent["plane_id"] = plane
    core = receipt(sent, run=RUN)
    core["plane_id"] = plane
    return sent, observation(sent, core=core)


def through_call_boundary(fixture, monkeypatch, method, sent, value):
    if method == "validate":
        return client.validate_call(value, "local", sent)
    values = [canonical(value)]
    if method == "submit":
        values.insert(0, canonical(capabilities()))
    opener = Opener(values)
    monkeypatch.setattr(client, "build_opener", lambda *handlers: opener)
    connection = client.WorkflowOSClient(reference(fixture["profile"]), "local")
    result = getattr(connection, method)(sent, deadline=time.monotonic() + 1)
    if method == "submit":
        assert [request.get_method() for request, _ in opener.requests] == [
            "OPTIONS",
            "POST",
        ]
        assert opener.requests[-1][0].data == canonical(
            {"home": "local", "request": sent}
        )
    else:
        assert [request.get_method() for request, _ in opener.requests] == ["GET"]
        assert parse_qs(urlsplit(opener.requests[0][0].full_url).query) == {
            "home": ["local"],
            "instance_id": [CHILD],
            "run_id": [RUN],
        }
    return result


@pytest.mark.parametrize("method", ["validate", "submit", "observe"])
@pytest.mark.parametrize("plane", [None, "foreground", "service"])
def test_exact_receipt_plane_survives_all_call_boundaries(
    fixture, monkeypatch, method, plane
):
    sent, value = plane_call(fixture, plane)
    before = canonical(sent), canonical(value)
    assert client.validate_receipt(value["core_receipt"], sent) == value["core_receipt"]
    assert through_call_boundary(fixture, monkeypatch, method, sent, value) == value
    assert (canonical(sent), canonical(value)) == before


@pytest.mark.parametrize("method", ["validate", "submit", "observe"])
@pytest.mark.parametrize(
    "expected,actual",
    [
        ("foreground", None),
        ("foreground", "learning"),
        ("foreground", "missing"),
        (None, "foreground"),
        (None, False),
        (None, ""),
    ],
)
def test_wrong_null_or_missing_receipt_plane_refuses_without_normalization(
    fixture, monkeypatch, method, expected, actual
):
    sent, value = plane_call(fixture, expected)
    if actual == "missing":
        value["core_receipt"].pop("plane_id")
    else:
        value["core_receipt"]["plane_id"] = actual
    before = canonical(sent), canonical(value)
    with pytest.raises(SubworkflowError, match="wrong_core_target"):
        through_call_boundary(fixture, monkeypatch, method, sent, value)
    assert (canonical(sent), canonical(value)) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "f" * 32),
        ("request_digest", "0" * 64),
        ("operation", "cancel_run"),
        ("status", "other"),
        ("instance_id", "f" * 32),
        ("run_id", "f" * 32),
    ],
)
def test_matching_plane_cannot_hide_other_receipt_identity_or_digest_failures(
    fixture, field, value
):
    sent, observed = plane_call(fixture, "foreground")
    observed["core_receipt"][field] = value
    with pytest.raises(SubworkflowError):
        client.validate_call(observed, "local", sent)
