"""One-shot patcher: make the fake MDD server speak the real wire format.

The real agent_server wraps every command result:

    命令执行成功 (返回码: 0)
    --------------------------------------------------
    标准输出:
    <real stdout>

    ==================================================

The fake server answered with the bare stdout, so it did not reproduce the
production behaviour. The patch makes it emit the same envelope (and the
same "错误信息" section for failures), which is what the real peer does.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "tests" / "fake_mdd_server.py"

OLD_CMD = '''    def _handle_cmd(self, command: str, device: FakeDevice) -> None:
        device.commands.append(command)
        output = self._run_adb_like(command, device)
        self.wfile.write(f"{output}\\n{END_MARKER}\\n".encode())
'''

NEW_CMD = '''    def _handle_cmd(self, command: str, device: FakeDevice) -> None:
        device.commands.append(command)
        output, failed = self._run_adb_like(command, device)
        if failed:
            body = (
                "命令执行失败 (返回码: 1)\\n"
                "--------------------------------------------------\\n"
                "错误信息:\\n"
                f"{output}\\n\\n"
                "=================================================="
            )
        else:
            body = (
                "命令执行成功 (返回码: 0)\\n"
                "--------------------------------------------------\\n"
                "标准输出:\\n"
                f"{output}\\n\\n"
                "=================================================="
            )
        self.wfile.write(f"{body}\\n{END_MARKER}\\n".encode())
'''

OLD_RUN = '''    def _run_adb_like(self, command: str, device: FakeDevice) -> str:
        if command.startswith("adb devices"):
            return "List of devices attached\\nemulator-5554\\tdevice"
        if " shell ls " in command:
            path = command.rsplit(" ", 1)[-1]
            return device.ls(path)
        if " shell " in command:
            return ""
        return ""
'''

NEW_RUN = '''    def _run_adb_like(self, command: str, device: FakeDevice) -> tuple[str, bool]:
        """Return (output, failed) like the real agent_server does."""
        if command.startswith("adb devices"):
            return "List of devices attached\\nemulator-5554\\tdevice", False
        if " shell ls " in command:
            path = command.rsplit(" ", 1)[-1]
            listing = device.ls(path)
            if listing.startswith("ls:"):
                return listing, True
            return listing, False
        if " shell " in command:
            return "", False
        return "", False
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "命令执行成功 (返回码: 0)" in source:
        print("ALREADY_PATCHED")
        return 0
    if OLD_CMD not in source:
        print("ANCHOR_CMD_MISSING")
        return 1
    if OLD_RUN not in source:
        print("ANCHOR_RUN_MISSING")
        return 1
    source = source.replace(OLD_CMD, NEW_CMD, 1)
    source = source.replace(OLD_RUN, NEW_RUN, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
