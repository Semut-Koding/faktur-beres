"""A fake Odoo JSON-2 server for tool tests (httpx.MockTransport), and a gateway on top of it."""

import json

import httpx

from faktur_beres.tools.odoo_gateway import AgentOdooGateway

KEY = "agentkey-0123456789abcdef0123456789abcdef"


class FakeOdoo:
    """Answers by ``model/method``; records every request it receives."""

    def __init__(self, answers: dict[str, object] | None = None) -> None:
        self.answers = answers or {}
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/json/2/")
        body = json.loads(request.content)
        self.requests.append((path, body))
        answer = self.answers.get(path, [])
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    def methods(self) -> list[str]:
        return [path for path, _ in self.requests]


def gateway(fake: FakeOdoo) -> AgentOdooGateway:
    return AgentOdooGateway.connect(
        "http://odoo.test", "fakturberes", KEY, transport=httpx.MockTransport(fake)
    )
