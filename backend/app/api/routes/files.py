"""API routes for files: browse, search, pull and push on one device."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File as FormFile, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import BaseResponse
from app.db.session import get_session
from app.logger import logger
from app.schemas.device import (
    FileInfo,
    FileListResponse,
    MkdirRequest,
    PathPart,
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
    db: AsyncSession = Depends(get_session),
    resolver: TransportResolver = Depends(get_transport_resolver),
) -> BaseResponse[FileListResponse]:
    """Search files by name below a path."""
    listing = await _guard(
        uid,
        db,
        resolver,
        lambda transport, _record: FileService(transport).search(
            _record.serial, keyword, path
        ),
    )
    return BaseResponse.success(_listing_to_response(listing))


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
        await service.pull(record.serial, normalized)
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

    staged = service.download_dir / name
    if not staged.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"文件未落在服务器下载目录：{staged}",
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
    content = await file.read()
    service = TransferService(transport)
    try:
        staged = service.stage_upload(file.filename or "upload.bin", content)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"保存上传文件失败: {exc}"
        ) from exc

    try:
        result = await service.push(record.serial, str(staged), device_path)
    except PermissionDeniedError as exc:
        logger.warning(f"upload permission denied on {uid}: {exc}")
        raise _permission_error(device_path, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TransportError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

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
