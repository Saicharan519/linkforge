import { useEffect } from "react";

/** Sets the browser tab title ("Links · LinkForge"). Screen readers announce it on navigation. */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · LinkForge`;
  }, [title]);
}
