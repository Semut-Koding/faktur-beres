"""``python -m faktur_beres.generator [tulis|periksa]`` (npm run gen:faktur / gen:periksa)."""

import argparse
import sys
import time
from collections import Counter

from faktur_beres.generator.build import EVAL_DIR, build_outputs, check_outputs, write_outputs


def _summary(files: dict[str, bytes]) -> list[str]:
    sizes: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    for rel, content in files.items():
        if rel.startswith("berkas/"):
            ext = rel.rsplit(".", 1)[-1].lower()
            counts[ext] += 1
            sizes[ext] += len(content)
    total = sum(sizes.values())
    lines = [f"  {ext}: {counts[ext]} berkas, {sizes[ext] / 1e6:.2f} MB" for ext in sorted(counts)]
    lines.append(f"  total eval/berkas: {sum(counts.values())} berkas, {total / 1e6:.2f} MB")
    labels = sum(1 for rel in files if rel.startswith("skenario/"))
    lines.append(f"  label: {labels}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m faktur_beres.generator")
    parser.add_argument(
        "perintah",
        nargs="?",
        default="tulis",
        choices=("tulis", "periksa"),
        help="tulis: tulis ulang eval/; periksa: bandingkan hasil generator dengan eval/",
    )
    parser.add_argument(
        "--byte",
        action="store_true",
        help="periksa: bandingkan byte per byte (bawaan: isi PDF, piksel gambar, isi label)",
    )
    args = parser.parse_args(argv)
    start = time.perf_counter()

    if args.perintah == "tulis":
        files = build_outputs()
        write_outputs(files)
        print(f"gen:faktur menulis {len(files)} berkas ke {EVAL_DIR.relative_to(EVAL_DIR.parent)}/")
        print("\n".join(_summary(files)))
        print(f"  waktu: {time.perf_counter() - start:.1f} detik")
        return 0

    result = check_outputs(by_bytes=args.byte)
    elapsed = time.perf_counter() - start
    mode = "byte" if args.byte else "isi"
    counts = (
        f"{len(result.same_bytes)} identik byte, {len(result.same_content)} identik isi, "
        f"{len(result.problems)} berbeda"
    )
    for rel in result.same_content:
        print(f"  identik isi (byte berbeda): {rel}")
    if not result.ok:
        print(
            f"gen:periksa GAGAL (perbandingan {mode}): {counts} ({elapsed:.1f} detik)",
            file=sys.stderr,
        )
        for line in result.problems[:50]:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"gen:periksa lulus (perbandingan {mode}): {counts} ({elapsed:.1f} detik)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
