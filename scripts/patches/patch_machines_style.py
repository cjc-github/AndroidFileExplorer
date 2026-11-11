"""One-shot patcher: bring the machines table up to the device-page style.

Same visual language the device resources page already uses: two-line name
cell (title + kind subtitle), semantic kind tags, copyable monospace address,
a device count with hierarchy, and ellipsis+tooltip for long errors.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PAGE = ROOT / "frontend" / "src" / "pages" / "machines" / "MachinesPage.tsx"
source = PAGE.read_text(encoding="utf-8")
edits = 0


def sub(old: str, new: str, label: str) -> None:
    global source, edits
    if old in source:
        source = source.replace(old, new, 1)
        edits += 1
        print(f"  {label} OK")
    elif new in source:
        print(f"  {label} already applied")
    else:
        print(f"  {label} ANCHOR MISSING")
        raise SystemExit(1)


# ---- kind metadata (label + tag color + subtitle) ----------------------
sub(
    '''const engineStatusLabel: Record<string, string> = {''',
    '''const KIND_META: Record<
  string,
  { label: string; color: string | undefined; hint: string }
> = {
  local: { label: "本机 adb", color: undefined, hint: "本机 adb server" },
  remote: { label: "agent", color: "blue", hint: "MDD agent 通道" },
  ssh: { label: "SSH 直连", color: "purple", hint: "SSH 直连（零部署）" },
};

const engineStatusLabel: Record<string, string> = {''',
    "kind meta",
)

# ---- name column: two lines like the device page ------------------------
sub(
    '''      render: (value: string | null, record) => (
        <Space>
          <span>{value || record.host}</span>
          {record.kind === "local" ? <Tag>本机</Tag> : <Tag color="blue">远程</Tag>}
        </Space>
      ),''',
    '''      render: (value: string | null, record) => {
        const meta = KIND_META[record.kind] ?? {
          label: record.kind,
          color: undefined,
          hint: "",
        };
        return (
          <div className="device-name">
            <Space size={6}>
              <Typography.Text strong>{value || record.host}</Typography.Text>
              <Tag color={meta.color}>{meta.label}</Tag>
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {meta.hint}
            </Typography.Text>
          </div>
        );
      },''',
    "name column",
)

# ---- address: copyable ---------------------------------------------------
sub(
    '''      render: (_, record) => (
        <Typography.Text type="secondary" style={{ fontFamily: "monospace" }}>
          {record.host}:{record.port}
        </Typography.Text>
      ),''',
    '''      render: (_, record) => (
        <Typography.Text
          type="secondary"
          copyable
          style={{ fontFamily: "monospace", fontSize: 12 }}
        >
          {record.host}:{record.port}
        </Typography.Text>
      ),''',
    "address column",
)

# ---- device count: hierarchy ---------------------------------------------
sub(
    '''    {
      title: "设备数",
      dataIndex: "device_count",
      key: "device_count",
      width: 100,
    },''',
    '''    {
      title: "设备数",
      dataIndex: "device_count",
      key: "device_count",
      width: 90,
      render: (value: number) => (
        <Typography.Text strong={value > 0} type={value > 0 ? undefined : "secondary"}>
          {value}
        </Typography.Text>
      ),
    },''',
    "count column",
)

# ---- error message: ellipsis + tooltip ------------------------------------
sub(
    '''      render: (value: string | null) =>
        value ? (
          <Typography.Text type="danger" style={{ fontSize: 12 }}>
            {value}
          </Typography.Text>
        ) : (
          "-"
        ),''',
    '''      render: (value: string | null) =>
        value ? (
          <Tooltip title={value}>
            <Typography.Text
              type="danger"
              style={{ fontSize: 12 }}
              ellipsis={{ showTitle: false }}
            >
              {value}
            </Typography.Text>
          </Tooltip>
        ) : (
          "-"
        ),''',
    "error column",
)

# ---- actions: icon + consistent spacing -----------------------------------
sub(
    '''      render: (_, record) => (
        <Space>
          <Button
            size="small"
            loading={testMutation.isPending}
            onClick={() => testMutation.mutate(record.id)}
          >
            测试连接
          </Button>''',
    '''      render: (_, record) => (
        <Space size={4}>
          <Button
            size="small"
            icon={<ApiOutlined />}
            loading={testMutation.isPending}
            onClick={() => testMutation.mutate(record.id)}
          >
            测试连接
          </Button>''',
    "actions column",
)

PAGE.write_text(source, encoding="utf-8")
print(f"DONE ({edits})")
