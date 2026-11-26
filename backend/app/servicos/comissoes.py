"""Importação e reconciliação de comissões com os relatórios oficiais (hoje: Shopee).

  * Importação diária: 1 vez por dia, a partir das 6h (horário local). Se o agendador do GitHub
    atrasar, a importação acontece na primeira rodada depois das 6h (antes havia uma janela de
    20 minutos que podia ser perdida). Lê os últimos `dias` (padrão 7).
  * Mudança de status é ATUALIZADA (pendente → confirmada → cancelada). Comissão marcada como
    'paga' no painel não volta atrás.
  * Reconciliação semanal: relê 90 dias do relatório oficial, atualiza tudo e compara com o banco.
    Divergências (comissão que sumiu do relatório, valor diferente) ficam registradas em
    `comissoes_importacoes` e geram AVISO no log.
"""
from __future__ import annotations

from datetime import timedelta

from ..erros import ErroBanco, ErroIntegracao
from ..integracoes.base import REAL, FonteOfertas
from ..logs import Log
from ..repositorio import Repositorio
from .comum import agora_utc, fuso, inicio_do_dia

DIAS_RECONCILIACAO = 90
INTERVALO_RECONCILIACAO = timedelta(days=7)


class ServicoComissoes:
    def __init__(self, repo: Repositorio, fontes: dict[str, FonteOfertas], log: Log, relogio=agora_utc):
        self.repo, self.fontes, self.log, self.relogio = repo, fontes, log, relogio

    def _fontes(self):
        marketplaces = {m["codigo"]: m["id"] for m in self.repo.marketplaces()}
        for fonte in self.fontes.values():
            if fonte.suporta_comissoes and fonte.modo == REAL and fonte.codigo in marketplaces:
                yield fonte, marketplaces[fonte.codigo]

    def executar(self, cfg: dict, forcar: bool = False, dias: int = 7) -> int:
        agora = self.relogio()
        if not forcar and agora.astimezone(fuso(cfg)).hour < 6:
            return 0
        total = 0
        for fonte, mkt in self._fontes():
            ultima = self.repo.ultima_importacao(mkt, "diaria")
            if not forcar and ultima and ultima >= inicio_do_dia(cfg, agora):
                continue  # já importou hoje
            total += self._importar(fonte, mkt, agora - timedelta(days=dias), agora, "diaria")
        if not forcar:
            self.reconciliar(cfg)
        return total

    def _importar(self, fonte, mkt, inicio, fim, tipo) -> int:
        try:
            itens = fonte.comissoes(inicio, fim)
            for i in itens:
                i["id_externo_item"] = i.pop("id_externo", None)
            r = self.repo.salvar_comissoes(mkt, itens)
        except (ErroIntegracao, ErroBanco) as e:
            self.log.erro("comissoes", f"{fonte.codigo}: não consegui importar comissões", erro=str(e))
            self._registrar(mkt, tipo, inicio, fim, status="erro", erro=str(e)[:1000])
            return 0
        self.log.info("comissoes", f"{fonte.codigo}: {len(itens)} itens lidos ({r['novos']} novos, "
                                   f"{r['atualizados']} com status/valor atualizado, {r['ignorados']} já pagos)")
        if tipo == "diaria":
            self._registrar(mkt, tipo, inicio, fim, itens_api=len(itens), novos=r["novos"], atualizados=r["atualizados"],
                            total_api=round(sum(float(i["valor_comissao"]) for i in itens), 2), status="ok")
        return len(itens)

    def reconciliar(self, cfg: dict, forcar: bool = False) -> dict:
        """Relê 90 dias do relatório oficial e compara com o banco. Roda 1x por semana (ou forçado)."""
        agora = self.relogio()
        resumo = {"divergencias": 0}
        for fonte, mkt in self._fontes():
            ultima = self.repo.ultima_importacao(mkt, "reconciliacao")
            if not forcar and ultima and agora - ultima < INTERVALO_RECONCILIACAO:
                continue
            inicio, fim = agora - timedelta(days=DIAS_RECONCILIACAO), agora
            ini_txt, fim_txt = inicio.astimezone(fuso(cfg)).date().isoformat(), fim.astimezone(fuso(cfg)).date().isoformat()
            try:
                oficiais = {i["referencia_externa"]: i for i in fonte.comissoes(inicio, fim)}
                for i in oficiais.values():
                    i["id_externo_item"] = i.pop("id_externo", None)
                r = self.repo.salvar_comissoes(mkt, list(oficiais.values()))
                banco = {c["referencia_externa"]: c for c in self.repo.comissoes_api_periodo(mkt, ini_txt, fim_txt)}
            except (ErroIntegracao, ErroBanco) as e:
                self.log.erro("comissoes", f"{fonte.codigo}: reconciliação falhou", erro=str(e))
                self._registrar(mkt, "reconciliacao", inicio, fim, status="erro", erro=str(e)[:1000])
                continue
            divergencias = []
            for ref, c in banco.items():
                oficial = oficiais.get(ref)
                if oficial is None:
                    divergencias.append({"referencia": ref, "problema": "não aparece mais no relatório oficial",
                                         "banco": c["status"], "valor": float(c["valor_comissao"])})
                elif c["status"] != "paga" and abs(float(c["valor_comissao"]) - float(oficial["valor_comissao"])) > 0.009:
                    divergencias.append({"referencia": ref, "problema": "valor diferente do relatório oficial",
                                         "banco": float(c["valor_comissao"]), "oficial": float(oficial["valor_comissao"])})
            total_api = round(sum(float(i["valor_comissao"]) for i in oficiais.values() if i.get("status") != "cancelada"), 2)
            total_banco = round(sum(float(c["valor_comissao"]) for c in banco.values() if c["status"] != "cancelada"), 2)
            status = "divergente" if divergencias or abs(total_api - total_banco) > 0.009 else "ok"
            self._registrar(mkt, "reconciliacao", inicio, fim, itens_api=len(oficiais), novos=r["novos"],
                            atualizados=r["atualizados"], total_api=total_api, total_banco=total_banco,
                            divergencias=divergencias[:200], status=status)
            if status == "divergente":
                self.log.aviso("comissoes", f"{fonte.codigo}: reconciliação com {len(divergencias)} divergência(s) "
                                            f"(relatório oficial R$ {total_api:.2f} × banco R$ {total_banco:.2f})",
                               exemplos=divergencias[:5])
            else:
                self.log.info("comissoes", f"{fonte.codigo}: reconciliação de 90 dias conferida (R$ {total_api:.2f})")
            resumo["divergencias"] += len(divergencias)
        return resumo

    def _registrar(self, mkt, tipo, inicio, fim, **campos):
        dados = {"marketplace_id": mkt, "tipo": tipo, "periodo_inicio": inicio.date().isoformat(),
                 "periodo_fim": fim.date().isoformat(), "itens_api": 0, "novos": 0, "atualizados": 0,
                 "divergencias": [], **campos}
        try:
            self.repo.registrar_importacao(dados)
        except ErroBanco as e:
            self.log.aviso("comissoes", "Não consegui registrar a importação", erro=str(e))
