"""How vendors print each product: description (with the vendor product code) and unit.

Generator configuration, not dataset data. Descriptions may differ from the product names in
Odoo but stay unambiguous; each one carries the vendor's product code (``kode_vendor``).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PrintedProduct:
    description: str
    unit: str  # printed unit
    ubl_unit: str  # KGM for kg, H87 for unit items


PRINTED: dict[str, PrintedProduct] = {
    "p01": PrintedProduct("Kertas Duplex Board 350 gsm (DB-350)", "kg", "KGM"),
    "p02": PrintedProduct("Kertas Ivory Board 310 gsm (IV-310)", "kg", "KGM"),
    "p03": PrintedProduct("Kraft Liner 200 gsm (KL-200)", "kg", "KGM"),
    "p04": PrintedProduct("Tinta Offset Cyan (TO-C)", "kg", "KGM"),
    "p05": PrintedProduct("Tinta Offset Magenta (TO-M)", "kg", "KGM"),
    "p06": PrintedProduct("Tinta Offset Hitam (TO-K)", "kg", "KGM"),
    "p07": PrintedProduct("Lem PVAc untuk Laminasi (PVAC-L)", "kg", "KGM"),
    "p08": PrintedProduct("Hot Melt Adhesive Standar (HMA-01)", "kg", "KGM"),
    "p09": PrintedProduct("Film BOPP Glossy 20 mikron (BOPP-G20)", "kg", "KGM"),
    "p10": PrintedProduct("Film BOPP Doff 20 mikron (BOPP-D20)", "kg", "KGM"),
    "p11": PrintedProduct("Palet Kayu 120x100 cm (PK-1210)", "pcs", "H87"),
    "p12": PrintedProduct("Blanket Offset 1030 x 790 mm (BLK-1030)", "lembar", "H87"),
    "p13": PrintedProduct("Pisau Die-Cut Custom (DC-CST)", "set", "H87"),
    "p14": PrintedProduct("Bearing 6205-2RS (BRG-6205)", "pcs", "H87"),
    "p15": PrintedProduct("Varnish UV Glossy (UVG-01)", "kg", "KGM"),
    "p16": PrintedProduct("Water Based Coating (WBC-02)", "kg", "KGM"),
    "p17": PrintedProduct("Karton Gelombang B-Flute per lembar (BF-SHT)", "lembar", "H87"),
    "p18": PrintedProduct("Label Stiker Barcode 100x50 mm (LB-10050)", "roll", "H87"),
    "p19": PrintedProduct("Strapping Band PP 15 mm (SB-15)", "roll", "H87"),
    "p20": PrintedProduct("Stretch Film 50 cm (SF-50)", "roll", "H87"),
}
