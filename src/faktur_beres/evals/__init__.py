"""Evaluation code: label conversion, the dataset-backed ERP reader, and the oracle (SPEC §12.1).

Only evaluation code and tests may read labels (``eval/skenario``) and the dataset; agent code
(``faktur_beres.domain``, ``faktur_beres.tools``) never imports this package.
"""
