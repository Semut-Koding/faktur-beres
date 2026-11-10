import { useCallback, useEffect, useRef, useState } from "react";
import { ambil, kirim, kodeTersimpan, simpanKode, type Detail, type Jejak, type Konfigurasi, type Obj } from "./api";
import { DaftarAlasan, Kartu, LencanaJalur, LencanaStatus, Pesan } from "./bagian";
import { AKSI, JALUR, alasan, angka, detik, jam, rupiah, waktu } from "./format";
import { usePembaruan } from "./hooks";

const BERJALAN = new Set(["received", "processing"]);

export function DetailFaktur({ id, konfigurasi }: { id: string; konfigurasi: Konfigurasi | null }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [jejak, setJejak] = useState<Jejak[]>([]);
  const [galat, setGalat] = useState<string | null>(null);

  const muat = useCallback(
    (faktur: string[] | null = null) => {
      if (faktur && !faktur.includes(id)) return;
      Promise.all([ambil<Detail>(`/api/faktur/${id}`), ambil<Jejak[]>(`/api/faktur/${id}/jejak`)])
        .then(([d, j]) => {
          setDetail(d);
          setJejak(j);
          setGalat(null);
        })
        .catch((e: Error) => setGalat(e.message));
    },
    [id],
  );
  useEffect(() => muat(), [muat]);
  usePembaruan(muat);
  // While the agent is working, also poll: a status change must not wait for the next event.
  const sibuk = detail ? detail.sibuk || BERJALAN.has(detail.faktur.status) : false;
  useEffect(() => {
    if (!sibuk) return;
    const t = window.setInterval(() => muat(), 2000);
    return () => window.clearInterval(t);
  }, [sibuk, muat]);

  if (galat && !detail) {
    return (
      <>
        <p><a href="#/">← Antrean</a></p>
        <Pesan jenis="galat">{galat}</Pesan>
      </>
    );
  }
  if (!detail) return <p className="redup">Memuat…</p>;
  const f = detail.faktur;

  return (
    <>
      <p className="remah"><a href="#/">← Antrean</a></p>
      <section className="kartu ringkasan">
        <div className="ringkasan-atas">
          <h1>{f.id}</h1>
          <LencanaJalur jalur={f.jalur} />
          <LencanaStatus status={f.status} />
          {sibuk && <span className="berputar" role="status">Agent sedang bekerja…</span>}
        </div>
        <dl className="butir">
          <div><dt>Vendor</dt><dd>{f.vendor ?? "belum dikenali"}</dd></div>
          <div><dt>No. faktur</dt><dd>{f.nomor_faktur ?? "–"}</dd></div>
          <div><dt>Total</dt><dd className="angka">{rupiah(f.total)}</dd></div>
          <div><dt>PO</dt><dd>{f.nomor_po ?? "–"}</dd></div>
          <div><dt>Diterima</dt><dd>{waktu(f.diterima)} · {f.kanal === "email" ? "email" : "unggahan"}</dd></div>
          <div><dt>Sampai jalur</dt><dd>{detik(f.detik_sampai_jalur)}</dd></div>
        </dl>
        {f.jalur && <p className="redup">Jalur {f.jalur}: {JALUR[f.jalur]}.</p>}
        {f.alasan.length > 0 && <p>Alasan: <DaftarAlasan kode={f.alasan} /></p>}
        {f.duplikat_dari && <p>Duplikat dari <a href={`#/faktur/${f.duplikat_dari}`}>{f.duplikat_dari}</a>.</p>}
        {f.pesan_gagal && <Pesan jenis="galat">{f.pesan_gagal.replace(/ Coba lagi dengan npm run agent:ulang\.| Periksa hak agent, lalu jalankan npm run agent:ulang\./, "")}</Pesan>}
        {f.status === "failed" && !sibuk && <Ulang id={id} selesai={() => muat()} />}
      </section>

      {detail.persetujuan && <Persetujuan id={id} p={detail.persetujuan} sibuk={sibuk} selesai={() => muat()} />}

      <div className="dua-kolom lebar">
        <Kartu judul="Berkas asli" samping={<span className="redup">{f.nama_berkas}{f.revisi && f.revisi > 1 ? ` · revisi ${f.revisi}` : ""}</span>}>
          <Berkas id={id} mime={f.mime} revisi={f.revisi} />
        </Kartu>
        <Kartu judul="Hasil ekstraksi" samping={<span className="redup">{detail.ekstraksi.sumber ? `sumber: ${detail.ekstraksi.sumber}` : ""}</span>}>
          <Ekstraksi inv={detail.ekstraksi.invoice} status={detail.ekstraksi.status} />
        </Kartu>
      </div>

      <Kartu judul="Pencocokan tiga arah" samping={<span className="redup">{detail.pencocokan.nomor_po ? `PO ${detail.pencocokan.nomor_po}` : "PO belum ditemukan"}</span>}>
        <Pencocokan baris={detail.pencocokan.baris} />
      </Kartu>

      <Kartu judul="Temuan dan alasan">
        <Temuan temuan={detail.temuan} catatan={detail.catatan} />
      </Kartu>

      <Kartu judul="Aksi yang dijalankan">
        <Aksi aksi={detail.aksi} konfigurasi={konfigurasi} />
      </Kartu>

      <Kartu judul="Jejak penalaran" samping={<span className="redup">{jejak.length} langkah · NPWP dan rekening disamarkan</span>}>
        <Linimasa jejak={jejak} />
      </Kartu>
    </>
  );
}

