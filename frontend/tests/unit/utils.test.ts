import { describe, expect, it } from "vitest";
import { fmtDateTime, fmtMoney } from "@/lib/utils";

describe("fmtMoney", () => {
  it("正数人民币：千分位 + 两位小数 + 无符号", () => {
    expect(fmtMoney(24_000_00)).toBe("¥24,000.00");
    expect(fmtMoney(326_40)).toBe("¥326.40");
  });

  it("负数人民币：负号在货币符号前", () => {
    expect(fmtMoney(-4_372_66)).toBe("-¥4,372.66");
  });

  it("sign=true：正数加 +，负数仍为 -，零不带符号", () => {
    expect(fmtMoney(24_000_00, "CNY", true)).toBe("+¥24,000.00");
    expect(fmtMoney(-500, "CNY", true)).toBe("-¥5.00");
    expect(fmtMoney(0, "CNY", true)).toBe("¥0.00");
  });

  it("外币：用货币代码代替 ¥", () => {
    expect(fmtMoney(29_00, "USD")).toBe("USD 29.00");
    expect(fmtMoney(-120_00, "JPY", true)).toBe("-JPY 120.00");
  });
});

describe("fmtDateTime", () => {
  it("输出 YYYY-MM-DD HH:mm，月日时分补零", () => {
    expect(fmtDateTime("2026-07-28T09:12:00+08:00")).toBe("2026-07-28 09:12");
    expect(fmtDateTime("2026-08-02T21:14:00+08:00")).toBe("2026-08-02 21:14");
    expect(fmtDateTime("2026-12-05T03:04:00+08:00")).toBe("2026-12-05 03:04");
  });
});
