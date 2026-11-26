"""Provedor de IA: API de Mensagens do Claude (Anthropic), via HTTP simples."""
from __future__ import annotations

from dataclasses import dataclass

from ..cliente_http import ClienteHTTP
from ..erros import ErroIntegracao

URL_API = "https://api.anthropic.com/v1/messages"
VERSAO_API = "2023-06-01"


@dataclass
class RespostaIA:
    texto: str
    tokens_entrada: int
    tokens_saida: int
    modelo: str


class ProvedorIA:
    nome = "ia"

    def gerar(self, sistema: str, usuario: str, *, modelo: str, max_tokens: int) -> RespostaIA:  # pragma: no cover
        raise NotImplementedError


class ProvedorClaude(ProvedorIA):
    nome = "anthropic"

    def __init__(self, chave: str, http: ClienteHTTP | None = None):
        if not chave:
            raise ValueError("chave da API do Claude ausente")
        self._chave = chave
        # 2 tentativas: 429/5xx/529 são repetidos com espera (o cliente respeita Retry-After)
        self.http = http or ClienteHTTP("anthropic", timeout=45, tentativas=2)

    def gerar(self, sistema, usuario, *, modelo, max_tokens):
        resp = self.http.post(URL_API, json={
            "model": modelo, "max_tokens": int(max_tokens), "temperature": 0.7,
            "system": sistema, "messages": [{"role": "user", "content": usuario}],
        }, headers={"x-api-key": self._chave, "anthropic-version": VERSAO_API, "content-type": "application/json"})
        try:
            corpo = resp.json()
        except ValueError:
            raise ErroIntegracao("anthropic: resposta não é JSON", servico="anthropic", temporario=True) from None
        partes = [b.get("text", "") for b in corpo.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
        uso = corpo.get("usage") or {}
        return RespostaIA("".join(partes), int(uso.get("input_tokens") or 0), int(uso.get("output_tokens") or 0),
                          str(corpo.get("model") or modelo))


class ProvedorFalso(ProvedorIA):
    """Para testes: devolve respostas prontas (ou lança erros) e guarda o que recebeu."""
    nome = "falso"

    def __init__(self, respostas=None, tokens=(400, 80)):
        self.respostas = list(respostas or [])
        self.tokens = tokens
        self.chamadas: list[dict] = []

    def gerar(self, sistema, usuario, *, modelo, max_tokens):
        self.chamadas.append({"sistema": sistema, "usuario": usuario, "modelo": modelo, "max_tokens": max_tokens})
        r = self.respostas.pop(0) if self.respostas else '{"chamada": "OLHA ESSE ACHADINHO 💖", "texto": "Prático e bonito, vale muito a pena."}'
        if isinstance(r, Exception):
            raise r
        return RespostaIA(r, self.tokens[0], self.tokens[1], modelo)
