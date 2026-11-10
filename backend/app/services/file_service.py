"""File browsing: directory listing, breadcrumbs and keyword search."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.logger import logger
from app.transports.base import (
    FileEntry,
    PermissionDeniedError,
    Transport,
    TransportError,
    normalize_device_path,
    parent_device_path,
    path_parts,
)

#: Pseudo file systems: huge, volatile and useless for a name search
#: (``/proc`` alone has one directory per process).
SKIPPED_ROOTS = frozenset(
    {
        "/proc",
        "/sys",
        "/dev",
        "/system/app/.overlay",
        "/system/vendor",
        "/apex",
        "/oem",
        "/odm",
        "/odm_dlkm",
        "/product",
        "/vendor",
        "/system_ext",
        "/second_stage_resources",
        "/debug_ramdisk",
    }
)

#: How many directories are listed in parallel.
SEARCH_CONCURRENCY = 8


@dataclass(slots=True)
class DirectoryListing:
    """Result of a directory listing."""

    current_path: str
    parent_path: str | None
    parts: list[tuple[str, str]]
    items: list[FileEntry]
    truncated: bool = False
    """True when a search hit its result limit."""


class FileService:
    """Directory browsing on top of a :class:`Transport`."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def list_dir(self, serial: str, path: str = "/") -> DirectoryListing:
        """List one directory of a device.

        The breadcrumb is built from the requested path, not from the parent
        lookup: a directory can be readable while its parents are not
        (``/data/local/tmp`` on an unrooted device), and the user still needs
        the full path to navigate.
        """
        normalized = normalize_device_path(path)
        entries = await self.transport.list_dir(serial, normalized)
        entries.sort(key=lambda item: (not item.is_dir, item.name.lower()))
        return DirectoryListing(
            current_path=normalized,
            parent_path=parent_device_path(normalized) or None,
            parts=path_parts(normalized),
            items=entries,
        )

    async def search(
        self,
        serial: str,
        keyword: str,
        path: str = "/",
        max_depth: int = 4,
        max_items: int = 200,
        max_dirs: int = 400,
    ) -> DirectoryListing:
        """Search file names below a path.

        The walk runs the directory listings in parallel and skips the pseudo
        file systems: a serial walk over ``/`` issued hundreds of ``adb shell
        ls`` commands and took seconds, while the pseudo file systems alone
        contributed thousands of empty listings.
        """
        needle = (keyword or "").strip().lower()
        if not needle:
            raise ValueError("搜索关键字不能为空")

        root = normalize_device_path(path)
        matches: list[FileEntry] = []
        visited: set[str] = set()
        listed_dirs = 0
        truncated = False

        async def list_one(current: str) -> list[FileEntry]:
            try:
                return await self.transport.list_dir(serial, current)
            except PermissionDeniedError:
                # A protected sub directory must not abort the whole search.
                logger.debug(f"search skipped protected dir {current}")
                return []
            except TransportError as exc:
                logger.debug(f"search stopped at {current}: {exc}")
                return []

        frontier: list[tuple[str, int]] = [(root, 0)]
        while frontier:
            batch = frontier[:SEARCH_CONCURRENCY]
            frontier = frontier[SEARCH_CONCURRENCY:]
            if listed_dirs + len(batch) > max_dirs:
                batch = batch[: max(0, max_dirs - listed_dirs)]
                truncated = True
            if not batch:
                break

            listings = await asyncio.gather(*(list_one(d) for d, _ in batch))
            listed_dirs += len(batch)

            for (current, depth), entries in zip(batch, listings):
                visited.add(current)
                for entry in entries:
                    if needle in entry.name.lower():
                        matches.append(entry)
                        if len(matches) >= max_items:
                            truncated = True
                            break
                    if entry.is_dir and not entry.is_link:
                        if depth + 1 > max_depth:
                            continue
                        child = entry.path
                        if child in visited:
                            continue
                        if any(child == skip or child.startswith(skip + "/") for skip in SKIPPED_ROOTS):
                            continue
                        frontier.append((child, depth + 1))
                if len(matches) >= max_items:
                    break

            if len(matches) >= max_items or truncated:
                break

        matches.sort(key=lambda item: item.path.lower())
        return DirectoryListing(
            current_path=root,
            parent_path=parent_device_path(root) or None,
            parts=path_parts(root),
            items=matches[:max_items],
            truncated=truncated,
        )

    async def stat(self, serial: str, path: str) -> FileEntry | None:
        """Return metadata of a single file."""
        normalized = normalize_device_path(path)
        parent = parent_device_path(normalized)
        if not parent:
            parent = "/"
        try:
            entries = await self.transport.list_dir(serial, parent)
        except TransportError:
            return None
        name = normalized.rsplit("/", 1)[-1]
        for entry in entries:
            if entry.name == name:
                return entry
        return None
