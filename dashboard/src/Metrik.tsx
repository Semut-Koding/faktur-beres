import { useCallback, useEffect, useState } from "react";
import { ambil, type Obj } from "./api";
import { Kartu, Pesan } from "./bagian";
import { JALUR, STATUS, angka, detik } from "./format";
import { usePembaruan } from "./hooks";

const USD = new Intl.NumberFormat("id-ID", { style: "currency", currency: "USD", maximumFractionDigits: 4 });

function Angka({ nama, nilai, ket }: { nama: string; nilai: string; ket?: string }) {
  return (
    <div className="angka-kartu">
      <span className="angka-nama">{nama}</span>
      <strong className="angka-nilai">{nilai}</strong>
      {ket && <span className="redup">{ket}</span>}
    </div>
  );
}

export function Metrik() {
  const [m, setM] = useState<Obj | null>(null);
  const [galat, setGalat] = useState<string | null>(null);
  const muat = useCallback(() => {
    ambil<Obj>("/api/metrik")
      .then((d) => {
        setM(d);
        setGalat(null);
      })
      .catch((e: Error) => setGalat(e.message));
  }, []);
  useEffect(muat, [muat]);
  usePembaruan(muat);

  if (galat) return <Pesan jenis="galat">Metrik tidak bisa dimuat: {galat}</Pesan>;
  if (!m) return <p className="redup">Memuat…</p>;
  const ev = m.evaluasi as Obj | null;
  const rate = m.touchless_rate === null ? "–" : `${angka(Math.round(m.touchless_rate * 1000) / 10)}%`;
  return (
    <>
      <Kartu judul="Metrik demo ini">
        <div className="angka-deret">
          <Angka nama="Faktur masuk" nilai={angka(m.total)} ket={`${angka(m.selesai)} sudah selesai`} />
          <Angka nama="Touchless rate" nilai={rate} ket={`${angka(m.touchless)} selesai di jalur A tanpa approver`} />
          <Angka nama="Median sampai jalur" nilai={detik(m.median_detik_jalur)} ket="dari diterima sampai jalur ditentukan" />
          <Angka nama="Biaya LLM setara" nilai={USD.format(Number(m.llm.biaya_usd) || 0)} ket={`${angka(m.llm.panggilan)} panggilan tercatat di audit`} />
        </div>
        <div className="dua-kolom">
          <div>
            <h3>Per jalur</h3>
            <table className="tabel">
              <tbody>
                {Object.entries(m.per_jalur as Record<string, number>).map(([k, v]) => (
                  <tr key={k}>
                    <td>{k === "belum" ? "Belum ditentukan" : `Jalur ${k} – ${JALUR[k] ?? ""}`}</td>
                    <td className="kanan angka">{angka(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <h3>Per status</h3>
            <table className="tabel">
              <tbody>
                {Object.entries(m.per_status as Record<string, number>).map(([k, v]) => (
                  <tr key={k}>
                    <td>{STATUS[k] ?? k}</td>
                    <td className="kanan angka">{angka(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Kartu>
      {ev && (
        <Kartu judul="Hasil evaluasi terakhir" samping={<span className="redup">{ev.tanggal} · {ev.skenario} skenario</span>}>
          <div className="gulir">
            <table className="tabel">
              <thead>
                <tr>
                  <th>Mode</th>
                  <th>Pertama berhenti</th>
                  <th>Status akhir</th>
                  <th>Ketahanan</th>
                  <th className="kanan">Balasan</th>
                  <th className="kanan">Pilihan jalur LLM</th>
                  <th className="kanan">Tidak selesai</th>
                </tr>
              </thead>
              <tbody>
                {(ev.mode as Obj[]).map((x) => (
                  <tr key={x.nama}>
                    <td>{x.nama}</td>
                    <td>{x.pertama_berhenti}</td>
                    <td>{x.status_akhir}</td>
                    <td>{x.ketahanan}</td>
                    <td className="kanan angka">{x.balasan}</td>
                    <td className="kanan angka">{x.pilihan_jalur_llm}</td>
                    <td className="kanan angka">{x.tidak_selesai}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="angka-deret">
            <Angka nama="Median sampai jalur" nilai={detik(ev.waktu_sampai_jalur_detik.median)} ket={`p95 ${detik(ev.waktu_sampai_jalur_detik.p95)}, n = ${ev.waktu_sampai_jalur_detik.n}`} />
            <Angka nama="Biaya per faktur" nilai={USD.format(ev.biaya_llm_segar.biaya_usd_per_faktur)} ket={`${angka(ev.biaya_llm_segar.panggilan_flash_lite)} panggilan Flash-Lite, total ${USD.format(ev.biaya_llm_segar.biaya_usd)}`} />
          </div>
          <p className="redup">Batas tafsir: {ev.batas_tafsir}</p>
          <p className="redup">Sumber: {ev.sumber}.</p>
        </Kartu>
      )}
    </>
  );
}
