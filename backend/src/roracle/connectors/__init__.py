"""Connector sources: each yields `Item`s for the run loop (connectors/run.py)."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import BinaryIO

from roracle.config import Settings
from roracle.models import Connector


@dataclass
class Item:
    key: str
    etag: str
    filename: str
    content_type: str
    size: int | None
    open: Callable[[], BinaryIO]  # valid only until the iterator advances; caller closes the stream


def source_for(connector: Connector, settings: Settings) -> Iterator[Item]:
    """Validates eagerly (so a bad config fails the run at once), then returns the lazy item iterator."""
    if connector.kind == "s3":
        from roracle.connectors import s3

        return s3.items(connector.config)
    if connector.kind == "fs":
        from roracle.connectors import fs

        return fs.items(connector.config, settings.connector_fs_roots)
    raise ValueError(f"unsupported connector kind: {connector.kind}")
