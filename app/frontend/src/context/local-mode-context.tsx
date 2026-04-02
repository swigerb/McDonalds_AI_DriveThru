import { createContext, useContext, useState, useEffect, useMemo, type ReactNode } from "react";

interface LocalModeContextProps {
    localMode: boolean;
    setLocalMode: (value: boolean) => void;
}

const LocalModeContext = createContext<LocalModeContextProps | undefined>(undefined);

export const LocalModeProvider = ({ children }: { children: ReactNode }) => {
    const [localMode, setLocalMode] = useState<boolean>(() => {
        const stored = localStorage.getItem("localMode");
        return stored === "true";
    });

    useEffect(() => {
        localStorage.setItem("localMode", localMode.toString());
    }, [localMode]);

    const value = useMemo(() => ({ localMode, setLocalMode }), [localMode]);

    return <LocalModeContext.Provider value={value}>{children}</LocalModeContext.Provider>;
};

export const useLocalMode = () => {
    const context = useContext(LocalModeContext);
    if (!context) {
        throw new Error("useLocalMode must be used within a LocalModeProvider");
    }
    return context;
};
