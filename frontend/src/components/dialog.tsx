import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/** Radix Dialog 封装：玻璃拟态弹层。sheet="bottom" 时为手机端底部抽屉。 */
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({
  className,
  sheet,
  children,
  ...props
}: DialogPrimitive.DialogContentProps & { sheet?: "bottom" }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-slate-900/30 backdrop-blur-sm motion-safe:data-[state=open]:animate-overlay motion-safe:data-[state=closed]:animate-overlay-out" />
      <DialogPrimitive.Content
        className={cn(
          "glass-strong fixed z-50 outline-none",
          sheet === "bottom"
            ? "inset-x-0 bottom-0 max-h-[88dvh] rounded-t-3xl p-5 pb-[calc(1.25rem+env(safe-area-inset-bottom))] motion-safe:data-[state=open]:animate-sheet-up motion-safe:data-[state=closed]:animate-sheet-down"
            : "left-1/2 top-1/2 w-[min(32rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-2xl p-6 motion-safe:data-[state=open]:animate-dialog-in motion-safe:data-[state=closed]:animate-dialog-out",
          className,
        )}
        {...props}
      >
        {children}
        <DialogPrimitive.Close className="absolute right-4 top-4 rounded-full p-1 text-ink-faint hover:bg-slate-900/5">
          <X className="h-4 w-4" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogHeader({ title, desc }: { title: string; desc?: string }) {
  return (
    <div className="mb-4 pr-8">
      <DialogPrimitive.Title className="text-base font-semibold text-ink">{title}</DialogPrimitive.Title>
      {desc && <DialogPrimitive.Description className="mt-1 text-xs text-ink-soft">{desc}</DialogPrimitive.Description>}
    </div>
  );
}
