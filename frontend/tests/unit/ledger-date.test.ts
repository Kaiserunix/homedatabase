/**
 * 上海账期口径（lib/ledger-date）纯函数契约：
 * 后端返回 UTC Z，账期月/日分组/显示一律按 Asia/Shanghai 解释。
 * 回归锚点：2026-07-31T16:30:00Z 必须归入上海 2026-08-01 / 2026-08，
 * 绝不能被 UTC 口径错划进 7 月 31 日 / 2026-07。
 */
import { describe, expect, it } from "vitest";
import {
  fmtLedgerDate,
  fmtLedgerDateTime,
  ledgerDateKey,
  ledgerMonthKey,
} from "@/lib/ledger-date";

// UTC 7/31 16:30 = 上海 8/1 00:30（跨月边界，UTC 口径会误判）
const BOUNDARY = "2026-07-31T16:30:00Z";
// 对照组：再早 30 分 1 秒仍是上海 7/31 23:59:59，属 7 月
const BEFORE = "2026-07-31T15:59:59Z";

describe("ledger-date：上海月首边界", () => {
  it("2026-07-31T16:30:00Z 归上海 2026-08-01，不是 UTC 的 2026-07-31", () => {
    expect(ledgerDateKey(BOUNDARY)).toBe("2026-08-01");
    expect(ledgerDateKey(BEFORE)).toBe("2026-07-31");
  });

  it("账期月键：边界时刻归 2026-08，不是 2026-07", () => {
    expect(ledgerMonthKey(BOUNDARY)).toBe("2026-08");
    expect(ledgerMonthKey(BEFORE)).toBe("2026-07");
  });

  it("显示口径：短日期 8月1日，完整时间 2026-08-01 00:30", () => {
    expect(fmtLedgerDate(BOUNDARY)).toBe("8月1日");
    expect(fmtLedgerDate(BEFORE)).toBe("7月31日");
    expect(fmtLedgerDateTime(BOUNDARY)).toBe("2026-08-01 00:30");
  });

  it("非法日期输入直接抛 RangeError，绝不静默归错月份", () => {
    expect(() => ledgerDateKey("not-a-date")).toThrow(RangeError);
    expect(() => ledgerMonthKey("")).toThrow(RangeError);
  });
});
