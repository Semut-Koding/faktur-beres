"""Schema of the robustness labels (M3.6): strict, same document truth as the 40 scenarios."""

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from faktur_beres.evals.labels import load_labels
from faktur_beres.generator.label_schema import RobustnessLabel

TRUTH = load_labels()["COC-01"].kebenaran_dokumen.model_dump(mode="json")
VALID: dict[str, Any] = {
    "id": "KTH-01",
    "versi_label": 1,
    "ciri": ["pindaian_tanpa_teks"],
    "format": "pdf",
    "gaya_gambar": "pindaian",
    "kanal": "unggahan",
    "vendor": "v01",
    "po_rujukan": "P00015",
    "berkas": {
        "path": "eval/ketahanan/berkas/KTH-01/faktur.pdf",
        "nama_kirim": "x.pdf",
        "sha256": "0" * 64,
    },
    "kebenaran_dokumen": TRUTH,
    "harapan": {
        "jalur_awal": "A",
        "alasan_wajib": [],
        "alasan_boleh_tambahan": [],
        "catatan": None,
    },
}


def changed(path: tuple[str, ...], value: Any) -> dict[str, Any]:
    data = copy.deepcopy(VALID)
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


def test_a_valid_label() -> None:
    label = RobustnessLabel.model_validate(VALID)
    assert label.harapan.menunggu is None
    assert (
        RobustnessLabel.model_validate(changed(("harapan", "menunggu"), "M4")).harapan.menunggu
        == "M4"
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("id",), "COC-01"),
        (("ciri",), []),
        (("ciri",), ["sesuatu_lain"]),
        (("kanal",), "email"),
        (("format",), "ubl"),
        (("harapan", "menunggu"), "M5"),
        (("harapan", "jalur_awal"), "D"),
        (("harapan", "alasan_wajib"), ["alasan_lain"]),
    ],
)
def test_invalid_labels_are_refused(path: tuple[str, ...], value: Any) -> None:
    with pytest.raises(ValidationError):
        RobustnessLabel.model_validate(changed(path, value))


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(ValidationError):
        RobustnessLabel.model_validate(VALID | {"kategori": "cocok"})
