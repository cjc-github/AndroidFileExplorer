import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
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
import { useState } from "react";

import { getDeviceList } from "@/api";
import type { Device } from "@/api/types";
import { deviceStatusColor, deviceStatusLabel } from "@/api/status";
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

  const devicesQuery = useQuery({
    queryKey: ["devices"],
    queryFn: () => getDeviceList(true),
    refetchInterval: 5000,
  });

  const devices = devicesQuery.data ?? [];
  const filtered = keyword.trim()
    ? devices.filter((device) =>
        [device.model, device.serial, device.uid]
          .filter(Boolean)
          .some((field) => field!.toLowerCase().includes(keyword.trim().toLowerCase())),
      )
    : devices;

  const openBrowser = (device: Device) => {
    navigate({ to: "/devices/$uid/files", params: { uid: device.uid } });
  };

  const columns: TableProps<Device>["columns"] = [
    {
      title: "设备",
      key: "device",
      render: (_, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{record.model ?? record.serial}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.serial}
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
          placeholder="按型号 / 序列号 / UID 过滤"
          style={{ width: 280 }}
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
      </Space>

      <Table
        rowKey="uid"
        size="small"
        dataSource={filtered}
        columns={columns}
        loading={devicesQuery.isFetching}
        pagination={false}
        locale={{
          emptyText: (
            <Empty description="没有可浏览的设备。请先在「远程机器」登记机器并确保其上有设备在线" />
          ),
        }}
      />
    </div>
  );
}
