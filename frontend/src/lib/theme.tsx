import { useCallback, useEffect, useState } from "react";
import { load, save } from "./storage";

type Theme = "system" | "light" | "dark";
const sysDark = () => !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => load<Theme>("axl.theme", "system"));
  useEffect(() => {
    const el = document.documentElement;
    if (theme === "system") delete el.dataset.theme;
    else el.dataset.theme = theme;
    save("axl.theme", theme);
  }, [theme]);
  const effective = theme === "system" ? (sysDark() ? "dark" : "light") : theme;
  const toggle = useCallback(() => setTheme(effective === "dark" ? "light" : "dark"), [effective]);
  return { effective, toggle };
}
