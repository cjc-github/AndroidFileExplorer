import { useEffect, useState } from "react";
import { useParams, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Breadcrumb,
  Button,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import type { TableProps } from "antd";
import {
  ArrowLeftOutlined,
  CloudDownloadOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  EditOutlined,
  FileOutlined,
  FolderAddOutlined,
  FolderOutlined,
  ReloadOutlined,
  SafetyOutlined,
  SwapOutlined,
} from "@ant-design/icons";

import {
  getDeviceDetail,
  getDeviceFileList,
  mkdirOnDevice,
  removeOnDevice,
  renameOnDevice,
  searchDeviceFiles,
  tryRootDevice,
  unRootDevice,
  uploadDeviceFile,
} from "@/api";
import { extractErrorMessage, isPermissionError } from "@/api/request";
import type { FileItem } from "@/api/types";
import { formatBytes, formatTime } from "@/utils/format";

/** Normalize a user typed path into an absolute POSIX path. */
function normalizePath(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) return "/";
  const withSlash = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
  const segments = withSlash.split("/").filter(Boolean);
  return `/${segments.join("/")}`;
}

export function FileBrowserPage() {
  const { uid } = useParams({ from: "/devices/$uid/files" });
  const navigate = useNavigate();
  const { message } = App.useApp();
  const queryClient = useQueryClient();

  const [path, setPath] = useState("/");
  const [pathInput, setPathInput] = useState("/");
  const [keyword, setKeyword] = useState("");
  const [searchMode, setSearchMode] = useState(false);

  const [mkdirOpen, setMkdirOpen] = useState(false);
  const [mkdirName, setMkdirName] = useState("");
  const [renameTarget, setRenameTarget] = useState<FileItem | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const deviceQuery = useQuery({
    queryKey: ["device-detail", uid],
    queryFn: () => getDeviceDetail(uid),
    enabled: Boolean(uid),
    refetchInterval: 3000,
  });

  const device = deviceQuery.data;
  const deviceOnline = device?.status === "online";
  // While the device detail is still loading the state is UNKNOWN, not
  // "offline": disabling the controls in that window would make the page
  // unusable for a moment and hide the real reason.
  const deviceReady = device !== undefined;
  const deviceOffline = deviceReady && !deviceOnline;

  const filesQuery = useQuery({
    queryKey: ["device-files", uid, path, searchMode ? keyword : ""],
    queryFn: () =>
      searchMode
        ? searchDeviceFiles(uid, keyword, path)
        : getDeviceFileList(uid, path),
    enabled: Boolean(uid) && deviceOnline,
    retry: false,
  });

  // Keep the path box in sync when the user navigates by clicking.
  useEffect(() => {
    setPathInput(path);
  }, [path]);

  const pullToBrowser = (filePath: string) => {
    // The server pulls the file into its staging directory and answers with
    // the bytes as an attachment; an anchor click hands it to the browser.
    const name = filePath.split("/").pop() || "download.bin";
    const url = `/api/v1/devices/${uid}/files/download?path=${encodeURIComponent(filePath)}`;
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    message.info(`正在下载 ${name}`);
  };

  const uploadMutation = useMutation({
    mutationFn: (payload: { path: string; file: File }) =>
      uploadDeviceFile(uid, payload.path, payload.file),
    onSuccess: (result) => message.success(`上传成功：${result.message || result.path}`),
    onError: (error: Error) => message.error(error.message || "上传失败"),
  });

  const unrootMutation = useMutation({
    mutationFn: () => unRootDevice(uid),
    onSuccess: (updated) => {
      queryClient.setQueryData(["device-detail", uid], updated);
      queryClient.invalidateQueries({ queryKey: ["device-files", uid] });
      message.success("已恢复为普通用户模式，受保护目录将重新不可访问");
    },
    onError: (error: Error) => message.error(error.message || "取消 root 失败"),
  });

  const rootMutation = useMutation({
    mutationFn: () => tryRootDevice(uid),
    onSuccess: (updated) => {
      queryClient.setQueryData(["device-detail", uid], updated);
      // adbd restarted: everything on the device changed, reload the listing.
      queryClient.invalidateQueries({ queryKey: ["device-files", uid] });
      message.success(
        updated.is_rooted
          ? "已以 root 模式重启 adbd，现在可以访问受保护目录"
          : "已执行 adb root，但未确认取得 root 身份",
      );
    },
    onError: (error: Error) => message.error(error.message || "尝试 root 失败"),
  });

  const goToPath = (target: string) => {
    const normalized = normalizePath(target);
    setSearchMode(false);
    setKeyword("");
    setPath(normalized);
  };

  const refreshFiles = () => {
    queryClient.invalidateQueries({ queryKey: ["device-files", uid] });
  };

  const mkdirMutation = useMutation({
    mutationFn: (name: string) => mkdirOnDevice(uid, `${path.replace(/\/$/, "")}/${name}`),
    onSuccess: () => {
      message.success("目录已创建");
      setMkdirOpen(false);
      setMkdirName("");
      refreshFiles();
    },
    onError: (error: Error) => message.error(error.message || "新建目录失败"),
  });

  const renameMutation = useMutation({
    mutationFn: (payload: { source: string; target: string }) =>
      renameOnDevice(uid, payload.source, payload.target),
    onSuccess: () => {
      message.success("重命名成功");
      setRenameTarget(null);
      setRenameValue("");
      refreshFiles();
    },
    onError: (error: Error) => message.error(error.message || "重命名失败"),
  });

  const removeMutation = useMutation({
    mutationFn: (payload: { path: string; recursive: boolean }) =>
      removeOnDevice(uid, payload.path, payload.recursive),
    onSuccess: () => {
      message.success("删除成功");
      refreshFiles();
    },
    onError: (error: Error) => message.error(error.message || "删除失败"),
  });

  const parentOf = (target: string) => target.slice(0, target.lastIndexOf("/") || 0);

  const submitRename = () => {
    if (!renameTarget || !renameValue.trim()) return;
    renameMutation.mutate({
      source: renameTarget.path,
      target: `${parentOf(renameTarget.path)}/${renameValue.trim()}`,
    });
  };

  const columns: TableProps<FileItem>["columns"] = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      render: (_, record) => (
        <Space>
          {record.is_dir ? <FolderOutlined /> : <FileOutlined />}
          <Typography.Link
            onClick={() => {
              if (!record.is_dir) return;
              goToPath(record.path);
            }}
          >
            {record.name}
          </Typography.Link>
          {record.is_link ? <Tag>链接</Tag> : null}
        </Space>
      ),
    },
    {
      title: "权限",
      dataIndex: "permissions",
      key: "permissions",
      width: 140,
      render: (value: string | null) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {value ?? "-"}
        </Typography.Text>
      ),
    },
    {
      title: "所属",
      key: "owner",
      width: 150,
      render: (_, record) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {record.owner ?? "-"}:{record.group ?? "-"}
        </Typography.Text>
      ),
    },
    {
      title: "修改时间",
      dataIndex: "modified_at",
      key: "modified_at",
      width: 180,
      render: (value: string | null) => formatTime(value),
    },
    {
      title: "大小",
      dataIndex: "size",
      key: "size",
      width: 110,
      render: (value: number | null) => formatBytes(value),
    },
    {
      title: "操作",
      key: "actions",
      width: 230,
      render: (_, record) => (
        <Space size={4}>
          <Button
            size="small"
            icon={<CloudDownloadOutlined />}
            disabled={record.is_dir}
            onClick={() => pullToBrowser(record.path)}
          >
            下载
          </Button>
          <Tooltip title="重命名">
            <Button
              size="small"
              icon={<EditOutlined />}
              onClick={() => {
                setRenameTarget(record);
                setRenameValue(record.name);
              }}
            >
              重命名
            </Button>
          </Tooltip>
          <Popconfirm
            title={`删除 ${record.name}？`}
            description={record.is_dir ? "目录将被递归删除" : undefined}
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={() =>
              removeMutation.mutate({ path: record.path, recursive: record.is_dir })
            }
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const data = filesQuery.data;
  const errorDetail = filesQuery.error
    ? extractErrorMessage(filesQuery.error)
    : null;
  const permissionDenied = filesQuery.error
    ? isPermissionError(filesQuery.error)
    : false;

  return (
    <div>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 12 }}>
        <Space>
          <Button icon={<SwapOutlined />} onClick={() => navigate({ to: "/files" })}>
            切换设备
          </Button>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate({ to: "/devices" })}>
            设备列表
          </Button>
          <Typography.Text strong>文件浏览</Typography.Text>
          <Typography.Text type="secondary">
            {device ? `${device.model ?? device.serial} · ${device.serial}` : uid}
          </Typography.Text>
          {device ? (
            <Tag color={deviceOnline ? "success" : "default"}>
              {deviceOnline ? "在线" : device.status}
            </Tag>
          ) : null}
        </Space>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => filesQuery.refetch()}
            disabled={deviceOffline}
          >
            刷新
          </Button>
          {device?.is_rooted ? (
            <Tooltip title="以 adb unroot 恢复为普通用户">
              <Button
                icon={<SafetyOutlined />}
                loading={unrootMutation.isPending}
                disabled={deviceOffline}
                onClick={() => unrootMutation.mutate()}
              >
                取消 root
              </Button>
            </Tooltip>
          ) : (
            <Tooltip title="以 adb root 重启 adbd，之后可访问需要 root 的目录">
              <Button
                icon={<SafetyOutlined />}
                loading={rootMutation.isPending}
                disabled={deviceOffline}
                onClick={() => rootMutation.mutate()}
              >
                尝试 root
              </Button>
            </Tooltip>
          )}
          <Input.Search
            placeholder="按文件名搜索"
            allowClear
            style={{ width: 200 }}
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onSearch={(value) => {
              setSearchMode(Boolean(value.trim()));
              setPath("/");
            }}
          />
          <Upload
            showUploadList={false}
            beforeUpload={(file) => {
              const target = `${path.replace(/\/$/, "")}/${file.name}`;
              uploadMutation.mutate({ path: target, file });
              return false;
            }}
          >
            <Button icon={<CloudUploadOutlined />} disabled={deviceOffline}>
              上传到当前目录
            </Button>
          </Upload>
          <Button
            icon={<FolderAddOutlined />}
            disabled={deviceOffline}
            onClick={() => {
              setMkdirName("");
              setMkdirOpen(true);
            }}
          >
            新建目录
          </Button>
        </Space>
      </Space>

      <Space.Compact style={{ width: "100%", marginBottom: 8 }}>
        <Input
          style={{ width: 420 }}
          placeholder="直接输入路径，例如 /data/local/tmp"
          value={pathInput}
          onChange={(event) => setPathInput(event.target.value)}
          onPressEnter={() => goToPath(pathInput)}
          disabled={deviceOffline}
        />
        <Button type="primary" onClick={() => goToPath(pathInput)} disabled={deviceOffline}>
          跳转
        </Button>
        <Button onClick={() => goToPath("/")} disabled={deviceOffline}>
          根目录
        </Button>
      </Space.Compact>

      <Breadcrumb
        className="path-breadcrumb"
        items={[
          {
            title: <span className="crumb-link">根目录</span>,
            onClick: () => goToPath("/"),
          },
          ...(data?.parts ?? []).map((part) => ({
            title: <span className="crumb-link">{part.name}</span>,
            onClick: () => goToPath(part.path),
          })),
          ...(searchMode
            ? [
                {
                  title: (
                    <span>
                      搜索 <Typography.Text strong>{keyword}</Typography.Text> 结果
                    </span>
                  ),
                },
              ]
            : []),
        ]}
      />

      {data?.truncated ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="结果过多，已截断"
          description="请缩小搜索范围（指定更具体的目录或关键字）以获得完整结果。"
        />
      ) : null}

      {errorDetail ? (
        <Alert
          type={permissionDenied ? "warning" : "error"}
          showIcon
          style={{ marginBottom: 12 }}
          message={permissionDenied ? "权限不足，无法访问该目录" : "读取目录失败"}
          description={
            <Space direction="vertical" size={4}>
              <Typography.Text style={{ fontSize: 12 }}>{errorDetail}</Typography.Text>
              {permissionDenied ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  该路径需要 root 权限。可以直接在上方路径框输入更深层的可访问路径
                  （例如 /data/local/tmp），或改用已 root 的设备。
                </Typography.Text>
              ) : null}
            </Space>
          }
          action={
            <Button size="small" onClick={() => filesQuery.refetch()}>
              重试
            </Button>
          }
        />
      ) : null}

      <Modal
        className="afe-modal-mkdir"
        title="新建目录"
        open={mkdirOpen}
        onCancel={() => setMkdirOpen(false)}
        onOk={() => mkdirMutation.mutate(mkdirName.trim())}
        confirmLoading={mkdirMutation.isPending}
        okText="创建"
        cancelText="取消"
        okButtonProps={{ disabled: !mkdirName.trim() }}
      >
        <Input
          placeholder="目录名"
          value={mkdirName}
          onChange={(event) => setMkdirName(event.target.value)}
          onPressEnter={() => mkdirMutation.mutate(mkdirName.trim())}
        />
      </Modal>

      <Modal
        className="afe-modal-rename"
        title="重命名"
        open={Boolean(renameTarget)}
        onCancel={() => setRenameTarget(null)}
        onOk={submitRename}
        confirmLoading={renameMutation.isPending}
        okText="保存"
        cancelText="取消"
        okButtonProps={{ disabled: !renameValue.trim() }}
      >
        <Input
          placeholder="新名称"
          value={renameValue}
          onChange={(event) => setRenameValue(event.target.value)}
          onPressEnter={submitRename}
        />
      </Modal>

      {deviceOffline ? (
        <Empty description="设备当前不在线，无法浏览文件" />
      ) : (
        <Table
          rowKey="path"
          size="small"
          dataSource={data?.items ?? []}
          columns={columns}
          loading={filesQuery.isFetching || deviceQuery.isLoading}
          pagination={false}
          locale={{ emptyText: <Empty description="该目录为空" /> }}
        />
      )}
    </div>
  );
}
