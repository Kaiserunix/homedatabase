import { describe, expect, it } from "vitest";
import { validateTransaction, type TxnFormInput } from "@/lib/validate-txn";

const base: TxnFormInput = {
  type: "expense",
  payee: "海底捞",
  amount: "12.5",
  categoryId: "c-food",
  accountId: "a-cmb",
};

const messages = (input: TxnFormInput) => validateTransaction(input).map((e) => e.message);

describe("validateTransaction：账面名称（payee，必填）", () => {
  it("空账面名称报错", () => {
    expect(validateTransaction({ ...base, payee: "" })).toContainEqual({
      field: "payee",
      message: "请填写账面名称",
    });
  });

  it("纯空格账面名称报错", () => {
    expect(validateTransaction({ ...base, payee: "   " })).toContainEqual({
      field: "payee",
      message: "请填写账面名称",
    });
  });

  it("有账面名称不报 payee 错误", () => {
    expect(validateTransaction(base)).toEqual([]);
  });
});

describe("validateTransaction：金额", () => {
  it("空金额报错", () => {
    expect(messages({ ...base, amount: "" })).toContain("请输入金额");
  });

  it("金额为 0 报错", () => {
    expect(messages({ ...base, amount: "0" })).toContain("金额必须大于 0");
    expect(messages({ ...base, amount: "0.00" })).toContain("金额必须大于 0");
  });

  it("金额为负数报错", () => {
    expect(messages({ ...base, amount: "-5" })).toContain("金额必须大于 0");
  });

  it("金额三位小数报错", () => {
    expect(messages({ ...base, amount: "1.234" })).toContain("金额最多支持两位小数");
  });

  it("合法金额通过（整数 / 一位 / 两位小数）", () => {
    expect(validateTransaction({ ...base, amount: "12" })).toEqual([]);
    expect(validateTransaction({ ...base, amount: "12.5" })).toEqual([]);
    expect(validateTransaction({ ...base, amount: "12.50" })).toEqual([]);
  });
});

describe("validateTransaction：分类与账户", () => {
  it("支出分类允许为空（categoryId=null 通过）", () => {
    expect(validateTransaction({ ...base, categoryId: null })).toEqual([]);
  });

  it("收入分类允许为空；缺账户报错", () => {
    const income: TxnFormInput = { ...base, type: "income", categoryId: "c-salary" };
    expect(validateTransaction(income)).toEqual([]);
    expect(validateTransaction({ ...income, categoryId: null })).toEqual([]);
    expect(messages({ ...income, accountId: "" })).toContain("请选择账户");
  });
});

describe("validateTransaction：转账", () => {
  const transfer: TxnFormInput = {
    type: "transfer",
    payee: "招行转家庭账户",
    amount: "100",
    categoryId: null,
    accountId: "a-cmb",
    toAccountId: "a-family",
  };

  it("转出与转入相同报错", () => {
    expect(messages({ ...transfer, toAccountId: "a-cmb" })).toContain("转出与转入账户不能相同");
  });

  it("缺转入账户报错", () => {
    expect(messages({ ...transfer, toAccountId: undefined })).toContain("请选择转入账户");
  });

  it("缺转出账户报错", () => {
    expect(messages({ ...transfer, accountId: "" })).toContain("请选择转出账户");
  });

  it("转账无需分类，两个不同账户即通过", () => {
    expect(validateTransaction(transfer)).toEqual([]);
  });

  it("转账也必须填账面名称", () => {
    expect(validateTransaction({ ...transfer, payee: "" })).toContainEqual({
      field: "payee",
      message: "请填写账面名称",
    });
  });
});

describe("validateTransaction：退款必须选择原支出交易", () => {
  const refund: TxnFormInput = {
    type: "refund",
    payee: "海底捞退款",
    amount: "12.5",
    categoryId: "c-food",
    accountId: "a-cmb",
    refundOf: "t-origin",
  };

  it("缺 refundOf 报错（缺 refundOf 的退款请求不允许发出）", () => {
    expect(validateTransaction({ ...refund, refundOf: undefined })).toContainEqual({
      field: "refundOf",
      message: "退款必须选择原支出交易",
    });
    expect(messages({ ...refund, refundOf: "" })).toContain("退款必须选择原支出交易");
  });

  it("携带 refundOf 通过", () => {
    expect(validateTransaction(refund)).toEqual([]);
  });

  it("退款同样必须有账户与账面名称", () => {
    expect(messages({ ...refund, accountId: "" })).toContain("请选择账户");
    expect(messages({ ...refund, payee: "" })).toContain("请填写账面名称");
  });
});

describe("validateTransaction：精确金额边界（不经浮点）", () => {
  it("安全整数边界精确：.90 / .91 都通过且不碰撞", () => {
    expect(validateTransaction({ ...base, amount: "90071992547409.90" })).toEqual([]);
    expect(validateTransaction({ ...base, amount: "90071992547409.91" })).toEqual([]);
  });

  it("超过 MAX_SAFE_INTEGER 的分值被拒绝", () => {
    expect(messages({ ...base, amount: "90071992547409.92" })).toContain("金额超出可安全表示的范围");
  });

  it("指数与空输入被拒绝", () => {
    expect(messages({ ...base, amount: "1e3" })).toContain("金额最多支持两位小数");
    expect(messages({ ...base, amount: "" })).toContain("请输入金额");
  });
});
