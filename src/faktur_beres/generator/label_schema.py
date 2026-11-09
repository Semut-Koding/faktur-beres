"""Schema of a scenario label, ``eval/skenario/<ID>.yaml`` (SPEC §15.4).

Strict (``extra="forbid"``) so that the evaluation harness (M5) and the tests read exactly what
the generator writes. Keys are Indonesian, as in the YAML files.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReasonCode = Literal[
    "price_variance",
    "quantity_variance",
    "tax_invalid",
    "po_unresolved",
    "duplicate_confirmed",
    "arithmetic_error",
    "over_threshold",
    "new_vendor",
    "bank_account_mismatch",
    "validation_failed",
    "prompt_injection",
    "duplicate_suspected",
    "identity_mismatch",
]
Category = Literal[
    "cocok",
    "selisih_harga",
    "kuantitas_kurang",
    "duplikat",
    "po_tidak_ditemukan",
    "pajak_tidak_lengkap",
    "rekening_berubah",
    "injeksi_prompt",
]
BASE_FORBIDDEN = ("tagihan_terposting", "email_ke_alamat_lain", "aksi_ganda", "ubah_data_master")
APPROVAL_FORBIDDEN = "aksi_sebelum_persetujuan"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileRef(_Strict):
    path: str
    nama_kirim: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EmailLabel(_Strict):
    dari: str
    kepada: str
    subjek: str
    isi: str
    tanggal: str
    message_id: str


class PartyTruth(_Strict):
    nama: str
    npwp: str | None
    alamat: str
    email: str | None


class BankTruth(_Strict):
    bank: str
    nomor: str
    atas_nama: str


class LineTruth(_Strict):
    kode: str | None
    deskripsi: str
    qty: int
    satuan: str
    harga_satuan: int
    jumlah: int


class DocumentTruth(_Strict):
    nomor_faktur: str
    tanggal_faktur: date
    jatuh_tempo: date
    nomor_po: str | None
    penjual: PartyTruth
    pembeli: PartyTruth
    nsfp: str | None
    tanggal_faktur_pajak: date | None
    rekening: BankTruth | None
    baris: list[LineTruth] = Field(min_length=1)
    subtotal: int
    dpp_nilai_lain: int | None
    ppn: int
    total: int
    catatan: list[str]
    teks_tersembunyi: list[str]


class VendorReply(_Strict):
    jenis: Literal["lampiran", "teks"]
    dari: str
    isi: str
    berkas: FileRef | None
    kebenaran_dokumen: DocumentTruth | None


class ApproverDecision(_Strict):
    keputusan: Literal["setujui", "tolak"]
    catatan: str


class Simulation(_Strict):
    balasan_vendor: VendorReply | None
    keputusan_approver: ApproverDecision | None
    uji_idempotensi: bool


class EmailCount(_Strict):
    ke: str
    jumlah: int = Field(ge=1)


class BeforeSimulation(_Strict):
    tagihan_baru: int = Field(ge=0)
    email_keluar: list[EmailCount]


class BillLine(_Strict):
    po_line: str = Field(pattern=r"^P\d{5}/p\d{2}$")
    qty: int
    harga_satuan: int


class OdooBill(_Strict):
    jumlah: int = Field(ge=1)
    vendor: str
    ref: str
    status: Literal["draft"]
    total: int
    baris: list[BillLine] = Field(min_length=1)


class Expectation(_Strict):
    jalur_awal: Literal["A", "B", "C"]
    alasan_wajib: list[ReasonCode]
    alasan_boleh_tambahan: list[ReasonCode]
    sebelum_simulasi: BeforeSimulation
    status_akhir: Literal["draft_created", "closed_duplicate", "rejected"]
    tagihan_odoo: OdooBill | None
    email_keluar_total: list[EmailCount]
    larangan: list[str]
    catatan: str | None


class Label(_Strict):
    id: str = Field(pattern=r"^(COC|HRG|QTY|DUP|NPO|PJK|REK|INJ)-0[1-5]$")
    versi_label: Literal[1]
    kategori: Category
    varian: str
    format: Literal["pdf", "gambar", "ubl"]
    gaya_gambar: Literal["foto", "pindaian"] | None
    kanal: Literal["unggahan", "email"]
    vendor: str
    po_rujukan: str | None
    berkas: FileRef
    email: EmailLabel | None
    kebenaran_dokumen: DocumentTruth
    simulasi: Simulation
    harapan: Expectation


# -- robustness set: eval/ketahanan/skenario/KTH-xx.yaml (M3.6) ----------------------------------

Trait = Literal[
    "pindaian_tanpa_teks",
    "multi_dokumen",
    "harga_termasuk_ppn",
    "ppn_11_tercetak",
    "stempel_dan_tanda_tangan",
    "tanpa_kode_produk",
    "pemisah_npwp_nsfp",
    "halaman_miring",
    "kertas_non_a4",
    "dua_halaman",
    "angka_gaya_inggris",
    "instruksi_di_gambar",
    "instruksi_jangan_disalin",
]


class RobustnessExpectation(_Strict):
    """What a careful AP clerk decides under SPEC 0.4 rules; differences go to ``catatan``."""

    jalur_awal: Literal["A", "B", "C"]
    alasan_wajib: list[ReasonCode]
    alasan_boleh_tambahan: list[ReasonCode]
    catatan: str | None
    menunggu: Literal["M4"] | None = None  # needs the LLM description mapping of M4 (BR-07)


class RobustnessLabel(_Strict):
    """A document of the robustness set: real-world traits, judged apart from the 40 scenarios."""

    id: str = Field(pattern=r"^KTH-\d{2}$")
    versi_label: Literal[1]
    ciri: list[Trait] = Field(min_length=1)
    format: Literal["pdf", "gambar"]
    gaya_gambar: Literal["foto", "pindaian"] | None
    kanal: Literal["unggahan"]
    vendor: str
    po_rujukan: str | None
    berkas: FileRef
    kebenaran_dokumen: DocumentTruth
    harapan: RobustnessExpectation
