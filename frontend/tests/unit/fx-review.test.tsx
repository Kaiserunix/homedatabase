/**
 * needs_fx_review 外币候选的组件/纯函数测试（mock 模式，非端到端）。
 *
 * 覆盖：待补汇率候选显著标出、补全保存前不可勾选确认、人工填写
 * fxRate 与 baseAmountCents 后 PATCH 保存并以返回候选更新。
 * mock 仅由显式开关 VITE_USE_MOCKS=true 启用；真实联调需另行验证。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ImportDetailPage from "@/admin/pages/ImportDetail";
import ImportPage from "@/mobile/pages/Import";
import { setMockLatency } from "@/api/resources";
import { importBatches } from "@/data/mock";
import { isConfirmable, isDuplicate, needsFxReview, parseFxInput } from "@/lib/fx-review";

const fxCandidate = importBatches
  .find((b) => b.id === "b-003")!
  .candidates.find((c) => c.id === "cd-7")!;

describe("fx-review 纯函数", () => {
  it("needsFxReview：仅 pending 且 validationStatus=needs_fx_review 的候选", () => {
    expect(needsFxReview(fxCandidate)).toBe(true);
    expect(needsFxReview({ ...fxCandidate, validationStatus: "valid" })).toBe(false);
    expect(needsFxReview({ ...fxCandidate, status: "confirmed" })).toBe(false);
  });

  it("isConfirmable：与后端一致，仅 pending 且 validationStatus=valid 可确认", () => {
    expect(isConfirmable(fxCandidate)).toBe(false); // needs_fx_review
    expect(isConfirmable({ ...fxCandidate, validationStatus: "valid" })).toBe(true);
    expect(isConfirmable({ ...fxCandidate, validationStatus: "valid", status: "skipped" })).toBe(false);
  });

  it("回归：exact_duplicate / duplicate_source_record 不可确认且计为重复（不只看 duplicateOfTxnId）", () => {
    const exact = { ...fxCandidate, validationStatus: "exact_duplicate", duplicateOfTxnId: "t-1" };
    // duplicate_source_record 可能没有 duplicateOfTxnId
    const srcRec = { ...fxCandidate, validationStatus: "duplicate_source_record", duplicateOfTxnId: null };
    expect(isConfirmable(exact)).toBe(false);
    expect(isConfirmable(srcRec)).toBe(false);
    expect(isDuplicate(exact)).toBe(true);
    expect(isDuplicate(srcRec)).toBe(true);
    expect(isDuplicate({ ...fxCandidate, validationStatus: "valid", duplicateOfTxnId: null })).toBe(false);
  });

  it("回归：其他非 valid 状态同样不可确认（必然 422 的候选不送后端）", () => {
    expect(isConfirmable({ ...fxCandidate, validationStatus: "invalid_amount" })).toBe(false);
    expect(isConfirmable({ ...fxCandidate, validationStatus: "ok" })).toBe(false); // 旧词汇不再有效
  });

  it("parseFxInput：合法输入解析为 fxRate 字符串与整数分", () => {
    const parsed = parseFxInput("7.20", "115.13");
    expect(parsed).toEqual({ ok: true, fxRate: "7.20", baseAmountCents: 115_13 });
  });

  it("parseFxInput：非法汇率/金额一律拒绝", () => {
    for (const [fx, amt] of [
      ["", "115.13"],
      ["abc", "115.13"],
      ["0", "115.13"],
      ["-7.2", "115.13"],
      ["7.20", ""],
      ["7.20", "0"],
      ["7.20", "1.005"],
      ["7.20", "xyz"],
    ]) {
      expect(parseFxInput(fx, amt).ok, `fxRate=${fx} baseAmount=${amt} 应被拒绝`).toBe(false);
    }
  });

  it("parseFxInput：金额精确不经浮点（边界不碰撞、超界拒绝）；汇率大数不靠 Number", () => {
    expect(parseFxInput("7.20", "90071992547409.90")).toEqual({
      ok: true,
      fxRate: "7.20",
      baseAmountCents: 9007199254740990,
    });
    expect(parseFxInput("7.20", "90071992547409.91")).toEqual({
      ok: true,
      fxRate: "7.20",
      baseAmountCents: 9007199254740991,
    });
    expect(parseFxInput("7.20", "90071992547409.92").ok).toBe(false); // 超界拒绝
    expect(parseFxInput("99999999999999999999999", "1.00").ok).toBe(true); // 大数汇率原样提交
  });
});

describe("管理端导入详情：needs_fx_review 候选", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "true");
    setMockLatency(0);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  const renderAdmin = () =>
    render(
      <MemoryRouter initialEntries={["/imports/b-003"]}>
        <Routes>
          <Route path="/imports/:batchId" element={<ImportDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

  it("待补汇率候选显著标出、勾选框禁用；全选确认只入账 4 条 valid 候选", async () => {
    const user = userEvent.setup();
    renderAdmin();

    // 徽标与人工补录面板如实展示
    expect(await screen.findByText("待补汇率")).toBeInTheDocument();
    expect(screen.getByText(/缺少汇率与折合人民币金额/)).toBeInTheDocument();

    // 该候选勾选框禁用：不得在 UI 中当作可正常确认
    const fxCheckbox = screen.getByRole("checkbox", { name: "选择 Amazon US" });
    expect(fxCheckbox).toBeDisabled();
    // 疑似重复候选同样不可勾选（exact_duplicate 不是 valid）
    expect(screen.getByRole("checkbox", { name: "选择 盒马鲜生" })).toBeDisabled();

    // 全选 + 确认入账：只提交 4 条 valid 候选，外币/重复候选保持 pending
    await user.click(screen.getByRole("button", { name: "全选" }));
    await user.click(screen.getByRole("button", { name: "确认入账" }));
    expect(await screen.findByText(/已入账 4 笔交易/)).toBeInTheDocument();
    expect(screen.getByText("待补汇率")).toBeInTheDocument();
    expect(fxCheckbox).toBeDisabled();
  });

  it("非法输入行内报错且不发请求；补全保存后候选变为可确认", async () => {
    const user = userEvent.setup();
    renderAdmin();

    const fxInput = await screen.findByLabelText("汇率（Amazon US）");
    const baseInput = screen.getByLabelText("折合人民币（元）（Amazon US）");

    // 非法汇率：行内错误，不发起保存
    await user.type(fxInput, "0");
    await user.type(baseInput, "115.13");
    await user.click(screen.getByRole("button", { name: "保存汇率" }));
    expect(await screen.findByText("请输入有效汇率（大于 0 的数字）")).toBeInTheDocument();
    expect(screen.getByText("待补汇率")).toBeInTheDocument();

    // 合法填写并保存：以返回候选更新，徽标消失、勾选框恢复可用
    await user.clear(fxInput);
    await user.type(fxInput, "7.20");
    await user.click(screen.getByRole("button", { name: "保存汇率" }));
    await waitFor(() => expect(screen.queryByText("待补汇率")).not.toBeInTheDocument());
    expect(screen.getByRole("checkbox", { name: "选择 Amazon US" })).toBeEnabled();
  });
});

describe("手机端导入：needs_fx_review 候选", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "true");
    setMockLatency(0);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("待补汇率候选不计入可确认数量；补全保存后恢复可确认", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <ImportPage />
      </MemoryRouter>,
    );

    // 7 条 pending：2 条疑似重复默认不勾、1 条待补汇率不可勾 → 可确认 4 条
    expect(
      await screen.findByRole("button", { name: /确认入账（\s*4\s*）/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("Amazon US")).toBeInTheDocument();
    expect(screen.getByText("待补汇率")).toBeInTheDocument();

    // 该候选勾选框禁用
    const fxRow = screen.getByText("Amazon US").closest("label")!;
    expect(within(fxRow).getByRole("checkbox")).toBeDisabled();

    // 补全汇率与折合人民币金额并保存
    await user.type(screen.getByLabelText("汇率（Amazon US）"), "7.20");
    await user.type(screen.getByLabelText("折合人民币（元）（Amazon US）"), "115.13");
    await user.click(screen.getByRole("button", { name: "保存" }));

    // 以返回候选更新后：徽标消失，候选默认可勾选，确认数变为 5
    await waitFor(() => expect(screen.queryByText("待补汇率")).not.toBeInTheDocument());
    expect(
      await screen.findByRole("button", { name: /确认入账（\s*5\s*）/ }),
    ).toBeInTheDocument();
  });
});
