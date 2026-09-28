import { useEffect, useState } from "react";

/** 极简 hash 路由：#/pipelines/3/canvas?x=1 */
export interface Route {
  path: string[];
  query: URLSearchParams;
}

function parse(): Route {
  const h = window.location.hash.replace(/^#/, "") || "/pipelines";
  const [p, q] = h.split("?");
  return { path: p.split("/").filter(Boolean).map(decodeURIComponent), query: new URLSearchParams(q || "") };
}

export function useRoute(): Route {
  const [r, setR] = useState(parse());
  useEffect(() => {
    const on = () => setR(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return r;
}

let guard: (() => boolean) | null = null;
/** 离开未保存页面时的确认（WIREFRAME §3.3） */
export function setLeaveGuard(fn: (() => boolean) | null) {
  guard = fn;
}

export function navigate(to: string) {
  if (guard && !guard()) return;
  window.location.hash = to;
}
