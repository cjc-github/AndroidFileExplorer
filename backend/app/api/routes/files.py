"""API routes for files: browse, search, pull and push on one device."""

from __future__ import annotations

import base64
import mimetypes
import tempfile
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, File as FormFile, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.config import settings
from app.core.response import BaseResponse
from app.db.session import get_session
from app.logger import logger
from app.schemas.device import (
    ArchiveSelectionRequest,
    CopyRequest,
    FileInfo,
    FileListResponse,
    MkdirRequest,
    PathPart,
    PermissionsRequest,
    PullRequest,
    PushRequest,
    RemoveRequest,
    RenameRequest,
    TransferInfo,
)
from app.services.file_service import DirectoryListing, FileService
from app.services.registry import DeviceRegistry
from app.services.transfer import TransferService
from app.services.transport_resolver import TransportResolver
from app.state import get_transport_resolver
from app.transports.base import (
    DeviceOfflineError,
    DeviceState,
    DeviceUnauthorizedError,
    PermissionDeniedError,
    TransportError,
    normalize_device_path,
)

router = APIRouter(prefix="/api/v1/devices/{uid}/files", tags=["files"])

#: Text / image previews are pulled completely, so cap them tightly.
PREVIEW_MAX_BYTES = 2 * 1024 * 1024
#: Directory archives are assembled on the server; keep the work bounded.
ARCHIVE_MAX_FILES = 200
ARCHIVE_MAX_BYTES = 200 * 1024 * 1024
ARCHIVE_MAX_DEPTH = 6


def _listing_to_response(listing: DirectoryListing) -> FileListResponse:
    return FileListResponse(
        current_path=listing.current_path,
        parent_path=listing.parent_path,
        parts=[PathPart(name=name, path=path) for name, path in listing.parts],
        items=[
            FileInfo(
                name=item.name,
                path=item.path,
                is_dir=item.is_dir,
                size=item.size,
                modified_at=item.modified_at,
                permissions=item.permissions,
                owner=item.owner,
                group=item.group,
                is_link=item.is_link,
                link_target=item.link_target,
            )
            for item in listing.items
        ],
        truncated=listing.truncated,
    )


