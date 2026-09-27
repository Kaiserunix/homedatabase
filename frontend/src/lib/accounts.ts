import type { Account } from "@/data/types";

/**
 * 写入权限判定：所有写入场景的账户选择器（记账、转账、导入目标账户、
 * 候选补选账户）只允许 permission 为 write/manage 的账户；
 * view 账户可展示为只读，但绝不能可选、可提交。
 */
export function canWriteAccount(a: Account): boolean {
  return a.permission !== "view";
}

/** 可写账户子集（用于预选默认值等场景） */
export function writableAccounts(accts: Account[]): Account[] {
  return accts.filter(canWriteAccount);
}

/** 账户选择器选项后缀：只读账户的标注 */
export function accountOptionSuffix(a: Account): string {
  return canWriteAccount(a) ? "" : "（仅查看）";
}
