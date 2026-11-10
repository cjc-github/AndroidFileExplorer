import type { ReactNode } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { Tooltip } from "antd";
import {
  ApiOutlined,
  ClusterOutlined,
  FolderOpenOutlined,
  SettingOutlined,
} from "@ant-design/icons";

/**
 * The three workspaces of this tool.
 *
 * The file browser has its own entry: picking a device is the first step of
 * browsing files, and hiding that behind a button inside a table row makes
 * the feature undiscoverable.
 */
const MENU = [
  {
    to: "/machines",
    label: "远程机器",
    icon: <ApiOutlined />,
    hint: "远程机器连接配置",
  },
  {
    to: "/devices",
    label: "设备资源",
    icon: <ClusterOutlined />,
    hint: "设备资源管理",
  },
  {
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
] as const;

export function AppLayout({ children }: { children: ReactNode }) {
  const pathname = useRouterState({ select: (state) => state.location.pathname });

  const isActive = (to: string) => {
    if (to === "/files") {
      // Both /files (device picker) and /devices/<uid>/files belong here.
      return pathname.startsWith("/files") || pathname.includes("/files");
    }
    if (to === "/devices") {
      return pathname.startsWith("/devices");
    }
    return pathname.startsWith(to);
  };

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>Android 文件资源管理器</h1>
        <span className="app-subtitle">设备发现 · 文件浏览 · 拉取上传 · 运行状态监控</span>
      </header>
      <div className="app-body">
        <nav className="app-sider">
          {MENU.map((item) => (
            <Tooltip key={item.to} title={item.hint} placement="right">
              <Link
                to={item.to}
                className={isActive(item.to) ? "sider-item sider-item-active" : "sider-item"}
              >
                <span className="sider-icon">{item.icon}</span>
                <span className="sider-label">{item.label}</span>
              </Link>
            </Tooltip>
          ))}
        </nav>
        <main className="app-main">{children}</main>
      </div>
    </div>
  );
}
