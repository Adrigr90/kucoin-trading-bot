"""The calls the service makes to the SDK of Oracle, with a client that records them
(02 §13.1, point 4, E4c-2, points 2, 3 and 5): one ``PutObject`` with ``If-None-Match: *``
and no multipart call (E4C2-01), no automatic retry of the SDK (E4C2-03), and each answer
of Object Storage translated for the service."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kbot.upload.oci_store import OciStore
from kbot.upload.store import AlreadyExists, NotFound, StoreError, Transient

NONE = object()  # the NoneRetryStrategy of the SDK
WHEN = datetime(2026, 12, 1, tzinfo=UTC)


class ServiceError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"status {status}")
        self.status = status


class Raw:
    def __init__(self, chunks: list[bytes], fail: Exception | None = None) -> None:
        self.chunks = chunks
        self.fail = fail

    def stream(self, size: int, decode_content: bool) -> Any:
        assert decode_content is False
        yield from self.chunks
        if self.fail is not None:
            raise self.fail


class Client:
    """Only the methods the service may call: a multipart call would not even exist."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.answers: dict[str, Any] = {}

    def _answer(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((method, args, kwargs))
        answer = self.answers.get(method)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get_namespace(self, **kwargs: Any) -> Any:
        return self._answer("get_namespace", **kwargs)

    def get_bucket(self, *args: Any, **kwargs: Any) -> Any:
        return self._answer("get_bucket", *args, **kwargs)

    def list_object_versions(self, *args: Any, **kwargs: Any) -> Any:
        return self._answer("list_object_versions", *args, **kwargs)

    def put_object(self, namespace: str, bucket: str, name: str, body: Any, **kwargs: Any) -> Any:
        kwargs["read"] = body.read()
        return self._answer("put_object", namespace, bucket, name, **kwargs)

    def get_object(self, *args: Any, **kwargs: Any) -> Any:
        return self._answer("get_object", *args, **kwargs)

    def delete_object(self, *args: Any, **kwargs: Any) -> Any:
        return self._answer("delete_object", *args, **kwargs)


def test_a_new_object_goes_in_one_put_object_with_if_none_match(tmp_path: Path) -> None:
    client = Client()
    store = OciStore(client, NONE)
    dump = tmp_path / "volcado.age"
    dump.write_bytes(b"x" * 1000)
    store.put_new("ns", "dia/dia-2026-11-27/volcado.age", dump)
    [(method, args, kwargs)] = client.calls
    assert method == "put_object"
    assert args == ("ns", "kbot-backups", "dia/dia-2026-11-27/volcado.age")
    assert kwargs == {
        "content_length": 1000,
        "if_none_match": "*",
        "retry_strategy": NONE,
        "read": b"x" * 1000,
    }
    assert not any(hasattr(client, m) for m in ("create_multipart_upload", "upload_part"))


@pytest.mark.parametrize(
    ("status", "kind"),
    [(412, AlreadyExists), (404, NotFound), (429, Transient), (503, Transient), (403, StoreError)],
)
def test_the_answers_of_object_storage_reach_the_service_by_kind(
    tmp_path: Path, status: int, kind: type[Exception]
) -> None:
    client = Client()
    client.answers["put_object"] = ServiceError(status)
    dump = tmp_path / "volcado.age"
    dump.write_bytes(b"x")
    with pytest.raises(kind) as raised:
        OciStore(client, NONE).put_new("ns", "a", dump)
    if kind is StoreError:
        assert type(raised.value) is StoreError  # not retried: a refusal is not transient


def test_a_connection_error_is_transient() -> None:
    client = Client()
    client.answers["get_namespace"] = ConnectionError("reset by peer")
    with pytest.raises(Transient, match="ConnectionError"):
        OciStore(client, NONE).namespace()


def test_the_listing_asks_for_every_version_page_by_page() -> None:
    client = Client()
    item = SimpleNamespace(
        name="dia/x/volcado.age",
        version_id="v1",
        size=5,
        time_modified=WHEN,
        time_created=WHEN,
        is_delete_marker=False,
    )
    client.answers["list_object_versions"] = SimpleNamespace(
        data=SimpleNamespace(items=[item]), next_page="p2"
    )
    store = OciStore(client, NONE)
    versions, page = store.list_versions("ns", None)
    assert page == "p2" and versions[0].name == "dia/x/volcado.age" and versions[0].size == 5
    store.list_versions("ns", "p2")
    first, second = (call[2] for call in client.calls)
    assert "page" not in first and second["page"] == "p2"
    assert first["limit"] == 1000 and first["retry_strategy"] is NONE


def test_versioning_and_reading_and_deleting() -> None:
    client = Client()
    client.answers["get_bucket"] = SimpleNamespace(data=SimpleNamespace(versioning="Enabled"))
    store = OciStore(client, NONE)
    assert store.versioning_enabled("ns")
    client.answers["get_bucket"] = SimpleNamespace(data=SimpleNamespace(versioning="Suspended"))
    assert not store.versioning_enabled("ns")
    client.answers["get_object"] = SimpleNamespace(
        data=SimpleNamespace(raw=Raw([b"ab", b"c"])), headers={"version-id": "v9"}
    )
    version, chunks = store.read("ns", "a")
    assert version == "v9" and b"".join(chunks) == b"abc"  # the version that was read
    client.answers["get_object"] = SimpleNamespace(
        data=SimpleNamespace(raw=Raw([b"ab"], fail=ConnectionError("lost"))), headers={}
    )
    version, chunks = store.read("ns", "a")
    assert version is None
    with pytest.raises(Transient):
        b"".join(chunks)
    store.delete("ns", "a")
    assert all(call[2]["retry_strategy"] is NONE for call in client.calls)
