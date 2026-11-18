import { BarChart, GraphChart, LineChart, PieChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useRef, useState } from "react";

echarts.use([BarChart, LineChart, PieChart, GraphChart, GridComponent, TooltipComponent, LegendComponent, DataZoomComponent,
  MarkLineComponent, SVGRenderer]);

/** Categorical palette, readable on light and dark surfaces. */
export const SERIES = ["#3b7ddd", "#17a589", "#e39a2d", "#d6524c", "#8e6bd8", "#5b8fa8", "#c2588f", "#7a9a3a", "#c77d47", "#6577b3",
  "#4fb0c6", "#a6763a", "#9b59b6", "#2e86de", "#16a085", "#e67e22", "#7f8c8d", "#34495e"];
export const TONE = { good: "#17a589", warn: "#e39a2d", bad: "#d6524c", info: "#3b7ddd", neutral: "#9aa3af" };

function themeColors() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string, d: string) => cs.getPropertyValue(n).trim() || d;
  return { text: v("--muted", "#6b7280"), grid: v("--line", "#e1e5eb"), surface: v("--surface", "#fff"), strong: v("--text", "#111") };
}

function useThemeVersion() {
  const [v, setV] = useState(0);
  useEffect(() => {
    const o = new MutationObserver(() => setV((x) => x + 1));
    o.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => o.disconnect();
  }, []);
  return v;
}

export function Chart({ option, height = 260, onClick }: { option: Record<string, any>; height?: number; onClick?: (p: any) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts>();
  const themeV = useThemeVersion();

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
    const { legend, tooltip, grid, ...rest } = option;
    inst.current?.setOption(
      {
        color: SERIES,
        textStyle: { color: c.text, fontFamily: "inherit", fontSize: 11 },
        grid: { left: 8, right: 12, top: legend ? 40 : 16, bottom: 8, containLabel: true, ...(grid || {}) },
        tooltip: { backgroundColor: c.surface, borderColor: c.grid, textStyle: { color: c.strong, fontSize: 12 }, confine: true, ...(tooltip || {}) },
        legend: legend ? { textStyle: { color: c.text }, icon: "roundRect", itemWidth: 10, itemHeight: 10, itemGap: 14, type: "scroll",
          pageIconColor: c.text, pageTextStyle: { color: c.text }, ...legend } : { show: false },
        ...rest,
      },
      true,
    );
    inst.current?.off("click");
    if (onClick) inst.current?.on("click", onClick);
  }, [option, onClick, themeV]);

  return <div ref={ref} style={{ height, width: "100%" }} />;
}

export function axisStyle() {
  const c = themeColors();
  return {
    axisLine: { lineStyle: { color: c.grid } },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: c.grid, type: "dashed" as const } },
    axisLabel: { color: c.text, fontSize: 11 },
  };
}

/** A time x-axis; ECharts picks sensible ticks (no repeated identical labels). */
export function timeAxis() {
  const ax = axisStyle();
  return { type: "time" as const, ...ax, splitLine: { show: false }, axisLabel: { ...ax.axisLabel, hideOverlap: true } };
}
