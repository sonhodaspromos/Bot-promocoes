"""Auditoria complementar · P1 16 e 17: importação de comissões (paginação, status, duplicidade) e reconciliação."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from app.cliente_http import ClienteHTTP
from app.integracoes.base import REAL, FonteOfertas
from app.integracoes.fontes.shopee import ShopeeFonte
from app.logs import Log
from app.repositorio import SupabaseRepositorio
from app.repositorio_memoria import MemoriaRepositorio
from app.servicos.comissoes import ServicoComissoes
from dubles import RespostaFalsa, SessaoFalsa

NOVE = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)   # 09:00 em Recife (cron atrasado)


def cli(s):
    return ClienteHTTP("t", sessao=s, tentativas=1, dormir=lambda x: None)


def no(conv, pedido, status, item, valor, venda=100):
    return {"conversionId": conv, "purchaseTime": int((NOVE - timedelta(days=2)).timestamp()),
            "orders": [{"orderId": pedido, "orderStatus": status,
                        "items": [{"itemId": item, "itemName": f"Item {item}", "itemPrice": str(venda), "itemTotalCommission": str(valor)}]}]}


def pagina(nos, proxima=None):
    return RespostaFalsa(200, {"data": {"conversionReport": {"nodes": nos, "pageInfo": {
        "hasNextPage": proxima is not None, "scrollId": proxima or ""}}}})


# ---------------------------------------------------------------- Shopee: paginação
def test_relatorio_paginado_e_lido_ate_o_fim():
    s = SessaoFalsa().quando("POST", "shopee", [pagina([no(1, "A", "COMPLETED", 1, 5)], "scroll-2"),
                                                pagina([no(2, "B", "PENDING", 2, 3)], "scroll-3"),
                                                pagina([no(3, "C", "CANCELLED", 3, 1)])])
    itens = ShopeeFonte("1", "s", cli(s)).comissoes(NOVE - timedelta(days=7), NOVE)
    assert [i["referencia_externa"] for i in itens] == ["1-A-1", "2-B-2", "3-C-3"]
    import json
    consultas = [json.loads(c["data"].decode())["query"] for c in s.chamadas]
    assert 'scrollId:' not in consultas[0] and 'scrollId:"scroll-2"' in consultas[1] and 'scrollId:"scroll-3"' in consultas[2]


def test_paginacao_tem_limite_de_seguranca():
    s = SessaoFalsa().quando("POST", "shopee", lambda m, u, **k: pagina([no(9, "Z", "PENDING", 9, 1)], "de-novo"))
    itens = ShopeeFonte("1", "s", cli(s)).comissoes(NOVE - timedelta(days=7), NOVE)
    assert len(s.chamadas) <= 50 and len({i["referencia_externa"] for i in itens}) == 1   # duplicados removidos


# ---------------------------------------------------------------- repositório: status muda, duplicidade
def test_supabase_atualiza_status_em_vez_de_ignorar():
    s = (SessaoFalsa().quando("GET", "comissoes", RespostaFalsa(200, [{"referencia_externa": "A", "status": "pendente", "valor_comissao": 2}]))
         .quando("POST", "comissoes", RespostaFalsa(201, texto="")))
    repo = SupabaseRepositorio("https://abc.supabase.co", "sb_secret_x", cli(s))
    r = repo.salvar_comissoes(1, [{"referencia_externa": "A", "valor_comissao": 2, "data_venda": "2026-11-20", "status": "cancelada"},
                                  {"referencia_externa": "B", "valor_comissao": 1, "data_venda": "2026-11-20", "status": "pendente"}])
    post = next(c for c in s.chamadas if c["metodo"] == "POST")
    assert "merge-duplicates" in post["headers"]["Prefer"] and post["params"]["on_conflict"] == "marketplace_id,referencia_externa"
    assert r == {"novos": 1, "atualizados": 1, "ignorados": 0}


def test_comissao_ja_paga_nao_volta_atras():
    repo = MemoriaRepositorio()
    mkt = repo._um("marketplaces", codigo="shopee")["id"]
    repo.salvar_comissoes(mkt, [{"referencia_externa": "P", "valor_comissao": 4, "data_venda": "2026-11-01", "status": "confirmada"}])
    repo._um("comissoes", referencia_externa="P").update(status="paga", data_pagamento="2026-11-20")
    r = repo.salvar_comissoes(mkt, [{"referencia_externa": "P", "valor_comissao": 4, "data_venda": "2026-11-01", "status": "confirmada"}])
    assert repo._um("comissoes", referencia_externa="P")["status"] == "paga" and r["ignorados"] == 1


def test_cancelamento_posterior_atualiza_a_comissao():
    repo = MemoriaRepositorio()
    mkt = repo._um("marketplaces", codigo="shopee")["id"]
    repo.salvar_comissoes(mkt, [{"referencia_externa": "X", "valor_comissao": 9, "data_venda": "2026-11-01", "status": "pendente"}])
    r = repo.salvar_comissoes(mkt, [{"referencia_externa": "X", "valor_comissao": 9, "data_venda": "2026-11-01", "status": "cancelada"}])
    assert repo._um("comissoes", referencia_externa="X")["status"] == "cancelada" and r["atualizados"] == 1
    assert len(repo.t["comissoes"]) == 1


# ---------------------------------------------------------------- serviço: 1x por dia, reconciliação
class Relatorio(FonteOfertas):
    codigo, modo, suporta_comissoes = "shopee", REAL, True

    def __init__(self, itens):
        self.itens, self.chamadas = itens, []

    def buscar(self, p, limite=30):
        return []

    def comissoes(self, inicio, fim):
        self.chamadas.append((inicio, fim))
        return [dict(i) for i in self.itens]


def item(ref, status="pendente", valor=5.0):
    return {"referencia_externa": ref, "valor_comissao": valor, "data_venda": "2026-11-20", "status": status}


def test_importa_uma_vez_por_dia_mesmo_com_cron_atrasado():
    repo = MemoriaRepositorio()
    fonte = Relatorio([item("A")])
    s = ServicoComissoes(repo, {"shopee": fonte}, Log(saida=io.StringIO()), relogio=lambda: NOVE)
    assert s.executar(repo.configuracoes()) == 1          # 9h: a janela das 6h passou, mas ainda não importou hoje
    chamadas = len(fonte.chamadas)                        # importação do dia (+ 1ª reconciliação)
    assert s.executar(repo.configuracoes()) == 0          # mesmo dia: não repete
    assert len(fonte.chamadas) == chamadas
    assert [i["tipo"] for i in repo.t["comissoes_importacoes"]].count("diaria") == 1


def test_antes_das_6h_nao_importa():
    repo = MemoriaRepositorio()
    cedo = datetime(2026, 11, 25, 8, 0, tzinfo=timezone.utc)   # 05:00 em Recife
    assert ServicoComissoes(repo, {"shopee": Relatorio([item("A")])}, Log(saida=io.StringIO()), relogio=lambda: cedo).executar(
        repo.configuracoes()) == 0


def test_reconciliacao_semanal_compara_com_o_relatorio_oficial():
    repo = MemoriaRepositorio()
    mkt = repo._um("marketplaces", codigo="shopee")["id"]
    repo.salvar_comissoes(mkt, [item("A", "confirmada", 5), item("FANTASMA", "pendente", 7)])   # FANTASMA sumiu do relatório
    fonte = Relatorio([item("A", "cancelada", 5), item("NOVA", "pendente", 2)])
    log = Log(saida=io.StringIO())
    rel = ServicoComissoes(repo, {"shopee": fonte}, log, relogio=lambda: NOVE).reconciliar(repo.configuracoes(), forcar=True)
    assert fonte.chamadas[0][0] <= NOVE - timedelta(days=89)                    # janela de 90 dias
    assert repo._um("comissoes", referencia_externa="A")["status"] == "cancelada"
    imp = repo.t["comissoes_importacoes"][-1]
    assert imp["tipo"] == "reconciliacao" and imp["status"] == "divergente"
    assert any("FANTASMA" in d["referencia"] for d in imp["divergencias"])
    assert log.contagem["AVISO"] >= 1 and rel["divergencias"] == 1


def test_reconciliacao_roda_sozinha_toda_semana():
    repo = MemoriaRepositorio()
    fonte = Relatorio([item("A")])
    s = ServicoComissoes(repo, {"shopee": fonte}, Log(saida=io.StringIO()), relogio=lambda: NOVE)
    s.executar(repo.configuracoes())
    assert [i["tipo"] for i in repo.t["comissoes_importacoes"]] == ["diaria", "reconciliacao"]   # 1ª vez: já reconcilia
    s2 = ServicoComissoes(repo, {"shopee": fonte}, Log(saida=io.StringIO()), relogio=lambda: NOVE + timedelta(days=1))
    s2.executar(repo.configuracoes())
    assert [i["tipo"] for i in repo.t["comissoes_importacoes"]].count("reconciliacao") == 1   # só daqui a 7 dias
