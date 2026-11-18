import * as echarts from "echarts";
import { useEffect, useRef } from "react";

export const SERIES = ["#2f5bea", "#14a38b", "#e8a33d", "#d9534f", "#8b5cf6", "#64748b"];

function themeColors() {
  const cs = getComputedStyle(document.documentElement);
  return { text: cs.getPropertyValue("--muted").trim() || "#667", grid: cs.getPropertyValue("--line").trim() || "#ddd" };
}

export function Chart({ option, height = 260, onClick }: { option: Record<string, any>; height?: number; onClick?: (p: any) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts>();

  useEffect(() => {
    if (!ref.current) return;
    inst.current = echarts.init(ref.current, undefined, { renderer: "svg" });
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      inst.current?.dispose();
    };
  }, []);

  useEffect(() => {
    const c = themeColors();
    inst.current?.setOption(
      {
        color: SERIES,
        textStyle: { color: c.text, fontFamily: "inherit" },
        grid: { left: 40, right: 16, top: 36, bottom: 28, containLabel: true },
        ...option,
      },
      true,
    );
    inst.current?.off("click");
    if (onClick) inst.current?.on("click", onClick);
  }, [option, onClick]);

  return <div ref={ref} style={{ height, width: "100%" }} />;
}

export function axisStyle() {
  const c = themeColors();
  return {
    axisLine: { lineStyle: { color: c.grid } },
    splitLine: { lineStyle: { color: c.grid } },
    axisLabel: { color: c.text },
  };
}
