"""One-shot patcher: normalise the requested path and fix the "not found" status.

Two problems were found by probing the API:

1. ``path=../../etc`` was accepted and silently resolved to ``/etc``: the
   caller asked for a directory under ``/sdcard`` and got something else,
   while the breadcrumb said something else again. The path is now
   normalised once, at the edge, and the breadcrumb is built from the very
   same value.

2. A path that does not exist answered ``502`` (the transport error was
   passed through). It is a client error and must be ``404``.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "app" / "api" / "routes" / "files.py"

OLD_IMPORTS = '''from app.transports.base import (
    DeviceOfflineError,
    DeviceState,
    DeviceUnauthorizedError,
    PermissionDeniedError,
    TransportError,
)'''

NEW_IMPORTS = '''from app.transports.base import (
    DeviceOfflineError,
    DeviceState,
    DeviceUnauthorizedError,
    PermissionDeniedError,
    TransportError,
    normalize_device_path,
)'''

OLD_GUARD = '''async def _guard(uid: str, db: AsyncSession, resolver: TransportResolver, coro_factory):
    """Run a transport call and translate its errors into HTTP answers."""
    record, transport = await _resolve_device(uid, db, resolver)
    try:
        return await coro_factory(transport, record)
    except PermissionDeniedError as exc:'''

NEW_GUARD = '''async def _guard(uid: str, db: AsyncSession, resolver: TransportResolver, coro_factory):
    """Run a transport call and translate its errors into HTTP answers."""
    record, transport = await _resolve_device(uid, db, resolver)
    try:
        return await coro_factory(transport, record)
    except PermissionDeniedError as exc:'''

OLD_TRANSPORT_ERR = '''    except TransportError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("", response_model=BaseResponse[FileListResponse], summary="查询设备文件列表")'''

NEW_TRANSPORT_ERR = '''    except TransportError as exc:
        # "No such file or directory" is a client error, not an upstream
        # failure: the requested path simply does not exist.
        message = str(exc)
        lowered = message.lower()
        if "no such file" in lowered or "not found" in lowered:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"路径不存在或不是目录：{message}",
            ) from exc
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message) from exc


@router.get("", response_model=BaseResponse[FileListResponse], summary="查询设备文件列表")'''

OLD_LIST = '''    listing = await _guard(
        uid,
        db,
        resolver,
        lambda transport, _record: FileService(transport).list_dir(_record.serial, path),
    )
    return BaseResponse.success(_listing_to_response(listing))'''

NEW_LIST = '''    # Normalise once at the edge: "..", trailing slashes and duplicates must
    # not let the request and the breadcrumb disagree.
    normalized = normalize_device_path(path)
    listing = await _guard(
        uid,
        db,
        resolver,
        lambda transport, _record: FileService(transport).list_dir(
            _record.serial, normalized
        ),
    )
    return BaseResponse.success(_listing_to_response(listing))'''

OLD_SEARCH = '''    listing = await _guard(
        uid,
        db,
        resolver,
        lambda transport, _record: FileService(transport).search(
            _record.serial, keyword, path
        ),
    )'''

NEW_SEARCH = '''    normalized = normalize_device_path(path)
    listing = await _guard(
        uid,
        db,
        resolver,
        lambda transport, _record: FileService(transport).search(
            _record.serial, keyword, normalized
        ),
    )'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    applied = 0
    for old, new in (
        (OLD_IMPORTS, NEW_IMPORTS),
        (OLD_TRANSPORT_ERR, NEW_TRANSPORT_ERR),
        (OLD_LIST, NEW_LIST),
        (OLD_SEARCH, NEW_SEARCH),
    ):
        if old in source:
            source = source.replace(old, new, 1)
            applied += 1
        elif new in source:
            print(f"already applied: {old[:40]!r}")
        else:
            print(f"ANCHOR MISSING: {old[:60]!r}")
            return 1
    TARGET.write_text(source, encoding="utf-8")
    print(f"PATCHED ({applied} replacements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
