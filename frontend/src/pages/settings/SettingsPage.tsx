import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Card, Form, InputNumber, Switch, Typography } from "antd";

import { getSettings, updateSettings } from "@/api";

export function SettingsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: getSettings,
  });

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
    <Card
      title="平台设置"
      style={{ maxWidth: 620 }}
      loading={settingsQuery.isLoading}
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        保存后立即生效，无需重启后端。
      </Typography.Paragraph>

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
            extra="设备资源页运行状态（CPU / 内存）的自动刷新周期，1–60 秒"
            rules={[{ required: true, message: "请输入刷新间隔" }]}
          >
            <InputNumber min={1} max={60} style={{ width: 160 }} />
          </Form.Item>

          <Form.Item
            name="auto_sync_seconds"
            label="自动同步间隔（秒）"
            extra="后台自动发现设备的周期；0 表示只在点击「同步」按钮时刷新"
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
  );
}
