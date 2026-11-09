"""Turn a rendered invoice PDF into a phone photo (JPEG) or a scan (PNG/JPEG).

* **foto**: slight perspective, rotation within ±3°, a desk background, uneven light, a little
  blur and noise; JPEG.
* **pindaian**: rotation within ±1°, greyscale, light paper tone.

Text stays clearly legible (blurred scans are out of scope). Width is at most 1600 px, files carry
no EXIF, and compression settings are fixed. All randomness comes from the scenario seed; Pillow's
own random effects are not used because they are not reproducible.
"""

import math
from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas

from faktur_beres.generator.determinism import rng
from faktur_beres.generator.pdfinfo import render_page
from faktur_beres.generator.render_pdf import PRINTED_PARTIES as PRINTED_PARTIES
from faktur_beres.generator.render_pdf import SYNTHETIC_MARK

DPI = 150
MAX_WIDTH = 1600
PHOTO_SIZE = (1500, 2000)
JPEG_OPTIONS = {"quality": 82, "optimize": True, "progressive": False, "subsampling": 2}


# -- small rendered overlays ----------------------------------------------------------------


def _render_overlay(width_pt: float, height_pt: float, draw) -> Image.Image:
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=(width_pt, height_pt), invariant=1, pageCompression=1)
    draw(canvas)
    canvas.showPage()
    canvas.save()
    return render_page(buffer.getvalue(), dpi=DPI)


def sticky_note(text: str) -> Image.Image:
    """A yellow sticky note with handwritten-looking text, as an RGBA image."""
    size = 190.0

    def draw(c: Canvas) -> None:
        c.setFillColorRGB(1.0, 0.92, 0.42)
        c.rect(0, 0, size, size, stroke=0, fill=1)
        c.setFillColorRGB(0.98, 0.86, 0.32)
        c.rect(0, size - 22, size, 22, stroke=0, fill=1)
        c.setFillColorRGB(0.08, 0.14, 0.42)
        y = size - 42
        for line in simpleSplit(text, "Helvetica-Oblique", 12.5, size - 24):
            c.setFont("Helvetica-Oblique", 12.5)
            c.drawString(12, y, line)
            y -= 16

    return _render_overlay(size, size, draw).convert("RGBA")


def mark_label() -> Image.Image:
    """The synthetic-document mark as a small white label."""
    width, height = 230.0, 18.0

    def draw(c: Canvas) -> None:
        c.setFillColorRGB(1, 1, 1)
        c.roundRect(0, 0, width, height, 4, stroke=0, fill=1)
        c.setFillColorRGB(0.2, 0.2, 0.2)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawCentredString(width / 2, 5.5, SYNTHETIC_MARK)

    return _render_overlay(width, height, draw)