function Berkas({ id, mime, revisi }: { id: string; mime: string | null; revisi: number | null }) {
  const src = `/api/faktur/${id}/berkas?r=${revisi ?? 1}`;
  const [teks, setTeks] = useState<string | null>(null);
  const [gagal, setGagal] = useState(false);
  const xml = !!mime && mime.includes("xml");
  useEffect(() => {
    if (!xml) return;
    fetch(src)
      .then((r) => (r.ok ? r.text() : Promise.reject()))
      .then(setTeks)
      .catch(() => setGagal(true));
  }, [src, xml]);
  if (!mime) return <p className="redup">Berkas belum tersedia.</p>;
  if (gagal) return <p className="redup">Berkas tidak tersedia di server ini.</p>;
  if (xml) return <pre className="berkas-teks">{teks ?? "Memuat…"}</pre>;
  if (mime.startsWith("image/")) return <img className="berkas-gambar" src={src} alt="Berkas faktur asli" onError={() => setGagal(true)} />;
  return (
    <>
      <iframe className="berkas-pdf" src={src} title="Berkas faktur asli (PDF)" />
      <p><a href={src} target="_blank" rel="noreferrer">Buka berkas di tab baru</a></p>
    </>
  );
}

function Ekstraksi({ inv, status }: { inv: Obj | null; status: string | null }) {
  if (!inv) return <p className="redup">{status && status !== "ok" ? `Ekstraksi: ${status}.` : "Belum ada hasil ekstraksi."}</p>;
  const s = inv.seller ?? {};
  const b = inv.buyer ?? {};
  const bank = inv.bank_account ?? {};
  const baris: [string, string | null | undefined][] = [
    ["Penjual", s.name],
    ["NPWP penjual", s.npwp],
    ["Email penjual", s.email],
    ["Pembeli", b.name],
    ["NPWP pembeli", b.npwp],
    ["Nomor faktur", inv.invoice_number],
    ["Tanggal faktur", inv.invoice_date],
    ["Jatuh tempo", inv.due_date],
    ["Nomor PO tercetak", inv.po_number],
    ["NSFP", inv.nsfp],
    ["Subtotal", rupiah(inv.subtotal)],
    ["DPP nilai lain", inv.dpp_nilai_lain ? rupiah(inv.dpp_nilai_lain) : null],
    ["PPN", rupiah(inv.vat)],
    ["Total", rupiah(inv.total)],
    ["Rekening", bank.number ? `${bank.bank ?? ""} ${bank.number} a.n. ${bank.holder ?? "–"}` : null],
  ];
  return (
    <>
      <dl className="daftar">
        {baris.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v || "–"}</dd>
          </div>
        ))}
      </dl>
      <p className="redup kecil">NPWP tampil 4 digit awal dan akhir, rekening 4 digit akhir (BR-24).</p>
    </>
  );
}

function beda(a: unknown, b: unknown): boolean {
  if (a === null || a === undefined || b === null || b === undefined) return false;
  return Math.abs(Number(a) - Number(b)) > 1e-6;
}

