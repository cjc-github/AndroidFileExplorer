import { useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Badge,
  Button,
  Dropdown,
  Empty,
  Form,
  Input,
  InputNumber,
  Radio,
  Modal,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableProps } from "antd";
import {
  ApiOutlined,
  CloudServerOutlined,
  ClusterOutlined,
  DeleteOutlined,
  EditOutlined,
  ExclamationCircleOutlined,
  FileSearchOutlined,
  MinusOutlined,
  MoreOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyOutlined,
} from "@ant-design/icons";

import {
  connectDevice,
  createEngine,
  deleteEngine,
  disconnectDevice,
  getDevicePage,
  getEngineList,
  testEngine,
  tryRootDevice,
  unRootDevice,
  updateDeviceAlias,
  updateEngine,
} from "@/api";
import { extractErrorMessage } from "@/api/request";
import { deviceStatusColor, deviceStatusLabel } from "@/api/status";
import type { Device, Engine } from "@/api/types";
import { formatTime } from "@/utils/format";
import { useFillHeight } from "@/hooks/useFillHeight";

const KIND_META: Record<
  string,
  { label: string; color: string | undefined; hint: string }
> = {
  local: { label: "本机 adb", color: undefined, hint: "本机 adb server" },
  remote: { label: "agent", color: "blue", hint: "MDD agent 通道" },
  ssh: { label: "SSH 直连", color: "purple", hint: "SSH 直连（零部署）" },
};

const engineStatusLabel: Record<string, string> = {
  connected: "已连接",
  disconnected: "未连接",
  error: "异常",
  unknown: "未探测",
};

const engineStatusColor: Record<string, string> = {
  connected: "success",
  disconnected: "default",
  error: "error",
  unknown: "warning",
};

