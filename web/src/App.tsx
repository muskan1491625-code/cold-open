import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { NavTabs, RunLayout, TopBar } from "./components/Shell";
import { ToastProvider } from "./components/Toast";
import { AuthCtx } from "./lib/auth";
import { type Health, health as fetchHealth, onAuthChange, unlockFromLink } from "./lib/api";
import { CutRoomPage } from "./pages/CutRoom";
import { MapPage } from "./pages/Map";
import { QualityPage } from "./pages/Quality";
import { RunPage } from "./pages/Run";
import { RunsPage } from "./pages/Runs";
import { SlatePage } from "./pages/Slate";

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [, force] = useState(0);
  useEffect(() => {
    // Unlock from a #code= link first, so the first screen is already the right one.
    unlockFromLink()
      .then(setLinkError)
      .finally(() =>
        // If health fails we assume a code IS required: fail closed, the server decides anyway.
        fetchHealth()
          .then(setHealth)
          .catch(() => setHealth({ ok: false, access_code_required: true, public_runs: [], code_hint: "" })),
      );
    return onAuthChange(() => force((n) => n + 1));
  }, []);

  if (!health) return null;
  const auth = { required: health.access_code_required, publicRuns: health.public_runs ?? [], hint: health.code_hint ?? "" };

  return (
    <AuthCtx.Provider value={auth}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Standalone><RunsPage linkError={linkError} /></Standalone>} />
            {/* Open without the code: someone with their own API key can run a show. */}
            <Route path="/new" element={<Standalone><RunPage /></Standalone>} />
            <Route path="/run/:runId" element={<RunLayout />}>
              <Route index element={<Navigate to="map" replace />} />
              <Route path="map" element={<MapPage />} />
              <Route path="slate" element={<SlatePage />} />
              <Route path="promo/:pid?" element={<CutRoomPage />} />
              <Route path="quality" element={<QualityPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </AuthCtx.Provider>
  );
}

function Standalone({ children }: { children: React.ReactNode }) {
  return (
    <>
      <a href="#main" className="skip-link">Skip to content</a>
      <TopBar title="Cold Open" />
      <NavTabs />
      <main id="main" className="mx-auto max-w-[1600px] px-4 py-6 md:px-6">{children}</main>
    </>
  );
}
