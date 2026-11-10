import { useCallback, useEffect, useState } from "react";
import { ambil, type Konfigurasi } from "./api";
import { useRute } from "./hooks";
import { Antrean } from "./Antrean";
import { DetailFaktur } from "./DetailFaktur";
import { Unggah } from "./Unggah";
import { Metrik } from "./Metrik";

const MENU = [
  { ke: "/", nama: "Antrean" },
  { ke: "/unggah", nama: "Unggah" },
  { ke: "/metrik", nama: "Metrik" },
];

export function App() {
  const rute = useRute();
  const [konfigurasi, setKonfigurasi] = useState<Konfigurasi | null>(null);
  useEffect(() => {
    ambil<Konfigurasi>("/api/konfigurasi").then(setKonfigurasi).catch(() => setKonfigurasi(null));
  }, []);
  const aktif = useCallback(
    (ke: string) => (ke === "/" ? rute === "/" || rute.startsWith("/faktur/") : rute.startsWith(ke)),
    [rute],
  );

  let layar;
  if (rute.startsWith("/faktur/")) {
    layar = <DetailFaktur key={rute} id={decodeURIComponent(rute.slice("/faktur/".length))} konfigurasi={konfigurasi} />;
  } else if (rute.startsWith("/unggah")) {
    layar = <Unggah batas={konfigurasi?.batas_berkas_byte ?? 15 * 1024 * 1024} />;
  } else if (rute.startsWith("/metrik")) {
    layar = <Metrik />;
  } else {
    layar = <Antrean />;
  }

  return (
    <>
      <header className="kepala">
        <div className="kepala-isi">
          <a className="merek" href="#/">
            <span className="merek-tanda" aria-hidden="true">FB</span>
            <span>
              <strong>Faktur Beres</strong>
              <small>Data sintetis – demo GTNIC 2026</small>
            </span>
          </a>
          <nav aria-label="Menu utama">
            {MENU.map((m) => (
              <a key={m.ke} href={`#${m.ke}`} className={aktif(m.ke) ? "aktif" : undefined} aria-current={aktif(m.ke) ? "page" : undefined}>
                {m.nama}
              </a>
            ))}
          </nav>
        </div>
      </header>
      <main className="isi">{layar}</main>
      <footer className="kaki">
        Faktur Beres menuntaskan exception faktur pemasok: ekstraksi, pencocokan tiga arah dengan Odoo, lalu jalur A, B, atau C.
        Semua vendor, NPWP, rekening, dan faktur di sini sintetis.
      </footer>
    </>
  );
}
