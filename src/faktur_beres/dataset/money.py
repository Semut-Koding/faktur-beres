"""Whole-rupiah arithmetic for dataset amounts.

PPN on goods (PMK 131/2024): PPN = 12% x DPP nilai lain, with DPP nilai lain = 11/12 x price,
so PPN = 11% x price. The dataset keeps every document subtotal a multiple of Rp300, which makes
both DPP nilai lain and PPN whole numbers; these helpers refuse anything that would need rounding.
"""


def dpp_nilai_lain(subtotal: int) -> int:
    """DPP nilai lain = 11/12 x subtotal. Raises if the result is not a whole rupiah."""
    if (subtotal * 11) % 12:
        raise ValueError(f"DPP nilai lain dari Rp{subtotal} tidak bulat")
    return subtotal * 11 // 12


def ppn_amount(subtotal: int, rate_percent: int = 11) -> int:
    """PPN = subtotal x rate / 100. Raises if the result is not a whole rupiah."""
    if (subtotal * rate_percent) % 100:
        raise ValueError(f"PPN {rate_percent}% dari Rp{subtotal} tidak bulat")
    return subtotal * rate_percent // 100


def total_with_ppn(subtotal: int, rate_percent: int = 11) -> int:
    return subtotal + ppn_amount(subtotal, rate_percent)
