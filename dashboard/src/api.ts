// Client of the Faktur Beres API. Reads are public; actions send an access code.

export type Ringkas = {
  id: string;
  vendor: string | null;
  nomor_faktur: string | null;
  total: number | null;
  status: string;
  jalur: string | null;
  alasan: string[];
  diterima: string | null;
  detik_sampai_jalur: number | null;
  kanal: string | null;
  duplikat_dari: string | null;
  pesan_gagal: string | null;
};

export type Obj = Record<string, any>;

export type Detail = {
  faktur: Ringkas & {
    nama_berkas: string | null;
    mime: string | null;
    revisi: number | null;
    nomor_po: string | null;
    tanggal_faktur: string | null;
    pengirim: string | null;
    jalur_diizinkan: string[];
  };
  ekstraksi: { status: string | null; sumber: string | null; catatan: string[]; invoice: Obj | null };
  pencocokan: { nomor_po: string | null; baris: Obj[] };
  temuan: Obj[];
  catatan: string[];
  persetujuan: {
    bukti: Obj | null;
    alasan: string[];
    usulan: string | null;
    pilihan: string[];
    terbuka: boolean;
    keputusan: string | null;
    oleh: string | null;
    catatan: string | null;
    diputuskan: string | null;
  } | null;
  aksi: { jenis: string; target: string; status: string; hasil: Obj | null; galat: string | null; waktu: string | null }[];
  sibuk: boolean;
};

export type Jejak = {
  id: number;
  at: string | null;
  node: string | null;
  kind: string;
  name: string;
  params: unknown;
  summary: string | null;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  thinking_tokens: number | null;
  cost_usd: number | null;
  model: string | null;
  fallback: boolean | null;
  status: string;
};

export type Konfigurasi = { mailpit_url: string | null; odoo_url: string | null; batas_berkas_byte: number };

export class GalatApi extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function baca(response: Response): Promise<any> {
  const text = await response.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : `Permintaan gagal (${response.status}).`;
    throw new GalatApi(response.status, detail);
  }
  return body;
}

export async function ambil<T>(path: string): Promise<T> {
  return baca(await fetch(path, { headers: { Accept: "application/json" } }));
}

export async function kirim<T>(path: string, kode: string, body?: FormData | Obj): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${kode}` };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  return baca(await fetch(path, { method: "POST", headers, body: payload }));
}

const KUNCI = { ap: "fb-kode-ap", approver: "fb-kode-approver" } as const;
export type Peran = keyof typeof KUNCI;

export function kodeTersimpan(peran: Peran): string {
  try {
    return sessionStorage.getItem(KUNCI[peran]) ?? "";
  } catch {
    return "";
  }
}

export function simpanKode(peran: Peran, kode: string): void {
  try {
    if (kode) sessionStorage.setItem(KUNCI[peran], kode);
    else sessionStorage.removeItem(KUNCI[peran]);
  } catch {
    /* session storage unavailable: the code stays in memory for this page only */
  }
}