function Pencocokan({ baris }: { baris: Obj[] }) {
  if (!baris.length) return <p className="redup">Belum ada baris yang dicocokkan.</p>;
  return (
    <div className="gulir">
      <table className="tabel">
        <thead>
          <tr>
            <th>#</th>
            <th>Baris faktur</th>
            <th>Baris PO</th>
            <th className="kanan">Qty faktur</th>
            <th className="kanan">Qty diterima</th>
            <th className="kanan">Harga faktur</th>
            <th className="kanan">Harga PO</th>
            <th className="kanan">Jumlah</th>
            <th>Hasil</th>
          </tr>
        </thead>
        <tbody>
          {baris.map((b) => {
            const fk = b.faktur ?? {};
            const po = b.po as Obj | null;
            const sisa = po ? Number(po.qty_received) - Number(po.qty_invoiced) : null;
            const qtyBeda = b.kuantitas_cocok === false || (sisa !== null && Number(fk.quantity) > sisa + 1e-6);
            const hargaBeda = b.harga_cocok === false || (po ? beda(fk.unit_price, po.price_unit) : false);
            const cocok = b.status === "matched" && !qtyBeda && !hargaBeda;
            return (
              <tr key={b.baris} className={cocok ? "" : "selisih-baris"}>
                <td>{b.baris}</td>
                <td>{fk.description}{fk.vendor_code ? <span className="redup"> · {fk.vendor_code}</span> : null}</td>
                <td>{po ? po.description : <span className="tanda">tidak ada padanan</span>}</td>
                <td className={`kanan angka ${qtyBeda ? "selisih" : ""}`}>{angka(fk.quantity)} {fk.unit}</td>
                <td className={`kanan angka ${qtyBeda ? "selisih" : ""}`}>{po ? `${angka(po.qty_received)} (${angka(po.qty_invoiced)} sudah ditagih)` : "–"}</td>
                <td className={`kanan angka ${hargaBeda ? "selisih" : ""}`}>{rupiah(fk.unit_price)}</td>
                <td className={`kanan angka ${hargaBeda ? "selisih" : ""}`}>{po ? rupiah(po.price_unit) : "–"}</td>
                <td className="kanan angka">{rupiah(fk.amount)}</td>
                <td>
                  <span className={`keping ${cocok ? "baik" : "buruk"}`}>{cocok ? "Cocok" : hargaBeda ? "Selisih harga" : qtyBeda ? "Selisih kuantitas" : (b.status ?? "belum")}</span>
                  {b.metode && <span className="redup kecil"> via {b.metode}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Temuan({ temuan, catatan }: { temuan: Obj[]; catatan: string[] }) {
  const alasanTemuan = temuan.filter((t) => t.reason);
  const lain = temuan.filter((t) => !t.reason);
  if (!temuan.length && !catatan.length) return <p className="redup">Tidak ada temuan: semua aturan lulus.</p>;
  return (
    <>
      {alasanTemuan.length > 0 && (
        <ul className="temuan">
          {alasanTemuan.map((t, i) => (
            <li key={i}>
              <span className="keping buruk">{alasan(t.reason)}</span> <span className="keping">{t.rule}</span>
              <p>{t.message}</p>
            </li>
          ))}
        </ul>
      )}
      {(lain.length > 0 || catatan.length > 0) && (
        <>
          <h3>Catatan</h3>
          <ul className="catatan">
            {lain.map((t, i) => (
              <li key={`t${i}`}><span className="keping">{t.rule}</span> {t.message}</li>
            ))}
            {catatan.filter((c) => !lain.some((t) => t.message === c)).map((c, i) => (
              <li key={`c${i}`}>{c}</li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

const NAMA_PILIHAN: Record<string, string> = { setujui: "Setujui", tolak: "Tolak", klarifikasi: "Minta klarifikasi vendor" };
const HASIL_PILIHAN: Record<string, string> = { setujui: "Disetujui", tolak: "Ditolak", klarifikasi: "Klarifikasi ke vendor diminta" };

function Persetujuan({ id, p, sibuk, selesai }: { id: string; p: NonNullable<Detail["persetujuan"]>; sibuk: boolean; selesai: () => void }) {
  const [kode, setKode] = useState(() => kodeTersimpan("approver"));
  const [catatan, setCatatan] = useState("");
  const [galat, setGalat] = useState<string | null>(null);
  const [terkirim, setTerkirim] = useState<string | null>(null);
  const bukti = p.bukti ?? {};
  const kutipan = (bukti.policy_quotes ?? []) as Obj[];
  const temuan = (bukti.alasan ?? []) as Obj[];
  const pilihanJalur = bukti.pilihan_jalur as Obj | null;
  const verifikasi = (bukti.verifikasi_tagihan ?? []) as string[];

  async function putuskan(keputusan: string) {
    if (!kode.trim()) return setGalat("Masukkan kode akses approver.");
    setGalat(null);
    setTerkirim(keputusan);
    try {
      await kirim(`/api/faktur/${id}/keputusan`, kode.trim(), { keputusan, catatan: catatan.trim() || null });
      simpanKode("approver", kode.trim());
      selesai();
    } catch (e) {
      setGalat((e as Error).message);
      setTerkirim(null);
    }
  }

  return (
    <Kartu judul="Paket bukti untuk approver" samping={p.terbuka ? <span className="keping peringatan">Menunggu keputusan</span> : p.keputusan ? <span className="keping">{HASIL_PILIHAN[p.keputusan]}</span> : null}>
      {p.usulan && <p><strong>Usulan agent:</strong> {p.usulan}</p>}
      {temuan.length > 0 && (
        <ul className="temuan">
          {temuan.map((t, i) => (
            <li key={i}>
              <span className="keping buruk">{alasan(t.reason)}</span> <span className="keping">{t.rule}</span>
              <p>{t.message}</p>
            </li>
          ))}
        </ul>
      )}
      {pilihanJalur?.pertimbangan && <p><strong>Pertimbangan pilihan jalur ({pilihanJalur.oleh ?? "agent"}):</strong> {pilihanJalur.pertimbangan}</p>}
      {verifikasi.length > 0 && <p><strong>Verifikasi tagihan:</strong> {verifikasi.join("; ")}</p>}
      {kutipan.length > 0 && (
        <>
          <h3>Kutipan kebijakan</h3>
          <ul className="kutipan">
            {kutipan.map((k, i) => (
              <li key={i}>
                <blockquote>{k.kutipan}</blockquote>
                <cite>{k.judul} · {k.ref}{k.alasan ? ` · untuk: ${alasan(k.alasan)}` : ""}</cite>
              </li>
            ))}
          </ul>
        </>
      )}
      {bukti.setujui_tidak_tersedia && <Pesan jenis="info">{bukti.setujui_tidak_tersedia}</Pesan>}
      {p.terbuka && !p.pilihan.includes("setujui") && !bukti.setujui_tidak_tersedia && (
        <Pesan jenis="info">“Setujui” tidak tersedia: draft tagihan tidak bisa disusun otomatis dari faktur ini.</Pesan>
      )}
      {p.terbuka && !p.pilihan.includes("klarifikasi") && <Pesan jenis="info">“Minta klarifikasi vendor” tidak tersedia: vendor belum teridentifikasi, jadi tidak ada alamat penagihan terdaftar.</Pesan>}

      {p.terbuka ? (
        <div className="keputusan">
          <label>
            Catatan approver (opsional)
            <textarea value={catatan} maxLength={500} rows={2} onChange={(e) => setCatatan(e.target.value)} />
          </label>
          <label>
            Kode akses approver
            <input type="password" value={kode} autoComplete="off" onChange={(e) => setKode(e.target.value)} placeholder="Kode dari proposal" />
          </label>
          {galat && <Pesan jenis="galat">{galat}</Pesan>}
          <div className="tombol-deret">
            {p.pilihan.map((nama) => (
              <button key={nama} type="button" className={nama === "setujui" ? "utama" : nama === "tolak" ? "bahaya" : ""} disabled={sibuk || terkirim !== null} onClick={() => putuskan(nama)}>
                {terkirim === nama ? "Mengirim…" : NAMA_PILIHAN[nama]}
              </button>
            ))}
          </div>
        </div>
      ) : (
        p.keputusan && (
          <p className="redup">
            {HASIL_PILIHAN[p.keputusan]} oleh {p.oleh ?? "approver"} pada {waktu(p.diputuskan)}.{p.catatan ? ` Catatan: ${p.catatan}` : ""}
          </p>
        )
      )}
    </Kartu>
  );
}

function Ulang({ id, selesai }: { id: string; selesai: () => void }) {
  const [kode, setKode] = useState(() => kodeTersimpan("ap"));
  const [galat, setGalat] = useState<string | null>(null);
  async function ulang() {
    if (!kode.trim()) return setGalat("Masukkan kode akses staf AP.");
    try {
      await kirim(`/api/faktur/${id}/ulang`, kode.trim());
      simpanKode("ap", kode.trim());
      setGalat(null);
      selesai();
    } catch (e) {
      setGalat((e as Error).message);
    }
  }
  return (
    <div className="keputusan">
      <label>
        Kode akses staf AP
        <input type="password" value={kode} autoComplete="off" onChange={(e) => setKode(e.target.value)} />
      </label>
      {galat && <Pesan jenis="galat">{galat}</Pesan>}
      <div><button type="button" onClick={ulang}>Jalankan ulang dari langkah terakhir</button></div>
    </div>
  );
}

function Aksi({ aksi, konfigurasi }: { aksi: Detail["aksi"]; konfigurasi: Konfigurasi | null }) {
  if (!aksi.length) return <p className="redup">Belum ada aksi ke Odoo atau email.</p>;
  const odoo = konfigurasi?.odoo_url?.replace(/\/$/, "");
  const mailpit = konfigurasi?.mailpit_url?.replace(/\/$/, "");
  return (
    <ul className="aksi">
      {aksi.map((a, i) => {
        const h = a.hasil ?? {};
        let tautan = null;
        if (a.jenis === "create_draft_bill" && h.bill_id && odoo) {
          tautan = <a href={`${odoo}/odoo/action-account.action_move_in_invoice_type/${h.bill_id}`} target="_blank" rel="noreferrer">Buka draft #{h.bill_id} di Odoo</a>;
        } else if (a.jenis === "send_email" && mailpit) {
          tautan = <a href={`${mailpit}/search?q=${encodeURIComponent(`to:${a.target}`)}`} target="_blank" rel="noreferrer">Lihat di Mailpit</a>;
        }
        return (
          <li key={i}>
            <span className={`keping ${a.status === "done" ? "baik" : a.status === "failed" ? "buruk" : ""}`}>{a.status === "done" ? "selesai" : a.status === "failed" ? "gagal" : "berjalan"}</span>
            <strong>{AKSI[a.jenis] ?? a.jenis}</strong>
            <span className="redup">{a.target}</span>
            {a.jenis === "create_draft_bill" && h.bill_id && <span>draft #{h.bill_id} ({h.outcome})</span>}
            {a.jenis === "send_email" && h.message_id && <code className="kecil">Message-ID {h.message_id}</code>}
            {a.galat && <span className="tanda">{a.galat}</span>}
            {tautan}
          </li>
        );
      })}
    </ul>
  );
}

const JENIS: Record<string, string> = { node: "node", tool: "alat", llm: "LLM", guard: "pagar", decision: "keputusan", security: "keamanan" };

function Linimasa({ jejak }: { jejak: Jejak[] }) {
  const [rinci, setRinci] = useState(false);
  const ujung = useRef<HTMLOListElement>(null);
  if (!jejak.length) return <p className="redup">Belum ada jejak.</p>;
  // A node's own row closes its group; show it as the group's footer with the duration.
  const tampil = rinci ? jejak : jejak.filter((j) => !(j.kind === "node" && j.name === j.node));
  return (
    <>
      <label className="centang">
        <input type="checkbox" checked={rinci} onChange={(e) => setRinci(e.target.checked)} /> Tampilkan juga baris penutup node
      </label>
      <ol className="linimasa" ref={ujung}>
        {tampil.map((j) => (
          <li key={j.id} className={`jenis-${j.kind} ${j.status !== "ok" ? "tidak-ok" : ""}`}>
            <time>{jam(j.at)}</time>
            <span className={`keping jenis jenis-${j.kind}`}>{JENIS[j.kind] ?? j.kind}</span>
            <span className="simpul">{j.node ?? "–"}</span>
            <div className="isi-jejak">
              <strong>{j.name}</strong>
              {j.status !== "ok" && <span className="keping buruk">{j.status}</span>}
              {j.params !== null && j.params !== undefined && <code className="param">{JSON.stringify(j.params)}</code>}
              {j.summary && <span className="hasil">→ {j.summary}</span>}
              <span className="redup kecil">
                {j.model ? `${j.model}${j.fallback ? " (cadangan)" : ""} · token ${j.input_tokens ?? 0}/${j.output_tokens ?? 0}/${j.thinking_tokens ?? 0}` : ""}
                {j.duration_ms !== null ? ` ${j.model ? "· " : ""}${angka(j.duration_ms)} ms` : ""}
              </span>
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}