# -- geometry -------------------------------------------------------------------------------------


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (8x8 is enough; no numpy needed)."""
    n = len(vector)
    rows = [row[:] + [value] for row, value in zip(matrix, vector, strict=True)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(n):
            if r != col:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col], strict=True)]
    return [rows[i][n] / rows[i][i] for i in range(n)]


def perspective_coeffs(target: list[tuple[float, float]], source: list[tuple[float, float]]):
    """Coefficients that map output points ``target`` back to input points ``source``."""
    matrix, vector = [], []
    for (x, y), (u, v) in zip(target, source, strict=True):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        vector += [u, v]
    return _solve(matrix, vector)


def _rotate(point, center, degrees: float):
    angle = math.radians(degrees)
    x, y = point[0] - center[0], point[1] - center[1]
    return (
        center[0] + x * math.cos(angle) - y * math.sin(angle),
        center[1] + x * math.sin(angle) + y * math.cos(angle),
    )


# -- photo ----------------------------------------------------------------------------------------


def desk_background(key: str, size: tuple[int, int]) -> Image.Image:
    r = rng(key, "meja")
    base = (r.randint(118, 138), r.randint(86, 100), r.randint(58, 70))
    desk = Image.new("RGB", size, base)
    draw = ImageDraw.Draw(desk)
    x = 0
    while x < size[0]:
        width = r.randint(2, 9)
        shade = r.randint(-16, 14)
        color = tuple(max(0, min(255, c + shade)) for c in base)
        draw.rectangle([x, 0, x + width, size[1]], fill=color)
        x += width
    return desk.filter(ImageFilter.GaussianBlur(3))


def _noise(key: str, size: tuple[int, int], amplitude: int) -> Image.Image:
    raw = rng(key, "derau").randbytes(size[0] * size[1])
    noise = Image.frombytes("L", size, raw)
    lut = [128 + (v - 128) * amplitude // 128 for v in range(256)]
    return noise.point(lut)


def render_photo(pdf: bytes, key: str, *, note: str | None = None) -> bytes:
    """Phone photo of the printed invoice lying on a desk (JPEG)."""
    r = rng(key, "foto")
    page = render_page(pdf, dpi=DPI)
    if note:
        angle = rng(key, "kertas-tempel").uniform(-7, 7)
        sticker = sticky_note(note).rotate(angle, Image.Resampling.BICUBIC, True)
        page.paste(sticker, (int(page.width * 0.52), int(page.height * 0.64)), sticker)

    width, height = PHOTO_SIZE
    paper_w = width * r.uniform(0.82, 0.86)
    paper_h = paper_w * page.height / page.width
    center = (width / 2 + r.uniform(-18, 18), height / 2 + r.uniform(-14, 14))
    tilt = paper_w * r.uniform(0.015, 0.03)  # top edge slightly narrower: camera above, tilted
    corners = [
        (center[0] - paper_w / 2 + tilt, center[1] - paper_h / 2),
        (center[0] + paper_w / 2 - tilt, center[1] - paper_h / 2),
        (center[0] + paper_w / 2, center[1] + paper_h / 2),
        (center[0] - paper_w / 2, center[1] + paper_h / 2),
    ]
    angle = r.uniform(-3, 3)
    corners = [_rotate(p, center, angle) for p in corners]
    corners = [(x + r.uniform(-5, 5), y + r.uniform(-5, 5)) for x, y in corners]
    source = [(0, 0), (page.width, 0), (page.width, page.height), (0, page.height)]
    coeffs = perspective_coeffs(corners, source)

    warped = page.transform(
        PHOTO_SIZE, Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC
    )
    mask = Image.new("L", page.size, 255).transform(
        PHOTO_SIZE, Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC
    )
    photo = desk_background(key, PHOTO_SIZE)
    shadow = ImageChops.offset(mask, 10, 14).filter(ImageFilter.GaussianBlur(14))
    darker = photo.point(lambda v: int(v * 0.55))
    photo = Image.composite(darker, photo, shadow)
    photo.paste(warped, (0, 0), mask)

    light = (
        Image.linear_gradient("L")
        .resize(PHOTO_SIZE)
        .rotate(r.uniform(0, 360), Image.Resampling.BICUBIC, fillcolor=128)
    )
    low = r.randint(196, 212)
    light = light.point(lambda v: low + (255 - low) * v // 255)
    photo = ImageChops.multiply(photo, Image.merge("RGB", (light, light, light)))
    photo = photo.filter(ImageFilter.GaussianBlur(0.6))
    noise = _noise(key, PHOTO_SIZE, 14)
    photo = ImageChops.add(photo, Image.merge("RGB", (noise, noise, noise)), 1.0, -128)

    label = mark_label()
    photo.paste(label, (int(width * 0.05), height - label.height - 26))
    return _save_jpeg(photo)


# -- scan -----------------------------------------------------------------------------------------


def render_scan(pdf: bytes, key: str, *, png: bool) -> bytes:
    """Flatbed scan: greyscale, slightly rotated, light paper tone."""
    r = rng(key, "pindaian")
    page = render_page(pdf, dpi=DPI).convert("L")
    page = page.point(lambda v: 10 + v * 236 // 255)
    page = page.rotate(r.uniform(-1, 1), Image.Resampling.BICUBIC, expand=True, fillcolor=246)
    if page.width > MAX_WIDTH:
        page = page.resize((MAX_WIDTH, page.height * MAX_WIDTH // page.width))
    if png:
        buffer = BytesIO()
        page.save(buffer, format="PNG", optimize=False, compress_level=9)
        return buffer.getvalue()
    noise = _noise(key, page.size, 8)
    page = ImageChops.add(page, noise, 1.0, -128)
    return _save_jpeg(page)


def _save_jpeg(image: Image.Image) -> bytes:
    if image.width > MAX_WIDTH:
        image = image.resize((MAX_WIDTH, image.height * MAX_WIDTH // image.width))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", **JPEG_OPTIONS)
    return buffer.getvalue()
