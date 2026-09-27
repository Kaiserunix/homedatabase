import * as React from "react";
import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export const Select = SelectPrimitive.Root;

export function SelectTrigger({ className, placeholder, ...props }: SelectPrimitive.SelectTriggerProps & { placeholder?: string }) {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        "glass flex h-10 w-full items-center justify-between rounded-xl px-3 text-sm text-ink outline-none data-[placeholder]:text-ink-faint",
        className,
      )}
      {...props}
    >
      <SelectPrimitive.Value placeholder={placeholder} />
      <SelectPrimitive.Icon>
        <ChevronDown className="h-4 w-4 text-ink-faint" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

export function SelectContent({ className, ...props }: SelectPrimitive.SelectContentProps) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        className={cn(
          "glass-strong z-50 max-h-72 overflow-auto rounded-xl p-1 motion-safe:data-[state=open]:animate-select-in",
          className,
        )}
        position="popper"
        sideOffset={6}
        {...props}
      />
    </SelectPrimitive.Portal>
  );
}

export function SelectItem({ className, children, ...props }: SelectPrimitive.SelectItemProps) {
  return (
    <SelectPrimitive.Item
      className={cn(
        "relative flex cursor-pointer select-none items-center rounded-lg py-2 pl-8 pr-3 text-sm text-ink outline-none data-[highlighted]:bg-slate-900/5",
        className,
      )}
      {...props}
    >
      <SelectPrimitive.ItemIndicator className="absolute left-2">
        <Check className="h-4 w-4 text-accent" />
      </SelectPrimitive.ItemIndicator>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
}
