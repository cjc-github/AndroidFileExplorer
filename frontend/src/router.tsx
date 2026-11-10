import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp, ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";

import { AppLayout } from "@/layout/AppLayout";
import { MachinesPage } from "@/pages/machines/MachinesPage";
import { SettingsPage } from "@/pages/settings/SettingsPage";
import { DevicesPage } from "@/pages/devices/DevicesPage";
import { FileBrowserPickerPage } from "@/pages/files/FileBrowserPickerPage";
import { FileBrowserPage } from "@/pages/files/FileBrowserPage";

import "antd/dist/reset.css";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

const rootRoute = createRootRoute({
  component: () => (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          algorithm: theme.defaultAlgorithm,
          token: { colorPrimary: "#1668dc" },
        }}
      >
        <AntApp>
          <AppLayout>
            <Outlet />
          </AppLayout>
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  ),
});

/** 远程机器连接配置 */
const machinesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/machines",
  component: MachinesPage,
});

/** 设备资源管理 */
const devicesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/devices",
  component: DevicesPage,
});

/** 平台设置 */
const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: SettingsPage,
});

/** 文件浏览：先选设备 */
const filePickerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/files",
  component: FileBrowserPickerPage,
});

/** 文件浏览：某台设备的文件目录 */
const fileBrowserRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/devices/$uid/files",
  component: FileBrowserPage,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/devices" });
  },
});

const routeTree = rootRoute.addChildren([
  machinesRoute,
  devicesRoute,
  filePickerRoute,
  fileBrowserRoute,
  settingsRoute,
  indexRoute,
]);

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  defaultNotFoundComponent: () => (
    <div style={{ padding: 24 }}>页面不存在，请从左侧菜单选择功能。</div>
  ),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
