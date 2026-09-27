import "@testing-library/jest-dom/vitest";

// jsdom 缺少 Radix Select 等组件依赖的 Pointer Capture / scrollIntoView API，补空实现
if (typeof Element !== "undefined") {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {};
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {};
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
}

// Node 25 自带一个全局 localStorage（无 --localstorage-file 时是没有任何方法的空壳），
// 会盖掉 jsdom 的实现，导致 getTheme/applyTheme 这类读 localStorage 的代码无法测试。
// 检测到残缺时替换为标准的内存 Storage 实现。
if (typeof globalThis.localStorage?.clear !== "function") {
  const store = new Map<string, string>();
  const memoryStorage: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key) => (store.has(key) ? store.get(key)! : null),
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => void store.delete(key),
    setItem: (key, value) => void store.set(key, String(value)),
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: memoryStorage,
    configurable: true,
    writable: true,
  });
}
