"""Unit tests for the pure logic in core.py (no network / no Gmail calls)."""

import json

import pytest

import core


def test_build_query_basic():
    assert core.build_query("promotions") == "category:promotions -in:trash"


def test_build_query_with_age():
    assert (
        core.build_query("primary", "30d")
        == "category:primary older_than:30d -in:trash"
    )


def test_build_query_all_categories_have_tokens():
    for name in core.CATEGORY_QUERIES:
        q = core.build_query(name)
        assert q.startswith(f"category:{name}")
        assert q.endswith("-in:trash")


def test_primary_is_a_known_category():
    assert core.PRIMARY in core.CATEGORY_QUERIES


def test_validate_credentials_rejects_bad_json():
    with pytest.raises(core.CredentialsError):
        core.validate_credentials_json("not json{")


def test_validate_credentials_rejects_web_client():
    web = json.dumps({"web": {"client_id": "x", "client_secret": "y"}})
    with pytest.raises(core.CredentialsError):
        core.validate_credentials_json(web)


def test_validate_credentials_requires_secret():
    creds = json.dumps({"installed": {"client_id": "x"}})
    with pytest.raises(core.CredentialsError):
        core.validate_credentials_json(creds)


def test_validate_credentials_accepts_desktop_client():
    creds = json.dumps(
        {"installed": {"client_id": "abc", "client_secret": "shh"}}
    )
    data = core.validate_credentials_json(creds)
    assert data["installed"]["client_id"] == "abc"


class _FakeMessages:
    """Minimal stand-in for service.users().messages() for trash/list tests."""

    def __init__(self, pages):
        self._pages = pages
        self.batched = []

    def list(self, userId, q, maxResults, pageToken=None):
        page = self._pages[pageToken or 0]
        return _FakeExec(page)

    def batchModify(self, userId, body):
        self.batched.append(body["ids"])
        return _FakeExec({})


class _FakeExec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FakeService:
    def __init__(self, messages):
        self._messages = messages

    def users(self):
        return self

    def messages(self):
        return self._messages


def test_list_message_ids_handles_paging():
    pages = {
        0: {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"},
        "p2": {"messages": [{"id": "c"}]},
    }
    svc = _FakeService(_FakeMessages(pages))
    assert core.list_message_ids(svc, "q") == ["a", "b", "c"]


def test_trash_ids_batches_in_chunks(monkeypatch):
    monkeypatch.setattr(core, "BATCH_SIZE", 2)
    msgs = _FakeMessages({})
    svc = _FakeService(msgs)
    progress_calls = []
    count = core.trash_ids(
        svc, ["1", "2", "3"], progress=lambda d, t: progress_calls.append((d, t))
    )
    assert count == 3
    assert msgs.batched == [["1", "2"], ["3"]]
    assert progress_calls == [(2, 3), (3, 3)]
