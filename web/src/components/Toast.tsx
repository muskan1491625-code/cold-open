import { AnimatePresence, motion } from "framer-motion";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Tone = "teal" | "tungsten" | "rec" | "bone";
type Toast = { id: number; text: string; tone: Tone };
const Ctx = createContext<(text: string, tone?: Tone) => void>(() => {});

export const useToast = () => useContext(Ctx);

const BAR: Record<Tone, string> = { teal: "bg-teal", tungsten: "bg-tungsten", rec: "bg-rec", bone: "bg-bone" };

/** Toasts confirm with the same verb as the button ("Approved for paid"); announced politely. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: Tone = "bone") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, text, tone }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 3200);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div aria-live="polite" role="status" className="fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 flex-col items-center gap-2">
        <AnimatePresence>
          {items.map((t) => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              transition={{ duration: 0.16 }}
              className="flex items-center gap-3 rounded-panel border border-rule bg-console py-2 pl-2 pr-4 text-sm"
            >
              <span aria-hidden className={`h-6 w-1 rounded-full ${BAR[t.tone]}`} />
              {t.text}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </Ctx.Provider>
  );
}
