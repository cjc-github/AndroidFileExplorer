import { useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Modal,
  Progress,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableProps } from "antd";
import {
  EditOutlined,
  FileSearchOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
  SafetyOutlined,
  SearchOutlined,
} from "@ant-design/icons";

import {
  batchRootDevices,
  connectDevice,
  disconnectDevice,
  getDeviceDetail,
  getDevicePage,
  tryRootDevice,
  unRootDevice,
  updateDeviceAlias,
} from "@/api";
import { extractErrorMessage } from "@/api/request";
import type { Device, DevicePage, DeviceStorage } from "@/api/types";
import { deviceStatusColor, deviceStatusLabel } from "@/api/status";
import { useEnvironmentCheck } from "@/hooks/useEnvironmentCheck";
import { useRefreshInterval } from "@/hooks/useRefreshInterval";
import { formatBytes, formatPercent, formatTime } from "@/utils/format";

/** How often the foreground polls the runtime state (requirement R3). */
export const POLL_INTERVAL_MS = 3000;

function storageOf(device: Device, path: string): DeviceStorage | null {
  return device.metrics?.storage?.find((item) => item.path === path) ?? null;
}

function storagePercent(item: DeviceStorage | null): number | null {
  if (!item?.total_bytes) return null;
  const used = item.used_bytes ?? 0;
  return Math.max(0, Math.min(100, (used / item.total_bytes) * 100));
}

function DeviceDetail({ device }: { device: Device }) {
  const metrics = device.metrics;
  const data = storageOf(device, "/data");
  const sdcard = storageOf(device, "/sdcard");
  const freq = (khz: number | null | undefined) =>
    khz != null ? `${(khz / 1_000_000).toFixed(2)} GHz` : "-";
  const storageText = (item: DeviceStorage | null) => {
    if (!item) return "-";
    const percent = storagePercent(item);
    return `${formatBytes(item.used_bytes)} / ${formatBytes(item.total_bytes)}${
      percent != null ? `（${percent.toFixed(1)}%）` : ""
    }`;
  };

  return (
    <Descriptions
      bordered
      size="small"
      column={1}
      items={[
        {
          key: "status",
          label: "状态",
          children: (
            <Tag color={deviceStatusColor[device.status] ?? "default"}>
              {deviceStatusLabel[device.status] ?? device.status}
            </Tag>
          ),
        },
        { key: "serial", label: "序列号", children: device.serial },
        {
          key: "uid",
          label: "唯一标识 UID",
          children: <Typography.Text code>{device.uid}</Typography.Text>,
        },
        { key: "transport", label: "传输方式", children: device.transport_kind },
        {
          key: "engine",
          label: "所属机器 ID",
          children: device.engine_id ?? "-",
        },
        { key: "model", label: "型号", children: device.model ?? "-" },
        { key: "brand", label: "品牌", children: device.brand ?? "-" },
        { key: "product", label: "产品", children: device.product ?? "-" },
        { key: "device", label: "设备代号", children: device.device ?? "-" },
        {
          key: "android",
          label: "系统",
          children: device.android_version
            ? `Android ${device.android_version}${
                device.api_level ? ` (API ${device.api_level})` : ""
              }`
            : "-",
        },
        { key: "abi", label: "CPU 架构", children: device.cpu_abi ?? "-" },
        { key: "soc", label: "SoC", children: device.board_platform ?? "-" },
        {
          key: "cores",
          label: "CPU 核心",
          children: device.cpu_cores != null ? `${device.cpu_cores} 核` : "-",
        },
        {
          key: "current_freq",
          label: "CPU 当前频率",
          children: freq(metrics?.cpu_current_freq_khz),
        },
        {
          key: "max_freq",
          label: "CPU 最高频率",
          children: freq(device.cpu_max_freq_khz),
        },
        {
          key: "cpu",
          label: "CPU 使用率",
          children:
            metrics?.cpu_percent != null
              ? formatPercent(metrics.cpu_percent)
              : "-",
        },
        {
          key: "memory",
          label: "内存",
          children:
            metrics?.memory_total_bytes != null
              ? `${formatBytes(metrics.memory_used_bytes)} / ${formatBytes(
                  metrics.memory_total_bytes,
                )}（${formatPercent(metrics.memory_percent)}）`
              : "-",
        },
        {
          key: "storage_data",
          label: "数据分区 /data",
          children: storageText(data),
        },
        {
          key: "storage_sdcard",
          label: "内部存储 /sdcard",
          children: storageText(sdcard),
        },
        {
          key: "root",
          label: "Root",
          children: device.is_rooted ? "是" : "否",
        },
        {
          key: "last_seen",
          label: "最后在线",
          children: formatTime(device.last_seen),
        },
        {
          key: "updated",
          label: "更新时间",
          children: formatTime(device.updated_at),
        },
        {
          key: "error",
          label: "错误信息",
          children: device.last_error ?? "-",
        },
      ]}
    />
  );
}