/** Devices attributed to one machine, shown inside an expanded machine row. */
function MachineDevicesPanel({ engine }: { engine: Engine }) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [aliasTarget, setAliasTarget] = useState<Device | null>(null);
  const [aliasValue, setAliasValue] = useState("");
  const hasSyncedRef = useRef(false);

  const devicesQuery = useQuery({
    queryKey: ["devices", "engine", engine.id],
    queryFn: () => {
      // The machines page never discovers devices by itself, so the first
      // expand re-syncs the registry; later polls use the cheap path.
      const refresh = !hasSyncedRef.current;
      hasSyncedRef.current = true;
      return getDevicePage({
        page: 1,
        pageSize: 200,
        engineId: engine.id,
        refresh,
      });
    },
    refetchInterval: 5000,
  });

  const devices = devicesQuery.data?.items ?? [];
  const total = devicesQuery.data?.total ?? 0;

  const invalidateDevices = () =>
    queryClient.invalidateQueries({ queryKey: ["devices"] });

  const aliasMutation = useMutation({
    mutationFn: (payload: { uid: string; alias: string }) =>
      updateDeviceAlias(payload.uid, payload.alias),
    onSuccess: () => {
      setAliasTarget(null);
      void invalidateDevices();
      message.success("设备名称已保存");
    },
    onError: (error: Error) => message.error(error.message || "保存失败"),
  });

  const rootMutation = useMutation({
    mutationFn: (payload: { uid: string; enable: boolean }) =>
      payload.enable
        ? tryRootDevice(payload.uid)
        : unRootDevice(payload.uid),
    onSuccess: (updated) => {
      void invalidateDevices();
      message.success(
        updated.is_rooted
          ? "已以 root 模式重启 adbd"
          : "已恢复为普通用户模式",
      );
    },
    onError: (error: Error) => message.error(error.message || "操作失败"),
  });

  const connectMutation = useMutation({
    mutationFn: (uid: string) => connectDevice(uid),
    onSuccess: () => {
      void invalidateDevices();
      message.success("连接成功");
    },
    onError: (error: Error) => message.error(error.message || "连接失败"),
  });

  const disconnectMutation = useMutation({
    mutationFn: (uid: string) => disconnectDevice(uid),
    onSuccess: () => {
      void invalidateDevices();
      message.success("已断开");
    },
    onError: (error: Error) => message.error(error.message || "断开失败"),
  });

  const openAlias = (device: Device) => {
    setAliasTarget(device);
    setAliasValue(device.alias ?? device.model ?? device.serial);
  };

  const deviceName = (device: Device) =>
    device.alias ?? device.model ?? device.serial;

  const columns: TableProps<Device>["columns"] = [
    {
      title: "设备",
      key: "device",
      width: 220,
      render: (_, record) => (
        <div className="device-name">
          <Space size={6}>
            <Typography.Link onClick={() => openAlias(record)}>
              <Typography.Text strong>{deviceName(record)}</Typography.Text>
            </Typography.Link>
            <Tooltip title="修改设备名称">
              <EditOutlined
                onClick={() => openAlias(record)}
                style={{ cursor: "pointer", color: "#1668dc", fontSize: 12 }}
              />
            </Tooltip>
            {record.is_rooted ? <Tag color="gold">root</Tag> : null}
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.serial}
            {record.brand ? ` · ${record.brand}` : ""}
          </Typography.Text>
        </div>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (value: Device["status"]) => (
        <Tag color={deviceStatusColor[value] ?? "default"}>
          {deviceStatusLabel[value] ?? value}
        </Tag>
      ),
    },
    {
      title: "系统",
      key: "system",
      width: 150,
      render: (_, record) => (
        <Typography.Text style={{ fontSize: 12 }}>
          {record.android_version
            ? `Android ${record.android_version}${
                record.api_level ? ` (API ${record.api_level})` : ""
              }`
            : "-"}
        </Typography.Text>
      ),
    },
    {
      title: "最后在线",
      dataIndex: "last_seen",
      key: "last_seen",
      width: 160,
      render: (value: string | null) => formatTime(value),
    },
    {
      title: "操作",
      key: "actions",
      width: 200,
      render: (_, record) => {
        const online = record.status === "online";
        const rootPending =
          rootMutation.isPending && rootMutation.variables?.uid === record.uid;
        const connectPending =
          connectMutation.isPending && connectMutation.variables === record.uid;
        const disconnectPending =
          disconnectMutation.isPending &&
          disconnectMutation.variables === record.uid;
        const menuItems = [
          { key: "rename", icon: <EditOutlined />, label: "修改名称" },
          {
            key: "root",
            icon: <SafetyOutlined />,
            label: record.is_rooted ? "取消 root" : "尝试 root",
            disabled: !online || rootPending,
          },
          ...(record.is_network_device
            ? [
                { key: "connect", label: "连接", disabled: connectPending },
                {
                  key: "disconnect",
                  label: "断开",
                  danger: true,
                  disabled: disconnectPending,
                },
              ]
            : []),
        ];
        return (
          <Space size={4}>
            <Tooltip title="浏览该设备的文件目录">
              <Button
                type="primary"
                ghost
                size="small"
                icon={<FileSearchOutlined />}
                disabled={!online}
                onClick={() =>
                  navigate({
                    to: "/devices/$uid/files",
                    params: { uid: record.uid },
                  })
                }
              >
                浏览文件
              </Button>
            </Tooltip>
            <Dropdown
              menu={{
                items: menuItems,
                onClick: ({ key }) => {
                  if (key === "rename") openAlias(record);
                  if (key === "root")
                    rootMutation.mutate({
                      uid: record.uid,
                      enable: !record.is_rooted,
                    });
                  if (key === "connect") connectMutation.mutate(record.uid);
                  if (key === "disconnect")
                    disconnectMutation.mutate(record.uid);
                },
              }}
            >
              <Tooltip title="更多操作">
                <Button size="small" icon={<MoreOutlined />} />
              </Tooltip>
            </Dropdown>
          </Space>
        );
      },
    },
  ];

  return (
    <div className="machine-devices">
      <div className="machine-devices-head">
        <div className="machine-devices-title">
          <span className="machine-devices-icon">
            <ClusterOutlined />
          </span>
          <div>
            <Typography.Text strong>该机器上的设备</Typography.Text>
            <div className="machine-devices-sub">
              名称、root 与连接状态可直接在这里调整
            </div>
          </div>
          <Tag color={total > 0 ? "blue" : undefined}>{total} 台</Tag>
          {total > devices.length ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              仅显示前 {devices.length} 台
            </Typography.Text>
          ) : null}
        </div>
        <Button
          size="small"
          icon={<ReloadOutlined />}
          loading={devicesQuery.isFetching}
          onClick={() => devicesQuery.refetch()}
        >
          刷新
        </Button>
      </div>

      {devicesQuery.isError ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 8 }}
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
        pagination={false}
        scroll={{ x: "max-content" }}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="该机器暂未发现设备，可回到机器行点「测试连接」后重试"
            />
          ),
        }}
      />

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

