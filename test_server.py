"""Unit tests for the confirmation-gate logic in server.py.

These cover the pure decision helpers and the elicitation fallback path -- no
network, no Gmail, no MCP client required.
"""

import asyncio

import core
import server


# --------------------------------------------------------------------------- #
# _needs_confirmation: ask the human when large, recent, or Primary.
# --------------------------------------------------------------------------- #
def test_small_old_non_primary_run_needs_no_confirmation():
    assert server._needs_confirmation(total=10, recent=0, categories=["promotions"]) is False


def test_large_run_needs_confirmation():
    assert server._needs_confirmation(total=51, recent=0, categories=["promotions"]) is True


def test_recent_mail_needs_confirmation():
    assert server._needs_confirmation(total=5, recent=1, categories=["updates"]) is True


def test_primary_always_needs_confirmation():
    assert server._needs_confirmation(total=1, recent=0, categories=["primary"]) is True


def test_threshold_is_exclusive_at_50():
    # Exactly COUNT_THRESHOLD should NOT trip the gate; one more should.
    assert server._needs_confirmation(50, 0, ["promotions"]) is False
    assert server._needs_confirmation(51, 0, ["promotions"]) is True


# --------------------------------------------------------------------------- #
# _confirm_message: human-readable summary.
# --------------------------------------------------------------------------- #
def test_confirm_message_mentions_total():
    msg = server._confirm_message(123, 0, ["promotions"])
    assert "123" in msg and msg.endswith("Proceed?")


def test_confirm_message_flags_recent_and_primary():
    msg = server._confirm_message(200, 7, ["primary"])
    assert server.RECENT_WINDOW in msg
    assert "7" in msg
    assert "Primary" in msg


# --------------------------------------------------------------------------- #
# _confirm fallback: no client elicitation -> typed token, else fail closed.
# --------------------------------------------------------------------------- #
def test_confirm_fallback_accepts_token():
    assert asyncio.run(server._confirm(None, "ok?", "CONFIRM")) is True
    assert asyncio.run(server._confirm(None, "ok?", "primary")) is True  # case-insensitive


def test_confirm_fallback_fails_closed_without_token():
    assert asyncio.run(server._confirm(None, "ok?", "")) is False
    assert asyncio.run(server._confirm(None, "ok?", "nope")) is False


# --------------------------------------------------------------------------- #
# _confirm via elicitation: respects the user's action/data.
# --------------------------------------------------------------------------- #
class _FakeData:
    def __init__(self, confirm):
        self.confirm = confirm


class _FakeElicitResult:
    def __init__(self, action, confirm=None):
        self.action = action
        self.data = _FakeData(confirm) if confirm is not None else None


class _FakeCtx:
    def __init__(self, result):
        self._result = result

    async def elicit(self, message, schema):
        return self._result


def test_confirm_accepts_when_user_accepts():
    ctx = _FakeCtx(_FakeElicitResult("accept", confirm=True))
    assert asyncio.run(server._confirm(ctx, "ok?", "")) is True


def test_confirm_rejects_when_user_declines():
    ctx = _FakeCtx(_FakeElicitResult("decline"))
    assert asyncio.run(server._confirm(ctx, "ok?", "")) is False


def test_confirm_rejects_when_accepted_but_unchecked():
    ctx = _FakeCtx(_FakeElicitResult("accept", confirm=False))
    assert asyncio.run(server._confirm(ctx, "ok?", "")) is False


def test_confirm_falls_back_when_elicit_unsupported():
    class _BadCtx:
        async def elicit(self, message, schema):
            raise RuntimeError("client does not support elicitation")

    # elicit blows up (headless client) -> token still works, empty fails closed.
    assert asyncio.run(server._confirm(_BadCtx(), "ok?", "CONFIRM")) is True
    assert asyncio.run(server._confirm(_BadCtx(), "ok?", "")) is False
