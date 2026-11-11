"""One-shot patcher: the machines page gains the SSH connection mode.

The "新增机器" form gets a connection-mode selector: agent (MDD port +
shared secret) or ssh (SSH port + username + password, zero deployment).
The machine table shows the mode as a tag.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
FE = ROOT / "frontend" / "src"

edits = 0


def patch(path: pathlib.Path, pairs, label: str) -> None:
    global edits
    source = path.read_text(encoding="utf-8")
    for old, new, name in pairs:
        if old in source:
            source = source.replace(old, new, 1)
            edits += 1
            print(f"  {label}.{name} OK")
        elif new in source:
            print(f"  {label}.{name} already applied")
        else:
            print(f"  {label}.{name} ANCHOR MISSING")
            raise SystemExit(1)
    path.write_text(source, encoding="utf-8")


# ---- types -------------------------------------------------------------
patch(
    FE / "api" / "types.ts",
    [
        (
            '''export interface Engine {
  id: number;
  name: string;
  host: string;
  port: number;
  kind: string;''',
            '''export interface Engine {
  id: number;
  name: string;
  host: string;
  port: number;
  kind: string;
  ssh_username: string | null;
  ssh_port: number;
  has_ssh_password: boolean;
  adb_path: string | null;''',
            "engine fields",
        ),
    ],
    "types",
)

# ---- api ---------------------------------------------------------------
patch(
    FE / "api" / "index.ts",
    [
        (
            '''export const createEngine = (payload: {
  name: string;
  host: string;
  port: number;
  token?: string;
}) => unwrap<Engine>(request.post("/engines", payload));''',
            '''export const createEngine = (payload: {
  name: string;
  host: string;
  port: number;
  kind: "remote" | "ssh";
  token?: string;
  ssh_username?: string;
  ssh_password?: string;
  ssh_port?: number;
  adb_path?: string;
}) => unwrap<Engine>(request.post("/engines", payload));''',
            "create payload",
        ),
    ],
    "api",
)

# ---- machines page -----------------------------------------------------
patch(
    FE / "pages" / "machines" / "MachinesPage.tsx",
    [
        (
            '''import { createEngine, deleteEngine, getEngineList, testEngine } from "@/api";''',
            '''import {
  createEngine,
  deleteEngine,
  getEngineList,
  testEngine,
} from "@/api";''',
            "api import",
        ),
        (
            '''  const [form] = Form.useForm();''',
            '''  const [form] = Form.useForm();
  const connectionMode = Form.useWatch("connection", form) ?? "agent";''',
            "watch",
        ),
        # the form: connection mode selector + dynamic fields
        (
            '''          <Form.Item
            name="port"
            label="端口"
            rules={[{ required: true, message: "请输入端口" }]}
          >
            <InputNumber min={1} max={65535} style={{ width: "100%" }} placeholder="12345" />
          </Form.Item>
          <Form.Item
            name="token"
            label="共享密钥"
            extra="设备侧 agent 的认证密钥；留空表示不启用认证"
          >
            <Input.Password placeholder="例如：a-long-random-secret" />
          </Form.Item>''',
            '''          <Form.Item name="connection" label="连接方式" initialValue="agent">
            <Radio.Group
              options={[
                { value: "agent", label: "部署 agent（MDD 端口）" },
                { value: "ssh", label: "SSH 直连（无需部署）" },
              ]}
              optionType="button"
              buttonStyle="solid"
            />
          </Form.Item>

          {connectionMode === "agent" ? (
            <>
              <Form.Item
                name="port"
                label="agent 端口"
                rules={[{ required: true, message: "请输入端口" }]}
              >
                <InputNumber min={1} max={65535} style={{ width: "100%" }} placeholder="12345" />
              </Form.Item>
              <Form.Item
                name="token"
                label="共享密钥"
                extra="设备侧 agent 的认证密钥；留空表示不启用认证"
              >
                <Input.Password placeholder="例如：a-long-random-secret" />
              </Form.Item>
            </>
          ) : (
            <>
              <Form.Item
                name="ssh_port"
                label="SSH 端口"
                rules={[{ required: true, message: "请输入 SSH 端口" }]}
              >
                <InputNumber min={1} max={65535} style={{ width: "100%" }} placeholder="22" />
              </Form.Item>
              <Form.Item
                name="ssh_username"
                label="SSH 用户名"
                rules={[{ required: true, message: "请输入用户名" }]}
              >
                <Input placeholder="例如：test" />
              </Form.Item>
              <Form.Item
                name="ssh_password"
                label="SSH 密码"
                extra="留空时使用本机已配置的 SSH 密钥免密登录"
              >
                <Input.Password placeholder="用户密码；内网环境请知悉保管范围" />
              </Form.Item>
              <Form.Item name="adb_path" label="adb 路径（可选）">
                <Input placeholder="adb 不在 PATH 时填写，例如 /usr/lib/android-sdk/platform-tools/adb" />
              </Form.Item>
            </>
          )''',
            "dynamic form",
        ),
        # map the form values to the API payload (kind + port semantics)
        (
            '''          onFinish={(values) => createMutation.mutate(values)}''',
            '''          onFinish={(values) => {
            const isSsh = values.connection === "ssh";
            createMutation.mutate({
              ...values,
              kind: isSsh ? "ssh" : "remote",
              port: isSsh ? (values.ssh_port ?? 22) : values.port,
            });
          }}''',
            "submit mapping",
        ),
    ],
    "machines",
)

print(f"DONE ({edits})")
