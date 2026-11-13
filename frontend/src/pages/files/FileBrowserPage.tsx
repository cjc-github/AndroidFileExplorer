import { useEffect, useRef, useState, type Key } from "react";
import { useParams, useNavigate, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Breadcrumb,
  Button,
  Dropdown,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Spin,
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
  MoreOutlined,
  ReloadOutlined,
  SafetyOutlined,
  SwapOutlined,
} from "@ant-design/icons";

import {
  changeDevicePermissions,
  copyOnDevice,
  downloadDeviceFile,
  downloadSelectionArchive,
  getDeviceDetail,
  getDeviceFileList,
  mkdirOnDevice,
  previewDeviceFile,
  removeOnDevice,
  renameOnDevice,
  searchDeviceFiles,
  tryRootDevice,
  unRootDevice,
  uploadDeviceFile,
} from "@/api";
import {
  extractErrorMessage,
  extractErrorMessageAsync,
  getApiToken,
  isPermissionError,
} from "@/api/request";
import type { FileItem, FilePreview } from "@/api/types";
import { deviceStatusLabel } from "@/api/status";
import { useRefreshInterval } from "@/hooks/useRefreshInterval";
import { formatBytes, formatTime } from "@/utils/format";

/** Above this size the browser handles the download instead of a Blob. */
const LARGE_DOWNLOAD_BYTES = 100 * 1024 * 1024;

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
  const routeSearch = useSearch({ from: "/devices/$uid/files" });
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const pollMs = useRefreshInterval();

  // The current path (and the active search keyword) live in the URL, so a
  // directory can be shared or restored with the browser back button. The
  // state initialises from the URL and follows it on back/forward.
  const [path, setPath] = useState(() => normalizePath(routeSearch.path ?? "/"));
  const [pathInput, setPathInput] = useState(() => normalizePath(routeSearch.path ?? "/"));
  const [keyword, setKeyword] = useState(routeSearch.q ?? "");
  const [searchMode, setSearchMode] = useState(Boolean(routeSearch.q));
  const [searchScope, setSearchScope] = useState<"current" | "root">("current");
  const [searchLimit, setSearchLimit] = useState(200);

  const [mkdirOpen, setMkdirOpen] = useState(false);
  const [mkdirName, setMkdirName] = useState("");
  const [renameTarget, setRenameTarget] = useState<FileItem | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameDir, setRenameDir] = useState("");
  const [copyTarget, setCopyTarget] = useState<FileItem | null>(null);
  const [copyPath, setCopyPath] = useState("");
  const [permissionTarget, setPermissionTarget] = useState<FileItem | null>(null);
  const [permissionMode, setPermissionMode] = useState("");
  const [permissionOwner, setPermissionOwner] = useState("");
  const [permissionGroup, setPermissionGroup] = useState("");
  const [previewTarget, setPreviewTarget] = useState<FileItem | null>(null);
  const [previewData, setPreviewData] = useState<FilePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewRequestRef = useRef(0);
  const [selectedKeys, setSelectedKeys] = useState<Key[]>([]);

  const [uploading, setUploading] = useState(false);
  const [uploadState, setUploadState] = useState<{
    name: string;
    index: number;
    total: number;
    percent: number;
  } | null>(null);
  const uploadAbortRef = useRef<AbortController | null>(null);
  const uploadQueueRef = useRef<File[]>([]);
  const uploadFlushRef = useRef<number | null>(null);

  const [downloading, setDownloading] = useState(false);
  const [downloadState, setDownloadState] = useState<{
    name: string;
    percent: number;
  } | null>(null);
  const downloadAbortRef = useRef<AbortController | null>(null);

  const deviceQuery = useQuery({
    queryKey: ["device-detail", uid],
    queryFn: () => getDeviceDetail(uid),
    enabled: Boolean(uid),
    refetchInterval: pollMs,
  });

  const device = deviceQuery.data;
  const deviceOnline = device?.status === "online";
  // While the device detail is still loading the state is UNKNOWN, not
  // "offline": disabling the controls in that window would make the page
  // unusable for a moment and hide the real reason.
  const deviceReady = device !== undefined;
  const deviceOffline = deviceReady && !deviceOnline;
  const deviceName = device
    ? (device.alias ?? device.model ?? device.serial)
    : uid;
  const controlsDisabled = deviceOffline || deviceQuery.isError;

  const filesQuery = useQuery({
    queryKey: [
      "device-files",
      uid,
      path,
      searchMode ? `${keyword}:${searchLimit}` : "",
    ],
    queryFn: () =>
      searchMode
        ? searchDeviceFiles(uid, keyword, path, searchLimit)
        : getDeviceFileList(uid, path),
    enabled: Boolean(uid) && deviceOnline,
    retry: false,
  });

  // Keep the path box in sync when the user navigates by clicking.
  useEffect(() => {
    setPathInput(path);
  }, [path]);

  // Leaving the page must not leave a timer or a request running.
  useEffect(
    () => () => {
      if (uploadFlushRef.current !== null) {
        window.clearTimeout(uploadFlushRef.current);
      }
      uploadAbortRef.current?.abort();
      downloadAbortRef.current?.abort();
    },
    [],
  );

  // Browser back/forward: the URL is the source of truth.
  useEffect(() => {
    if (routeSearch.path) setPath(normalizePath(routeSearch.path));
  }, [routeSearch.path]);

  useEffect(() => {
    setKeyword(routeSearch.q ?? "");
    setSearchMode(Boolean(routeSearch.q));
  }, [routeSearch.q]);

  const saveBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    // Give the browser a tick to start the download before revoking.
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  const startDownload = async (record: FileItem) => {
    if (downloading) {
      message.warning("请等待当前下载完成或取消");
      return;
    }
    setDownloading(true);
    setDownloadState({ name: record.name, percent: 0 });
    const controller = new AbortController();
    downloadAbortRef.current = controller;
    try {
      const response = await downloadDeviceFile(uid, record.path, {
        signal: controller.signal,
        onDownloadProgress: (event) => {
          const percent = event.total
            ? Math.round((event.loaded * 100) / event.total)
            : 0;
          setDownloadState((previous) =>
            previous ? { ...previous, percent } : previous,
          );
        },
      });
      saveBlob(response.data, record.name || "download.bin");
      message.success(`已下载 ${record.name}`);
    } catch (error) {
      if (!controller.signal.aborted) {
        message.error(await extractErrorMessageAsync(error));
      }
    } finally {
      downloadAbortRef.current = null;
      setDownloading(false);
      setDownloadState(null);
    }
  };

  const startSelectionDownload = async () => {
    const selected = (filesQuery.data?.items ?? []).filter((item) =>
      selectedKeys.includes(item.path),
    );
    if (selected.length === 0) {
      message.warning("请选择要下载的文件或目录");
      return;
    }
    if (downloading) {
      message.warning("请等待当前下载完成或取消");
      return;
    }
    setDownloading(true);
    setDownloadState({ name: `选中 ${selected.length} 项.zip`, percent: 0 });
    const controller = new AbortController();
    downloadAbortRef.current = controller;
    try {
      const response = await downloadSelectionArchive(
        uid,
        selected.map((item) => item.path),
        {
          signal: controller.signal,
          onDownloadProgress: (event) => {
            const percent = event.total
              ? Math.round((event.loaded * 100) / event.total)
              : 0;
            setDownloadState((previous) =>
              previous ? { ...previous, percent } : previous,
            );
          },
        },
      );
      saveBlob(response.data, "selection.zip");
      message.success(`已打包下载 ${selected.length} 项`);
    } catch (error) {
      if (!controller.signal.aborted) {
        message.error(await extractErrorMessageAsync(error));
      }
    } finally {
      downloadAbortRef.current = null;
      setDownloading(false);
      setDownloadState(null);
    }
  };

  const downloadArchive = (directory: string) => {
    const token = getApiToken();
    const url = `/api/v1/devices/${uid}/files/archive?path=${encodeURIComponent(directory)}${token ? `&token=${encodeURIComponent(token)}` : ""}`;
    const name = directory.replace(/\/$/, "").split("/").pop() || "root";
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${name}.zip`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    message.info("正在服务端打包目录，完成后浏览器会开始下载");
  };

  const downloadViaAnchor = (record: FileItem) => {
    // Very large files stay on the anchor path: buffering them in a Blob
    // would hurt the browser. The prompt says progress is not available.
    const token = getApiToken();
    const url = `/api/v1/devices/${uid}/files/download?path=${encodeURIComponent(record.path)}${token ? `&token=${encodeURIComponent(token)}` : ""}`;
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = record.name || "download.bin";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    message.info(
      `大文件已交给浏览器下载：${record.name}（进度以浏览器为准）`,
    );
  };

  const pullToBrowser = (record: FileItem) => {
    // Unknown size is treated as large: buffering an unbounded file in a
    // Blob is worse than losing the progress display.
    if (record.size === null || record.size > LARGE_DOWNLOAD_BYTES) {
      downloadViaAnchor(record);
      return;
    }
    void startDownload(record);
  };

  const cancelDownload = () => {
    downloadAbortRef.current?.abort();
  };

  const startUpload = async (files: File[], basePath: string) => {
    if (uploading) {
      message.warning("请等待当前上传完成或取消");
      return;
    }
    setUploading(true);
    const failures: string[] = [];
    let succeeded = 0;
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      const controller = new AbortController();
      uploadAbortRef.current = controller;
      setUploadState({
        name: file.name,
        index: index + 1,
        total: files.length,
        percent: 0,
      });
      try {
        const target = `${basePath.replace(/\/$/, "")}/${file.name}`;
        await uploadDeviceFile(uid, target, file, {
          signal: controller.signal,
          onUploadProgress: (event) => {
            const percent = event.total
              ? Math.round((event.loaded * 100) / event.total)
              : 0;
            setUploadState((previous) =>
              previous ? { ...previous, percent } : previous,
            );
          },
        });
        succeeded += 1;
      } catch (error) {
        if (controller.signal.aborted) {
          uploadAbortRef.current = null;
          setUploadState(null);
          setUploading(false);
          refreshFiles();
          message.info("已取消上传");
          return;
        }
        failures.push(`${file.name}：${extractErrorMessage(error)}`);
      } finally {
        uploadAbortRef.current = null;
      }
    }

    setUploadState(null);
    setUploading(false);
    if (succeeded > 0) {
      message.success(`上传完成：${succeeded}/${files.length}`);
    }
    if (failures.length > 0) {
      modal.error({
        title: `部分文件上传失败（${failures.length} 个）`,
        width: 560,
        content: (
          <div style={{ maxHeight: 260, overflow: "auto" }}>
            {failures.map((failure) => (
              <div key={failure} style={{ marginTop: 8 }}>
                <Typography.Text type="danger">{failure}</Typography.Text>
              </div>
            ))}
          </div>
        ),
      });
    }
    refreshFiles();
  };

  const enqueueUpload = (files: File[]) => {
    const existing = new Set((filesQuery.data?.items ?? []).map((item) => item.name));
    const conflicts = files.filter((file) => existing.has(file.name));
    const start = () => {
      void startUpload(files, path);
    };
    if (conflicts.length > 0) {
      modal.confirm({
        title: "目标文件已存在",
        content: `${conflicts.map((file) => file.name).join("、")} 将被覆盖，是否继续？`,
        okText: "覆盖上传",
        cancelText: "取消",
        onOk: start,
      });
      return;
    }
    start();
  };

  // Ant Design calls beforeUpload once per file; a microtask batches a
  // multi-file selection into one overwrite confirmation and one queue.
  const queueUpload = (file: File) => {
    uploadQueueRef.current.push(file);
    if (uploadFlushRef.current !== null) {
      window.clearTimeout(uploadFlushRef.current);
    }
    uploadFlushRef.current = window.setTimeout(() => {
      const batch = uploadQueueRef.current;
      uploadQueueRef.current = [];
      uploadFlushRef.current = null;
      if (batch.length > 0) enqueueUpload(batch);
    }, 0);
  };

  const cancelUpload = () => {
    uploadAbortRef.current?.abort();
    uploadQueueRef.current = [];
  };

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
    setSearchLimit(200);
    setPath(normalized);
    setSelectedKeys([]);
    navigate({
      to: "/devices/$uid/files",
      params: { uid },
      search: { path: normalized },
    });
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

  const copyMutation = useMutation({
    mutationFn: (payload: { source: string; target: string }) =>
      copyOnDevice(uid, payload.source, payload.target),
    onSuccess: () => {
      message.success("复制成功");
      setCopyTarget(null);
      refreshFiles();
    },
    onError: (error: Error) => message.error(error.message || "复制失败"),
  });

  const permissionMutation = useMutation({
    mutationFn: (payload: {
      path: string;
      mode?: string;
      owner?: string;
      group?: string;
    }) => changeDevicePermissions(uid, payload.path, payload),
    onSuccess: () => {
      message.success("权限已修改");
      setPermissionTarget(null);
      refreshFiles();
    },
    onError: (error: Error) => message.error(error.message || "修改权限失败"),
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

  const batchDeleteMutation = useMutation({
    mutationFn: async (targets: FileItem[]) => {
      // Delete in small batches: selecting a whole large directory must not
      // fire hundreds of concurrent shell commands at the device.
      const outcomes: { item: FileItem; ok: boolean; error?: string }[] = [];
      const batchSize = 4;
      for (let index = 0; index < targets.length; index += batchSize) {
        const batch = targets.slice(index, index + batchSize);
        const results = await Promise.allSettled(
          batch.map((item) => removeOnDevice(uid, item.path, item.is_dir)),
        );
        results.forEach((result, offset) => {
          const item = batch[offset];
          if (result.status === "fulfilled") {
            outcomes.push({ item, ok: true });
          } else {
            outcomes.push({
              item,
              ok: false,
              error: extractErrorMessage(result.reason) || "未知原因",
            });
          }
        });
      }
      return outcomes;
    },
    onSuccess: (outcomes) => {
      const failed = outcomes.filter((outcome) => !outcome.ok);
      const succeeded = outcomes.length - failed.length;
      setSelectedKeys(failed.map((outcome) => outcome.item.path));
      if (failed.length === 0) {
        message.success(`已删除 ${succeeded} 项`);
      } else {
        modal.warning({
          title: `批量删除完成：${succeeded}/${outcomes.length} 项成功`,
          width: 560,
          content: (
            <div style={{ maxHeight: 260, overflow: "auto" }}>
              {failed.map((outcome) => (
                <div key={outcome.item.path} style={{ marginTop: 8 }}>
                  <Typography.Text strong>{outcome.item.name}</Typography.Text>
                  <Typography.Text type="danger">：{outcome.error}</Typography.Text>
                </div>
              ))}
            </div>
          ),
        });
      }
      refreshFiles();
    },
    onError: (error: Error) => {
      message.error(error.message || "批量删除失败");
      refreshFiles();
    },
  });

  const parentOf = (target: string) => target.slice(0, target.lastIndexOf("/") || 0);

  const submitRename = () => {
    if (!renameTarget || !renameValue.trim()) return;
    const directory = renameDir.trim()
      ? normalizePath(renameDir)
      : parentOf(renameTarget.path);
    const target = `${directory.replace(/\/$/, "")}/${renameValue.trim()}`;
    renameMutation.mutate({ source: renameTarget.path, target });
  };

  const openCopy = (record: FileItem) => {
    setCopyTarget(record);
    setCopyPath(`${parentOf(record.path)}/${record.name} 副本`);
  };

  const submitCopy = () => {
    if (!copyTarget || !copyPath.trim()) return;
    copyMutation.mutate({
      source: copyTarget.path,
      target: normalizePath(copyPath.trim()),
    });
  };

  const openPreview = async (record: FileItem) => {
    const requestId = ++previewRequestRef.current;
    setPreviewTarget(record);
    setPreviewData(null);
    setPreviewError(null);
    setPreviewLoading(true);
    try {
      const data = await previewDeviceFile(uid, record.path);
      if (requestId !== previewRequestRef.current) return;
      setPreviewData(data);
    } catch (error) {
      if (requestId !== previewRequestRef.current) return;
      setPreviewError(extractErrorMessage(error));
    } finally {
      if (requestId === previewRequestRef.current) setPreviewLoading(false);
    }
  };

  const openPermissions = (record: FileItem) => {
    setPermissionTarget(record);
    setPermissionMode("");
    setPermissionOwner("");
    setPermissionGroup("");
  };

  const submitPermissions = () => {
    if (!permissionTarget) return;
    const mode = permissionMode.trim();
    const owner = permissionOwner.trim();
    const group = permissionGroup.trim();
    if (!mode && !owner && !group) {
      message.warning("至少填写权限模式或所有者");
      return;
    }
    permissionMutation.mutate({
      path: permissionTarget.path,
      mode: mode || undefined,
      owner: owner || undefined,
      group: group || undefined,
    });
  };

  const columns: TableProps<FileItem>["columns"] = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      width: 300,
      sorter: (a, b) => a.name.localeCompare(b.name, "zh-CN"),
      render: (_, record) => {
        const canEnter = record.is_dir || record.is_link;
        return (
          <div>
            <Space>
              {record.is_dir || record.is_link ? (
                <FolderOutlined />
              ) : (
                <FileOutlined />
              )}
              {canEnter ? (
                <Typography.Link onClick={() => goToPath(record.path)}>
                  {record.name}
                </Typography.Link>
              ) : (
                <Typography.Text>{record.name}</Typography.Text>
              )}
              {record.is_link ? (
                <Tooltip title={record.link_target ?? "符号链接"}>
                  <Tag>链接</Tag>
                </Tooltip>
              ) : null}
            </Space>
            {record.link_target ? (
              <Typography.Text
                type="secondary"
                style={{ display: "block", fontSize: 11 }}
                ellipsis={{ tooltip: record.link_target }}
              >
                → {record.link_target}
              </Typography.Text>
            ) : null}
          </div>
        );
      },
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
      sorter: (a, b) => (a.modified_at ?? "").localeCompare(b.modified_at ?? ""),
      render: (value: string | null) => formatTime(value),
    },
    {
      title: "大小",
      dataIndex: "size",
      key: "size",
      width: 110,
      sorter: (a, b) => (a.size ?? -1) - (b.size ?? -1),
      render: (value: number | null) => formatBytes(value),
    },
    {
      title: "操作",
      key: "actions",
      width: 270,
      render: (_, record) => (
        <Space size={4}>
          <Button
            size="small"
            icon={<CloudDownloadOutlined />}
            disabled={record.is_dir}
            loading={downloading && downloadState?.name === record.name}
            onClick={() => pullToBrowser(record)}
          >
            下载
          </Button>
          <Tooltip title="重命名或移动到其它目录">
            <Button
              size="small"
              icon={<EditOutlined />}
              onClick={() => {
                setRenameTarget(record);
                setRenameValue(record.name);
                setRenameDir(parentOf(record.path));
              }}
            >
              重命名
            </Button>
          </Tooltip>
          <Dropdown
            menu={{
              items: [
                { key: "preview", label: "预览", disabled: record.is_dir },
                { key: "copy", label: "复制" },
                { key: "archive", label: "打包下载", disabled: !record.is_dir },
                { key: "permissions", label: "修改权限" },
              ],
              onClick: ({ key }) => {
                if (key === "preview") void openPreview(record);
                if (key === "copy") openCopy(record);
                if (key === "archive") downloadArchive(record.path);
                if (key === "permissions") openPermissions(record);
              },
            }}
          >
            <Tooltip title="更多操作">
              <Button size="small" icon={<MoreOutlined />} />
            </Tooltip>
          </Dropdown>
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
            {device ? `${deviceName} · ${device.serial}` : uid}
          </Typography.Text>
          {device ? (
            <Tag color={deviceOnline ? "success" : "default"}>
              {deviceOnline
                ? "在线"
                : (deviceStatusLabel[device.status] ?? device.status)}
            </Tag>
          ) : null}
        </Space>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            loading={filesQuery.isFetching && !filesQuery.isPending}
            onClick={() => filesQuery.refetch()}
            disabled={controlsDisabled}
          >
            刷新
          </Button>
          {device?.is_rooted ? (
            <Tooltip title="以 adb unroot 恢复为普通用户">
              <Button
                icon={<SafetyOutlined />}
                loading={unrootMutation.isPending}
                disabled={controlsDisabled}
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
                disabled={controlsDisabled}
                onClick={() => rootMutation.mutate()}
              >
                尝试 root
              </Button>
            </Tooltip>
          )}
          <Tooltip title="搜索范围">
            <Select
              size="small"
              style={{ width: 96 }}
              value={searchScope}
              onChange={(value) =>
                setSearchScope(value as "current" | "root")
              }
              options={[
                { value: "current", label: "当前目录" },
                { value: "root", label: "全设备" },
              ]}
            />
          </Tooltip>
          <Input.Search
            placeholder={
              searchScope === "root"
                ? "按文件名搜索（全设备）"
                : "按文件名搜索（当前目录）"
            }
            allowClear
            style={{ width: 200 }}
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onSearch={(value) => {
              const trimmed = value.trim();
              const basePath = searchScope === "root" ? "/" : path;
              setSearchMode(Boolean(trimmed));
              setKeyword(trimmed);
              setSearchLimit(200);
              if (trimmed) {
                setPath(basePath);
                setPathInput(basePath);
              }
              setSelectedKeys([]);
              navigate({
                to: "/devices/$uid/files",
                params: { uid },
                search: trimmed
                  ? { path: basePath, q: trimmed }
                  : { path: basePath },
              });
            }}
          />
          <Tooltip title="把当前目录打包为 zip 下载（大目录请拆分后再下载）">
            <Button
              icon={<CloudDownloadOutlined />}
              disabled={controlsDisabled || searchMode}
              onClick={() => downloadArchive(path)}
            >
              打包目录
            </Button>
          </Tooltip>
          <Upload
            multiple
            showUploadList={false}
            disabled={controlsDisabled || searchMode || uploading}
            beforeUpload={(file) => {
              queueUpload(file as File);
              return false;
            }}
          >
            <Button
              icon={<CloudUploadOutlined />}
              disabled={controlsDisabled || searchMode || uploading}
            >
              {uploading ? "上传中…" : "上传到当前目录"}
            </Button>
          </Upload>
          <Button
            icon={<FolderAddOutlined />}
            disabled={controlsDisabled}
            onClick={() => {
              setMkdirName("");
              setMkdirOpen(true);
            }}
          >
            新建目录
          </Button>
          {selectedKeys.length > 0 ? (
            <Button
              icon={<CloudDownloadOutlined />}
              loading={downloading}
              onClick={() => void startSelectionDownload()}
            >
              下载选中 ({selectedKeys.length})
            </Button>
          ) : null}
          {selectedKeys.length > 0 ? (
            <Popconfirm
              title={`删除选中的 ${selectedKeys.length} 项？`}
              description="目录将被递归删除，操作不可恢复"
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => {
                const items = data?.items ?? [];
                const targets = items.filter((item) =>
                  selectedKeys.includes(item.path),
                );
                if (targets.length === 0) {
                  setSelectedKeys([]);
                  return;
                }
                batchDeleteMutation.mutate(targets);
              }}
            >
              <Button
                danger
                icon={<DeleteOutlined />}
                loading={batchDeleteMutation.isPending}
              >
                删除选中 ({selectedKeys.length})
              </Button>
            </Popconfirm>
          ) : null}
        </Space>
      </Space>

      {deviceQuery.isError ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="设备信息加载失败"
          description={extractErrorMessage(deviceQuery.error)}
          action={
            <Button size="small" onClick={() => deviceQuery.refetch()}>
              重试
            </Button>
          }
        />
      ) : null}

      {uploadState ? (
        <Space style={{ marginBottom: 12 }} wrap>
          <Typography.Text>
            上传 {uploadState.index}/{uploadState.total}：{uploadState.name}
          </Typography.Text>
          <Progress
            percent={uploadState.percent}
            size="small"
            style={{ width: 220 }}
          />
          <Button size="small" onClick={cancelUpload}>
            取消
          </Button>
        </Space>
      ) : null}

      {downloadState ? (
        <Space style={{ marginBottom: 12 }} wrap>
          <Typography.Text>下载：{downloadState.name}</Typography.Text>
          <Progress
            percent={downloadState.percent}
            size="small"
            style={{ width: 220 }}
          />
          <Button size="small" onClick={cancelDownload}>
            取消
          </Button>
        </Space>
      ) : null}

      <Space.Compact style={{ width: "100%", marginBottom: 8 }}>
        <Input
          style={{ width: 260 }}
          placeholder="直接输入路径，例如 /data/local/tmp"
          value={pathInput}
          onChange={(event) => setPathInput(event.target.value)}
          onPressEnter={() => goToPath(pathInput)}
          disabled={controlsDisabled}
        />
        <Button type="primary" onClick={() => goToPath(pathInput)} disabled={controlsDisabled}>
          跳转
        </Button>
        <Button onClick={() => goToPath("/")} disabled={controlsDisabled}>
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

      {data ? (
        <Typography.Text
          type="secondary"
          style={{ display: "block", marginBottom: 8, fontSize: 12 }}
        >
          {searchMode
            ? `搜索到 ${data.items.length} 项`
            : `共 ${data.items.length} 项`}
        </Typography.Text>
      ) : null}

      {data?.truncated ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={
            searchMode
              ? `结果过多，已截断（当前上限 ${searchLimit} 条）`
              : "结果过多，已截断"
          }
          description={
            searchMode
              ? "可以继续提高结果上限，或缩小搜索范围（更具体的目录 / 关键字）。"
              : "请缩小搜索范围（指定更具体的目录或关键字）以获得完整结果。"
          }
          action={
            searchMode ? (
              <Button
                size="small"
                disabled={searchLimit >= 1000}
                onClick={() =>
                  setSearchLimit((current) => Math.min(current + 300, 1000))
                }
              >
                {searchLimit >= 1000 ? "已达上限" : "加载更多"}
              </Button>
            ) : undefined
          }
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
        <Space direction="vertical" size={4} style={{ width: "100%" }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            新名称
          </Typography.Text>
          <Input
            placeholder="新名称"
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
            onPressEnter={submitRename}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            目标目录（可输入其它目录实现移动）
          </Typography.Text>
          <Input
            placeholder="例如 /sdcard/Download"
            value={renameDir}
            onChange={(event) => setRenameDir(event.target.value)}
            onPressEnter={submitRename}
          />
        </Space>
      </Modal>

      <Modal
        title="复制"
        open={Boolean(copyTarget)}
        onCancel={() => setCopyTarget(null)}
        onOk={submitCopy}
        confirmLoading={copyMutation.isPending}
        okText="复制"
        cancelText="取消"
        okButtonProps={{ disabled: !copyPath.trim() }}
      >
        <Space direction="vertical" size={6} style={{ width: "100%" }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            源路径：{copyTarget?.path}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            目标路径
          </Typography.Text>
          <Input
            placeholder="例如 /sdcard/Download/副本"
            value={copyPath}
            onChange={(event) => setCopyPath(event.target.value)}
            onPressEnter={submitCopy}
          />
        </Space>
      </Modal>

      <Modal
        title="修改权限"
        open={Boolean(permissionTarget)}
        onCancel={() => setPermissionTarget(null)}
        onOk={submitPermissions}
        confirmLoading={permissionMutation.isPending}
        okText="保存"
        cancelText="取消"
        okButtonProps={{
          disabled:
            !permissionMode.trim() &&
            !permissionOwner.trim() &&
            !permissionGroup.trim(),
        }}
      >
        <Space direction="vertical" size={6} style={{ width: "100%" }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            路径：{permissionTarget?.path}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            当前权限：{permissionTarget?.permissions ?? "-"} · 所有者：
            {permissionTarget?.owner ?? "-"}:{permissionTarget?.group ?? "-"}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            权限模式（chmod，3~4 位八进制，例如 644）
          </Typography.Text>
          <Input
            placeholder="例如 644"
            value={permissionMode}
            onChange={(event) => setPermissionMode(event.target.value)}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            所有者 / 用户组（chown，可只填一项）
          </Typography.Text>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              placeholder="所有者，例如 shell"
              value={permissionOwner}
              onChange={(event) => setPermissionOwner(event.target.value)}
            />
            <Input
              placeholder="用户组，例如 shell"
              value={permissionGroup}
              onChange={(event) => setPermissionGroup(event.target.value)}
            />
          </Space.Compact>
          <Typography.Text type="warning" style={{ fontSize: 12 }}>
            修改系统目录权限通常需要 root；生产版本设备可能会被拒绝。
          </Typography.Text>
        </Space>
      </Modal>

      <Modal
        title={`预览：${previewTarget?.name ?? ""}`}
        open={Boolean(previewTarget)}
        onCancel={() => setPreviewTarget(null)}
        footer={null}
        width={760}
      >
        {previewLoading ? (
          <div style={{ padding: 32, textAlign: "center" }}>
            <Spin />
          </div>
        ) : previewError ? (
          <Alert type="error" showIcon message="预览失败" description={previewError} />
        ) : previewData?.kind === "text" ? (
          <pre
            style={{
              maxHeight: 520,
              overflow: "auto",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontSize: 12,
              margin: 0,
            }}
          >
            {previewData.content}
          </pre>
        ) : previewData?.kind === "image" ? (
          <div style={{ textAlign: "center" }}>
            <img
              src={`data:${previewData.content_type ?? "image/png"};base64,${previewData.content}`}
              alt={previewTarget?.name ?? "preview"}
              style={{ maxWidth: "100%", maxHeight: 520 }}
            />
          </div>
        ) : (
          <Empty description="二进制文件不支持预览，请直接下载" />
        )}
      </Modal>

      {deviceQuery.isError ? (
        <Empty description="设备信息加载失败，无法浏览文件" />
      ) : deviceOffline ? (
        <Empty description="设备当前不在线，无法浏览文件" />
      ) : (
        <Table
          rowKey="path"
          size="small"
          virtual
          scroll={{ x: 1182, y: 520 }}
          rowSelection={{
            selectedRowKeys: selectedKeys,
            onChange: (keys) => setSelectedKeys(keys),
            preserveSelectedRowKeys: false,
            // Virtual tables default to a much wider selection column;
            // a checkbox only needs 32px.
            columnWidth: 32,
          }}
          dataSource={data?.items ?? []}
          columns={columns}
          loading={filesQuery.isPending || deviceQuery.isPending}
          pagination={false}
          locale={{
            emptyText: (
              <Empty
                description={
                  searchMode ? "没有匹配的文件" : "该目录为空"
                }
              />
            ),
          }}
        />
      )}
    </div>
  );
}
