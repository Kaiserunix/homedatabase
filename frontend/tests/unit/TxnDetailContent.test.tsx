import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { TxnDetailContent } from "@/components/TxnDetailContent";
import { accounts, categories, importBatches, members, splits, transactions } from "@/data/mock";
import type { Transaction } from "@/data/types";

// 与组件内一致的金额格式（分 -> 千分位两位小数）
const fmt = (cents: number) =>
  (cents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2 });

// 组件不再直连 mock：名称解析/关联查询所需数据全部由测试注入
const renderDetail = (txn: Transaction) =>
  render(
    <TxnDetailContent
      txn={txn}
      accounts={accounts}
      categories={categories}
      members={members}
      batches={importBatches}
      splits={splits}
      transactions={transactions}
    />,
  );

describe("TxnDetailContent", () => {
  it("渲染收入交易：基本信息与借贷两列合计文本", () => {
    const txn = transactions.find((t) => t.id === "t-001")!;
    renderDetail(txn);

    // 类型/来源徽标与基本信息
    expect(screen.getByText("收入")).toBeInTheDocument();
    expect(screen.getByText("手动")).toBeInTheDocument();
    expect(screen.getByText("某公司")).toBeInTheDocument();
    expect(screen.getByText("工资")).toBeInTheDocument();
    expect(screen.getByText("7月工资")).toBeInTheDocument();

    // 分录表头
    const table = screen.getByRole("table");
    expect(within(table).getByText("科目")).toBeInTheDocument();
    expect(within(table).getByText("借方")).toBeInTheDocument();
    expect(within(table).getByText("贷方")).toBeInTheDocument();

    // 借贷两列的合计文本（该交易每列一行，行金额即列合计）
    const debitTotal = txn.lines.reduce((s, l) => s + l.debitCents, 0);
    const creditTotal = txn.lines.reduce((s, l) => s + l.creditCents, 0);
    expect(debitTotal).toBe(creditTotal);
    const occurrences = within(table).getAllByText(fmt(debitTotal));
    expect(occurrences).toHaveLength(2); // 借方列一次、贷方列一次

    // 科目名：真实账户名 + equity 损益科目别名
    expect(within(table).getByText("招商银行储蓄卡")).toBeInTheDocument();
    expect(within(table).getByText("收入 · 工资")).toBeInTheDocument();
  });

  it("渲染退款交易：退款徽标与退款关联指向原交易", () => {
    const txn = transactions.find((t) => t.id === "t-007")!;
    renderDetail(txn);

    expect(screen.getByText("退款")).toBeInTheDocument();
    expect(screen.getByText(/退款关联：/)).toBeInTheDocument();
    expect(screen.getByText(/原交易「京东 · /)).toBeInTheDocument();

    const table = screen.getByRole("table");
    const total = txn.lines.reduce((s, l) => s + l.debitCents, 0);
    expect(within(table).getAllByText(fmt(total))).toHaveLength(2);
  });

  it("渲染外币交易：外币徽标带币种与汇率", () => {
    const txn = transactions.find((t) => t.id === "t-008")!;
    renderDetail(txn);

    expect(screen.getByText("外币 USD · 汇率 7.16")).toBeInTheDocument();
    // 分录按折合人民币入账
    const table = screen.getByRole("table");
    const total = txn.lines.reduce((s, l) => s + l.debitCents, 0);
    expect(fmt(total)).toBe("207.64");
    expect(within(table).getAllByText("207.64")).toHaveLength(2);
  });

  it("private:counterparty 分录只显示脱敏占位，不显示原始 ID、不做账户映射", () => {
    const base = transactions.find((t) => t.id === "t-002")!;
    const txn: Transaction = {
      ...base,
      // 共享账户涉及他人私人账户：后端以占位 accountId 返回
      lines: base.lines.map((l) =>
        // 新契约：损益行 accountId=null + ledgerCode，保持不动；账户行替换为脱敏占位
        l.accountId === null ? l : { ...l, accountId: "private:counterparty" },
      ),
    };
    renderDetail(txn);

    expect(screen.getByText("私人账户（已脱敏）")).toBeInTheDocument();
    expect(screen.queryByText(/private:counterparty/)).toBeNull();
    // 不得把占位 ID 映射成任何真实账户名
    expect(screen.queryByText("支付宝余额")).toBeNull();
  });
});
