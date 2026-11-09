"""``npm run graph:diagram``: the Mermaid diagram of the compiled graph, in a document."""

import sys
from typing import Any, cast

from faktur_beres.config import REPO_ROOT
from faktur_beres.graph.build import build_graph
from faktur_beres.graph.deps import Dependencies

DOCUMENT = REPO_ROOT / "docs" / "diagram-graph.md"
HEADER = """# Diagram graph faktur

Dibuat `npm run graph:diagram` dari graph yang dikompilasi; jangan disunting tangan.

```mermaid
"""


def mermaid() -> str:
    deps = cast(Dependencies, _Wiring())
    return build_graph(deps, None).get_graph().draw_mermaid()


class _Wiring:
    """The compiled shape needs no service: the nodes are never run here."""

    retry_attempts = 3
    retry_interval_seconds = 1.0

    def __getattr__(self, name: str) -> Any:
        return None


def document() -> str:
    return HEADER + mermaid().rstrip() + "\n```\n"


def main() -> int:
    DOCUMENT.write_text(document(), encoding="utf-8")
    print(f"Ditulis: {DOCUMENT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
