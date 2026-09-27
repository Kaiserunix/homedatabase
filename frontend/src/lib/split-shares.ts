/**
 * 分摊均分：先整除得到每人基准份额，余数（总分差）按成员顺序每人加 1 分，
 * 保证所有份额之和精确等于原交易总额（分的整数，无浮点）。
 * 因此成员份额可能相差 1 分，UI 不得宣称“每人完全相等”。
 */

export interface EvenShare {
  memberId: string;
  shareCents: number;
}

/**
 * @param totalCents 原交易总额（分）
 * @param memberIds 参与成员，顺序即余数分配顺序（调用方应传稳定顺序，如后端成员列表顺序）
 */
export function evenShares(totalCents: number, memberIds: string[]): EvenShare[] {
  const n = memberIds.length;
  if (n === 0) return [];
  const base = Math.floor(totalCents / n);
  const remainder = totalCents - base * n; // 0 <= remainder < n
  return memberIds.map((memberId, i) => ({
    memberId,
    shareCents: base + (i < remainder ? 1 : 0),
  }));
}
