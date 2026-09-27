import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Amount, Badge, Button } from "@/components/ui";

describe("Amount", () => {
  it("支出：负号 + 人民币符号 + 千分位", () => {
    render(<Amount cents={326_40} kind="expense" />);
    const el = screen.getByText("-¥326.40");
    expect(el).toBeInTheDocument();
    expect(el).toHaveClass("text-ink");
  });

  it("收入：正号 + accent 色", () => {
    render(<Amount cents={24_000_00} kind="income" />);
    const el = screen.getByText("+¥24,000.00");
    expect(el).toBeInTheDocument();
    expect(el).toHaveClass("text-accent");
  });

  it("外币：货币代码代替 ¥", () => {
    render(<Amount cents={29_00} kind="expense" currency="USD" />);
    expect(screen.getByText("-USD 29.00")).toBeInTheDocument();
  });

  it("neutral 且金额为负：自动带负号", () => {
    render(<Amount cents={-864_372_66} />);
    expect(screen.getByText("-¥864,372.66")).toBeInTheDocument();
  });

  it("signed：负数显示 -（不得因 signed 错成 +），0 不加符号", () => {
    const { rerender } = render(<Amount cents={-100_00} signed />);
    expect(screen.getByText("-¥100.00")).toBeInTheDocument();
    rerender(<Amount cents={100_00} signed />);
    expect(screen.getByText("+¥100.00")).toBeInTheDocument();
    rerender(<Amount cents={0} signed />);
    expect(screen.getByText("¥0.00")).toBeInTheDocument();
  });

  it("大数精确显示：.90 与 .91 不碰撞", () => {
    render(<Amount cents={9007199254740990} />);
    expect(screen.getByText("¥90,071,992,547,409.90")).toBeInTheDocument();
  });
});

describe("Badge", () => {
  it("tone 映射到对应语义类名", () => {
    const { rerender } = render(<Badge tone="neutral">支出</Badge>);
    expect(screen.getByText("支出")).toHaveClass("text-ink-soft");

    rerender(<Badge tone="accent">收入</Badge>);
    expect(screen.getByText("收入")).toHaveClass("text-accent-ink");

    rerender(<Badge tone="danger">危险</Badge>);
    expect(screen.getByText("危险")).toHaveClass("text-danger");

    rerender(<Badge tone="warn">警示</Badge>);
    expect(screen.getByText("警示")).toHaveClass("text-warn");

    rerender(<Badge tone="ok">正常</Badge>);
    expect(screen.getByText("正常")).toHaveClass("text-emerald-700");
  });
});

describe("Button", () => {
  it("disabled 时不可点击态生效", () => {
    render(<Button disabled>保存</Button>);
    const btn = screen.getByRole("button", { name: "保存" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveClass("disabled:opacity-50");
  });

  it("variant 类名：danger 与 secondary", () => {
    const { rerender } = render(<Button variant="danger">删除</Button>);
    expect(screen.getByRole("button", { name: "删除" })).toHaveClass("text-danger");

    rerender(<Button variant="secondary">取消</Button>);
    expect(screen.getByRole("button", { name: "取消" })).toHaveClass("glass");
  });
});
