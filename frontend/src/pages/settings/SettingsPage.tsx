import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  InputNumber,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";

import { getSettings, updateSettings } from "@/api";
import { extractErrorMessage } from "@/api/request";
import { useEnvironmentCheck } from "@/hooks/useEnvironmentCheck";

export function SettingsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: getSettings,
  });
  const environmentQuery = useEnvironmentCheck();

  const saveMutation = useMutation({
    mutationFn: updateSettings,
    onSuccess: (saved) => {
      queryClient.setQueryData(["settings"], saved);
      message.success("设置已保存，立即生效");
    },
    onError: (error: Error) => message.error(error.message || "保存失败"),
  });

  const [form] = Form.useForm();
  const values = settingsQuery.data;

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card
        title="平台设置"
        loading={settingsQuery.isLoading}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          保存后立即生效，无需重启后端。
        </Typography.Paragraph>

        {settingsQuery.isError ? (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            message="设置加载失败"
            description={extractErrorMessage(settingsQuery.error)}
            action={
              <Button size="small" onClick={() => settingsQuery.refetch()}>
                重试
              </Button>
            }
          />
        ) : null}

        {values ? (
          <Form
            form={form}
            layout="vertical"
            initialValues={values}
            onFinish={(payload) => saveMutation.mutate(payload)}
          >
            <Form.Item
              name="sync_interval_seconds"
              label="设备列表刷新间隔（秒）"
              extra="设备资源页、文件选择页和文件浏览页的设备状态刷新周期，1–60 秒"
              rules={[{ required: true, message: "请输入刷新间隔" }]}
            >
              <InputNumber min={1} max={60} style={{ width: 160 }} />
            </Form.Item>

            <Form.Item
              name="auto_sync_seconds"
              label="自动同步间隔（秒）"
              extra="后台自动发现设备的周期；0 表示后台不再周期探测（设备页/文件选择页打开期间仍会做恢复性同步，也可点击「同步」手动刷新）"
              rules={[{ required: true, message: "请输入自动同步间隔" }]}
            >
              <InputNumber min={0} max={86400} style={{ width: 160 }} />
            </Form.Item>

            <Form.Item
              name="auto_root"
              label="自动尝试 root"
              valuePropName="checked"
              extra="每次同步后，自动对未 root 的在线设备执行 adb root；生产版本设备会被拒绝并记录日志"
            >
              <Switch />
            </Form.Item>

            <Button type="primary" loading={saveMutation.isPending} htmlType="submit">
              保存设置
            </Button>
          </Form>
        ) : null}
      </Card>

      <Card
        title="环境自检"
        loading={environmentQuery.isLoading}
        extra={
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={environmentQuery.isFetching}
            onClick={() => environmentQuery.refetch()}
          >
            重新检查
          </Button>
        }
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          检查 Python、后端依赖、adb、SSH 直连依赖（asyncssh）与数据目录写权限。
          标为「可选」的项只影响对应能力，不阻止其他功能运行。
        </Typography.Paragraph>

        {environmentQuery.isError ? (
          <Alert
            type="error"
            showIcon
            message="环境自检失败"
            description={extractErrorMessage(environmentQuery.error)}
            action={
              <Button size="small" onClick={() => environmentQuery.refetch()}>
                重试
              </Button>
            }
          />
        ) : null}

        {environmentQuery.data ? (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            {environmentQuery.data.checks.map((item) => (
              <div key={item.key}>
                <Space size={8} wrap>
                  <Tag
                    color={
                      item.ok ? "success" : item.required ? "error" : "warning"
                    }
                  >
                    {item.ok ? "正常" : item.required ? "缺失" : "警告"}
                  </Tag>
                  <Typography.Text strong>{item.label}</Typography.Text>
                  {!item.required ? (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      可选
                    </Typography.Text>
                  ) : null}
                </Space>
                <div>
                  <Typography.Text
                    type={item.ok ? "secondary" : "danger"}
                    style={{ fontSize: 12 }}
                  >
                    {item.message}
                  </Typography.Text>
                </div>
                {!item.ok && item.fix ? (
                  <Typography.Text
                    code
                    copyable
                    style={{
                      display: "block",
                      marginTop: 4,
                      fontSize: 12,
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {item.fix}
                  </Typography.Text>
                ) : null}
              </div>
            ))}
          </Space>
        ) : null}
      </Card>
    </Space>
  );
}
