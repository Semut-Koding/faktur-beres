import { useState, type FormEvent } from "react";
import { kirim, kodeTersimpan, simpanKode } from "./api";
import { Kartu, Pesan } from "./bagian";

export function Unggah({ batas }: { batas: number }) {
  const [berkas, setBerkas] = useState<File | null>(null);
  const [kode, setKode] = useState(() => kodeTersimpan("ap"));
  const [galat, setGalat] = useState<string | null>(null);
  const [mengirim, setMengirim] = useState(false);
  const mb = Math.round(batas / 1024 / 1024);

  async function unggah(event: FormEvent) {
    event.preventDefault();
    if (!berkas) return setGalat("Pilih satu berkas faktur dulu.");
    if (berkas.size > batas) return setGalat(`Berkas melebihi ${mb} MB.`);
    if (!kode.trim()) return setGalat("Masukkan kode akses staf AP.");
    setMengirim(true);
    setGalat(null);
    try {
      const form = new FormData();
      form.append("berkas", berkas);
      const hasil = await kirim<{ id: string }>("/api/faktur", kode.trim(), form);
      simpanKode("ap", kode.trim());
      window.location.hash = `#/faktur/${hasil.id}`;
    } catch (e) {
      setGalat((e as Error).message);
      setMengirim(false);
    }
  }

  return (
    <Kartu judul="Unggah faktur">
      <form className="formulir" onSubmit={unggah}>
        <p className="redup">
          Hanya faktur sintetis dari set demo. Jangan mengunggah dokumen nyata: isi berkas dikirim ke LLM. Format PDF, JPG, PNG, atau XML UBL, paling besar {mb} MB.
        </p>
        <label>
          Berkas faktur
          <input type="file" accept=".pdf,.jpg,.jpeg,.png,.xml,application/pdf,image/jpeg,image/png,application/xml,text/xml" onChange={(e) => setBerkas(e.target.files?.[0] ?? null)} />
        </label>
        <label>
          Kode akses staf AP
          <input type="password" value={kode} autoComplete="off" onChange={(e) => setKode(e.target.value)} placeholder="Kode dari proposal" />
          <small className="redup">Disimpan di tab ini saja (sessionStorage) dan hilang saat tab ditutup.</small>
        </label>
        {galat && <Pesan jenis="galat">{galat}</Pesan>}
        <div>
          <button type="submit" className="utama" disabled={mengirim}>
            {mengirim ? "Mengunggah…" : "Unggah dan proses"}
          </button>
        </div>
      </form>
    </Kartu>
  );
}
