// Hash routing: the server only serves index.html at /, so every screen lives in the fragment.

import { useEffect, useState } from "react";

export type Route =
  | { name: "home" }
  | { name: "new" }
  | { name: "job"; id: string }
  | { name: "brands" }
  | { name: "settings" }
  | { name: "brand"; id: string };

export function parseRoute(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "new") return { name: "new" };
  if (parts[0] === "jobs" && parts[1]) return { name: "job", id: parts[1] };
  if (parts[0] === "brands" && parts[1]) return { name: "brand", id: parts[1] };
  if (parts[0] === "brands") return { name: "brands" };
  if (parts[0] === "settings") return { name: "settings" };
  return { name: "home" };
}

export function href(r: Route): string {
  switch (r.name) {
    case "home":
      return "#/";
    case "new":
      return "#/new";
    case "job":
      return `#/jobs/${encodeURIComponent(r.id)}`;
    case "brands":
      return "#/brands";
    case "settings":
      return "#/settings";
    case "brand":
      return `#/brands/${encodeURIComponent(r.id)}`;
  }
}

export function go(r: Route): void {
  window.location.hash = href(r);
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const on = () => {
      setRoute(parseRoute(window.location.hash));
      window.scrollTo?.(0, 0);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
