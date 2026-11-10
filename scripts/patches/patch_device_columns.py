"""One-shot patcher (idempotent): insert the Root and 系统 columns before CPU."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TYPES = ROOT / "frontend" / "src" / "api" / "types.ts"
PAGE = ROOT / "frontend" / "src" / "pages" / "devices" / "DevicesPage.tsx"

types = TYPES.read_text(encoding="utf-8")
if "cpu_abi" not in types:
    OLD_TYPE = '''  brand: string | null;
  product: string | null;'''
    NEW_TYPE = '''  brand: string | null;
  cpu_abi: string | null;
  android_version: string | null;
  api_level: string | null;
  product: string | null;'''
    assert OLD_TYPE in types, "types anchor missing"
    TYPES.write_text(types.replace(OLD_TYPE, NEW_TYPE, 1), encoding="utf-8")
    print("TYPES_OK")
else:
    print("TYPES already applied")

source = PAGE.read_text(encoding="utf-8")
if 'title: "Root"' in source:
    print("COLUMNS already applied")
    raise SystemExit(0)

ANCHOR = '''    {
      title: "CPU",
      key: "cpu",'''
NEW_COLUMNS = '''    {
      title: "Root",
      dataIndex: "is_rooted",
      key: "root",
      width: 80,
      render: (value: boolean) =>
        value ? <Tag color="gold">root</Tag> : <Tag>否</Tag>,
    },
    {
      title: "系统",
      key: "system",
      width: 210,
      render: (_, record) => {
        const version = record.android_version;
        const api = record.api_level;
        const abi = record.cpu_abi;
        if (!version && !abi) return <Typography.Text type="secondary">-</Typography.Text>;
        return (
          <div className="device-name">
            <Typography.Text style={{ fontSize: 13 }}>
              Android {version ?? "?"}
              {api ? ` (API ${api})` : ""}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {abi ?? "-"}
            </Typography.Text>
          </div>
        );
      },
    },
    {
      title: "CPU",
      key: "cpu",'''

if ANCHOR in source:
    source = source.replace(ANCHOR, NEW_COLUMNS, 1)
    PAGE.write_text(source, encoding="utf-8")
    print("COLUMNS_OK")
else:
    print("ANCHOR MISSING: CPU column")
    raise SystemExit(1)
