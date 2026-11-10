import { useCallback, useEffect, useState } from "react";
import { ambil, type Ringkas } from "./api";
import { DaftarAlasan, Kartu, LencanaJalur, LencanaStatus, Pesan } from "./bagian";
import { STATUS, detik, rupiah, waktu } from "./format";
import { usePembaruan } from "./hooks";

const JALUR_SARING = [
  { nilai: "", nama: "Semua jalur" },
  { nilai: "A", nama: "Jalur A" },
  { nilai: "B", nama: "Jalur B" },
  { nilai: "C", nama: "Jalur C" },
  { nilai: "belum", nama: "Belum ditentukan" },
];

export function Antrean() {
  const [baris, setBaris] = useState<Ringkas[] | null>(null);
  const [galat, setGalat] = useState<string | null>(null);
  const [jalur, setJalur] = useState("");
  const [status, setStatus] = useState("");

  const muat = useCallback(() => {
    const q = new URLSearchParams();
    if (jalur) q.set("jalur", jalur);
    if (status) q.set("status", status);
    ambil<Ringkas[]>(`/api/faktur${q.size ? `?${q}` : ""}`)
      .then((data) => {
        setBaris(data);
        setGalat(null);
      })
      .catch((e: Error) => setGalat(e.message));
  }, [jalur, status]);

  useEffect(muat, [muat]);
  const langsung = usePembaruan(muat);

  return (
    <Kartu
      judul="Antrean faktur"
      samping={
        <span className={`langsung ${langsung ? "nyala" : ""}`} title={langsung ? "Pembaruan langsung (SSE)" : "Diperbarui tiap 3 detik"}>
          <span className="titik" aria-hidden="true" /> {langsung ? "Langsung" : "Polling 3 dtk"}
        </span>
      }
    >
      <div className="saring">
        <div className="tombol-kelompok" role="group" aria-label="Saring jalur">
          {JALUR_SARING.map((j) => (
            <button key={j.nilai} type="button" className={jalur === j.nilai ? "terpilih" : ""} aria-pressed={jalur === j.nilai} onClick={() => setJalur(j.nilai)}>
              {j.nama}
            </button>
          ))}
        </div>
        <label>
          <span className="sr">Saring status</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Semua status</option>
            {Object.entries(STATUS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </div>
      {galat && <Pesan jenis="galat">Antrean tidak bisa dimuat: {galat}</Pesan>}
      {baris === null && !galat && <p className="redup">Memuat…</p>}
      {baris !== null && baris.length === 0 && (
        <p className="kosong">
          Belum ada faktur{jalur || status ? " untuk saringan ini" : ""}. <a href="#/unggah">Unggah faktur</a> untuk memulai.
        </p>
      )}
      {baris !== null && baris.length > 0 && (
        <div className="gulir">
          <table className="tabel tabel-antrean">
            <thead>
              <tr>
                <th>Nomor</th>
                <th>Vendor</th>
                <th>No. faktur</th>
                <th className="kanan">Total</th>
                <th>Jalur</th>
                <th>Status</th>
                <th>Alasan</th>
                <th>Diterima</th>
                <th className="kanan" title="Detik dari diterima sampai jalur ditentukan">Sampai jalur</th>
              </tr>
            </thead>
            <tbody>
              {baris.map((f) => (
                <tr key={f.id} onClick={() => (window.location.hash = `#/faktur/${f.id}`)} className="bisa-klik">
                  <td>
                    <a href={`#/faktur/${f.id}`} onClick={(e) => e.stopPropagation()}>
                      {f.id}
                    </a>
                  </td>
                  <td>{f.vendor ?? <span className="redup">belum dikenali</span>}</td>
                  <td>{f.nomor_faktur ?? "–"}</td>
                  <td className="kanan angka">{rupiah(f.total)}</td>
                  <td>
                    <LencanaJalur jalur={f.jalur} />
                  </td>
                  <td>
                    <LencanaStatus status={f.status} />
                  </td>
                  <td>
                    <DaftarAlasan kode={f.alasan} />
                  </td>
                  <td className="rapat">{waktu(f.diterima)}</td>
                  <td className="kanan angka">{detik(f.detik_sampai_jalur)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Kartu>
  );
}
