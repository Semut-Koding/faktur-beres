"""Document style per vendor (generator configuration, not dataset data).

Four layout families (classic ruled, modern with a colour band, compact, dot-matrix print), two
number formats, three date formats, and amount-in-words for some vendors. A vendor keeps its
style on every document. Logos are simple geometric shapes with the vendor's initials; nothing
imitates a real brand.
"""

from dataclasses import dataclass
from typing import Literal

from faktur_beres.generator.formatting import DateFormat, NumberFormat

Layout = Literal["klasik", "modern", "ringkas", "dotmatrix"]
Shape = Literal["lingkaran", "persegi", "belah_ketupat", "segitiga", "segienam"]


@dataclass(frozen=True)
class VendorStyle:
    layout: Layout
    accent: tuple[float, float, float]
    shape: Shape
    initials: str
    title: str  # FAKTUR or INVOICE
    number_format: NumberFormat
    date_format: DateFormat
    terbilang: bool

    @property
    def fonts(self) -> tuple[str, str, str]:
        """(regular, bold, italic) built-in PDF fonts."""
        if self.layout == "klasik":
            return ("Times-Roman", "Times-Bold", "Times-Italic")
        if self.layout == "dotmatrix":
            return ("Courier", "Courier-Bold", "Courier-Oblique")
        return ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique")


STYLES: dict[str, VendorStyle] = {
    "v01": VendorStyle(
        "modern", (0.13, 0.25, 0.47), "lingkaran", "KSM", "FAKTUR", "rp", "long", True
    ),
    "v02": VendorStyle(
        "klasik", (0.45, 0.12, 0.15), "persegi", "TWL", "INVOICE", "idr", "dmy_slash", False
    ),
    "v03": VendorStyle(
        "ringkas", (0.12, 0.38, 0.25), "belah_ketupat", "PAS", "FAKTUR", "rp", "dmon", True
    ),
    "v04": VendorStyle(
        "modern", (0.00, 0.42, 0.45), "segitiga", "CFP", "INVOICE", "idr", "dmon", False
    ),
    "v05": VendorStyle(
        "dotmatrix", (0.15, 0.15, 0.15), "persegi", "RPK", "NOTA FAKTUR", "rp", "dmy_slash", True
    ),
    "v06": VendorStyle(
        "modern", (0.80, 0.38, 0.10), "segienam", "PSC", "INVOICE", "idr", "dmon", False
    ),
    "v07": VendorStyle(
        "klasik", (0.35, 0.20, 0.50), "lingkaran", "LKP", "FAKTUR", "rp", "long", True
    ),
    "v08": VendorStyle(
        "ringkas", (0.45, 0.30, 0.15), "persegi", "GKS", "FAKTUR", "rp", "dmy_slash", False
    ),
    "v09": VendorStyle(
        "modern", (0.62, 0.12, 0.42), "belah_ketupat", "GLC", "INVOICE", "rp", "long", False
    ),
    "v10": VendorStyle(
        "dotmatrix", (0.20, 0.25, 0.32), "lingkaran", "SMJ", "FAKTUR", "rp", "dmon", True
    ),
    "x01": VendorStyle(
        "klasik", (0.10, 0.35, 0.40), "segitiga", "MPB", "FAKTUR", "idr", "long", False
    ),
}
