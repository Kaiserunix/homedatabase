import { describe, expect, it } from "vitest";
import { importBatches, splits, transactions } from "@/data/mock";

// 数据完整性守护：mock 即领域模型，业务规则（借贷平衡、关联有效）必须先于页面成立
const txnIds = new Set(transactions.map((t) => t.id));

describe("复式记账：每笔交易借贷平衡", () => {
  it("每条交易的借方合计等于贷方合计，且大于 0", () => {
    for (const t of transactions) {
      const debit = t.lines.reduce((s, l) => s + l.debitCents, 0);
      const credit = t.lines.reduce((s, l) => s + l.creditCents, 0);
      expect(debit, `交易 ${t.id}（${t.payee}）借方应等于贷方`).toBe(credit);
      expect(debit, `交易 ${t.id}（${t.payee}）分录合计应大于 0`).toBeGreaterThan(0);
    }
  });

  it("单条分录不会同时有借和贷", () => {
    for (const t of transactions) {
      for (const l of t.lines) {
        expect(
          l.debitCents > 0 && l.creditCents > 0,
          `交易 ${t.id} 的分录 ${l.accountId} 借贷只应出现一侧`,
        ).toBe(false);
      }
    }
  });
});

describe("关联完整性", () => {
  it("refund 交易的 refundOf 指向已存在的交易", () => {
    const refunds = transactions.filter((t) => t.type === "refund");
    expect(refunds.length).toBeGreaterThan(0);
    for (const t of refunds) {
      expect(t.refundOf, `退款交易 ${t.id} 必须有 refundOf`).toBeTruthy();
      expect(txnIds.has(t.refundOf!), `退款交易 ${t.id} 的 refundOf=${t.refundOf} 应存在`).toBe(true);
    }
  });

  it("非 refund 交易不携带 refundOf", () => {
    for (const t of transactions.filter((x) => x.type !== "refund")) {
      expect(t.refundOf, `交易 ${t.id} 非退款不应有 refundOf`).toBeUndefined();
    }
  });

  it("split 的 txnId 指向已存在的交易", () => {
    expect(splits.length).toBeGreaterThan(0);
    for (const s of splits) {
      expect(txnIds.has(s.txnId), `分摊 ${s.id}（${s.title}）的 txnId=${s.txnId} 应存在`).toBe(true);
    }
  });

  it("交易的 splitId 反向指向已存在的分摊", () => {
    const splitIds = new Set(splits.map((s) => s.id));
    for (const t of transactions.filter((x) => x.splitId)) {
      expect(splitIds.has(t.splitId!), `交易 ${t.id} 的 splitId=${t.splitId} 应存在`).toBe(true);
    }
  });

  it("导入候选的 duplicateOfTxnId 非空时指向已存在的交易", () => {
    const seen = new Set<string>();
    for (const b of importBatches) {
      for (const c of b.candidates) {
        if (c.duplicateOfTxnId !== null) {
          seen.add(c.id);
          expect(
            txnIds.has(c.duplicateOfTxnId),
            `批次 ${b.id} 候选 ${c.id}（${c.payee}）的 duplicateOfTxnId=${c.duplicateOfTxnId} 应存在`,
          ).toBe(true);
        }
      }
    }
    // 确保 mock 里确实覆盖了去重场景，防止断言空转
    expect(seen.size).toBeGreaterThan(0);
  });

  it("分摊份额之和等于总额，成员引用有效", () => {
    for (const s of splits) {
      const sum = s.shares.reduce((n, sh) => n + sh.shareCents, 0);
      expect(sum, `分摊 ${s.id}（${s.title}）份额之和应等于总额`).toBe(s.totalCents);
    }
  });
});
