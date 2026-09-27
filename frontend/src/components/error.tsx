import * as React from "react";
import { Link } from "react-router-dom";
import { CircleAlert, House, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui";

/** 区块级错误态：与 EmptyState 同风格，附「重试」按钮。 */
export function ErrorState({
  icon,
  title = "加载失败",
  desc,
  onRetry,
  action,
  className,
}: {
  icon?: React.ReactNode;
  title?: string;
  desc?: string;
  onRetry?: () => void;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center gap-2 py-10 text-center", className)}>
      <div className="text-ink-faint">{icon ?? <CircleAlert className="h-8 w-8" />}</div>
      <p className="text-sm font-medium text-ink-soft">{title}</p>
      {desc && <p className="max-w-64 text-xs text-ink-faint">{desc}</p>}
      {(onRetry || action) && (
        <div className="mt-2 flex items-center gap-2">
          {onRetry && (
            <Button variant="secondary" size="sm" onClick={onRetry}>
              <RotateCcw className="h-3.5 w-3.5" />
              重试
            </Button>
          )}
          {action}
        </div>
      )}
    </div>
  );
}

type BoundaryProps = { children: React.ReactNode };
type BoundaryState = { error: Error | null };

/**
 * 路由级错误兜底：包在 Routes 外层。页面渲染抛错时展示通用文案
 * （不暴露堆栈，堆栈只进 console），可「重试」或「回首页」。
 */
export class RouteErrorBoundary extends React.Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[RouteErrorBoundary] 页面渲染出错：", error, info);
  }

  private reset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex min-h-[50vh] items-center justify-center px-4">
        <ErrorState
          icon={<CircleAlert className="h-9 w-9" />}
          title="页面出了点问题"
          desc="请重试，或回到首页稍后再看。"
          onRetry={this.reset}
          action={
            <Link to="/">
              <Button variant="ghost" size="sm">
                <House className="h-3.5 w-3.5" />
                回首页
              </Button>
            </Link>
          }
        />
      </div>
    );
  }
}
