import { useEffect, useState } from "react";
import { ApiError, get, onAuthChange } from "./api";

export type Async<T> = { data?: T; error?: ApiError | Error; loading: boolean };

/** Fetch JSON with the shared cache; re-runs when the access code changes. */
export function useApi<T>(path: string | null, deps: unknown[] = []): Async<T> & { reload: () => void } {
  const [state, setState] = useState<Async<T>>({ loading: !!path });
  const [nonce, setNonce] = useState(0);
  useEffect(() => onAuthChange(() => setNonce((n) => n + 1)), []);
  useEffect(() => {
    if (!path) return;
    let live = true;
    setState((s) => ({ ...s, loading: true }));
    get<T>(path, nonce > 0)
      .then((data) => live && setState({ data, loading: false }))
      .catch((error) => live && setState({ error, loading: false }));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, ...deps]);
  return { ...state, reload: () => setNonce((n) => n + 1) };
}

export function useReducedMotion(): boolean {
  const q = typeof window !== "undefined" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const [r, setR] = useState(q?.matches ?? false);
  useEffect(() => {
    if (!q) return;
    const f = () => setR(q.matches);
    q.addEventListener("change", f);
    return () => q.removeEventListener("change", f);
  }, [q]);
  return r;
}

/** Element width via ResizeObserver (CSS does layout; we only read it for canvas/SVG). */
export function useWidth<T extends HTMLElement>(): [(el: T | null) => void, number] {
  const [el, setEl] = useState<T | null>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.floor(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, w];
}

/** Global single-key shortcuts, ignored while typing in a field. */
export function useKeys(map: Record<string, (e: KeyboardEvent) => void>, deps: unknown[] = []) {
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const fn = map[e.key];
      if (fn) fn(e);
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
