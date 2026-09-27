import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./client";

/**
 * API 取数 hook：三态返回 { data, loading, error, retry }。
 *
 * error 直接保存捕获到的异常（http 分支即 ApiError，含 status/code/details），
 * 页面可据此区分 401/403/409 并展示真实服务端文案；非 ApiError 也原样保留。
 *
 * deps 变化自动重取；组件卸载或旧请求晚到时不会写入 state（卸载安全）。
 */
export function useApiQuery<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<{ data: T | null; loading: boolean; error: ApiError | null }>({
    data: null,
    loading: true,
    error: null,
  });
  const fnRef = useRef(fn);
  fnRef.current = fn; // 每次渲染同步最新闭包，避免 start 依赖 fn 反复重建
  const alive = useRef(true);
  const seq = useRef(0);

  const start = useCallback(() => {
    const mySeq = ++seq.current;
    setState({ data: null, loading: true, error: null });
    fnRef.current()
      .then((data) => {
        if (!alive.current || mySeq !== seq.current) return;
        setState({ data, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (!alive.current || mySeq !== seq.current) return;
        const error =
          err instanceof ApiError
            ? err
            : new ApiError(err instanceof Error ? err.message : "未知错误", 0, { code: "UNKNOWN" });
        setState({ data: null, loading: false, error });
      });
  }, []);

  useEffect(() => {
    alive.current = true;
    start();
    return () => {
      alive.current = false;
    };
    // deps 由调用方给出，语义与 useEffect 一致
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [start, ...deps]);

  return { ...state, retry: start };
}
