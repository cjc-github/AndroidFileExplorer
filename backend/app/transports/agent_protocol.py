"""The agent_server (MDD) response envelope.

The agent_server wraps every command result in a human readable envelope::

    命令执行成功 (返回码: 0)
    --------------------------------------------------
    标准输出:
    <real stdout>

    ==================================================

    命令执行失败 (返回码: 1)
    --------------------------------------------------
    错误信息:
    <real stderr>

This module lives on its own because both the transport base class and the
MDD client need it, and the base class must not import the client (that
would be a circular import).
"""

from __future__ import annotations

import re

_ENVELOPE_HEAD = re.compile(r"^命令执行(?:成功|失败)\s*\(返回码:\s*(-?\d+)\)\s*$")
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

    output = "\n".join(body).strip()
    if label == _STDERR_LABEL:
        return False, output
    return return_code == 0, output
