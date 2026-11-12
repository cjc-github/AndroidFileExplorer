import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Empty,
  Input,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableProps } from "antd";
import { FolderOpenOutlined, SearchOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";

import { getDevicePage } from "@/api";
import { extractErrorMessage } from "@/api/request";
import type { Device } from "@/api/types";
import { deviceStatusColor, deviceStatusLabel } from "@/api/status";
import { useRefreshInterval } from "@/hooks/useRefreshInterval";
import { formatTime } from "@/utils/format";

/**
 * The entry point of the file browser.
 *
 * Browsing files starts with choosing a device, so the device list is shown
 * here instead of hiding the feature behind a button inside the device table.
 */
export function FileBrowserPickerPage() {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const pollMs = useRefreshInterval();
  const hasSyncedRef = useRef(false);
  const lastFullSyncRef = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedKeyword(keyword.trim()), 300);
    return () => clearTimeout(timer);
  }, [keyword]);

  const devicesQuery = useQuery({
    queryKey: ["devices", page, pageSize, debouncedKeyword],
    queryFn: () => {
      // Full discovery once, then the cheap list poll: the picker used to
      // re-probe every machine every 5 seconds (and trigger auto-root).
      const now = Date.now();
      const fullSync =
        !hasSyncedRef.current || now - lastFullSyncRef.current >= 30_000;
      if (fullSync) {
        hasSyncedRef.current = true;
        lastFullSyncRef.current = now;
      }
      return getDevicePage({
        page,
        pageSize,
        keyword: debouncedKeyword,
        refresh: fullSync,
      });
    },
    refetchInterval: pollMs,
    placeholderData: (previous) => previous,
  });

  const devices = devicesQuery.data?.items ?? [];

  const openBrowser = (device: Device) => {
    navigate({ to: "/devices/$uid/files", params: { uid: device.uid } });
  };

  const columns: TableProps<Device>["columns"] = [
    {
      title: "设备",
      key: "device",
      render: (_, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>
            {record.alias ?? record.model ?? record.serial}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.serial}
            {record.brand ? ` · ${record.brand}` : ""}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "唯一标识 UID",
      dataIndex: "uid",
      key: "uid",
      width: 200,
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
      title: "最后在线",
      dataIndex: "last_seen",
      key: "last_seen",
      width: 180,
      render: (value: string | null) => formatTime(value),
    },
    {
      title: "操作",
      key: "actions",
      width: 140,
      render: (_, record) => (
        <Button
          type="primary"
          size="small"
          icon={<FolderOpenOutlined />}
          disabled={record.status !== "online"}
          onClick={() => openBrowser(record)}
        >
          浏览文件
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 12 }}>
        <Space>
          <Typography.Text strong>文件浏览</Typography.Text>
          <Typography.Text type="secondary">先选择要浏览文件的设备</Typography.Text>
        </Space>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="按型号 / 序列号 / UID 过滤；也支持名称 / 品牌"
          style={{ width: 280 }}
          value={keyword}
          onChange={(event) => {
            setKeyword(event.target.value);
            setPage(1);
          }}
        />
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

      <Table
        rowKey="uid"
        size="small"
        dataSource={devices}
        columns={columns}
        loading={devicesQuery.isPending}
        pagination={{
          current: page,
          pageSize,
          total: devicesQuery.data?.total ?? 0,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50, 100],
          showTotal: (total, range) =>
            range[0] === range[1]
              ? `第 ${range[0]} 台 / 共 ${total} 台`
              : `${range[0]}-${range[1]} / 共 ${total} 台`,
          onChange: (nextPage, nextPageSize) => {
            if (nextPageSize !== pageSize) {
              setPageSize(nextPageSize);
              setPage(1);
              return;
            }
            setPage(nextPage);
          },
        }}
        locale={{
          emptyText: (
            <Empty
              description={
                keyword.trim()
                  ? "没有匹配的设备"
                  : "没有可浏览的设备。请先在「远程机器」登记机器并确保其上有设备在线"
              }
            />
          ),
        }}
      />
    </div>
  );
}
