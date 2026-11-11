import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  App,
  Badge,
  Button,
  Form,
  Input,
  InputNumber,
  Radio,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableProps } from "antd";
import { ApiOutlined, DeleteOutlined, PlusOutlined } from "@ant-design/icons";

import {
  createEngine,
  deleteEngine,
  getEngineList,
  testEngine,
} from "@/api";
import type { Engine } from "@/api/types";
import { formatTime } from "@/utils/format";

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

export function MachinesPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const connectionMode = Form.useWatch("connection", form) ?? "agent";

  const enginesQuery = useQuery({
    queryKey: ["engines"],
    queryFn: () => getEngineList(),
    refetchInterval: 5000,
  });

  const createMutation = useMutation({
    mutationFn: createEngine,
    onSuccess: (engine, _vars, context) => {
      void context;
      queryClient.invalidateQueries({ queryKey: ["engines"] });
      setModalOpen(false);
      form.resetFields();
      message.success(`机器「${engine.name || engine.host}」已登记`);
    },
    onError: (error: Error) => message.error(error.message || "登记失败"),
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

  const columns: TableProps<Engine>["columns"] = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
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
          </div>
        );
      },
    },
    {
      title: "地址",
      key: "address",
      render: (_, record) => (
        <Typography.Text
          type="secondary"
          copyable
          style={{ fontFamily: "monospace", fontSize: 12 }}
        >
          {record.host}:{record.port}
        </Typography.Text>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (value: string) => (
        <Badge
          status={(engineStatusColor[value] ?? "default") as "success" | "default" | "error" | "warning"}
          text={engineStatusLabel[value] ?? value}
        />
      ),
    },
    {
      title: "设备数",
      dataIndex: "device_count",
      key: "device_count",
      width: 90,
      render: (value: number) => (
        <Typography.Text strong={value > 0} type={value > 0 ? undefined : "secondary"}>
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "最后探测",
      dataIndex: "last_seen",
      key: "last_seen",
      width: 180,
      render: (value: string | null) => formatTime(value),
    },
    {
      title: "错误信息",
      dataIndex: "last_error",
      key: "last_error",
      render: (value: string | null) =>
        value ? (
          <Tooltip title={value}>
            <Typography.Text
              type="danger"
              style={{ fontSize: 12 }}
              ellipsis
            >
              {value}
            </Typography.Text>
          </Tooltip>
        ) : (
          "-"
        ),
    },
    {
      title: "操作",
      key: "actions",
      width: 200,
      render: (_, record) => (
        <Space size={4}>
          <Button
            size="small"
            icon={<ApiOutlined />}
            loading={testMutation.isPending}
            onClick={() => testMutation.mutate(record.id)}
          >
            测试连接
          </Button>
          <Popconfirm
            title="删除该机器？"
            description="该机器下的设备记录也会一并删除"
            onConfirm={() => deleteMutation.mutate(record.id)}
            disabled={record.kind === "local"}
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              disabled={record.kind === "local"}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 12 }}>
        <Space>
          <Typography.Text strong>远程机器连接配置</Typography.Text>
          <Typography.Text type="secondary">
            本机与远程 agent_server 机器，设备从这里被发现
          </Typography.Text>
        </Space>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setModalOpen(true)}
        >
          新增机器
        </Button>
      </Space>

      <Table
        rowKey="id"
        size="small"
        dataSource={enginesQuery.data ?? []}
        columns={columns}
        loading={enginesQuery.isFetching}
        pagination={false}
        locale={{ emptyText: "暂无机器，点击右上角新增" }}
      />

      <Modal
        title="新增远程机器"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending}
        okText="登记并探测"
        cancelText="取消"
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => {
            const isSsh = values.connection === "ssh";
            createMutation.mutate({
              ...values,
              kind: isSsh ? "ssh" : "remote",
              port: isSsh ? (values.ssh_port ?? 22) : values.port,
            });
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
            <Input placeholder="例如：192.168.114.130" />
          </Form.Item>
          <Form.Item name="connection" label="连接方式" initialValue="agent">
            <Radio.Group
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
                <InputNumber min={1} max={65535} style={{ width: "100%" }} placeholder="12345" />
              </Form.Item>
              <Form.Item
                name="token"
                label="共享密钥"
                extra="设备侧 agent 的认证密钥；留空表示不启用认证"
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
                extra="留空时使用本机已配置的 SSH 密钥免密登录"
              >
                <Input.Password placeholder="用户密码；内网环境请知悉保管范围" />
              </Form.Item>
              <Form.Item name="adb_path" label="adb 路径（可选）">
                <Input placeholder="adb 不在 PATH 时填写，例如 /usr/lib/android-sdk/platform-tools/adb" />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
}
