import { useEffect, useRef, useState } from "react";

/** The hash route, e.g. "/faktur/FB-000001". */
export function useRute(): string {
  const baca = () => window.location.hash.replace(/^#/, "") || "/";
  const [rute, setRute] = useState(baca);
  useEffect(() => {
    const ubah = () => setRute(baca());
    window.addEventListener("hashchange", ubah);
    return () => window.removeEventListener("hashchange", ubah);
  }, []);
  return rute;
}

/**
 * Calls `saatUbah` when the server reports a change (SSE), or every 3 seconds when the stream
 * cannot be used. Returns whether the live stream is connected.
 */
export function usePembaruan(saatUbah: (faktur: string[] | null) => void): boolean {
  const [langsung, setLangsung] = useState(false);
  const panggil = useRef(saatUbah);
  panggil.current = saatUbah;
  useEffect(() => {
    let polling: number | undefined;
    let sumber: EventSource | null = null;
    let gagal = 0;
    const mulaiPolling = () => {
      setLangsung(false);
      if (polling === undefined) polling = window.setInterval(() => panggil.current(null), 3000);
    };
    if (typeof EventSource === "undefined") {
      mulaiPolling();
    } else {
      sumber = new EventSource("/api/stream");
      sumber.onopen = () => {
        gagal = 0;
        setLangsung(true);
        if (polling !== undefined) {
          window.clearInterval(polling);
          polling = undefined;
        }
        panggil.current(null);
      };
      sumber.addEventListener("ubah", (event) => {
        try {
          const data = JSON.parse((event as MessageEvent).data);
          panggil.current(Array.isArray(data.faktur) ? data.faktur : null);
        } catch {
          panggil.current(null);
        }
      });
      sumber.onerror = () => {
        gagal += 1;
        mulaiPolling();
        if (gagal >= 5 && sumber) {
          sumber.close(); // stay on polling
          sumber = null;
        }
      };
    }
    return () => {
      if (sumber) sumber.close();
      if (polling !== undefined) window.clearInterval(polling);
    };
  }, []);
  return langsung;
}