export function MachinesPage() {
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const { ref, height } = useFillHeight<HTMLDivElement>();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Engine | null>(null);
  const [expandedRowKeys, setExpandedRowKeys] = useState<number[]>([]);
  const [form] = Form.useForm();
  const connectionMode = Form.useWatch("connection", form) ?? "agent";

  const enginesQuery = useQuery({
    queryKey: ["engines"],
    queryFn: () => getEngineList(),
    refetchInterval: 5000,
  });

  const engines = enginesQuery.data ?? [];
  const connectedCount = engines.filter(
    (engine) => engine.status === "connected",
  ).length;
  const disconnectedCount = engines.length - connectedCount;

  const closeModal = () => {
    setModalOpen(false);
    setEditing(null);
    form.resetFields();
  };

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ connection: "agent", ssh_port: 22 });
    setModalOpen(true);
  };

  const openEdit = (engine: Engine) => {
    setEditing(engine);
    form.resetFields();
    form.setFieldsValue({
      name: engine.name || engine.host,
      host: engine.host,
      connection: engine.kind === "ssh" ? "ssh" : "agent",
      port: engine.kind === "ssh" ? undefined : engine.port,
      ssh_port: engine.kind === "ssh" ? engine.ssh_port : 22,
      ssh_username: engine.ssh_username ?? undefined,
      // Secrets are never returned by the API: leave the fields blank to
      // keep the stored value, type a new one to rotate it.
      token: undefined,
      ssh_password: undefined,
      adb_path: engine.adb_path ?? undefined,
    });
    setModalOpen(true);
  };

  const createMutation = useMutation({
    mutationFn: createEngine,
    onSuccess: (engine, _vars, context) => {
      void context;
      queryClient.invalidateQueries({ queryKey: ["engines"] });
      closeModal();
      message.success(`机器「${engine.name || engine.host}」已登记`);
    },
    onError: (error: Error) => message.error(error.message || "登记失败"),
  });

  const updateMutation = useMutation({
    mutationFn: (payload: {
      id: number;
      body: Parameters<typeof updateEngine>[1];
    }) => updateEngine(payload.id, payload.body),
    onSuccess: (engine) => {
      queryClient.invalidateQueries({ queryKey: ["engines"] });
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      closeModal();
      message.success(`机器「${engine.name || engine.host}」配置已保存`);
    },
    onError: (error: Error) => message.error(error.message || "保存失败"),
  });

  const testMutation = useMutation({
    mutationFn: (engineId: number) => testEngine(engineId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["engines"] });
      message.success(`连接成功，探测到 ${result.device_count} 台设备`);
    },
    onError: (error: Error) => message.error(error.message || "连接测试失败"),
  });

  const deleteMutation = useMutation({
    mutationFn: (engineId: number) => deleteEngine(engineId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["engines"] });
      queryClient.invalidateQueries({ queryKey: ["devices"] });
      message.success("机器已删除");
    },
    onError: (error: Error) => message.error(error.message || "删除失败"),
  });

  const confirmDelete = (engine: Engine) => {
    modal.confirm({
      title: "删除该机器？",
      content: "该机器下的设备记录也会一并删除",
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: () => deleteMutation.mutate(engine.id),
    });
  };

  const columns: TableProps<Engine>["columns"] = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      width: 250,
      render: (value: string | null, record) => {
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
            {record.kind === "ssh" ? (
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {record.has_ssh_password
                  ? "已保存 SSH 密码"
                  : "使用本机 SSH 密钥"}
              </Typography.Text>
            ) : null}
          </div>
        );
      },
    },
    {
      title: "地址",
      key: "address",
      width: 190,
      render: (_, record) => (
        <Typography.Text
          type="secondary"
          copyable
          style={{ fontFamily: "ui-monospace, SFMono-Regular, monospace", fontSize: 12 }}
        >
          {record.host}:{record.port}
        </Typography.Text>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 100,
      render: (value: string, record) => (
        <Space size={6}>
          <Badge
            status={(engineStatusColor[value] ?? "default") as "success" | "default" | "error" | "warning"}
            text={engineStatusLabel[value] ?? value}
          />
          {record.last_error ? (
            <Tooltip title={record.last_error}>
              <ExclamationCircleOutlined style={{ color: "#ff4d4f" }} />
            </Tooltip>
          ) : null}
        </Space>
      ),
    },
    {
      title: "设备数",
      dataIndex: "device_count",
      key: "device_count",
      width: 84,
      render: (value: number) => (
        <Tag
          color={value > 0 ? "blue" : undefined}
          style={{
            minWidth: 34,
            marginInlineEnd: 0,
            textAlign: "center",
            borderRadius: 999,
          }}
        >
          {value}
        </Tag>
      ),
    },
    {
      title: "最后探测",
      dataIndex: "last_seen",
      key: "last_seen",
      width: 170,
      render: (value: string | null) => formatTime(value),
    },
    {
      title: "操作",
      key: "actions",
      width: 250,
      render: (_, record) => (
        <Space size={4}>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit(record)}
          >
            编辑
          </Button>
          <Button
            size="small"
            icon={<ApiOutlined />}
            loading={
              testMutation.isPending && testMutation.variables === record.id
            }
            onClick={() => testMutation.mutate(record.id)}
          >
            测试连接
          </Button>
          <Dropdown
            menu={{
              items: [
                {
                  key: "delete",
                  danger: true,
                  icon: <DeleteOutlined />,
                  label: "删除机器",
                  disabled: record.kind === "local",
                },
              ],
              onClick: ({ key }) => {
                if (key === "delete") confirmDelete(record);
              },
            }}
          >
            <Tooltip title="更多操作">
              <Button size="small" icon={<MoreOutlined />} />
            </Tooltip>
          </Dropdown>
        </Space>
      ),
    },
  ];

  return (
    <div className="page-fill">
      <div className="page-hero">
        <div className="page-hero-main">
          <span className="page-hero-icon">
            <CloudServerOutlined />
          </span>
          <div>
            <div className="page-hero-title">远程机器连接配置</div>
            <div className="page-hero-subtitle">
              登记本机与远程 agent / SSH 机器；点行首 + 展开查看并设置设备
            </div>
          </div>
        </div>
        <div className="page-hero-stats">
          <div className="hero-stat">
            <span className="hero-stat-value">{engines.length}</span>
            <span className="hero-stat-label">机器</span>
          </div>
          <div className="hero-stat">
            <span className="hero-stat-value is-success">
              {connectedCount}
            </span>
            <span className="hero-stat-label">已连接</span>
          </div>
          <div className="hero-stat">
            <span
              className={
                disconnectedCount > 0
                  ? "hero-stat-value is-warning"
                  : "hero-stat-value"
              }
            >
              {disconnectedCount}
            </span>
            <span className="hero-stat-label">未连接</span>
          </div>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新增机器
        </Button>
      </div>

      {enginesQuery.isError ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="机器列表加载失败"
          description={extractErrorMessage(enginesQuery.error)}
          action={
            <Button size="small" onClick={() => enginesQuery.refetch()}>
              重试
            </Button>
          }
        />
      ) : null}

      <div ref={ref} className="table-fill machine-table-card">
        <Table
          rowKey="id"
          size="small"
          dataSource={engines}
          columns={columns}
          loading={enginesQuery.isPending}
          sticky
          scroll={{ x: "max-content", y: height }}
          pagination={false}
          expandable={{
            expandedRowKeys,
            onExpandedRowsChange: (keys) =>
              setExpandedRowKeys(keys.map((key) => Number(key))),
            expandedRowRender: (engine) => (
              <MachineDevicesPanel engine={engine} />
            ),
            columnWidth: 52,
            expandIcon: ({ expanded, onExpand, record }) => (
              <Button
                type="text"
                size="small"
                className="machine-expand-btn"
                icon={expanded ? <MinusOutlined /> : <PlusOutlined />}
                onClick={(event) => {
                  event.stopPropagation();
                  onExpand(record, event);
                }}
              />
            ),
          }}
          locale={{ emptyText: "暂无机器，点击右上角新增" }}
        />
      </div>

      <Modal
        title={editing ? "编辑机器" : "新增远程机器"}
        open={modalOpen}
        onCancel={closeModal}
        onOk={() => form.submit()}
        confirmLoading={
          editing ? updateMutation.isPending : createMutation.isPending
        }
        okText={editing ? "保存" : "登记并探测"}
        cancelText="取消"
        forceRender
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => {
            const isSsh = values.connection === "ssh";
            const body = {
              name: values.name,
              host: values.host,
              port: isSsh ? (values.ssh_port ?? 22) : values.port,
              adb_path: values.adb_path ?? undefined,
              ...(isSsh
                ? {
                    ssh_port: values.ssh_port ?? 22,
                    ssh_username: values.ssh_username,
                    // Blank means "keep the stored secret" while editing.
                    ssh_password: values.ssh_password || undefined,
                  }
                : { token: values.token || undefined }),
            };
            if (editing) {
              updateMutation.mutate({ id: editing.id, body });
            } else {
              createMutation.mutate({
                ...body,
                kind: isSsh ? "ssh" : "remote",
              });
            }
          }}
        >
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: "请输入名称" }]}
          >
            <Input placeholder="例如：虚拟机" prefix={<ApiOutlined />} />
          </Form.Item>
          <Form.Item
            name="host"
            label="主机地址"
            rules={[{ required: true, message: "请输入主机地址" }]}
          >
            <Input
              placeholder="例如：192.168.114.130"
              disabled={editing?.kind === "local"}
            />
          </Form.Item>
          <Form.Item name="connection" label="连接方式" initialValue="agent">
            <Radio.Group
              disabled={Boolean(editing)}
              options={[
                { value: "agent", label: "部署 agent（MDD 端口）" },
                { value: "ssh", label: "SSH 直连（无需部署）" },
              ]}
              optionType="button"
              buttonStyle="solid"
            />
          </Form.Item>

          {connectionMode === "agent" ? (
            <>
              <Form.Item
                name="port"
                label="agent 端口"
                rules={[{ required: true, message: "请输入端口" }]}
              >
                <InputNumber
                  min={1}
                  max={65535}
                  style={{ width: "100%" }}
                  placeholder="12345"
                  disabled={editing?.kind === "local"}
                />
              </Form.Item>
              <Form.Item
                name="token"
                label="共享密钥"
                extra={
                  editing
                    ? "留空表示保留现有密钥；填写新值才会替换"
                    : "设备侧 agent 的认证密钥；留空表示不启用认证"
                }
              >
                <Input.Password placeholder="例如：a-long-random-secret" />
              </Form.Item>
            </>
          ) : (
            <>
              <Form.Item
                name="ssh_port"
                label="SSH 端口"
                rules={[{ required: true, message: "请输入 SSH 端口" }]}
              >
                <InputNumber min={1} max={65535} style={{ width: "100%" }} placeholder="22" />
              </Form.Item>
              <Form.Item
                name="ssh_username"
                label="SSH 用户名"
                rules={[{ required: true, message: "请输入用户名" }]}
              >
                <Input placeholder="例如：test" />
              </Form.Item>
              <Form.Item
                name="ssh_password"
                label="SSH 密码"
                extra={
                  editing
                    ? "留空表示保留已保存的密码；填写新值才会替换"
                    : "留空时使用本机已配置的 SSH 密钥免密登录"
                }
              >
                <Input.Password placeholder="用户密码；内网环境请知悉保管范围" />
              </Form.Item>
            </>
          )}
          <Form.Item name="adb_path" label="adb 路径（可选）">
            <Input placeholder="adb 不在 PATH 时填写，例如 /usr/lib/android-sdk/platform-tools/adb" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
