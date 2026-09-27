import { useNavigate } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import { cn } from "@/lib/utils";

/** 手机端页面头部：主页面大标题，子页面带返回键 */
export function PageHeader({
  title,
  sub,
  back,
  action,
  className,
}: {
  title: string;
  sub?: string;
  back?: boolean;
  action?: React.ReactNode;
  className?: string;
}) {
  const navigate = useNavigate();
  return (
    <header className={cn("sticky top-0 z-30 -mx-4 mb-4 border-b border-slate-900/5 px-4 pb-3 pt-[calc(0.75rem+env(safe-area-inset-top))] backdrop-blur-xl backdrop-saturate-150", className)}
      style={{ background: "rgba(248,250,252,0.75)" }}
    >
      <div className="flex items-center gap-2">
        {back && (
          <button
            onClick={() => navigate(-1)}
            className="-ml-1 rounded-full p-1 text-ink-soft hover:bg-slate-900/5"
            aria-label="返回"
          >
            <ChevronLeft className="h-5 w-5" />
          </button>
        )}
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-lg font-semibold text-ink">{title}</h1>
          {sub && <p className="truncate text-xs text-ink-faint">{sub}</p>}
        </div>
        {action}
      </div>
    </header>
  );
}
