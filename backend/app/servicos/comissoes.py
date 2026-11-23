"""Importação de comissões das fontes que oferecem relatório (hoje: Shopee).

Roda na janela das 6h (horário local) ou quando chamado com `forcar=True` (comando `comissoes`).
Comissões repetidas são ignoradas pelo banco (referência única por marketplace).
"""
from __future__ import annotations

from datetime import timedelta

from ..erros import ErroBanco, ErroIntegracao
from ..integracoes.base import REAL, FonteOfertas
from ..logs import Log
from ..repositorio import Repositorio
from .comum import agora_utc, fuso


class ServicoComissoes:
    def __init__(self, repo: Repositorio, fontes: dict[str, FonteOfertas], log: Log, relogio=agora_utc):
        self.repo, self.fontes, self.log, self.relogio = repo, fontes, log, relogio

    def executar(self, cfg: dict, forcar: bool = False, dias: int = 7) -> int:
        agora = self.relogio()
        local = agora.astimezone(fuso(cfg))
        if not forcar and not (local.hour == 6 and local.minute < 20):
            return 0
        marketplaces = {m["codigo"]: m["id"] for m in self.repo.marketplaces()}
        total = 0
        for fonte in self.fontes.values():
            if not fonte.suporta_comissoes or fonte.modo != REAL or fonte.codigo not in marketplaces:
                continue
            try:
                itens = fonte.comissoes(agora - timedelta(days=dias), agora)
                for i in itens:
                    i.pop("id_externo", None)
                total += self.repo.salvar_comissoes(marketplaces[fonte.codigo], itens)
                self.log.info("comissoes", f"{fonte.codigo}: {len(itens)} itens de comissão lidos")
            except (ErroIntegracao, ErroBanco) as e:
                self.log.erro("comissoes", f"{fonte.codigo}: não consegui importar comissões", erro=str(e))
        return total
