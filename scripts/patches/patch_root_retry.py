"""One-shot patcher: make the root switch resilient to the adbd restart window.

`adb root` / `adb unroot` restart adbd: the device goes offline for a few
seconds.  Every command issued inside that window (a double click, a quick
root->unroot toggle, the retry of a batch) failed with
"timeout expired while waiting for device" / "device offline" and surfaced as
a 400 - even though the operation just needed to WAIT.

set_root now:
1. waits for the device to be online BEFORE issuing the switch,
2. retries ONCE after the device comes back when the failure looks transient,
3. only answers 400 when the retry also fails (with the real reason).
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BASE = ROOT / "backend" / "app" / "transports" / "base.py"
source = BASE.read_text(encoding="utf-8")

OLD = '''    async def set_root(self, serial: str, enabled: bool) -> tuple[bool, str]:
        """Enable or disable the root shell of a device.

        ``adb root`` / ``adb unroot`` restart adbd, so the transport has to
        reconnect afterwards. The output of the agent_server is unwrapped
        before it is evaluated.
        """
        try:
            raw = (
                await self.adb_root(serial)
                if enabled
                else await self.adb_unroot(serial)
            )
        except TransportError as exc:
            return False, str(exc)

        # The flag may have changed: drop the cached identity probe so the
        # next ``is_rooted`` call re-samples instead of reporting the old
        # value for the rest of the cache window.
        self._root_cache().pop(serial, None)

        output = unwrap_agent_output(raw or "")[1]
        lowered = (output or "").lower()

        # `adb root` restarts adbd, so the device is offline for a moment.
        # Give it time to come back, otherwise the next command fails with
        # "timeout expired while waiting for device".
        if "restarting adbd" in lowered:
            await self._wait_until_online(serial, timeout=30.0)

        if "cannot run as root" in lowered:
            return False, (
                f"该设备不允许以 root 运行（生产版本设备）：{output.strip()}"
                if output.strip()
                else "该设备不允许以 root 运行（生产版本）"
            )

        # The RETURN CODE IS NOT RELIABLE HERE: `adb root` restarts adbd, so adb
        # always exits non zero even when the switch worked. The output text is
        # the only trustworthy signal.
        if enabled:
            if "restarting adbd as root" in lowered or "already running as root" in lowered:
                return True, output.strip() or "已以 root 模式重启 adbd"
        else:
            if (
                "restarting adbd as non root" in lowered
                or "already running as non root" in lowered
            ):
                return True, output.strip() or "已恢复为普通用户模式"
            # The device was not rooted in the first place: the request is
            # already satisfied, so this is a success and not an error.
            if "not running as root" in lowered:
                return True, "设备本来就未以 root 运行"

        return False, output.strip() or ("切换 root 模式失败" if enabled else "取消 root 失败")'''

NEW = '''    async def set_root(self, serial: str, enabled: bool) -> tuple[bool, str]:
        """Enable or disable the root shell of a device.

        ``adb root`` / ``adb unroot`` restart adbd, so the device goes offline
        for a few seconds afterwards.  This method therefore:

        1. waits for the device to be online BEFORE issuing the switch (a
           double click or a quick root->unroot toggle would otherwise land
           inside the restart window of the previous call);
        2. retries ONCE after the device comes back when the failure looks
           transient (offline / timeout / device not found);
        3. only reports failure when the retry also fails, with the real
           reason in the message.

        The output of the agent_server is unwrapped before it is evaluated,
        and the return code of ``adb root`` is ignored on purpose (it always
        exits non zero because adbd restarts) - the output text decides.
        """
        verb = "切换 root 模式" if enabled else "取消 root"

        # Wait out any restart window left by a PREVIOUS switch.
        await self._wait_until_online(serial, timeout=15.0)

        transient_markers = (
            "offline",
            "timeout",
            "waiting for device",
            "device not found",
            "no devices",
            "closed",
        )

        last_message = ""
        for attempt in range(2):
            try:
                raw = (
                    await self.adb_root(serial)
                    if enabled
                    else await self.adb_unroot(serial)
                )
            except TransportError as exc:
                last_message = str(exc)
                lowered_exc = last_message.lower()
                if attempt == 0 and any(m in lowered_exc for m in transient_markers):
                    # Landed inside a restart window: wait, then try once more.
                    await self._wait_until_online(serial, timeout=30.0)
                    continue
                return False, f"{verb}失败: {last_message}"

            # The flag may have changed: drop the cached identity probe so the
            # next ``is_rooted`` call re-samples instead of reporting the old
            # value for the rest of the cache window.
            self._root_cache().pop(serial, None)

            output = unwrap_agent_output(raw or "")[1]
            lowered = (output or "").lower()

            if "restarting adbd" in lowered:
                await self._wait_until_online(serial, timeout=30.0)

            if "cannot run as root" in lowered:
                return False, (
                    f"该设备不允许以 root 运行（生产版本设备）：{output.strip()}"
                    if output.strip()
                    else "该设备不允许以 root 运行（生产版本）"
                )

            if enabled:
                if (
                    "restarting adbd as root" in lowered
                    or "already running as root" in lowered
                ):
                    return True, output.strip() or "已以 root 模式重启 adbd"
            else:
                if (
                    "restarting adbd as non root" in lowered
                    or "already running as non root" in lowered
                ):
                    return True, output.strip() or "已恢复为普通用户模式"
                # The device was not rooted in the first place: the request is
                # already satisfied, so this is a success and not an error.
                if "not running as root" in lowered:
                    return True, "设备本来就未以 root 运行"

            # Unrecognized output: if it looks like a transient device error,
            # wait for the device and retry once before giving up.
            if attempt == 0 and any(m in lowered for m in transient_markers):
                await self._wait_until_online(serial, timeout=30.0)
                continue
            return False, output.strip() or f"{verb}失败"

        return False, f"{verb}失败: {last_message}" if last_message else f"{verb}失败"'''

if OLD in source:
    source = source.replace(OLD, NEW, 1)
    BASE.write_text(source, encoding="utf-8")
    print("SET_ROOT_REWRITTEN")
else:
    print("ANCHOR MISSING")
    raise SystemExit(1)
