"""Object Storage through the SDK of Oracle, as the instance itself (02 §13.1, point 4,
E4c-2, points 2 and 3).

The identity is the instance principal: ``InstancePrincipalsSecurityTokenSigner`` takes
it from the instance metadata, which only kbot_upload may reach among the services
(E4-D04), and no credential is ever on the disk. Each method is one request:

* every object goes in one ``PutObject`` with ``If-None-Match: *``, never in parts: the
  policy has no ``OBJECT_OVERWRITE``, which the multipart calls need even for a new name
  (E4C2-01), and one ``PutObject`` takes up to 50 GiB;
* the automatic retries of the SDK are off, in the client and in every call: each retry
  goes through the budget (``store.CountedStore``; E4C2-03).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

from kbot.upload.store import (
    BUCKET,
    REGION,
    AlreadyExists,
    NotFound,
    StoreError,
    Transient,
    Version,
)

CHUNK = 1 << 20
T = TypeVar("T")


def _translate(error: Exception) -> StoreError:
    status = getattr(error, "status", None)
    if status == 412:
        return AlreadyExists(str(error))
    if status == 404:
        return NotFound(str(error))
    if status is None or status == 429 or (isinstance(status, int) and status >= 500):
        return Transient(f"{type(error).__name__}: {error}")
    return StoreError(f"{status}: {error}")


class OciStore:
    def __init__(self, client: Any, none_retry: Any, bucket: str = BUCKET) -> None:
        self._client = client
        self._none = none_retry
        self._bucket = bucket

    @classmethod
    def from_instance(cls, timeout: tuple[float, float] = (10.0, 600.0)) -> OciStore:
        import oci  # only kbot.upload needs the SDK (E4c-2, point 8)

        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        none = oci.retry.NoneRetryStrategy()
        client = oci.object_storage.ObjectStorageClient(
            {"region": REGION}, signer=signer, retry_strategy=none, timeout=timeout
        )
        return cls(client, none)

    def _do(self, call: Callable[[], T]) -> T:
        try:
            return call()
        except StoreError:
            raise
        except Exception as error:  # the SDK raises its own types and those of requests
            raise _translate(error) from error

    def namespace(self) -> str:
        return str(self._do(lambda: self._client.get_namespace(retry_strategy=self._none).data))

    def versioning_enabled(self, namespace: str) -> bool:
        bucket = self._do(
            lambda: self._client.get_bucket(namespace, self._bucket, retry_strategy=self._none)
        ).data
        return getattr(bucket, "versioning", None) == "Enabled"

    def list_versions(self, namespace: str, page: str | None) -> tuple[list[Version], str | None]:
        kwargs: dict[str, Any] = {
            "fields": "name,size,timeCreated,timeModified",
            "limit": 1000,
            "retry_strategy": self._none,
        }
        if page is not None:
            kwargs["page"] = page
        response = self._do(
            lambda: self._client.list_object_versions(namespace, self._bucket, **kwargs)
        )
        versions = [
            Version(
                name=item.name,
                version_id=item.version_id,
                size=int(item.size or 0),
                modified=item.time_modified or item.time_created,
                delete_marker=bool(item.is_delete_marker),
            )
            for item in response.data.items or []
        ]
        return versions, response.next_page

    def put_new(self, namespace: str, name: str, path: Path) -> None:
        size = path.stat().st_size
        with path.open("rb") as body:
            self._do(
                lambda: self._client.put_object(
                    namespace,
                    self._bucket,
                    name,
                    body,
                    content_length=size,
                    if_none_match="*",
                    retry_strategy=self._none,
                )
            )

    def read(self, namespace: str, name: str) -> tuple[str | None, Iterator[bytes]]:
        """The version that answered (its ``version-id`` header) and its bytes."""
        response = self._do(
            lambda: self._client.get_object(
                namespace, self._bucket, name, retry_strategy=self._none
            )
        )
        headers = getattr(response, "headers", None) or {}
        return headers.get("version-id"), self._chunks(response)

    def _chunks(self, response: Any) -> Iterator[bytes]:
        stream = response.data.raw.stream(CHUNK, decode_content=False)
        while True:
            try:
                chunk = next(stream)
            except StopIteration:
                return
            except Exception as error:  # a connection lost in the middle of the body
                raise _translate(error) from error
            yield chunk

    def delete(self, namespace: str, name: str) -> None:
        self._do(
            lambda: self._client.delete_object(
                namespace, self._bucket, name, retry_strategy=self._none
            )
        )
