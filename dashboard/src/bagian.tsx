import type { ReactNode } from "react";
import { JALUR, STATUS, alasan } from "./format";

export function LencanaJalur({ jalur }: { jalur: string | null }) {
  if (!jalur) return <span className="lencana jalur-kosong" title="Jalur belum ditentukan">–</span>;
  return (
    <span className={`lencana jalur-${jalur.toLowerCase()}`} title={JALUR[jalur] ?? jalur}>
      Jalur {jalur}
    </span>
  );
}

export function LencanaStatus({ status }: { status: string }) {
  return <span className={`lencana status-${status}`}>{STATUS[status] ?? status}</span>;
}

export function DaftarAlasan({ kode }: { kode: string[] }) {
  if (!kode.length) return <span className="redup">–</span>;
  return (
    <span className="alasan">
      {kode.map((k) => (
        <span key={k} className="keping" title={k}>
          {alasan(k)}
        </span>
      ))}
    </span>
  );
}

export function Kartu({ judul, samping, children }: { judul: string; samping?: ReactNode; children: ReactNode }) {
  return (
    <section className="kartu">
      <div className="kartu-kepala">
        <h2>{judul}</h2>
        {samping}
      </div>
      {children}
    </section>
  );
}

export function Pesan({ jenis, children }: { jenis: "galat" | "info" | "sukses"; children: ReactNode }) {
  return (
    <p className={`pesan pesan-${jenis}`} role={jenis === "galat" ? "alert" : "status"}>
      {children}
    </p>
  );
}
