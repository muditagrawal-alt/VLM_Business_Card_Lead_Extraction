"""Binary storage for card images.

Local disk is the default and is all a single-box deployment needs. The S3
implementation exists so moving to shared storage is a configuration change
rather than a rewrite, which matters if the app is ever run on more than one
instance.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class Storage(Protocol):
    async def write(self, key: str, data: bytes) -> None: ...

    async def read(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...


class LocalDiskStorage:
    """Files under a base directory, keyed by content hash.

    Writes go through a temporary file and an atomic rename, so a crash or a
    concurrent write for the same content can never leave a half-written image
    that later reads would treat as valid.
    """

    def __init__(self, base_path: Path) -> None:
        self._base = base_path
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Reject traversal outright rather than sanitising: keys are generated
        # from content hashes, so anything else is a bug or an attack.
        candidate = (self._base / key).resolve()
        if not candidate.is_relative_to(self._base.resolve()):
            raise ValueError(f"storage key escapes base directory: {key!r}")
        return candidate

    async def write(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._write_sync, key, data)

    def _write_sync(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    async def read(self, key: str) -> bytes:
        return await asyncio.to_thread(lambda: self._path(key).read_bytes())

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(lambda: self._path(key).unlink(missing_ok=True))

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(lambda: self._path(key).is_file())
