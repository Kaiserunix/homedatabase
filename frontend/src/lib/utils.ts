import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { centsSign, formatCentsGrouped } from "@/lib/decimal";
import { fmtLedgerDate, fmtLedgerDateTime } from "@/lib/ledger-date";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** 金额格式化：分 -> 带符号人民币字符串（精确十进制，不经浮点；接受 number 或 bigint） */
export function fmtMoney(cents: number | bigint, currency = "CNY", sign = false): string {
  const s = formatCentsGrouped(cents).replace(/^-/, "");
  const sym = currency === "CNY" ? "¥" : currency + " ";
  const sgn = centsSign(cents);
  const prefix = sgn < 0 ? "-" : sign && sgn > 0 ? "+" : "";
  return `${prefix}${sym}${s}`;
}

/** 面向用户的短日期：固定 Asia/Shanghai 账本口径（见 lib/ledger-date） */
export function fmtDate(iso: string): string {
  return fmtLedgerDate(iso);
}

/** 面向用户的完整时间：固定 Asia/Shanghai 账本口径（见 lib/ledger-date） */
export function fmtDateTime(iso: string): string {
  return fmtLedgerDateTime(iso);
}
