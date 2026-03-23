import { createContext, useContext, useState, useEffect, type ReactNode } from "react";

export type MenuMode = "breakfast" | "lunch";

interface MenuModeContextProps {
    menuMode: MenuMode;
    setMenuMode: (value: MenuMode) => void;
}

const MenuModeContext = createContext<MenuModeContextProps | undefined>(undefined);

export const MenuModeProvider = ({ children }: { children: ReactNode }) => {
    const [menuMode, setMenuMode] = useState<MenuMode>(() => {
        const stored = localStorage.getItem("menuMode");
        return stored === "breakfast" ? "breakfast" : "lunch";
    });

    useEffect(() => {
        localStorage.setItem("menuMode", menuMode);
    }, [menuMode]);

    return <MenuModeContext.Provider value={{ menuMode, setMenuMode }}>{children}</MenuModeContext.Provider>;
};

export const useMenuModeContext = () => {
    const context = useContext(MenuModeContext);
    if (!context) {
        throw new Error("useMenuModeContext must be used within a MenuModeProvider");
    }
    return context;
};
