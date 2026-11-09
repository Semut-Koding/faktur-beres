"""Tool ``extract_invoice`` (SPEC §5.2): an incoming file to ``InvoiceData``, without any action.

The file type comes from the first bytes, never from the name. UBL is read by the code parser and
never re-read; a PDF or an image is read by Gemini with the BR-04 validation and re-reads. A
refused file (type, size, page count) is ``validation_failed`` like any unreadable invoice.
``LlmUnavailable`` reaches the caller (BR-27 in M4). The tool writes nothing: no Odoo call, no
e-mail, no file.
"""

from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.extraction.filetype import FileKind, FileRejected, identify
from faktur_beres.extraction.outcome import ExtractionOutcome, read_ubl_file, rejected
from faktur_beres.extraction.reading import read_with_llm
from faktur_beres.llm.contracts import StructuredLlm


def extract_invoice(data: bytes, *, llm: StructuredLlm, policy: PolicyConfig) -> ExtractionOutcome:
    try:
        file = identify(data)
    except FileRejected as exc:
        return rejected(exc)
    if file.kind is FileKind.XML:
        return read_ubl_file(data)
    return read_with_llm(llm, data, file, policy)
