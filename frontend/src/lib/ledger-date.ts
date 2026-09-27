/**
 * 账本日期口径：后端统一返回 UTC（带 Z 的 ISO），而账期归属、日期分组、
 * 月份筛选与所有面向用户的日期显示一律按 Asia/Shanghai 解释。
 *
 * 禁止在任何页面/筛选里对原始 ISO 做 slice(0,7)/startsWith 或依赖
 * 浏览器/设备时区（new Date(...).getMonth() 等）——一律走本模块。
 *
 * 回归锚点：2026-07-31T16:30:00Z 必须归入上海 2026-08-01 / 2026-08。
 */

export const LEDGER_TIME_ZONE = "Asia/Shanghai";

const formatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: LEDGER_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

interface ShanghaiParts {
  year: string;
  month: string;
  day: string;
  hour: string;
  minute: string;
}

/** UTC ISO → 上海挂钟各部分；非法输入直接抛错（绝不静默归错月份） */
function shanghaiParts(iso: string): ShanghaiParts {
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) throw new RangeError(`非法日期时间：${iso}`);
  const out: Record<string, string> = {};
  for (const part of formatter.formatToParts(time)) {
    if (part.type !== "literal") out[part.type] = part.value;
  }
  return {
    year: out.year,
    month: out.month,
    day: out.day,
    hour: out.hour,
    minute: out.minute,
  };
}

/** 账期日键：上海口径 YYYY-MM-DD（用于按日分组） */
export function ledgerDateKey(iso: string): string {
  const p = shanghaiParts(iso);
  return `${p.year}-${p.month}-${p.day}`;
}

/** 账期月键：上海口径 YYYY-MM（用于月份筛选/本月统计） */
export function ledgerMonthKey(iso: string): string {
  const p = shanghaiParts(iso);
  return `${p.year}-${p.month}`;
}

/** 面向用户的短日期：上海口径 M月D日 */
export function fmtLedgerDate(iso: string): string {
  const p = shanghaiParts(iso);
  return `${Number(p.month)}月${Number(p.day)}日`;
}

/** 面向用户的完整时间：上海口径 YYYY-MM-DD HH:mm */
export function fmtLedgerDateTime(iso: string): string {
  const p = shanghaiParts(iso);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
}

/** 当前上海账期月键（“本月”口径）。注入 now 便于测试冻结时钟（vi.setSystemTime） */
export function currentLedgerMonthKey(now: Date = new Date()): string {
  return ledgerMonthKey(now.toISOString());
}

/** 当前上海账期日键（“今天”口径） */
export function currentLedgerDateKey(now: Date = new Date()): string {
  return ledgerDateKey(now.toISOString());
}

/** 上海日历月键平移：shiftLedgerMonthKey("2026-08", -1) → "2026-07"；跨年安全 */
export function shiftLedgerMonthKey(monthKey: string, delta: number): string {
  const m = /^(\d{4})-(\d{2})$/.exec(monthKey);
  if (!m) throw new RangeError(`非法月键：${monthKey}`);
  const total = Number(m[1]) * 12 + (Number(m[2]) - 1) + delta;
  const year = Math.floor(total / 12);
  const month = (total % 12) + 1;
  return `${year}-${String(month).padStart(2, "0")}`;
}

/** 上海日历月的起止日期键（均含）：ledgerMonthRange("2026-08") → { from: "2026-08-01", to: "2026-08-31" } */
export function ledgerMonthRange(monthKey: string): { from: string; to: string } {
  const next = shiftLedgerMonthKey(monthKey, 1);
  // 次月 1 日按 UTC 减 1 天得本月末（纯日期运算，与时区无关）
  const last = new Date(Date.parse(`${next}-01T00:00:00Z`) - 86_400_000);
  return { from: `${monthKey}-01`, to: last.toISOString().slice(0, 10) };
}

/** 上海日键平移：shiftLedgerDateKey("2026-08-01", -89) → "2026-05-04"（纯日期运算） */
export function shiftLedgerDateKey(dateKey: string, deltaDays: number): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateKey)) throw new RangeError(`非法日期键：${dateKey}`);
  const t = Date.parse(`${dateKey}T00:00:00Z`) + deltaDays * 86_400_000;
  return new Date(t).toISOString().slice(0, 10);
}
