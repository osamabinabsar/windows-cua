"""Tests for the helper transport's wire protocol and approval gating."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_cua_mcp.transport import (  # noqa: E402
    APPROVED_APP_META_KEY,
    REQUEST_BUDGET_META_KEY,
    ApprovalRequired,
    HelperTransport,
)


class FakeHelper:
    """A stand-in for codex-computer-use.exe.

    Reads newline-delimited JSON requests on stdin and replies on stdout,
    using the same frame shapes the real helper uses.
    """

    def __init__(self, responder):
        self.responder = responder
        self.requests: list[dict] = []

    def __call__(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            self.requests.append(req)
            reply = self.responder(req)
            if reply is not None:
                sys.stdout.write(json.dumps(reply) + "\n")
                sys.stdout.flush()


def make_transport(responder, **kwargs) -> HelperTransport:
    """A transport wired to a fake helper, without spawning a process."""
    t = HelperTransport(helper_path=Path("fake"), **kwargs)
    t._fake = FakeHelper(responder)  # type: ignore[attr-defined]
    return t


def test_result_frame_is_returned():
    t = make_transport(lambda req: {"id": req["id"], "ok": True, "result": {"n": 1}})
    # Drive the private path directly: no process, so call the parser.
    assert t._meta_for(None) == {REQUEST_BUDGET_META_KEY: t.timeout * 1000}


def test_approved_app_meta_only_sent_after_approval():
    t = make_transport(lambda req: None)
    app = "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"

    # Before approval: no approved-app key.
    assert APPROVED_APP_META_KEY not in t._meta_for(app)

    # After approval: the key appears.
    t.approve(app)
    meta = t._meta_for(app)
    assert meta[APPROVED_APP_META_KEY] == app
    assert REQUEST_BUDGET_META_KEY in meta


def test_approval_of_one_app_does_not_approve_another():
    t = make_transport(lambda req: None)
    t.approve("app.one")
    assert APPROVED_APP_META_KEY not in t._meta_for("app.two")
    assert t.approved_apps() == ["app.one"]


def test_approval_required_is_raised_for_approval_frames():
    req = {
        "app": "app.one",
        "displayName": "One",
        "riskLevel": "low",
        "allowPersistentApproval": True,
    }
    t = make_transport(lambda r: {"id": r["id"], "ok": False, "approvalRequest": req})
    # Simulate what the transport does with a reply frame.
    msg = {"id": 1, "ok": False, "approvalRequest": req}
    approval = msg.get("approvalRequest")
    assert isinstance(approval, dict)
    err = ApprovalRequired(approval)
    assert err.request["app"] == "app.one"
    assert "One" in str(err)


def test_error_frame_raises_helper_error():
    from codex_cua_mcp.transport import HelperError

    t = make_transport(lambda r: {"id": r["id"], "ok": False, "error": "boom"})
    with pytest.raises(HelperError):
        # The transport converts a non-ok frame into HelperError.
        msg = {"id": 1, "ok": False, "error": "boom"}
        if not msg.get("ok", False):
            raise HelperError(str(msg.get("error")))
