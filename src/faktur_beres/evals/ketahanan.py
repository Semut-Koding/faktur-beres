"""Oracle cross-check of the robustness set (M3.6): the rules on the document truth.

For every document without ``menunggu``, ``analyse()`` runs on the ``kebenaran_dokumen`` (a
perfect extraction) against the seeded ERP state, with layer 2 of BR-15 not run, and its first
route and reasons are compared with ``harapan`` as the oracle compares the 40 scenarios: the same
route, every required reason, and no reason outside the required and the allowed ones. A document
marked ``menunggu: M4`` needs the LLM description mapping of M4 and is reported, not judged.
"""

from dataclasses import dataclass

from faktur_beres.domain.engine import analyse
from faktur_beres.domain.erp import ErpReader
from faktur_beres.domain.invoice import Channel, IntakeContext
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.evals.labels import invoice_id_for, truth_fragments, truth_to_invoice
from faktur_beres.generator.label_schema import RobustnessLabel


@dataclass(frozen=True)
class CrossCheck:
    id: str
    route: str
    reasons: tuple[str, ...]
    waiting: str | None
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def robustness_intake(label: RobustnessLabel) -> IntakeContext:
    return IntakeContext(
        invoice_id=invoice_id_for(label.berkas.sha256),
        channel=Channel.UPLOAD,
        file_name=label.berkas.nama_kirim,
        file_sha256=label.berkas.sha256,
    )


def cross_check(label: RobustnessLabel, reader: ErpReader, policy: PolicyConfig) -> CrossCheck:
    truth = label.kebenaran_dokumen
    result = analyse(
        truth_to_invoice(truth),
        robustness_intake(label),
        truth_fragments(truth, document_format=label.format),
        reader,
        policy,
    )
    route = result.default_route.value
    reasons = tuple(r.value for r in result.reasons)
    expected = label.harapan
    problems: list[str] = []
    if expected.menunggu is None:
        if route != expected.jalur_awal:
            problems.append(f"jalur {route}, harapan {expected.jalur_awal}")
        missing = set(expected.alasan_wajib) - set(reasons)
        extra = set(reasons) - set(expected.alasan_wajib) - set(expected.alasan_boleh_tambahan)
        if missing:
            problems.append(f"alasan wajib tidak muncul: {', '.join(sorted(missing))}")
        if extra:
            problems.append(f"alasan di luar harapan: {', '.join(sorted(extra))}")
    return CrossCheck(label.id, route, reasons, expected.menunggu, tuple(problems))
