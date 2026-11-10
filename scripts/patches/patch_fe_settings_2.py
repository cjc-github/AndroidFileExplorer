"""One-shot patcher (v2, real anchors): settings route + nav + poll wiring."""

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


# ---- router ------------------------------------------------------------
patch(
    FE / "router.tsx",
    [
        (
            '''import { MachinesPage } from "@/pages/machines/MachinesPage";''',
            '''import { MachinesPage } from "@/pages/machines/MachinesPage";
import { SettingsPage } from "@/pages/settings/SettingsPage";''',
            "page import",
        ),
        (
            '''/** 文件浏览：先选设备 */
const filePickerRoute = createRoute({''',
            '''/** 平台设置 */
const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: SettingsPage,
});

/** 文件浏览：先选设备 */
const filePickerRoute = createRoute({''',
            "route def",
        ),
        (
            '''const routeTree = rootRoute.addChildren([
  machinesRoute,
  devicesRoute,
  filePickerRoute,
  fileBrowserRoute,
  indexRoute,
]);''',
            '''const routeTree = rootRoute.addChildren([
  machinesRoute,
  devicesRoute,
  filePickerRoute,
  fileBrowserRoute,
  settingsRoute,
  indexRoute,
]);''',
            "addChildren",
        ),
    ],
    "router",
)

# ---- nav item ----------------------------------------------------------
patch(
    FE / "layout" / "AppLayout.tsx",
    [
        (
            '''  {
    to: "/files",
    label: "文件浏览",
    icon: <FolderOpenOutlined />,
    hint: "浏览设备上的文件",
  },
] as const;''',
            '''  {
    to: "/files",
    label: "文件浏览",
    icon: <FolderOpenOutlined />,
    hint: "浏览设备上的文件",
  },
  {
    to: "/settings",
    label: "设置",
    icon: <SettingOutlined />,
    hint: "平台设置",
  },
] as const;''',
            "nav item",
        ),
        (
            '''import { ApiOutlined, ClusterOutlined, FolderOpenOutlined } from "@ant-design/icons";''',
            '''import {
  ApiOutlined,
  ClusterOutlined,
  FolderOpenOutlined,
  SettingOutlined,
} from "@ant-design/icons";''',
            "icon import",
        ),
    ],
    "layout",
)

# ---- devices page: settings driven poll ---------------------------------
patch(
    FE / "pages" / "devices" / "DevicesPage.tsx",
    [
        (
            '''import {
  batchRootDevices,
  connectDevice,
  disconnectDevice,
  getDeviceList,''',
            '''import {
  batchRootDevices,
  connectDevice,
  disconnectDevice,
  getDeviceList,
  getSettings,''',
            "api import",
        ),
        (
            '''  const devicesQuery = useQuery({''',
            '''  // The refresh interval is a platform setting (defaults to 3s).
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: getSettings,
  });
  const pollSeconds =
    settingsQuery.data?.sync_interval_seconds ?? POLL_INTERVAL_MS / 1000;

  const devicesQuery = useQuery({''',
            "settings query",
        ),
        (
            '''    refetchInterval: POLL_INTERVAL_MS,''',
            '''    refetchInterval: pollSeconds * 1000,''',
            "refetch",
        ),
        (
            '''            每 {POLL_INTERVAL_MS / 1000} 秒刷新一次运行状态''',
            '''            每 {pollSeconds} 秒刷新一次运行状态''',
            "copy",
        ),
    ],
    "devices",
)

print(f"DONE ({edits})")
