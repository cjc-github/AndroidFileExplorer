"""One-shot patcher: parse the real agent_server response envelope.

The MDD agent_server wraps every command result:

    命令执行成功 (返回码: 0)
    --------------------------------------------------
    标准输出:
    <real stdout>

    ==================================================

    命令执行失败 (返回码: 1)
    --------------------------------------------------
    错误信息:
    <real stderr>

The client treated the whole envelope as stdout, so ``adb devices -l``
parsed into one bogus device ("命令执行成功") instead of the real list.

The patch adds ``unwrap_agent_output`` (mirrors platform-api's
``parse_abi_output``) and makes the success/failure detection use it.
"""

import pathlib

TARGET = pathlib.Path(__file__).resolve().parent / "app" / "transports" / "mdd_client.py"

OLD_IMPORT_BLOCK = '''from __future__ import annotations

import json
import socket
from dataclasses import dataclass

END_MARKER = "===END==="
DEFAULT_BUFFER_SIZE = 8192
'''

NEW_IMPORT_BLOCK = '''from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass

END_MARKER = "===END==="
DEFAULT_BUFFER_SIZE = 8192

#: The agent_server wraps every result in a human readable envelope.
_ENVELOPE_HEAD = re.compile(r"^命令执行(?:成功|失败)\\s*\\(返回码:\\s*(-?\\d+)\\)\\s*$")
_SEPARATOR = "-" * 50
_FOOTER = "=" * 50
_STDOUT_LABEL = "标准输出:"
_STDERR_LABEL = "错误信息:"


def unwrap_agent_output(message: str) -> tuple[bool, str]:
    """Split an agent_server envelope into (ok, real_output).

    Returns ``(True, stdout)`` for a successful command and
    ``(False, stderr)`` for a failed one. When the message does not look like
    an envelope it is returned unchanged with ``ok=True`` - the raw output is
    then the best we have.
    """
    if not message:
        return True, ""

    lines = message.splitlines()
    if not lines:
        return True, message

    match = _ENVELOPE_HEAD.match(lines[0].strip())
    if match is None:
        return True, message

    return_code = int(match.group(1))
    # Find the section label after the separator line.
    body_start = None
    label = _STDOUT_LABEL
    for index in range(1, min(len(lines), 5)):
        stripped = lines[index].strip()
        if stripped == _SEPARATOR:
            continue
        if stripped in (_STDOUT_LABEL, _STDERR_LABEL):
            body_start = index + 1
            label = stripped
            break

    if body_start is None:
        return return_code == 0, message

    body: list[str] = []
    for line in lines[body_start:]:
        if line.strip() == _FOOTER:
            break
        body.append(line)

    output = "\\n".join(body).strip()
    if label == _STDERR_LABEL:
        return False, output
    return return_code == 0, output
'''

OLD_EXECUTE = '''        lowered = response.lower()
        if response.startswith("ERROR:"):
            return MddCommandResult(success=False, message=response, error=response)
        if "命令执行失败" in lowered:
            return MddCommandResult(success=False, message=response, error=response)
        return MddCommandResult(success=True, message=response)
'''

NEW_EXECUTE = '''        if response.startswith("ERROR:"):
            return MddCommandResult(success=False, message=response, error=response)

        ok, output = unwrap_agent_output(response)
        if not ok:
            return MddCommandResult(success=False, message=output, error=output)
        return MddCommandResult(success=True, message=output)
'''


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    if "unwrap_agent_output" in source:
        print("ALREADY_PATCHED")
        return 0
    if OLD_IMPORT_BLOCK not in source:
        print("ANCHOR_IMPORT_MISSING")
        return 1
    if OLD_EXECUTE not in source:
        print("ANCHOR_EXECUTE_MISSING")
        return 1
    source = source.replace(OLD_IMPORT_BLOCK, NEW_IMPORT_BLOCK, 1)
    source = source.replace(OLD_EXECUTE, NEW_EXECUTE, 1)
    TARGET.write_text(source, encoding="utf-8")
    print("PATCHED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
