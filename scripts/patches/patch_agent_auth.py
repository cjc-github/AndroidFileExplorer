"""One-shot patcher: add a shared secret to the device agent.

The agent listens on a TCP port and executes adb commands for whoever asks.
Without a secret, anyone who can reach the port can run arbitrary adb
commands on the machine - and through them on every attached device.

The client sends ``AUTH <token>`` as its first line.  When the agent has no
token configured the handshake is skipped, so a single user installation
keeps working.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
AGENT = ROOT / "agent" / "agent.py"
source = AGENT.read_text(encoding="utf-8")

OLD_HEADER = '''    runner: CommandRunner

    def handle(self) -> None:
        LOGGER.info("client connected: %s", self.client_address)
        try:
            while True:
                raw = self.rfile.readline()
                if not raw:
                    break
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                self.wfile.write(self._dispatch(line).encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            LOGGER.info("client disconnected: %s", self.client_address)'''

NEW_HEADER = '''    runner: CommandRunner
    token: str | None = None

    def handle(self) -> None:
        LOGGER.info("client connected: %s", self.client_address)
        try:
            if not self._authenticate():
                LOGGER.warning(
                    "client %s failed the authentication", self.client_address
                )
                return
            while True:
                raw = self.rfile.readline()
                if not raw:
                    break
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                self.wfile.write(self._dispatch(line).encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            LOGGER.info("client disconnected: %s", self.client_address)

    def _authenticate(self) -> bool:
        """Check the shared secret.

        The client sends ``AUTH <token>`` as the first line.  Without a
        configured token the handshake is skipped.
        """
        if not self.token:
            return True
        raw = self.rfile.readline()
        if not raw:
            return False
        line = raw.decode(errors="replace").strip()
        keyword, _, value = line.partition(" ")
        if keyword.upper() != "AUTH" or not hmac.compare_digest(
            value.strip(), self.token
        ):
            self.wfile.write(b"ERROR: authentication failed\\n")
            self.wfile.flush()
            return False
        self.wfile.write(b"OK\\n")
        self.wfile.flush()
        return True'''

OLD_BUILD = '''def build_server(
    host: str,
    port: int,
    adb_path: str | None = None,
    adb_server: tuple[str, int] | None = None,
) -> AgentServer:
    runner = CommandRunner(_resolve_adb(adb_path), adb_server)
    handler = type("BoundAgentHandler", (AgentHandler,), {"runner": runner})
    return AgentServer((host, port), handler)'''

NEW_BUILD = '''def build_server(
    host: str,
    port: int,
    adb_path: str | None = None,
    adb_server: tuple[str, int] | None = None,
    token: str | None = None,
) -> AgentServer:
    runner = CommandRunner(_resolve_adb(adb_path), adb_server)
    handler = type(
        "BoundAgentHandler", (AgentHandler,), {"runner": runner, "token": token}
    )
    return AgentServer((host, port), handler)'''

OLD_ARG = '''    parser.add_argument(
        "--log-level", default="INFO", help="日志级别 (DEBUG/INFO/WARNING)"
    )'''
NEW_ARG = '''    parser.add_argument(
        "--token",
        default=None,
        help="共享密钥：客户端需先发送 AUTH <token>（可用 AFE_AGENT_TOKEN 环境变量）",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="日志级别 (DEBUG/INFO/WARNING)"
    )'''

OLD_CALL = '''    try:
        server = build_server(args.host, args.port, args.adb, adb_server)
    except RuntimeError as exc:'''
NEW_CALL = '''    token = args.token or os.getenv("AFE_AGENT_TOKEN") or None
    if token:
        LOGGER.info("agent authentication is enabled")

    try:
        server = build_server(args.host, args.port, args.adb, adb_server, token)
    except RuntimeError as exc:'''

OLD_IMPORT = '''import argparse
import logging'''
NEW_IMPORT = '''import argparse
import hmac
import logging'''

for old, new, label in (
    (OLD_HEADER, NEW_HEADER, "handler"),
    (OLD_BUILD, NEW_BUILD, "build_server"),
    (OLD_ARG, NEW_ARG, "argument"),
    (OLD_CALL, NEW_CALL, "call"),
):
    if old in source:
        source = source.replace(old, new, 1)
        print(f"{label} OK")
    else:
        print(f"{label} ANCHOR MISSING")
        raise SystemExit(1)

if "import hmac" not in source:
    source = source.replace(OLD_IMPORT, NEW_IMPORT, 1)
    print("import OK")

AGENT.write_text(source, encoding="utf-8")
print("DONE")