def _permission_error(path: str, detail: str) -> HTTPException:
    """A 403 that explains what happened and what to do about it."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"权限不足：无法访问 {path}（{detail}）。"
            "该路径需要 root 权限，请使用已 root 的设备或以 su 身份访问。"
        ),
    )


async def _resolve_device(
    uid: str, db: AsyncSession, resolver: TransportResolver
) -> tuple[object, object]:
    """Return (record, transport) and reject devices that are not usable."""
    registry = DeviceRegistry(db)
    record = await registry.get_device(uid)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="设备不存在")
    if record.status == DeviceState.UNAUTHORIZED.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="设备未授权，请先在设备上确认调试授权"
        )
    if record.status == DeviceState.PERMISSION_DENIED.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=record.last_error or "访问该路径需要 root 权限，设备拒绝访问",
        )
    if record.status != DeviceState.ONLINE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"设备当前状态为 {record.status}"
        )
    transport = resolver.for_engine(record.engine_id)
    return record, transport


async def _guard(uid: str, db: AsyncSession, resolver: TransportResolver, coro_factory):
    """Run a transport call and translate its errors into HTTP answers."""
    record, transport = await _resolve_device(uid, db, resolver)
    try:
        return await coro_factory(transport, record)
    except PermissionDeniedError as exc:
        # A protected path is a local fact: report it, but do not change the
        # lifecycle state of the device.
        logger.warning(f"permission denied on {uid}: {exc}")
        raise _permission_error(getattr(record, "serial", uid), str(exc)) from exc
    except DeviceUnauthorizedError as exc:
        await DeviceRegistry(db).mark_status(record, DeviceState.UNAUTHORIZED.value, str(exc))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except DeviceOfflineError as exc:
        await DeviceRegistry(db).mark_status(record, DeviceState.OFFLINE.value, str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        # An unsafe path (shell metacharacters) is rejected by the transport.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TransportError as exc:
        # "No such file or directory" is a client error, not an upstream
        # failure: the requested path simply does not exist.
        message = str(exc)
        lowered = message.lower()
        if "no such file" in lowered:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"路径不存在：{message}",
            ) from exc
        if "not a directory" in lowered:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"该路径不是目录：{message}",
            ) from exc
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message) from exc


@router.get("", response_model=BaseResponse[FileListResponse], summary="查询设备文件列表")
async def list_files(
    uid: str,
    path: str = Query("/", description="要浏览的设备路径"),
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[FileListResponse]:
    """List one directory of the device file system.

    ``path`` may point at a directory whose parents are not readable
    (for example ``/data/local/tmp`` on an unrooted device): the listing
    still works, and the breadcrumb is built from the requested path.
    """
    listing = await _guard(
        uid,
        db,
        resolver,
        lambda transport, _record: FileService(transport).list_dir(_record.serial, path),
    )
    return BaseResponse.success(_listing_to_response(listing))


@router.get(
    "/search", response_model=BaseResponse[FileListResponse], summary="搜索设备文件"
)
async def search_files(
    uid: str,
    keyword: str = Query(..., min_length=1, description="搜索关键字"),
    path: str = Query("/", description="搜索起始路径"),
    limit: int = Query(200, ge=1, le=1000, description="最多返回的结果数"),
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[FileListResponse]:
    """Search files by name below a path."""
    listing = await _guard(
        uid,
        db,
        resolver,
        lambda transport, _record: FileService(transport).search(
            _record.serial, keyword, path, max_items=limit
        ),
    )
    return BaseResponse.success(_listing_to_response(listing))


@router.get("/preview", response_model=BaseResponse[dict], summary="预览设备文件")
async def preview_file(
    uid: str,
    path: str = Query(min_length=1, description="设备上的文件路径"),
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[dict]:
    """Return a small text / image preview of one device file.

    A preview must not pull a multi-gigabyte file just to show its first
    lines, so the entry's size is checked first and the endpoint refuses
    anything above ``PREVIEW_MAX_BYTES``.
    """
    record, transport = await _resolve_device(uid, db, resolver)
    normalized = normalize_device_path(path)
    if normalized == "/":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能预览根目录")

    entry = await FileService(transport).stat(record.serial, normalized)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"文件不存在或不可读：{normalized}"
        )
    if entry.is_dir:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="目录不能直接预览"
        )
    if entry.is_link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="符号链接暂不支持预览，请直接下载",
        )
    if entry.size is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="无法获取文件大小，暂不支持预览"
        )
    if entry.size > PREVIEW_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件过大（{entry.size} 字节），预览上限为 {PREVIEW_MAX_BYTES} 字节",
        )

    settings.download_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="afe-preview-", dir=settings.download_dir, delete=False
    ) as handle:
        temp_path = Path(handle.name)
    try:
        await transport.pull(record.serial, normalized, str(temp_path))
        payload = temp_path.read_bytes()
    except PermissionDeniedError as exc:
        raise _permission_error(normalized, str(exc)) from exc
    except TransportError as exc:
        message = str(exc)
        if "no such file" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"路径不存在：{message}"
            ) from exc
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message) from exc
    finally:
        temp_path.unlink(missing_ok=True)

    if len(payload) > PREVIEW_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件超过预览上限 {PREVIEW_MAX_BYTES} 字节",
        )

    if b"\x00" not in payload:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return BaseResponse.success(
                {
                    "kind": "text",
                    "content": text,
                    "size": len(payload),
                }
            )

    content_type, _ = mimetypes.guess_type(normalized)
    if content_type and content_type.startswith("image/"):
        return BaseResponse.success(
            {
                "kind": "image",
                "content_type": content_type,
                "content": base64.b64encode(payload).decode(),
                "size": len(payload),
            }
        )

    return BaseResponse.success({"kind": "binary", "size": len(payload)})


def _append_archive_file(
    entry,
    relative: str,
    files: list[tuple[str, str]],
    totals: dict[str, int],
) -> None:
    # Overlapping selections (a directory plus a file inside it) must not
    # produce duplicate zip entries.
    if any(existing == relative for _, existing in files):
        return
    if len(files) >= ARCHIVE_MAX_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件数超过上限 {ARCHIVE_MAX_FILES}，请分批下载",
        )
    totals["bytes"] += entry.size or 0
    if totals["bytes"] > ARCHIVE_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"总大小超过上限 {ARCHIVE_MAX_BYTES} 字节，请分批下载",
        )
    files.append((entry.path, relative))


async def _collect_archive_entries(
    transport,
    serial: str,
    current: str,
    relative: str,
    depth: int,
    files: list[tuple[str, str]],
    notes: list[str],
    totals: dict[str, int],
) -> None:
    try:
        entries = await transport.list_dir(serial, current)
    except PermissionDeniedError as exc:
        notes.append(f"{current}: 权限不足（{exc}）")
        return
    except TransportError as exc:
        message = str(exc)
        lowered = message.lower()
        if "not a directory" in lowered:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"该路径不是目录：{message}",
            ) from exc
        if "no such file" in lowered:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"路径不存在：{message}",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=message
        ) from exc

    for entry in entries:
        child_relative = f"{relative}/{entry.name}".lstrip("/")
        if entry.is_dir and not entry.is_link:
            if depth + 1 > ARCHIVE_MAX_DEPTH:
                notes.append(f"{entry.path}: 目录层级超过 {ARCHIVE_MAX_DEPTH}")
                continue
            await _collect_archive_entries(
                transport, serial, entry.path, child_relative, depth + 1,
                files, notes, totals,
            )
            continue
        if entry.is_dir or entry.is_link:
            notes.append(f"{entry.path}: 跳过符号链接")
            continue
        _append_archive_file(entry, child_relative, files, totals)


async def _write_archive(
    record,
    transport,
    files: list[tuple[str, str]],
    notes: list[str],
    filename: str,
) -> FileResponse:
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    archive_path = (
        settings.download_dir / f"afe-archive-{uuid.uuid4().hex[:12]}.zip"
    )
    failures: list[str] = []

    try:
        with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
            for device_path, relative in files:
                with tempfile.NamedTemporaryFile(
                    prefix="afe-archive-item-",
                    dir=settings.download_dir,
                    delete=False,
                ) as handle:
                    local_path = Path(handle.name)
                try:
                    await transport.pull(record.serial, device_path, str(local_path))
                    archive.write(local_path, arcname=relative)
                except (PermissionDeniedError, TransportError) as exc:
                    failures.append(f"{device_path}: {exc}")
                finally:
                    local_path.unlink(missing_ok=True)

            all_notes = notes + failures
            if all_notes:
                archive.writestr(
                    "_download_errors.txt",
                    "\n".join(all_notes) + "\n",
                )
    except BaseException:
        archive_path.unlink(missing_ok=True)
        raise

    logger.info(
        f"archive {filename} -> {archive_path} files={len(files)} failures={len(failures)}"
    )
    return FileResponse(
        archive_path,
        filename=filename,
        media_type="application/zip",
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


@router.get("/archive", summary="把设备目录打包为 zip 下载")
async def download_archive(
    uid: str,
    path: str = Query("/", description="要打包的设备目录"),
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> FileResponse:
    """Zip a device directory on the server and hand it to the browser."""
    record, transport = await _resolve_device(uid, db, resolver)
    root = normalize_device_path(path)

    files: list[tuple[str, str]] = []
    notes: list[str] = []
    totals = {"bytes": 0}
    await _collect_archive_entries(
        transport, record.serial, root, "", 0, files, notes, totals
    )

    base_name = root.rstrip("/").rsplit("/", 1)[-1] or "root"
    return await _write_archive(record, transport, files, notes, f"{base_name}.zip")


@router.post("/archive-selection", summary="把选中的多个文件 / 目录打包为 zip")
async def download_selection_archive(
    uid: str,
    payload: ArchiveSelectionRequest,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> FileResponse:
    """Zip an explicit selection of device paths (files and directories)."""
    record, transport = await _resolve_device(uid, db, resolver)
    files: list[tuple[str, str]] = []
    notes: list[str] = []
    totals = {"bytes": 0}
    seen: set[str] = set()
    service = FileService(transport)

    for raw in payload.paths:
        normalized = normalize_device_path(raw)
        if normalized in seen:
            continue
        seen.add(normalized)
        if normalized == "/":
            notes.append("跳过根目录")
            continue

        entry = await service.stat(record.serial, normalized)
        if entry is None:
            notes.append(f"{normalized}: 文件不存在或不可读")
            continue
        if entry.is_link:
            notes.append(f"{normalized}: 跳过符号链接")
            continue
        relative = normalized.lstrip("/")
        if entry.is_dir:
            await _collect_archive_entries(
                transport, record.serial, normalized, relative, 0,
                files, notes, totals,
            )
        else:
            _append_archive_file(entry, relative, files, totals)

    return await _write_archive(record, transport, files, notes, "selection.zip")


@router.get("/download", summary="下载设备文件到本机")
async def download_file(
    uid: str,
    path: str = Query(min_length=1, description="设备上的文件路径"),
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> FileResponse:
    """Pull a device file onto the server and stream it to the browser.

    The transfer chain is device -> agent -> server staging directory; the
    file then goes out as an attachment so the user actually receives it
    instead of only reading a server side path in a success message.
    """
    record, transport = await _resolve_device(uid, db, resolver)
    service = TransferService(transport)
    normalized = normalize_device_path(path)
    name = normalized.rsplit("/", 1)[-1] or "download.bin"

    try:
        result = await service.pull(record.serial, normalized)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionDeniedError as exc:
        logger.warning(f"download permission denied on {uid}: {exc}")
        raise _permission_error(normalized, str(exc)) from exc
    except TransportError as exc:
        message = str(exc)
        if "no such file" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"路径不存在：{message}"
            ) from exc
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message) from exc

    # The staging file carries a unique name; pull reports it in stored_at.
    staged = Path(result.stored_at) if result.stored_at else None
    if staged is None or not staged.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"文件未落在服务器下载目录：{result.stored_at or name}",
        )
    logger.info(f"download {normalized} -> browser as {name}")
    return FileResponse(staged, filename=name, media_type="application/octet-stream")


@router.post("/pull", response_model=BaseResponse[TransferInfo], summary="拉取设备文件")
async def pull_file(
    uid: str,
    payload: PullRequest,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[TransferInfo]:
    """Download a device file through the machine that hosts it."""
    result = await _guard(
        uid,
        db,
        resolver,
        lambda transport, record: TransferService(transport).pull(
            record.serial, payload.path
        ),
    )
    return BaseResponse.success(
        TransferInfo(
            ok=result.ok,
            path=result.path,
            size=result.size,
            stored_at=result.stored_at,
            message=result.message,
        )
    )


@router.post("/push", response_model=BaseResponse[TransferInfo], summary="上传文件到设备")
async def push_file(
    uid: str,
    payload: PushRequest,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[TransferInfo]:
    """Upload a local file onto the device."""
    result = await _guard(
        uid,
        db,
        resolver,
        lambda transport, record: TransferService(transport).push(
            record.serial, payload.local_path, payload.path
        ),
    )
    return BaseResponse.success(
        TransferInfo(
            ok=result.ok,
            path=result.path,
            size=result.size,
            stored_at=result.stored_at,
            message=result.message,
        )
    )


@router.post("/mkdir", response_model=BaseResponse[dict], summary="新建目录")
async def mkdir(
    uid: str,
    payload: MkdirRequest,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[dict]:
    """Create a directory (with its parents) on the device."""
    record, transport = await _resolve_device(uid, db, resolver)
    try:
        await transport.mkdir(record.serial, payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionDeniedError as exc:
        logger.warning(f"mkdir permission denied on {uid}: {exc}")
        raise _permission_error(payload.path, str(exc)) from exc
    except TransportError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BaseResponse.success({"path": payload.path}, message="目录已创建")


@router.post("/rename", response_model=BaseResponse[dict], summary="重命名文件")
async def rename(
    uid: str,
    payload: RenameRequest,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[dict]:
    """Rename or move a file / directory on the device."""
    record, transport = await _resolve_device(uid, db, resolver)
    if normalize_device_path(payload.source) == normalize_device_path(payload.target):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="新名称与原名称相同"
        )
    try:
        await transport.rename(record.serial, payload.source, payload.target)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionDeniedError as exc:
        logger.warning(f"rename permission denied on {uid}: {exc}")
        raise _permission_error(payload.source, str(exc)) from exc
    except TransportError as exc:
        logger.warning(
            f"rename failed on {uid}: {payload.source} -> {payload.target}: {exc!r}"
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BaseResponse.success(
        {"source": payload.source, "target": payload.target}, message="重命名成功"
    )


@router.post("/copy", response_model=BaseResponse[dict], summary="复制文件")
async def copy(
    uid: str,
    payload: CopyRequest,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[dict]:
    """Copy a file / directory to another path on the device."""
    record, transport = await _resolve_device(uid, db, resolver)
    try:
        await transport.copy(record.serial, payload.source, payload.target)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionDeniedError as exc:
        logger.warning(f"copy permission denied on {uid}: {exc}")
        raise _permission_error(payload.source, str(exc)) from exc
    except TransportError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BaseResponse.success(
        {"source": payload.source, "target": payload.target}, message="复制成功"
    )


@router.post("/permissions", response_model=BaseResponse[dict], summary="修改文件权限")
async def change_permissions(
    uid: str,
    payload: PermissionsRequest,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[dict]:
    """Apply chmod / chown to one device path."""
    record, transport = await _resolve_device(uid, db, resolver)
    try:
        await transport.change_permissions(
            record.serial, payload.path, payload.mode, payload.owner, payload.group
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionDeniedError as exc:
        logger.warning(f"chmod permission denied on {uid}: {exc}")
        raise _permission_error(payload.path, str(exc)) from exc
    except TransportError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BaseResponse.success(
        {
            "path": payload.path,
            "mode": payload.mode,
            "owner": payload.owner,
            "group": payload.group,
        },
        message="权限已修改",
    )


@router.post("/remove", response_model=BaseResponse[dict], summary="删除文件")
async def remove(
    uid: str,
    payload: RemoveRequest,
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[dict]:
    """Delete a file / directory on the device."""
    record, transport = await _resolve_device(uid, db, resolver)
    try:
        await transport.remove(record.serial, payload.path, payload.recursive)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionDeniedError as exc:
        logger.warning(f"remove permission denied on {uid}: {exc}")
        raise _permission_error(payload.path, str(exc)) from exc
    except TransportError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BaseResponse.success({"path": payload.path}, message="删除成功")


@router.post(
    "/upload",
    response_model=BaseResponse[TransferInfo],
    summary="上传本地文件到设备",
)
async def upload_file(
    uid: str,
    device_path: str = Query(..., description="设备上的目标路径"),
    file: UploadFile = FormFile(..., description="要上传的文件"),
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[TransferInfo]:
    """Receive a multipart file and push it onto the device."""
    record, transport = await _resolve_device(uid, db, resolver)
    service = TransferService(transport)
    staged: Path | None = None
    try:
        # Stream the body to disk in chunks: buffering a 2 GiB upload in
        # memory would OOM the server, and the size limit aborts the copy.
        staged = await service.stage_upload_stream(
            file.filename or "upload.bin",
            file.file,
            max_bytes=settings.max_upload_bytes,
        )
        result = await service.push(record.serial, str(staged), device_path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionDeniedError as exc:
        logger.warning(f"upload permission denied on {uid}: {exc}")
        raise _permission_error(device_path, str(exc)) from exc
    except TransportError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    finally:
        # The staged copy was pushed (or the push failed): never leave it behind.
        if staged is not None:
            try:
                staged.unlink()
            except OSError:
                pass

    logger.info(f"upload {file.filename} -> {device_path} on {uid}")
    return BaseResponse.success(
        TransferInfo(
            ok=result.ok,
            path=result.path,
            size=result.size,
            stored_at=result.stored_at,
            message=result.message,
        )
    )
