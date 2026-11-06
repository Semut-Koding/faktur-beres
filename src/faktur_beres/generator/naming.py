"""File names under which documents are sent or uploaded ("nama berkas kirim").

PDF and UBL files are named after the invoice number; photos and scans get camera or scanner
style names. All deterministic; none contains a scenario ID or category.
"""

from datetime import date

from faktur_beres.generator.determinism import rng


def _from_number(number: str) -> str:
    return number.replace("/", "-").replace(" ", "_")


def pdf_name(number: str) -> str:
    return f"{_from_number(number)}.pdf"


def ubl_name(number: str) -> str:
    return f"{_from_number(number)}.xml"


def photo_name(key: str, day: date) -> str:
    r = rng(key, "nama-foto")
    return (
        f"IMG_{day:%Y%m%d}_{r.randint(8, 16):02d}{r.randint(0, 59):02d}{r.randint(0, 59):02d}.jpg"
    )


def scan_name(key: str, day: date, extension: str) -> str:
    return f"SCAN_{day:%Y%m%d}_{rng(key, 'nama-pindaian').randint(1, 999):04d}.{extension}"
