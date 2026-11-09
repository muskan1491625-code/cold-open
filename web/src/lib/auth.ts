import { createContext, useContext } from "react";

/** What /api/health says about access: is a code needed, which sample runs are open, what to tell people. */
export type AuthInfo = { required: boolean; publicRuns: string[]; hint: string };

export const AuthCtx = createContext<AuthInfo>({ required: true, publicRuns: [], hint: "" });
export const useAuth = () => useContext(AuthCtx);
export const useAuthRequired = () => useContext(AuthCtx).required;
