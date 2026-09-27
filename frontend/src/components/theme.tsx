import { useCallback, useState } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

/** 主题色方案：与 index.css 的 [data-theme] 一一对应。 */
export const THEMES = [
  { id: "teal", name: "青", desc: "默认，冷静克制", rgb: "13 148 136", soft: "204 251 241", faint: "240 253 250" },
  { id: "indigo", name: "靛蓝", desc: "沉稳商务", rgb: "79 70 229", soft: "224 231 255", faint: "238 242 255" },
  { id: "violet", name: "紫", desc: "柔和雅致", rgb: "124 58 237", soft: "237 233 254", faint: "245 243 255" },
  { id: "rose", name: "玫红", desc: "明快温暖", rgb: "225 29 72", soft: "255 228 230", faint: "255 241 242" },
  { id: "amber", name: "琥珀", desc: "暖阳质感", rgb: "217 119 6", soft: "254 243 199", faint: "255 251 235" },
  { id: "slate", name: "石墨", desc: "极简无彩", rgb: "71 85 105", soft: "226 232 240", faint: "248 250 252" },
] as const;

export type ThemeId = (typeof THEMES)[number]["id"];

const STORAGE_KEY = "hd-theme";

export function getTheme(): ThemeId {
  const v = localStorage.getItem(STORAGE_KEY);
  return (THEMES.some((t) => t.id === v) ? v : "teal") as ThemeId;
}

export function applyTheme(id: ThemeId) {
  localStorage.setItem(STORAGE_KEY, id);
  document.documentElement.dataset.theme = id;
}

export function useTheme(): [ThemeId, (id: ThemeId) => void] {
  const [theme, setTheme] = useState<ThemeId>(getTheme);
  const set = useCallback((id: ThemeId) => {
    applyTheme(id);
    setTheme(id);
  }, []);
  return [theme, set];
}

/** 主题选择器：双端外观设置页共用 */
export function ThemePicker() {
  const [theme, setTheme] = useTheme();
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {THEMES.map((t, i) => {
        const active = t.id === theme;
        return (
          <button
            key={t.id}
            onClick={() => setTheme(t.id)}
            style={{ "--stagger-i": i } as React.CSSProperties}
            className={cn(
              "glass rounded-2xl p-4 text-left transition hover:bg-white/90 active:scale-[0.98]",
              active && "ring-2 ring-accent",
            )}
          >
            <span className="mb-3 flex items-center gap-1.5">
              <span className="h-5 w-5 rounded-full" style={{ background: `rgb(${t.rgb})` }} />
              <span className="h-4 w-4 rounded-full" style={{ background: `rgb(${t.soft})` }} />
              <span className="h-3 w-3 rounded-full border border-slate-900/10" style={{ background: `rgb(${t.faint})` }} />
              {active && <Check className="ml-auto h-4 w-4 text-accent motion-safe:animate-pop" />}
            </span>
            <span className="block text-sm font-medium text-ink">{t.name}</span>
            <span className="block text-xs text-ink-faint">{t.desc}</span>
          </button>
        );
      })}
    </div>
  );
}
