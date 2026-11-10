"""One-shot patcher: a path level permission error must not poison the device state.

The file routes translated EVERY ``PermissionDeniedError`` into a device
lifecycle transition (``permission_denied``). A protected directory such as
``/system`` therefore locked the whole device: afterwards even ``/sdcard``,
pull and push were rejected.

That is wrong: "this path needs root" is a local fact about the path, not a
lifecycle fact about the device. Only device wide operations (metrics, adb
state) may change the lifecycle state.

The patch removes the lifecycle mutation from the file routes and keeps the
403 answer, so the caller still learns that the path is protected.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
TARGET = ROOT / "app" / "api" / "routes" / "files.py"

OLD_GUARD = '''    except PermissionDeniedError as exc:
        await DeviceRegistry(db).mark_status(
            record, DeviceState.PERMISSION_DENIED.value, str(exc)
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
'''

NEW_GUARD = '''    except PermissionDeniedError as exc:
        # A protected path is a local fact: report it, but do not change the
        # lifecycle state of the device.
        logger.warning(f"permission denied on {uid}: {exc}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
'''

OLD_UPLOAD = '''    except PermissionDeniedError as exc:
        await DeviceRegistry(db).mark_status(
            record, DeviceState.PERMISSION_DENIED.value, str(exc)
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
'''

NEW_UPLOAD = '''    except PermissionDeniedError as exc:
        logger.warning(f"upload permission denied on {uid}: {exc}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "does not change the" in source:
        print("ALREADY_PATCHED")
        return 0
    if OLD_GUARD not in source:
        print("ANCHOR_GUARD_MISSING")
        return 1
    source = source.replace(OLD_GUARD, NEW_GUARD, 1)
    if OLD_UPLOAD in source:
        source = source.replace(OLD_UPLOAD, NEW_UPLOAD, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
