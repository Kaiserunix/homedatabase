import { describe, expect, it } from "vitest";
import { accountOptionSuffix, canWriteAccount, writableAccounts } from "@/lib/accounts";
import type { Account } from "@/data/types";

const account = (permission: string): Account => ({
  id: `a-${permission}`,
  name: "账户",
  kind: "asset",
  scope: "personal",
  ownerId: "m-dad",
  currency: "CNY",
  balanceCents: 0,
  icon: "wallet",
  permission: permission as Account["permission"],
  version: 1,
});

describe("canWriteAccount：写入账户选择器只允许 write/manage", () => {
  it("manage 可写", () => {
    expect(canWriteAccount(account("manage"))).toBe(true);
  });

  it("write 可写（契约未来可能细分的写权限）", () => {
    expect(canWriteAccount(account("write"))).toBe(true);
  });

  it("view 只读，不可写", () => {
    expect(canWriteAccount(account("view"))).toBe(false);
  });

  it("writableAccounts 过滤掉 view 账户；view 账户标注「（仅查看）」", () => {
    const list = [account("manage"), account("view"), account("write")];
    expect(writableAccounts(list).map((a) => a.permission)).toEqual(["manage", "write"]);
    expect(accountOptionSuffix(account("view"))).toBe("（仅查看）");
    expect(accountOptionSuffix(account("manage"))).toBe("");
  });
});
