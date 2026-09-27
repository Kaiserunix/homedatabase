import { describe, expect, it } from "vitest";
import {
  formatCents,
  formatCentsGrouped,
  isPositiveAmount,
  isPositiveDecimalString,
  parseDecimalCents,
  parsePositiveCents,
  sumCents,
} from "@/lib/decimal";

describe("parseDecimalCents：精确十进制元转分（不经浮点）", () => {
  it("常规转换", () => {
    expect(parseDecimalCents("12.34")).toEqual({ ok: true, cents: 1234 });
    expect(parseDecimalCents("0.1")).toEqual({ ok: true, cents: 10 });
    expect(parseDecimalCents("100")).toEqual({ ok: true, cents: 10000 });
    expect(parseDecimalCents("19.99")).toEqual({ ok: true, cents: 1999 });
    expect(parseDecimalCents("4.35")).toEqual({ ok: true, cents: 435 });
  });

  it("安全整数边界附近精确且不碰撞（parseFloat 会把 .90 错成 …991）", () => {
    const a = parseDecimalCents("90071992547409.90");
    const b = parseDecimalCents("90071992547409.91");
    expect(a).toEqual({ ok: true, cents: 9007199254740990 });
    expect(b).toEqual({ ok: true, cents: 9007199254740991 });
    expect(a.ok && b.ok && a.cents !== b.cents).toBe(true);
  });

  it("超过 MAX_SAFE_INTEGER 的分值明确拒绝，绝不悄悄取整", () => {
    const r = parseDecimalCents("90071992547409.92"); // = 9007199254740992 分 > MAX_SAFE
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("超出");
  });

  it("三位小数 / 指数 / 空输入 / 非数字一律拒绝", () => {
    for (const bad of ["1.234", "1e3", "", "   ", "abc", "12.", ".5", "-", "1,00", "0x10"]) {
      expect(parseDecimalCents(bad).ok, `输入 ${JSON.stringify(bad)} 应被拒绝`).toBe(false);
    }
  });

  it("负期初余额按现有语义精确（允许符号位）", () => {
    expect(parseDecimalCents("-5")).toEqual({ ok: true, cents: -500 });
    expect(parseDecimalCents("-4372.66")).toEqual({ ok: true, cents: -437266 });
    expect(parseDecimalCents("-90071992547409.91")).toEqual({ ok: true, cents: -9007199254740991 });
    expect(parseDecimalCents("-90071992547409.92").ok).toBe(false);
  });
});

describe("parsePositiveCents / isPositiveAmount：交易/分摊/FX 必须为正", () => {
  it("零与负数拒绝，正数给精确分", () => {
    expect(parsePositiveCents("0").ok).toBe(false);
    expect(parsePositiveCents("-5").ok).toBe(false);
    expect(parsePositiveCents("12.34")).toEqual({ ok: true, cents: 1234 });
    expect(isPositiveAmount("0.01")).toBe(true);
    expect(isPositiveAmount("0")).toBe(false);
    expect(isPositiveAmount("1.005")).toBe(false);
    expect(isPositiveAmount("90071992547409.92")).toBe(false);
  });
});

describe("isPositiveDecimalString：汇率正数校验（纯字符串，不经 Number）", () => {
  it("常规与大数", () => {
    expect(isPositiveDecimalString("7.16")).toBe(true);
    expect(isPositiveDecimalString("0.0001")).toBe(true);
    expect(isPositiveDecimalString("99999999999999999999999")).toBe(true); // 大数不失真
    expect(isPositiveDecimalString("0")).toBe(false);
    expect(isPositiveDecimalString("0.00")).toBe(false);
    expect(isPositiveDecimalString("-7.2")).toBe(false);
    expect(isPositiveDecimalString("abc")).toBe(false);
    expect(isPositiveDecimalString("")).toBe(false);
  });
});

describe("formatCents / formatCentsGrouped：精确分→元显示（不经浮点/toFixed）", () => {
  it("常规与千分位", () => {
    expect(formatCents(1234)).toBe("12.34");
    expect(formatCentsGrouped(1234)).toBe("12.34");
    expect(formatCentsGrouped(123456789)).toBe("1,234,567.89");
    expect(formatCents(5)).toBe("0.05");
    expect(formatCents(100)).toBe("1.00");
  });

  it("安全整数边界精确且不碰撞（浮点会把 .90 显示成 .91）", () => {
    expect(formatCentsGrouped(9007199254740990)).toBe("90,071,992,547,409.90");
    expect(formatCentsGrouped(9007199254740991)).toBe("90,071,992,547,409.91");
    expect(formatCents(9007199254740990)).not.toBe(formatCents(9007199254740991));
  });

  it("负数精确、零不带符号", () => {
    expect(formatCents(-437266)).toBe("-4372.66");
    expect(formatCentsGrouped(-9007199254740991)).toBe("-90,071,992,547,409.91");
    expect(formatCents(0)).toBe("0.00");
  });

  it("非安全整数输入直接拒绝", () => {
    expect(() => formatCents(9007199254740992)).toThrow(RangeError);
    expect(() => formatCents(1.5)).toThrow(RangeError);
  });
});

describe("sumCents：BigInt 合计（只读展示聚合）", () => {
  it("返回 bigint；两笔近 MAX_SAFE 的合法安全整数合并不失分", () => {
    expect(sumCents([9007199254740990, 1])).toBe(9007199254740991n);
    expect(sumCents([9007199254740900, 91])).toBe(9007199254740991n);
    expect(sumCents([])).toBe(0n);
  });

  it("合计超 MAX_SAFE 仍精确（bigint 贯穿，不抛错、不回退不安全 number）", () => {
    // MAX_SAFE + 1 分 → 90,071,992,547,409.92
    const over = sumCents([9007199254740991, 1]);
    expect(over).toBe(9007199254740992n);
    expect(formatCentsGrouped(over)).toBe("90,071,992,547,409.92");
    // 两个 MAX_SAFE → 180,143,985,094,819.82
    const twice = sumCents([9007199254740991, 9007199254740991]);
    expect(twice).toBe(18014398509481982n);
    expect(formatCentsGrouped(twice)).toBe("180,143,985,094,819.82");
  });

  it("元素非安全整数仍明确拒绝（后端字段必须合法）", () => {
    expect(() => sumCents([1.5])).toThrow(RangeError);
    expect(() => sumCents([9007199254740992])).toThrow(RangeError);
  });
});
