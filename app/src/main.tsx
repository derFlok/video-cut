import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { I18nProvider } from "./i18n";
import { ThemeProvider } from "./theme";
import "./styles.css";

async function boot() {
  if (__MOCK__) {
    const { installMock } = await import("./mock/server");
    installMock();
  }
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <I18nProvider>
        <ThemeProvider>
          <App />
        </ThemeProvider>
      </I18nProvider>
    </StrictMode>,
  );
}

void boot();
