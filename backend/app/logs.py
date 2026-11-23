"""Log estruturado: escreve no console (relatório do GitHub) e guarda em lote para a tabela `logs`.

Todo texto passa pela máscara de segredos ANTES de sair — inclusive URLs do Telegram,
que contêm o token. O banco aplica uma segunda máscara (defesa em profundidade).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from typing import Callable

NIVEIS = {"DEBUG": 10, "INFO": 20, "AVISO": 30, "ERRO": 40, "CRITICO": 50}

_PADROES = [
    (re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}"), "bot***"),
    (re.compile(r"sb_(secret|publishable)_[A-Za-z0-9_-]+"), r"sb_\1_***"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "***JWT***"),
    (re.compile(r"(github_pat_|ghp_|gho_)[A-Za-z0-9_]{10,}"), r"\1***"),
    (re.compile(r"(access_token|client_secret|secret|password|senha|api_key|apikey|token|key)=([^&\s\"']+)", re.I), r"\1=***"),
    (re.compile(r"\"(access_token|client_secret|secret|password|senha|api_key|apikey|token|chave)\"\s*:\s*\"[^\"]*\"", re.I), r'"\1": "***"'),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1***"),
    (re.compile(r"(Credential=)[^,\s]+|(Signature=)[0-9a-f]{16,}", re.I), lambda m: (m.group(1) or m.group(2)) + "***"),
]


def mascarar(texto: str, segredos: list[str] | None = None) -> str:
    """Troca tokens e chaves por ***. Também troca qualquer valor exato da lista `segredos`."""
    if not texto:
        return texto
    for segredo in sorted(segredos or [], key=len, reverse=True):
        if segredo and len(segredo) >= 6:
            texto = texto.replace(segredo, "***")
    for padrao, troca in _PADROES:
        texto = padrao.sub(troca, texto)
    return texto


def _mascarar_obj(obj, segredos):
    if isinstance(obj, str):
        return mascarar(obj, segredos)
    if isinstance(obj, dict):
        return {k: ("***" if re.search(r"token|secret|senha|password|chave|key", str(k), re.I) and isinstance(v, str)
                    else _mascarar_obj(v, segredos)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_mascarar_obj(v, segredos) for v in obj]
    return obj


class Log:
    def __init__(self, segredos: list[str] | None = None, nivel_minimo: str = "INFO",
                 saida=None, relogio: Callable[[], datetime] | None = None):
        self.segredos = list(segredos or [])
        self.nivel_minimo = NIVEIS[nivel_minimo]
        self.saida = saida or sys.stdout
        self.relogio = relogio or (lambda: datetime.now(timezone.utc))
        self.pendentes: list[dict] = []
        self.contagem = {n: 0 for n in NIVEIS}

    def adicionar_segredos(self, valores: list[str]) -> None:
        self.segredos += [v for v in valores if v]

    def registrar(self, nivel: str, modulo: str, mensagem: str, /, **contexto) -> None:
        nivel = nivel.upper()
        if nivel not in NIVEIS:
            raise ValueError(f"nível inválido: {nivel}")
        self.contagem[nivel] += 1
        mensagem = mascarar(str(mensagem), self.segredos)[:4000] or "-"
        contexto = _mascarar_obj(contexto, self.segredos)
        if NIVEIS[nivel] >= self.nivel_minimo:
            extra = f" {json.dumps(contexto, ensure_ascii=False, default=str)}" if contexto else ""
            print(f"{self.relogio():%H:%M:%S} {nivel:<7} [{modulo}] {mensagem}{extra}", file=self.saida, flush=True)
        if NIVEIS[nivel] >= NIVEIS["INFO"]:
            self.pendentes.append({"nivel": nivel, "modulo": modulo, "mensagem": mensagem,
                                   "contexto": json.loads(json.dumps(contexto, default=str))})

    def debug(self, modulo, mensagem, /, **c): self.registrar("DEBUG", modulo, mensagem, **c)
    def info(self, modulo, mensagem, /, **c): self.registrar("INFO", modulo, mensagem, **c)
    def aviso(self, modulo, mensagem, /, **c): self.registrar("AVISO", modulo, mensagem, **c)
    def erro(self, modulo, mensagem, /, **c): self.registrar("ERRO", modulo, mensagem, **c)
    def critico(self, modulo, mensagem, /, **c): self.registrar("CRITICO", modulo, mensagem, **c)

    def retirar_pendentes(self) -> list[dict]:
        itens, self.pendentes = self.pendentes, []
        return itens
