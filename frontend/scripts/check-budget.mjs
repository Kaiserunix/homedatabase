#!/usr/bin/env node
/**
 * 首屏性能预算：解析 dist 两个 HTML 入口的 <script> 与 modulepreload，
 * 累加其 gzip 体积，超过阈值即失败。随 `npm run build` 后执行。
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const DIST = fileURLToPath(new URL("../dist/", import.meta.url));
const BUDGET_KB = 90; // 每个入口首屏 JS gzip 上限

const entries = ["index.html", "admin.html"];
let failed = false;

for (const entry of entries) {
  const html = readFileSync(join(DIST, entry), "utf8");
  const refs = new Set();
  for (const m of html.matchAll(/<script[^>]+src="([^"]+)"/g)) refs.add(m[1]);
  for (const m of html.matchAll(/<link[^>]+rel="modulepreload"[^>]+href="([^"]+)"/g)) refs.add(m[1]);

  let total = 0;
  const rows = [];
  for (const ref of refs) {
    const file = join(DIST, ref);
    const gz = gzipSync(readFileSync(file)).length;
    total += gz;
    rows.push(`${(gz / 1024).toFixed(1).padStart(6)} kB  ${ref.split("/").pop()}`);
  }
  const kb = total / 1024;
  const ok = kb <= BUDGET_KB;
  if (!ok) failed = true;
  console.log(`\n${entry}: ${kb.toFixed(1)} kB gzip（预算 ${BUDGET_KB} kB）${ok ? " ✓" : " ✗ 超预算"}`);
  for (const r of rows.sort().reverse()) console.log(`  ${r}`);
}

if (failed) {
  console.error("\n性能预算未通过：请检查新引入的依赖或考虑代码分割。");
  process.exit(1);
}
console.log("\n性能预算通过。");
