/**
 * 十进制定点金额解析：金额（元）字符串 → 精确的整数分。
 *
 * 禁止用 parseFloat/Number × 100：90071992547409.90 会被浮点错成
 * 9007199254740991 分并与 .91 碰撞。这里用纯字符串拆分 + BigInt 计算，
 * 绝对值超过 Number.MAX_SAFE_INTEGER 时明确失败，绝不悄悄取整。
 * 符号位保留（账户期初余额允许正负）；正数约束由各调用点按业务校验。
 */

export type CentsParse = { ok: true; cents: number } | { ok: false; error: string };

const FORMAT_ERROR = "请输入有效金额（最多两位小数）";
const RANGE_ERROR = "金额超出可安全表示的范围";

const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);

/** number|bigint 分转 BigInt；非安全整数的 number 直接拒绝（绝不静默失真） */
function toBigIntCents(cents: number | bigint): bigint {
  if (typeof cents === "bigint") return cents;
  if (!Number.isSafeInteger(cents)) throw new RangeError("金额超出可安全表示的范围");
  return BigInt(cents);
}

/**
 * 精确分 → 元字符串（整数拆符号/元/两位分，不经浮点除法与 toFixed）。
 * 如 9007199254740990 → "90071992547409.90"；负数带 "-" 前缀。
 */
export function formatCents(cents: number | bigint): string {
  let c = toBigIntCents(cents);
  const neg = c < 0n;
  if (neg) c = -c;
  const yuan = (c / 100n).toString();
  const frac = (c % 100n).toString().padStart(2, "0");
  return `${neg ? "-" : ""}${yuan}.${frac}`;
}

/** 千分位分组（纯字符串插入，不转 number 重排） */
function groupThousands(intDigits: string): string {
  return intDigits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** 精确分 → 带千分位的元字符串，如 9007199254740990 → "90,071,992,547,409.90" */
export function formatCentsGrouped(cents: number | bigint): string {
  let c = toBigIntCents(cents);
  const neg = c < 0n;
  if (neg) c = -c;
  const yuan = groupThousands((c / 100n).toString());
  const frac = (c % 100n).toString().padStart(2, "0");
  return `${neg ? "-" : ""}${yuan}.${frac}`;
}

/**
 * BigInt 合计（只读展示聚合专用）：每个后端字段必须是合法安全整数（否则抛
 * RangeError），但合计不设上限、全程 BigInt，由精确 formatter 直接格式化——
 * “合计超 MAX_SAFE 但每个字段都合法”必须能正常展示，不得抛错或转回不安全
 * number。只有最终要写 API 的单项 cents 才用 parseDecimalCents 限制为安全 number。
 */
export function sumCents(values: readonly number[]): bigint {
  let total = 0n;
  for (const v of values) total += toBigIntCents(v);
  return total;
}

/** 金额符号：-1 负 / 0 零 / +1 正（number 与 bigint 通用） */
export function centsSign(cents: number | bigint): -1 | 0 | 1 {
  const c = toBigIntCents(cents);
  return c < 0n ? -1 : c > 0n ? 1 : 0;
}

/** 解析 "[-]ddd[.d|dd]" 形式的元字符串为整数分；非法或超界返回错误 */
export function parseDecimalCents(input: string): CentsParse {
  const s = input.trim();
  const m = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(s);
  if (!m) return { ok: false, error: FORMAT_ERROR };
  const [, sign, intPart, fracPart] = m;
  let cents = BigInt(intPart) * 100n + BigInt((fracPart ?? "").padEnd(2, "0") || "0");
  if (sign === "-") cents = -cents;
  if (cents > MAX_SAFE || cents < -MAX_SAFE) return { ok: false, error: RANGE_ERROR };
  return { ok: true, cents: Number(cents) };
}

/** 交易/分摊/FX 口径：必须为正数金额；返回错误或精确分 */
export function parsePositiveCents(input: string): CentsParse {
  const parsed = parseDecimalCents(input);
  if (!parsed.ok) return parsed;
  if (parsed.cents <= 0) return { ok: false, error: "金额必须大于 0" };
  return parsed;
}

/** 是否为正数金额的快捷判定（用于按钮 disabled 等写入前判断） */
export function isPositiveAmount(input: string): boolean {
  const parsed = parseDecimalCents(input);
  return parsed.ok && parsed.cents > 0;
}

/**
 * 正数小数字符串校验（汇率用，不转 number、不做大数比较）：
 * 允许任意位小数（如 7.1564），只要求 > 0。
 */
export function isPositiveDecimalString(input: string): boolean {
  const s = input.trim();
  const m = /^(\d+)(?:\.(\d+))?$/.exec(s);
  if (!m) return false;
  // 整数与小数部分不能全为 0
  return /[1-9]/.test(m[1]) || (m[2] !== undefined && /[1-9]/.test(m[2]));
}