export function DevicesPage() {
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [aliasTarget, setAliasTarget] = useState<Device | null>(null);
  const [aliasValue, setAliasValue] = useState("");
  const [selectedUids, setSelectedUids] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [debouncedFilter, setDebouncedFilter] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [sortBy, setSortBy] = useState<"device" | "last_seen">();
  const [sortOrder, setSortOrder] = useState<"ascend" | "descend">();
  const [detailUid, setDetailUid] = useState<string | null>(null);

  // The first load re-syncs the machines so the user never has to press
  // "同步" to see a device. Every later poll uses the cheap query, which
  // already returns the cached CPU / memory samples.
  const hasSyncedRef = useRef(false);
  const lastFullSyncRef = useRef(0);

  // The refresh interval is a platform setting (defaults to 3s), shared with
  // the file picker and the file browser.
  const pollMs = useRefreshInterval(POLL_INTERVAL_MS / 1000);
  const environmentQuery = useEnvironmentCheck();
  const adbIssue = environmentQuery.data?.checks.find(
    (item) => item.key === "adb" && !item.ok,
  );

  // Filtering happens on the server so it covers devices outside the
  // current page; debounce the input so typing does not spam the API.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedFilter(filter.trim()), 300);
    return () => clearTimeout(timer);
  }, [filter]);

  const sortParam: "asc" | "desc" | undefined =
    sortOrder === "ascend" ? "asc" : sortOrder === "descend" ? "desc" : undefined;

  const devicesQuery = useQuery({
    queryKey: [
      "devices",
      page,
      pageSize,
      debouncedFilter,
      sortBy,
      sortOrder,
    ],
    queryFn: () => {
      const now = Date.now();
      // Full discovery once, then the cheap poll. A periodic full sync (30s)
      // is still required: a device that was marked offline must be able to
      // come back without the user pressing 同步.
      const fullSync =
        !hasSyncedRef.current || now - lastFullSyncRef.current >= 30_000;
      if (fullSync) {
        hasSyncedRef.current = true;
        lastFullSyncRef.current = now;
      }
      return getDevicePage({
        page,
        pageSize,
        keyword: debouncedFilter,
        sortBy,
        sortOrder: sortParam,
        refresh: fullSync,
      });
    },
    refetchInterval: pollMs,
    placeholderData: keepPreviousData,
  });

  const deviceName = (device: Device) =>
    device.alias ?? device.model ?? device.serial;

  const devices = devicesQuery.data?.items ?? [];

  const detailQuery = useQuery({
    queryKey: ["device-detail", detailUid],
    queryFn: () => getDeviceDetail(detailUid!),
    enabled: Boolean(detailUid),
    refetchInterval: pollMs,
  });
  const detailDevice =
    detailQuery.data ?? devices.find((item) => item.uid === detailUid) ?? null;

  /** Why the CPU / memory gauge has no value: offline is not "sampling". */
  const metricPlaceholder = (record: Device) => {
    if (record.status !== "online") {
      return (
        <Typography.Text type="secondary">
          {deviceStatusLabel[record.status] ?? record.status}
        </Typography.Text>
      );
    }
    if (!record.metrics) {
      return <Typography.Text type="secondary">暂无数据</Typography.Text>;
    }
    return null;
  };

  const refreshMutation = useMutation({
    mutationFn: () =>
      getDevicePage({
        page,
        pageSize,
        keyword: debouncedFilter,
        sortBy,
        sortOrder: sortParam,
        refresh: true,
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(
        ["devices", page, pageSize, debouncedFilter, sortBy, sortOrder],
        data,
      );
      message.success(`已同步，共 ${data.total} 台设备`);
    },
    onError: (error: Error) => message.error(error.message || "同步失败"),
  });

  const batchRootMutation = useMutation({
    mutationFn: (payload: { uids: string[]; enable: boolean }) =>
      batchRootDevices(payload.uids, payload.enable),
    onSuccess: (results) => {
      const failed = results.filter((item) => !item.ok);
      const succeeded = results.length - failed.length;
      const known = queryClient
        .getQueriesData<DevicePage>({ queryKey: ["devices"] })
        .flatMap(([, data]) => data?.items ?? []);
      const nameOf = (uid: string) => {
        const device = known.find((item) => item.uid === uid);
        return device ? deviceName(device) : uid;
      };

      if (failed.length > 0) {
        // Keep the failures selected so the user can retry them directly.
        setSelectedUids(failed.map((item) => item.uid));
        modal.error({
          title: `批量操作完成：${succeeded}/${results.length} 台设备成功`,
          width: 560,
          content: (
            <div style={{ maxHeight: 260, overflow: "auto" }}>
              {failed.map((item) => (
                <div key={item.uid} style={{ marginTop: 8 }}>
                  <Typography.Text strong>{nameOf(item.uid)}</Typography.Text>
                  <Typography.Text type="danger">：{item.message || "操作失败"}</Typography.Text>
                </div>
              ))}
            </div>
          ),
        });
      } else {
        message.success(`批量操作完成：${succeeded}/${results.length} 台设备成功`);
        setSelectedUids([]);
      }
      queryClient.invalidateQueries({ queryKey: ["devices"] });
    },
    onError: (error: Error) => message.error(error.message || "批量操作失败"),
  });

  const rootSwitchMutation = useMutation({
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

  const aliasMutation = useMutation({
    mutationFn: (payload: { uid: string; alias: string }) =>
      updateDeviceAlias(payload.uid, payload.alias),
    onSuccess: (updated) => {
      patchDevice(updated);
      setAliasTarget(null);
      message.success("已保存");
    },
    onError: (error: Error) => message.error(error.message || "保存失败"),
  });

  const patchDevice = (updated: Device) => {
    // Paged queries and the legacy unpaged picker query share the same key
    // prefix, so update every matching cache instead of only one.
    queryClient.setQueriesData<DevicePage>({ queryKey: ["devices"] }, (old) => {
      if (!old || !Array.isArray(old.items)) return old;
      return {
        ...old,
        items: old.items.map((item) =>
          item.uid === updated.uid ? { ...item, ...updated } : item,
        ),
      };
    });
    queryClient.setQueriesData<Device[]>({ queryKey: ["devices"] }, (old) => {
      if (!Array.isArray(old)) return old;
      return old.map((item) =>
        item.uid === updated.uid ? { ...item, ...updated } : item,
      );
    });
    queryClient.setQueryData<Device>(["device-detail", updated.uid], (old) =>
      old ? { ...old, ...updated } : old,
    );
  };

  const connectMutation = useMutation({
    mutationFn: (uid: string) => connectDevice(uid),
    onSuccess: (updated) => {
      patchDevice(updated);
      message.success("连接成功");
    },
    onError: (error: Error) => message.error(error.message || "连接失败"),
  });

  const disconnectMutation = useMutation({
    mutationFn: (uid: string) => disconnectDevice(uid),
    onSuccess: (updated) => {
      patchDevice(updated);
      message.success("已断开");
    },
    onError: (error: Error) => message.error(error.message || "断开失败"),
  });

  const columns: TableProps<Device>["columns"] = [
    {
      title: "设备",
      key: "device",
      width: 240,
      sorter: true,
      sortOrder: sortBy === "device" ? sortOrder : null,
      render: (_, record) => {
        const openAlias = () => {
          setAliasTarget(record);
          setAliasValue(record.alias ?? record.model ?? record.serial);
        };
        return (
          <div className="device-name">
            <Space size={6}>
              <Typography.Link onClick={openAlias}>
                <Typography.Text strong>{deviceName(record)}</Typography.Text>
              </Typography.Link>
              <Tooltip title="修改设备名称">
                <EditOutlined
                  onClick={openAlias}
                  style={{ cursor: "pointer", color: "#1668dc", fontSize: 12 }}
                />
              </Tooltip>
              {record.is_rooted ? <Tag color="gold">root</Tag> : null}
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {record.serial}
            </Typography.Text>
          </div>
        );
      },
    },
    {
      title: "唯一标识 UID",
      dataIndex: "uid",
      key: "uid",
      width: 190,
      render: (value: string) => (
        <Typography.Text
          type="secondary"
          style={{ fontSize: 11, fontFamily: "monospace" }}
        >
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 110,
      render: (value: Device["status"]) => (
        <Tag color={deviceStatusColor[value] ?? "default"}>
          {deviceStatusLabel[value] ?? value}
        </Tag>
      ),
    },
    {
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
        );
      },
    },
    {
      title: "CPU",
      key: "cpu",
      width: 160,
      render: (_, record) => {
        const cores = record.cpu_cores;
        const currentGhz =
          record.metrics?.cpu_current_freq_khz != null
            ? (record.metrics.cpu_current_freq_khz / 1_000_000).toFixed(2)
            : null;
        const maxGhz =
          record.cpu_max_freq_khz != null
            ? (record.cpu_max_freq_khz / 1_000_000).toFixed(2)
            : null;
        const frequency = [
          currentGhz ? `当前 ${currentGhz} GHz` : "",
          maxGhz ? `最高 ${maxGhz} GHz` : "",
        ]
          .filter(Boolean)
          .join(" · ");
        const hardware = [
          cores != null ? `${cores} 核` : "",
          frequency,
        ]
          .filter(Boolean)
          .join(" · ");
        const placeholder = metricPlaceholder(record);
        return (
          <Space direction="vertical" size={2} align="center">
            {placeholder ??
              (record.metrics?.cpu_percent === null ||
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
              ))}
            {hardware ? (
              <Typography.Text
                type="secondary"
                style={{ fontSize: 12, textAlign: "center" }}
              >
                {hardware}
              </Typography.Text>
            ) : null}
          </Space>
        );
      },
    },
    {
      title: "内存",
      key: "memory",
      width: 130,
      render: (_, record) => {
        const placeholder = metricPlaceholder(record);
        if (placeholder) return placeholder;
        if (
          record.metrics?.memory_percent === null ||
          record.metrics?.memory_percent === undefined
        ) {
          return <Typography.Text type="secondary">采样中…</Typography.Text>;
        }
        // The ring, the percentage and the amounts belong to ONE block:
        // the gauge on top, the used / total underneath.
        return (
          <Space direction="vertical" size={2} align="center">
            <Progress
              type="circle"
              percent={record.metrics.memory_percent}
              size={72}
              strokeWidth={9}
              format={() => formatPercent(record.metrics?.memory_percent)}
            />
            <Typography.Text style={{ fontSize: 12, lineHeight: 1.3 }}>
              {formatBytes(record.metrics?.memory_used_bytes)}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 11, lineHeight: 1.3 }}>
              共 {formatBytes(record.metrics?.memory_total_bytes)}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      title: "存储",
      key: "storage",
      width: 190,
      render: (_, record) => {
        const placeholder = metricPlaceholder(record);
        if (placeholder) return placeholder;
        const data = storageOf(record, "/data");
        const sdcard = storageOf(record, "/sdcard");
        if (!data && !sdcard) {
          return <Typography.Text type="secondary">暂无数据</Typography.Text>;
        }
        const row = (label: string, item: DeviceStorage | null) => {
          const percent = storagePercent(item);
          return (
            <Tooltip
              title={
                item
                  ? `已用 ${formatBytes(item.used_bytes)} / 共 ${formatBytes(item.total_bytes)}，剩余 ${formatBytes(item.available_bytes)}`
                  : `${label} 暂无数据`
              }
            >
              <div style={{ width: 160 }}>
                <Space
                  size={4}
                  style={{ width: "100%", justifyContent: "space-between" }}
                >
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {label}
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {percent != null ? `${percent.toFixed(0)}%` : "-"}
                  </Typography.Text>
                </Space>
                <Progress
                  percent={percent ?? 0}
                  size="small"
                  showInfo={false}
                  status={percent != null && percent >= 90 ? "exception" : "normal"}
                />
              </div>
            </Tooltip>
          );
        };
        return (
          <Space direction="vertical" size={2}>
            {row("数据 /data", data)}
            {row("存储 /sdcard", sdcard)}
          </Space>
        );
      },
    },
    {
      title: "最后在线",
      dataIndex: "last_seen",
      key: "last_seen",
      width: 170,
      sorter: true,
      sortOrder: sortBy === "last_seen" ? sortOrder : null,
      render: (value: string | null) => formatTime(value),
    },
    {
      title: "错误",
      dataIndex: "last_error",
      key: "last_error",
      width: 200,
      render: (value: string | null) =>
        value ? (
          <Typography.Text
            type="danger"
            style={{ fontSize: 12 }}
            ellipsis={{ tooltip: value }}
          >
            {value}
          </Typography.Text>
        ) : (
          "-"
        ),
    },
    {
      title: "操作",
      key: "actions",
      width: 290,
      render: (_, record) => (
        <Space size={4}>
          <Tooltip title="查看完整设备信息">
            <Button
              size="small"
              icon={<InfoCircleOutlined />}
              onClick={() => setDetailUid(record.uid)}
            />
          </Tooltip>
          <Tooltip title="浏览该设备的文件目录">
            <Button
              type="primary"
              ghost
              size="small"
              icon={<FileSearchOutlined />}
              disabled={record.status !== "online"}
              onClick={() =>
                navigate({ to: "/devices/$uid/files", params: { uid: record.uid } })
              }
            >
              浏览文件
            </Button>
          </Tooltip>
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
              loading={
                rootSwitchMutation.isPending &&
                rootSwitchMutation.variables?.uid === record.uid
              }
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
          {record.is_network_device ? (
            <>
              <Button
                size="small"
                loading={
                  connectMutation.isPending &&
                  connectMutation.variables === record.uid
                }
                onClick={() => connectMutation.mutate(record.uid)}
              >
                连接
              </Button>
              <Button
                size="small"
                danger
                loading={
                  disconnectMutation.isPending &&
                  disconnectMutation.variables === record.uid
                }
                onClick={() => disconnectMutation.mutate(record.uid)}
              >
                断开
              </Button>
            </>
          ) : null}
        </Space>
      ),
    },
  ];

  const handleTableChange: TableProps<Device>["onChange"] = (
    paginationConfig,
    _filters,
    sorter,
    extra,
  ) => {
    if (extra.action === "sort") {
      const single = Array.isArray(sorter) ? sorter[0] : sorter;
      const key = single?.columnKey;
      const order = single?.order ?? undefined;
      if (order && (key === "device" || key === "last_seen")) {
        setSortBy(key);
        setSortOrder(order);
      } else {
        setSortBy(undefined);
        setSortOrder(undefined);
      }
      setPage(1);
      return;
    }
    if (extra.action === "paginate") {
      const nextPageSize = paginationConfig.pageSize ?? pageSize;
      if (nextPageSize !== pageSize) {
        setPageSize(nextPageSize);
        setPage(1);
        return;
      }
      setPage(paginationConfig.current ?? 1);
    }
  };

  return (
    <div>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 12 }}>
        <Space>
          <Typography.Text strong>设备资源管理</Typography.Text>
          <Typography.Text type="secondary">
            每 {Math.round(pollMs / 1000)} 秒刷新一次运行状态
          </Typography.Text>
        </Space>
        <Space>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="按名称 / 型号 / 序列号 / UID 过滤"
            style={{ width: 280 }}
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value);
              // The result set changes, so start over from the first page.
              setPage(1);
              // Do not keep operating on rows the user can no longer see.
              setSelectedUids([]);
            }}
          />
          <Tooltip title="从本机与远程机器重新同步设备清单">
            <Button
              icon={<ReloadOutlined />}
              loading={refreshMutation.isPending}
              onClick={() => refreshMutation.mutate()}
            >
              同步
            </Button>
          </Tooltip>
        </Space>
      </Space>

      {devicesQuery.isError ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="设备列表加载失败"
          description={extractErrorMessage(devicesQuery.error)}
          action={
            <Button size="small" onClick={() => devicesQuery.refetch()}>
              重试
            </Button>
          }
        />
      ) : null}

      {adbIssue && (devicesQuery.data?.total ?? 0) === 0 ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="本机 adb 不可用，暂无设备可发现"
          description={adbIssue.message}
          action={
            <Button size="small" onClick={() => navigate({ to: "/settings" })}>
              环境自检
            </Button>
          }
        />
      ) : null}

      {selectedUids.length > 0 ? (
        <Space style={{ marginBottom: 12 }} wrap>
          <Tag color="blue">已选 {selectedUids.length} 台</Tag>
          <Button
            icon={<SafetyOutlined />}
            loading={batchRootMutation.isPending}
            onClick={() =>
              batchRootMutation.mutate({ uids: selectedUids, enable: true })
            }
          >
            批量尝试 root
          </Button>
          <Button
            loading={batchRootMutation.isPending}
            onClick={() =>
              batchRootMutation.mutate({ uids: selectedUids, enable: false })
            }
          >
            批量取消 root
          </Button>
          <Button type="text" onClick={() => setSelectedUids([])}>
            取消选择
          </Button>
        </Space>
      ) : null}

      <Table
        rowKey="uid"
        size="small"
        dataSource={devices}
        columns={columns}
        loading={devicesQuery.isPending}
        onChange={handleTableChange}
        scroll={{ x: 2010 }}
        pagination={{
          current: page,
          pageSize,
          total: devicesQuery.data?.total ?? 0,
          showSizeChanger: true,
          hideOnSinglePage: true,
          pageSizeOptions: [10, 20, 50, 100],
          showTotal: (total, range) =>
            range[0] === range[1]
              ? `第 ${range[0]} 台 / 共 ${total} 台`
              : `${range[0]}-${range[1]} / 共 ${total} 台`,
        }}
        rowSelection={{
          selectedRowKeys: selectedUids,
          preserveSelectedRowKeys: true,
          onChange: (keys) => setSelectedUids(keys.map(String)),
        }}
        locale={{
          emptyText: (
            <Empty
              description={
                filter.trim()
                  ? "没有匹配的设备"
                  : "暂无设备，点击「同步」扫描本机与远程机器"
              }
            />
          ),
        }}
      />

      <Drawer
        title={detailDevice ? deviceName(detailDevice) : "设备详情"}
        placement="right"
        width={560}
        open={Boolean(detailUid)}
        onClose={() => setDetailUid(null)}
        destroyOnHidden
      >
        {detailDevice ? (
          <DeviceDetail device={detailDevice} />
        ) : (
          <Empty description="暂无设备详情" />
        )}
      </Drawer>

      <Modal
        title="设备名称"
        open={Boolean(aliasTarget)}
        onCancel={() => setAliasTarget(null)}
        onOk={() =>
          aliasMutation.mutate({
            uid: aliasTarget?.uid ?? "",
            alias: aliasValue.trim(),
          })
        }
        confirmLoading={aliasMutation.isPending}
        okText="保存"
        cancelText="取消"
      >
        <Input
          placeholder="给设备起个名字"
          value={aliasValue}
          onChange={(event) => setAliasValue(event.target.value)}
          onPressEnter={() =>
            aliasMutation.mutate({
              uid: aliasTarget?.uid ?? "",
              alias: aliasValue.trim(),
            })
          }
        />
      </Modal>
    </div>
  );
}
