import { describe, expect, it } from "vitest";
import { evenShares } from "@/lib/split-shares";

describe("evenShares：均分先整除、余数按成员顺序每人加 1 分", () => {
  it("12.34 元（1234 分）三人分摊：412/411/411，总和精确等于 1234", () => {
    const shares = evenShares(1234, ["m-dad", "m-mom", "m-son"]);
    expect(shares).toEqual([
      { memberId: "m-dad", shareCents: 412 },
      { memberId: "m-mom", shareCents: 411 },
      { memberId: "m-son", shareCents: 411 },
    ]);
    expect(shares.reduce((s, x) => s + x.shareCents, 0)).toBe(1234);
  });

  it("整除时每人完全相等", () => {
    const shares = evenShares(900, ["a", "b", "c"]);
    expect(shares.map((s) => s.shareCents)).toEqual([300, 300, 300]);
  });

  it("余数大于 1 时按顺序逐人加 1 分", () => {
    // 1000 / 3 = 333 余 1；1001 / 3 = 333 余 2
    expect(evenShares(1000, ["a", "b", "c"]).map((s) => s.shareCents)).toEqual([334, 333, 333]);
    expect(evenShares(1001, ["a", "b", "c"]).map((s) => s.shareCents)).toEqual([334, 334, 333]);
  });

  it("任意总额与人数：总和恒等于总额，且任意两人最多相差 1 分", () => {
    for (const total of [1, 7, 100, 1234, 99999]) {
      for (const n of [1, 2, 3, 5, 7]) {
        const ids = Array.from({ length: n }, (_, i) => `m-${i}`);
        const shares = evenShares(total, ids);
        expect(shares.reduce((s, x) => s + x.shareCents, 0)).toBe(total);
        const amounts = shares.map((s) => s.shareCents);
        expect(Math.max(...amounts) - Math.min(...amounts)).toBeLessThanOrEqual(1);
      }
    }
  });

  it("份额顺序与传入成员顺序一致（余数分给顺序靠前者）", () => {
    const shares = evenShares(1234, ["m-son", "m-dad", "m-mom"]);
    expect(shares[0]).toEqual({ memberId: "m-son", shareCents: 412 });
  });

  it("无成员时返回空数组", () => {
    expect(evenShares(1234, [])).toEqual([]);
  });
});
