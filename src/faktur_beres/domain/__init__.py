"""Canonical invoice model, normalisation, and the deterministic business rules (SPEC §8).

Agent code: never imports ``faktur_beres.evals``, ``generator``, ``dataset``, or ``seed`` and never
reads ``eval/`` or ``data/seed/`` (checked by ``tests/test_architecture.py``). ERP data reaches the
rules only through the ``ErpReader`` protocol.
"""
