import * as React from "react";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { cn } from "@/lib/utils";

export function Switch({ className, ...props }: SwitchPrimitive.SwitchProps) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        "h-6 w-11 rounded-full bg-slate-900/15 p-0.5 transition data-[state=checked]:bg-accent",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="block h-5 w-5 translate-x-0 rounded-full bg-white shadow transition data-[state=checked]:translate-x-5" />
    </SwitchPrimitive.Root>
  );
}
