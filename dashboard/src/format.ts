const RUPIAH = new Intl.NumberFormat("id-ID", { style: "currency", currency: "IDR", maximumFractionDigits: 2, minimumFractionDigits: 0 });
const ANGKA = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 4 });
const WAKTU = new Intl.DateTimeFormat("id-ID", { dateStyle: "medium", timeStyle: "medium", timeZone: "Asia/Jakarta" });
const JAM = new Intl.DateTimeFormat("id-ID", { timeStyle: "medium", timeZone: "Asia/Jakarta" });

function bilangan(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function rupiah(value: unknown): string {
  const n = bilangan(value);
  return n === null ? "–" : RUPIAH.format(n);
}

export function angka(value: unknown): string {
  const n = bilangan(value);
  return n === null ? "–" : ANGKA.format(n);
}

export function waktu(value: string | null | undefined): string {
  return value ? `${WAKTU.format(new Date(value))} WIB` : "–";
}

export function jam(value: string | null | undefined): string {
  return value ? JAM.format(new Date(value)) : "";
}

export function detik(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : `${angka(Math.round(value * 10) / 10)} dtk`;
}

export const STATUS: Record<string, string> = {
  received: "Diterima",
  processing: "Diproses",
  awaiting_vendor: "Menunggu vendor",
  awaiting_approval: "Menunggu persetujuan",
  draft_created: "Draft tagihan dibuat",
  closed_duplicate: "Ditutup (duplikat)",
  rejected: "Ditolak",
  failed: "Gagal",
};

export const ALASAN: Record<string, string> = {
  price_variance: "Selisih harga",
  quantity_variance: "Selisih kuantitas",
  tax_invalid: "Pajak tidak valid",
  po_unresolved: "PO tidak ditemukan",
  duplicate_confirmed: "Duplikat terkonfirmasi",
  arithmetic_error: "Salah hitung",
  over_threshold: "Melebihi ambang",
  new_vendor: "Vendor baru",
  bank_account_mismatch: "Rekening berbeda",
  validation_failed: "Validasi gagal",
  prompt_injection: "Indikasi injeksi prompt",
  duplicate_suspected: "Dugaan duplikat",
  identity_mismatch: "Identitas tidak cocok",
  pilihan_jalur: "Pilihan jalur",
};

export const JALUR: Record<string, string> = {
  A: "Draft tagihan otomatis",
  B: "Klarifikasi ke vendor",
  C: "Persetujuan approver",
};

export const AKSI: Record<string, string> = {
  create_draft_bill: "Draft tagihan di Odoo",
  attach_file: "Lampiran berkas faktur",
  post_note: "Catatan di tagihan",
  send_email: "Email ke vendor",
};

export function alasan(kode: string): string {
  return ALASAN[kode] ?? kode;
}
