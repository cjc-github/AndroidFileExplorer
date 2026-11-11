import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Result, Typography } from "antd";

type Props = { children: ReactNode };
type State = { error: Error | null };

/**
 * React error boundary around the routed content.
 *
 * A crash inside one page (bad data, a render bug) must not blank the whole
 * app without a trace: the boundary shows the reason and a way out instead.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Unhandled UI error:", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <Result
        status="error"
        title="页面出错了"
        subTitle={
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {error.message || "未知错误，请刷新重试"}
          </Typography.Text>
        }
        extra={
          <Button type="primary" onClick={() => window.location.reload()}>
            刷新页面
          </Button>
        }
      />
    );
  }
}
