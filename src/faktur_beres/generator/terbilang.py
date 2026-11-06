"""Rupiah amounts in Indonesian words ("terbilang")."""

_BASIC = (
    "nol",
    "satu",
    "dua",
    "tiga",
    "empat",
    "lima",
    "enam",
    "tujuh",
    "delapan",
    "sembilan",
    "sepuluh",
    "sebelas",
)
_SCALES = ((10**12, "triliun"), (10**9, "miliar"), (10**6, "juta"))


def _join(head: str, rest: int) -> str:
    return f"{head} {_words(rest)}" if rest else head


def _words(n: int) -> str:
    if n < 12:
        return _BASIC[n]
    if n < 20:
        return f"{_BASIC[n - 10]} belas"
    if n < 100:
        return _join(f"{_BASIC[n // 10]} puluh", n % 10)
    if n < 200:
        return _join("seratus", n - 100)
    if n < 1000:
        return _join(f"{_BASIC[n // 100]} ratus", n % 100)
    if n < 2000:
        return _join("seribu", n - 1000)
    if n < 10**6:
        return _join(f"{_words(n // 1000)} ribu", n % 1000)
    for scale, name in _SCALES:
        if n >= scale:
            return _join(f"{_words(n // scale)} {name}", n % scale)
    raise AssertionError("unreachable")  # pragma: no cover


def terbilang(n: int) -> str:
    """Whole number in Indonesian words, e.g. 1100 -> "seribu seratus"."""
    if n < 0:
        raise ValueError("terbilang hanya untuk bilangan tidak negatif")
    if n >= 10**15:
        raise ValueError("terbilang dibatasi sampai triliun")
    return _words(n)


def terbilang_rupiah(n: int) -> str:
    """E.g. 26107200 -> "dua puluh enam juta seratus tujuh ribu dua ratus rupiah"."""
    return f"{terbilang(n)} rupiah"
