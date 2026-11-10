"""One-shot patcher: per-row root switch in the devices page.

The file browser already has "尝试 root" / "取消 root"; the device table is
where the lifecycle lives, so the same action belongs in its action column.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PAGE = ROOT / "frontend" / "src" / "pages" / "devices" / "DevicesPage.tsx"
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


sub(
    '''import {
  batchRootDevices,
  connectDevice,
  disconnectDevice,
  getDeviceList,
  updateDeviceAlias,
} from "@/api";''',
    '''import {
  batchRootDevices,
  connectDevice,
  disconnectDevice,
  getDeviceList,
  tryRootDevice,
  unRootDevice,
  updateDeviceAlias,
} from "@/api";''',
    "imports",
)

sub(
    '''  const aliasMutation = useMutation({''',
    '''  const rootSwitchMutation = useMutation({
    mutationFn: (payload: { uid: string; enable: boolean }) =>
      payload.enable
        ? tryRootDevice(payload.uid)
        : unRootDevice(payload.uid),
    onSuccess: (updated) => {
      patchDevice(updated);
      message.success(
        updated.is_rooted
          ? "已以 root 模式重启 adbd"
          : "已恢复为普通用户模式",
      );
    },
    onError: (error: Error) => message.error(error.message || "操作失败"),
  });

  const aliasMutation = useMutation({''',
    "mutation",
)

sub(
    '''          </Tooltip>
          {record.is_network_device ? (''',
    '''          </Tooltip>
          <Tooltip
            title={
              record.is_rooted
                ? "以 adb unroot 恢复为普通用户"
                : "以 adb root 重启 adbd（生产版本设备会被拒绝）"
            }
          >
            <Button
              size="small"
              icon={<SafetyOutlined />}
              loading={rootSwitchMutation.isPending}
              disabled={record.status !== "online"}
              onClick={() =>
                rootSwitchMutation.mutate({
                  uid: record.uid,
                  enable: !record.is_rooted,
                })
              }
            >
              {record.is_rooted ? "取消 root" : "尝试 root"}
            </Button>
          </Tooltip>
          {record.is_network_device ? (''',
    "action button",
)

PAGE.write_text(source, encoding="utf-8")
print(f"DONE ({edits}/3)")
