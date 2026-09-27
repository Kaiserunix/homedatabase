/**
 * 账户隐私占位：共享账户涉及他人私人账户时，后端会以
 * accountId="private:counterparty" 或账面名称「私人账户（已脱敏）」返回。
 * 前端一律只显示脱敏占位：不查找映射、不猜测、不缓存真实账户。
 */
export const PRIVATE_COUNTERPARTY_ID = "private:counterparty";
export const PRIVATE_ACCOUNT_PLACEHOLDER = "私人账户（已脱敏）";

/** 该 accountId 是否为后端脱敏占位（此时绝不参与账户列表查找） */
export function isPrivateCounterparty(accountId: string): boolean {
  return accountId === PRIVATE_COUNTERPARTY_ID;
}

/**
 * 交易分录等场景里的账户显示名：
 * - private:counterparty 直接返回脱敏占位，不做任何查找；
 * - 其余按账户列表解析，查不到时由 fallback 兜底（同样不得猜测真实账户）。
 */
export function accountDisplayName(
  accountId: string,
  accounts: readonly { id: string; name: string }[],
  fallback: (id: string) => string,
): string {
  if (isPrivateCounterparty(accountId)) return PRIVATE_ACCOUNT_PLACEHOLDER;
  return accounts.find((a) => a.id === accountId)?.name ?? fallback(accountId);
}
