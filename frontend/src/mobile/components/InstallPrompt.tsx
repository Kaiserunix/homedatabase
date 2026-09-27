import { useEffect, useState } from "react";
import { Share, Smartphone, X } from "lucide-react";
import { Button, Card } from "@/components/ui";
import { cn } from "@/lib/utils";

/** Chrome/Edge 触发 beforeinstallprompt 时的事件类型（lib.dom 未内置） */
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const DISMISS_KEY = "hd-install-dismiss-at";
const DISMISS_DAYS = 30;
const EXIT_MS = 220; // 与 fade-out 200ms 对齐，留少量余量

type Mode = "hidden" | "install" | "ios";

function isIos(): boolean {
  const ua = navigator.userAgent;
  // iPadOS 13+ 伪装成 Mac，用触点数量区分
  return (
    /iP(hone|ad|od)/.test(ua) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  );
}

function isStandalone(): boolean {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

function dismissedRecently(): boolean {
  const ts = Number(localStorage.getItem(DISMISS_KEY) || 0);
  return ts > 0 && Date.now() - ts < DISMISS_DAYS * 24 * 60 * 60 * 1000;
}

/** 首页顶部的「添加到主屏幕」引导卡：Android 走原生安装弹窗，iOS 给 Safari 操作指引 */
export function InstallPrompt() {
  const [mode, setMode] = useState<Mode>("hidden");
  const [closing, setClosing] = useState(false);
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);

  useEffect(() => {
    if (isStandalone() || dismissedRecently()) return;

    const onPrompt = (e: Event) => {
      e.preventDefault();
      setDeferred(e as BeforeInstallPromptEvent);
      setMode("install");
    };
    window.addEventListener("beforeinstallprompt", onPrompt);

    // iOS 没有 beforeinstallprompt，改为展示手动指引
    if (isIos()) setMode("ios");

    return () => window.removeEventListener("beforeinstallprompt", onPrompt);
  }, []);

  if (mode === "hidden") return null;

  const close = () => {
    setClosing(true);
    window.setTimeout(() => setMode("hidden"), EXIT_MS);
  };

  const dismiss = () => {
    localStorage.setItem(DISMISS_KEY, String(Date.now()));
    close();
  };

  const install = async () => {
    if (!deferred) return;
    await deferred.prompt();
    await deferred.userChoice.catch(() => undefined);
    setDeferred(null);
    close();
  };

  return (
    <Card
      strong
      className={cn(
        "mb-4 flex items-start gap-3 p-4 motion-safe:animate-fade-up",
        closing && "motion-safe:animate-fade-out",
      )}
    >
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
        {mode === "ios" ? <Share className="h-5 w-5" /> : <Smartphone className="h-5 w-5" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium text-ink">添加到主屏幕</span>
        <span className="mt-0.5 block text-xs text-ink-faint">
          {mode === "ios"
            ? "用 Safari 分享 → 添加到主屏幕，像原生 App 一样打开"
            : "像原生 App 一样打开，记账更快"}
        </span>
        <span className="mt-3 flex gap-2">
          {mode === "install" && (
            <Button size="sm" onClick={install}>
              安装
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={dismiss}>
            暂不
          </Button>
        </span>
      </span>
      <button
        onClick={dismiss}
        aria-label="关闭"
        className="-mr-1 -mt-1 shrink-0 rounded-full p-1.5 text-ink-faint transition hover:bg-slate-900/5"
      >
        <X className="h-4 w-4" />
      </button>
    </Card>
  );
}
