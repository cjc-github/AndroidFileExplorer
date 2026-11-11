"""One-shot patcher: show CPU cores / max frequency / SoC in the device table.

The CPU column gets the same "gauge on top, facts underneath" structure the
memory column already uses; the 系统 column gains the SoC / board platform.
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
            '''  cpu_abi: string | null;''',
            '''  cpu_abi: string | null;
  cpu_cores: number | null;
  cpu_max_freq_khz: number | null;
  board_platform: string | null;''',
            "device fields",
        ),
    ],
    "types",
)

# ---- devices page ------------------------------------------------------
patch(
    FE / "pages" / "devices" / "DevicesPage.tsx",
    [
        (
            '''      render: (_, record) =>
        record.metrics?.cpu_percent === null ||
        record.metrics?.cpu_percent === undefined ? (
          <Typography.Text type="secondary">采样中…</Typography.Text>
        ) : (
          <Progress
            type="circle"
            percent={record.metrics.cpu_percent}
            size={72}
            strokeWidth={9}
            format={() => formatPercent(record.metrics?.cpu_percent)}
          />
        ),
    },''',
            '''      render: (_, record) => {
        const cores = record.cpu_cores;
        const freqGhz =
          record.cpu_max_freq_khz != null
            ? (record.cpu_max_freq_khz / 1_000_000).toFixed(2)
            : null;
        return (
          <Space direction="vertical" size={2} align="center">
            {record.metrics?.cpu_percent === null ||
            record.metrics?.cpu_percent === undefined ? (
              <Typography.Text type="secondary">采样中…</Typography.Text>
            ) : (
              <Progress
                type="circle"
                percent={record.metrics.cpu_percent}
                size={72}
                strokeWidth={9}
                format={() => formatPercent(record.metrics?.cpu_percent)}
              />
            )}
            {cores != null || freqGhz != null ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {cores != null ? `${cores} 核` : ""}
                {cores != null && freqGhz != null ? " · " : ""}
                {freqGhz != null ? `${freqGhz} GHz` : ""}
              </Typography.Text>
            ) : null}
          </Space>
        );
      },
    },''',
            "cpu column",
        ),
        (
            '''        const version = record.android_version;
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
        );''',
            '''        const version = record.android_version;
        const api = record.api_level;
        const abi = record.cpu_abi;
        const soc = record.board_platform;
        if (!version && !abi && !soc)
          return <Typography.Text type="secondary">-</Typography.Text>;
        return (
          <div className="device-name">
            <Typography.Text style={{ fontSize: 13 }}>
              Android {version ?? "?"}
              {api ? ` (API ${api})` : ""}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {[soc, abi].filter(Boolean).join(" · ") || "-"}
            </Typography.Text>
          </div>
        );''',
            "system column",
        ),
    ],
    "devices",
)

print(f"DONE ({edits})")
