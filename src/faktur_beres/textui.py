"""Plain-text output helpers for the command-line scripts."""

from collections.abc import Sequence


def print_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    """Print an aligned table with a dashed header rule."""
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]

    def fmt(cells: Sequence[object]) -> str:
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()

    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt(row))
