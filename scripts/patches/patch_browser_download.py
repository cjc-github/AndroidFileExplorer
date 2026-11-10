"""One-shot patcher: deliver pulled files to the BROWSER.

The pull chain already works device -> agent -> API server staging directory,
but nothing ever handed the file to the user: the `拉取` button only showed a
server-side path. This adds `GET .../files/download` (pull + FileResponse)
and turns the row button into a real browser download.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ROUTE = ROOT / "backend" / "app" / "api" / "routes" / "files.py"
PAGE = ROOT / "frontend" / "src" / "pages" / "files" / "FileBrowserPage.tsx"

# ---------------------------------------------------------------- backend
route = ROUTE.read_text(encoding="utf-8")

OLD_IMPORT = '''from fastapi import APIRouter, Depends, File as FormFile, HTTPException, Query, UploadFile, status'''
NEW_IMPORT = '''from fastapi import APIRouter, Depends, File as FormFile, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse'''

OLD_ENDPOINT = '''@router.post("/pull", response_model=BaseResponse[TransferInfo], summary="拉取设备文件")'''
NEW_ENDPOINT = '''@router.get("/download", summary="下载设备文件到本机")
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


@router.post("/pull", response_model=BaseResponse[TransferInfo], summary="拉取设备文件")'''

if OLD_IMPORT in route:
    route = route.replace(OLD_IMPORT, NEW_IMPORT, 1)
    print("  route import OK")
else:
    print("  route import ANCHOR MISSING")
    raise SystemExit(1)

if OLD_ENDPOINT in route and "@router.get(\"/download\"" not in route:
    route = route.replace(OLD_ENDPOINT, NEW_ENDPOINT, 1)
    print("  download endpoint OK")
elif '@router.get("/download"' in route:
    print("  download endpoint already applied")
else:
    print("  download endpoint ANCHOR MISSING")
    raise SystemExit(1)

ROUTE.write_text(route, encoding="utf-8")

# ---------------------------------------------------------------- frontend
page = PAGE.read_text(encoding="utf-8")

OLD_PULL = '''  const pullMutation = useMutation({
    mutationFn: (filePath: string) => pullDeviceFile(uid, filePath),
    onSuccess: (result) => message.success(`拉取成功：${result.message || result.path}`),
    onError: (error: Error) => message.error(error.message || "拉取失败"),
  });'''

NEW_PULL = '''  const pullToBrowser = (filePath: string) => {
    // The server pulls the file into its staging directory and answers with
    // the bytes as an attachment; an anchor click hands it to the browser.
    const name = filePath.split("/").pop() || "download.bin";
    const url = `/api/v1/devices/${uid}/files/download?path=${encodeURIComponent(filePath)}`;
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    message.info(`正在下载 ${name}`);
  };'''

if OLD_PULL in page:
    page = page.replace(OLD_PULL, NEW_PULL, 1)
    print("  frontend mutation OK")
elif "pullToBrowser" in page:
    print("  frontend mutation already applied")
else:
    print("  frontend mutation ANCHOR MISSING")
    raise SystemExit(1)

OLD_BUTTON = '''          <Button
            size="small"
            icon={<CloudDownloadOutlined />}
            disabled={record.is_dir}
            loading={pullMutation.isPending}
            onClick={() => pullMutation.mutate(record.path)}
          >
            拉取
          </Button>'''
NEW_BUTTON = '''          <Button
            size="small"
            icon={<CloudDownloadOutlined />}
            disabled={record.is_dir}
            onClick={() => pullToBrowser(record.path)}
          >
            下载
          </Button>'''

if OLD_BUTTON in page:
    page = page.replace(OLD_BUTTON, NEW_BUTTON, 1)
    print("  frontend button OK")
else:
    print("  frontend button ANCHOR MISSING")
    raise SystemExit(1)

PAGE.write_text(page, encoding="utf-8")
print("DONE")
