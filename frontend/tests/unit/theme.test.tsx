import { beforeEach, describe, expect, it } from "vitest";
import { applyTheme, getTheme, THEMES } from "@/components/theme";

describe("getTheme", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("localStorage 为空时回退默认 teal", () => {
    expect(getTheme()).toBe("teal");
  });

  it("localStorage 为非法值时回退 teal", () => {
    localStorage.setItem("hd-theme", "not-a-theme");
    expect(getTheme()).toBe("teal");
  });

  it("localStorage 为合法主题时原样返回", () => {
    for (const t of THEMES) {
      localStorage.setItem("hd-theme", t.id);
      expect(getTheme()).toBe(t.id);
    }
  });
});

describe("applyTheme", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("写入 localStorage 并同步 html[data-theme]", () => {
    applyTheme("indigo");
    expect(localStorage.getItem("hd-theme")).toBe("indigo");
    expect(document.documentElement.dataset.theme).toBe("indigo");

    applyTheme("amber");
    expect(localStorage.getItem("hd-theme")).toBe("amber");
    expect(document.documentElement.dataset.theme).toBe("amber");
  });
});
