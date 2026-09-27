import { useEffect, useRef } from "react";

/**
 * 背景粒子层：fixed 不动、极淡、慢速漂浮，内容滚动它不动。
 * 性能约束：DPR ≤ 1.5、粒子数按面积封顶（默认 ≤42）、隐藏页签暂停、
 * prefers-reduced-motion 时完全不渲染动画。不参与交互（pointer-events-none）。
 */
export function Particles({
  density = 40000, // 每多少平方像素一个粒子
  max = 42,
}: {
  density?: number;
  max?: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const LINK = 110; // 连线距离上限（px）
    // 强调色跟随主题（CSS 变量为 "r g b" 通道形式），切换主题即时更新
    const readAccent = () => {
      const v = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
      return v ? `rgb(${v.split(/\s+/).join(",")})` : "#0d9488";
    };
    let accent = readAccent();
    const themeObserver = new MutationObserver(() => {
      accent = readAccent();
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    let w = 0;
    let h = 0;
    let raf = 0;
    let last = performance.now();
    let dots: { x: number; y: number; r: number; vx: number; vy: number; a: number; teal: boolean }[] = [];

    const resize = () => {
      w = window.innerWidth;
      h = window.innerHeight;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.min(max, Math.max(12, Math.round((w * h) / density)));
      dots = Array.from({ length: n }, () => ({
        x: Math.random() * w,
        y: Math.random() * h,
        r: 1 + Math.random() * 2,
        vx: (Math.random() - 0.5) * 9, // 极慢漂移，px/s
        vy: (Math.random() - 0.5) * 7,
        a: 0.05 + Math.random() * 0.07,
        teal: Math.random() < 0.35,
      }));
    };

    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      ctx.clearRect(0, 0, w, h);

      // 粒子间极淡连线（n ≤ 42，双重循环可忽略开销）
      ctx.lineWidth = 1;
      for (let i = 0; i < dots.length; i++) {
        const a = dots[i];
        for (let j = i + 1; j < dots.length; j++) {
          const b = dots[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          if (Math.abs(dx) > LINK || Math.abs(dy) > LINK) continue;
          const dist = Math.hypot(dx, dy);
          if (dist > LINK) continue;
          ctx.globalAlpha = 0.05 * (1 - dist / LINK);
          ctx.strokeStyle = "#0f172a";
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }

      for (const d of dots) {
        d.x += d.vx * dt;
        d.y += d.vy * dt;
        if (d.x < -8) d.x = w + 8;
        else if (d.x > w + 8) d.x = -8;
        if (d.y < -8) d.y = h + 8;
        else if (d.y > h + 8) d.y = -8;
        ctx.globalAlpha = d.a;
        ctx.fillStyle = d.teal ? accent : "#0f172a";
        ctx.beginPath();
        ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(tick);
    };

    const onVisibility = () => {
      cancelAnimationFrame(raf);
      if (!document.hidden) {
        last = performance.now();
        raf = requestAnimationFrame(tick);
      }
    };

    resize();
    raf = requestAnimationFrame(tick);
    window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelAnimationFrame(raf);
      themeObserver.disconnect();
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [density, max]);

  return (
    <canvas
      ref={ref}
      aria-hidden
      className="pointer-events-none fixed inset-0 -z-10"
    />
  );
}
