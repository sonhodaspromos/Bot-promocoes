"""Auditoria da expansão modular: correções testadas com dublês (nenhuma chamada real).
Integração real continua NÃO validada: só o validar-integracoes com credenciais aprova."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

from app.ambiente import Ambiente
from app.cliente_http import ClienteHTTP, chamada_de_leitura
from app.config import Config
from app.erros import ErroBanco, ErroIntegracao
from app.integracoes import registro
from app.integracoes.fontes.aliexpress import AliExpressFonte
from app.integracoes.fontes.amazon import API_URL, TOKEN_URL_NA, AmazonFonte
from app.inteligencia.precos import JANELAS
from app.main import alerta_fila
from app.repositorio_memoria import MemoriaRepositorio
from app.servicos.vitrine import AVISO_AMAZON, GeradorVitrine
from app.servicos.whatsapp import ServicoWhatsApp
from app.worker import Worker
from dubles import RespostaFalsa, SessaoFalsa
from test_auditoria import cenario, cliente, novo_log

AGORA = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)
ITEM = {"asin": "B0TESTE", "detailPageURL": "https://www.amazon.com.br/dp/B0TESTE?tag=minhatag-20",
        "itemInfo": {"title": {"displayValue": "Air Fryer 4L"}},
        "offersV2": {"listings": [{"price": {"money": {"amount": 299.9}}, "availability": {"type": "IN_STOCK"}}]}}
TOKEN = RespostaFalsa(200, {"access_token": "T1", "expires_in": 3600})


# ============================================================================ Amazon (documentação oficial)
def test_amazon_usa_enderecos_oficiais_da_regiao_na_e_token_em_json_com_escopo():
    sessao = (SessaoFalsa().quando("POST", "api.amazon.com/auth/o2/token", TOKEN)
              .quando("POST", "creatorsapi.amazon/catalog/v1/searchItems", RespostaFalsa(200, {"searchResult": {"items": [ITEM]}})))
    f = AmazonFonte("id", "segredo", "minhatag-20", http=cliente(sessao))
    assert f.buscar("air fryer")[0].preco == 299.9
    tok, busca = sessao.chamadas
    assert tok["url"] == TOKEN_URL_NA and tok["json"] == {"grant_type": "client_credentials", "client_id": "id",
                                                         "client_secret": "segredo", "scope": "creatorsapi::default"}
    assert busca["url"] == f"{API_URL}/searchItems" and busca["headers"]["x-marketplace"] == "www.amazon.com.br"
    assert busca["json"]["marketplace"] == "www.amazon.com.br" and busca["json"]["partnerTag"] == "minhatag-20"
    assert chamada_de_leitura("POST", tok["url"], {"json": tok["json"]})      # validação: token é leitura


def test_amazon_renova_token_vencido_e_token_revogado_uma_vez():
    relogio = [1000.0]
    respostas = [RespostaFalsa(401, {"message": "token inválido"}), RespostaFalsa(200, {"itemsResult": {"items": [ITEM]}})]
    sessao = (SessaoFalsa().quando("POST", "auth/o2/token", [TOKEN, RespostaFalsa(200, {"access_token": "T2", "expires_in": 3600})])
              .quando("POST", "getItems", respostas))
    f = AmazonFonte("id", "s", "tag-20", http=cliente(sessao), relogio=lambda: relogio[0])
    assert f.consultar("B0TESTE").id_externo == "B0TESTE"                     # 401 → novo token → sucesso
    assert sum("token" in c["url"] for c in sessao.chamadas) == 2
    assert next(c for c in sessao.chamadas if "getItems" in c["url"])["json"]["itemIdType"] == "ASIN"
    relogio[0] += 3600                                                     # expirou: pede outro
    sessao.regras = [("POST", "auth/o2/token", RespostaFalsa(200, {"access_token": "T3", "expires_in": 3600})),
                     ("POST", "getItems", RespostaFalsa(200, {"itemsResult": {"items": [ITEM]}}))]
    f.consultar("B0TESTE")
    assert sessao.chamadas[-1]["headers"]["Authorization"] == "Bearer T3"


def test_amazon_401_persistente_e_429_viram_erro_claro():
    sessao = SessaoFalsa().quando("POST", "token", TOKEN).quando("POST", "searchItems", RespostaFalsa(401, {"message": "x"}))
    with pytest.raises(ErroIntegracao) as e:
        AmazonFonte("id", "s", "t", http=cliente(sessao)).buscar("x")
    assert e.value.autenticacao
    esperas = []
    http = ClienteHTTP("amazon", sessao=SessaoFalsa().quando("POST", "token", TOKEN).quando(
        "POST", "searchItems", RespostaFalsa(429, {"message": "TooManyRequests"}, headers={"Retry-After": "7"})),
        tentativas=3, dormir=esperas.append)
    with pytest.raises(ErroIntegracao) as e:
        AmazonFonte("id", "s", "t", http=http).buscar("x")
    assert e.value.limite and esperas == [7.0, 7.0]                     # respeita o Retry-After do serviço


def test_timeout_na_consulta_vira_erro_temporario_e_abre_o_disjuntor():
    sessao = SessaoFalsa().quando("POST", "api-sg.aliexpress.com", requests.ReadTimeout("lento"))
    http = ClienteHTTP("aliexpress", sessao=sessao, tentativas=2, dormir=lambda s: None, limite_falhas=1)
    f = AliExpressFonte("k", "s", "t", http=http)
    with pytest.raises(ErroIntegracao) as e:
        f.buscar("fone")
    assert e.value.temporario and len(sessao.chamadas) == 2
    with pytest.raises(ErroIntegracao, match="disjuntor"):
        f.buscar("fone")
    assert len(sessao.chamadas) == 2                                       # não insiste num serviço fora do ar


def test_amazon_em_producao_exige_so_as_tres_credenciais():
    cfg = Config(supabase_url="https://x.supabase.co", supabase_chave="sb_secret_x",
                 credenciais={"amazon": {"CREDENTIAL_ID": "i", "CREDENTIAL_SECRET": "s", "PARTNER_TAG": "t-20"}})
    integ = registro.montar(cfg, ambiente=Ambiente("producao", ["amazon"]))
    f = integ.fontes["amazon"]
    assert f.token_url == TOKEN_URL_NA and f.api_url == API_URL


# ============================================================================ WhatsApp: orçamento atômico
class WhatsFalso:
    def __init__(self, falha=None):
        self.enviadas, self.falha = [], falha

    def enviar_template(self, telefone, parametros):
        self.enviadas.append(telefone)
        if self.falha:
            raise self.falha
        return "wamid"


def _wa(falha=None, teto=1.0):
    repo = MemoriaRepositorio()
    repo.relogio = lambda: AGORA
    cfg = dict(repo.configuracoes(), **{"whatsapp.orcamento_diario_usd": teto, "whatsapp.custo_mensagem_usd": 0.25,
                                        "whatsapp.max_por_inscrito_dia": 3})
    for i in range(4):
        repo.whatsapp_inscrever(f"+55819111100{i:02d}", "formulario", "Quero receber ofertas pelo WhatsApp")
    return repo, cfg, WhatsFalso(falha)


OFERTA = {"promocao_id": 1, "titulo": "Air Fryer", "preco": 299.9, "link_id": 5, "categoria_id": 3}


def test_recusa_certa_devolve_o_orcamento_e_incerta_mantem():
    repo, cfg, cli = _wa(ErroIntegracao("número inválido", servico="whatsapp"))
    ServicoWhatsApp(repo, cli, novo_log(), "https://r", relogio=lambda: AGORA).enviar_oferta(OFERTA, cfg)
    assert repo.orcamento_situacao("whatsapp")["gasto_usd"] == 0.0           # nada cobrado
    repo, cfg, cli = _wa(ErroIntegracao("timeout", servico="whatsapp", incerto=True))
    ServicoWhatsApp(repo, cli, novo_log(), "https://r", relogio=lambda: AGORA).enviar_oferta(OFERTA, cfg)
    assert repo.orcamento_situacao("whatsapp")["gasto_usd"] == 1.0           # pode ter sido entregue: conta


def test_dois_envios_simultaneos_nao_passam_do_teto_e_alerta_de_consumo_dispara_uma_vez():
    repo, cfg, cli = _wa(teto=0.75)                                          # cabem 3 de 4
    log = novo_log()
    s = ServicoWhatsApp(repo, cli, log, "https://r", relogio=lambda: AGORA)
    r1 = s.enviar_oferta(OFERTA, cfg)
    r2 = s.enviar_oferta(dict(OFERTA, promocao_id=2), cfg)                  # segunda "rodada" concorrente
    assert r1["enviados"] == 3 and r1["bloqueado_orcamento"] and r2["enviados"] == 0
    assert len(cli.enviadas) == 3 and repo.orcamento_situacao("whatsapp")["gasto_usd"] == 0.75
    erros = [r for r in log.pendentes if r["nivel"] == "ERRO" and "Consumo do WhatsApp" in r["mensagem"]]
    assert len(erros) == 1


# ============================================================================ workers
def test_worker_renova_a_trava_durante_tarefa_longa():
    repo = MemoriaRepositorio()
    repo.relogio = lambda: AGORA
    repo.tarefa_enfileirar("vitrine.gerar", {})
    renovacoes = []
    original = repo.tarefa_renovar
    repo.tarefa_renovar = lambda *a: renovacoes.append(a) or original(*a)
    w = Worker(repo, {"vitrine.gerar": lambda p: time.sleep(0.25) or {"ok": 1}}, novo_log(), nome="w1", renovar_a_cada_s=0.05)
    assert w.rodar()["concluidas"] == 1 and len(renovacoes) >= 2


def test_trava_perdida_durante_a_execucao_descarta_o_resultado():
    repo = MemoriaRepositorio()
    repo.relogio = lambda: AGORA
    repo.tarefa_enfileirar("vitrine.gerar", {})
    repo.tarefa_renovar = lambda *a: False                                 # outro worker assumiu
    w = Worker(repo, {"vitrine.gerar": lambda p: time.sleep(0.15) or {}}, novo_log(), nome="w1", renovar_a_cada_s=0.03)
    r = w.rodar()
    assert r["descartadas"] == 1 and r["concluidas"] == 0


def test_alerta_da_fila_vira_erro_no_log_e_codigo_de_saida():
    repo = MemoriaRepositorio()
    repo.relogio = lambda: AGORA
    t = repo.tarefa_enfileirar("vitrine.gerar", {})
    repo._um("tarefas", id=t)["status"] = "falhou"
    log = novo_log()
    assert alerta_fila(repo, log) is True
    assert any("falha definitiva" in r["mensagem"] for r in log.pendentes if r["nivel"] == "ERRO")
    assert alerta_fila(MemoriaRepositorio(), novo_log()) is False


# ============================================================================ publicação
def test_sem_regras_de_categoria_os_canais_extras_ficam_de_fora():
    repo, pub, _, cfg = cenario({})
    repo.t["canais"].append({"id": 99, "codigo": "telegram_casa", "tipo": "telegram", "nome": "Casa", "modo": "demo",
                             "ativo": True, "identificador_externo": "@casa"})

    def quebra():
        raise ErroBanco("tabela ausente", status=404)
    repo.canais_categorias = quebra
    canais = {c["codigo"]: c for c in repo.t["canais"]}
    alvos, regras = pub._canais_alvo(dict(cfg, **{"recursos.canais_por_categoria": True}), canais)
    assert "telegram_casa" not in alvos and regras == {}


def test_ab_de_horario_variante_b_ignora_o_aprendizado_e_nao_muda_a_arte():
    from app.inteligencia.experimentos import atribuir
    repo, pub, _, cfg = cenario({})
    pub._experimento = {"id": 7, "tipo": "horario"}
    promo_b = next(i for i in range(1, 200) if atribuir(7, i, "telegram") == "B")
    promo_a = next(i for i in range(1, 200) if atribuir(7, i, "telegram") == "A")
    usados = []
    import app.servicos.agendamento as ag
    original = ag.proximo_horario
    ag.proximo_horario = lambda cfg, agora, ult, ap: usados.append(ap) or ag.Horario(agora, "teste")
    try:
        pub.selecao.contexto = lambda cfg: ("APRENDIZADO", None)
        cfg2 = dict(cfg, **{"agendamento.min_intervalo_canal_min": 20})
        pub._agendar(cfg2, AGORA, 1, {1: None}, promo_a, "telegram")
        pub._agendar(cfg2, AGORA, 1, {1: None}, promo_b, "telegram")
    finally:
        ag.proximo_horario = original
    assert usados == ["APRENDIZADO", None]


def test_ranking_usa_comissao_liquida_com_amostra_e_avisa_sem_amostra():
    from app.inteligencia.aprendizado import aprender
    from app.inteligencia.ranking import PesosRanking, _fatores
    base = {"pontuacao": 80, "comissao_pct": 10.0, "dados_confiaveis": True, "marketplace": "shopee"}
    com = dict(base, cancelamento={"taxa": 0.3, "amostra": 40})
    fat, sem, _, _ = _fatores(com, aprender([]), PesosRanking())
    f = next(x for x in fat if x.nome == "comissão")
    assert round(com["comissao_liquida_pct"], 1) == 7.0 and "líquida de 7.0%" in f.explicacao and "30% de cancelamento" in f.explicacao
    sem_amostra = dict(base, cancelamento={"taxa": None, "amostra": 4, "minimo": 20})
    fat, sem, _, _ = _fatores(sem_amostra, aprender([]), PesosRanking())
    assert sem_amostra["comissao_liquida_pct"] == 10.0 and any("só 4 comissões decididas" in t for t in sem)


def test_taxa_de_cancelamento_ignora_pendente_e_demo():
    repo = MemoriaRepositorio()
    mkt = repo._um("marketplaces", codigo="shopee")["id"]
    hoje = repo.relogio().date()
    for st, orig in [("confirmada", "api"), ("cancelada", "api"), ("pendente", "api"), ("cancelada", "demo"), ("paga", "manual")]:
        repo.t["comissoes"].append({"marketplace_id": mkt, "status": st, "origem": orig, "data_venda": hoje})
    assert repo.taxas_cancelamento()["shopee"] == {"canceladas": 1, "decididas": 3}


def test_imagem_de_produto_amazon_nao_e_baixada_para_a_arte():
    repo, pub, _, cfg = cenario({})
    pedidas = []
    pub._foto = lambda url, cache: pedidas.append(url) or None
    item = next(iter(repo.t["fila"]))
    from app.modelos import ItemFila
    dados = dict(repo.dados_publicacao(item["promocao_id"]), marketplace="amazon", imagem_url="https://m.media-amazon.com/x.jpg")
    canal = next(c for c in pub.canais.values())
    try:
        pub._montar(ItemFila(**{k: item[k] for k in ItemFila.__dataclass_fields__ if k in item}), dados, canal, cfg, {})
    except Exception:  # noqa: BLE001 — só interessa qual URL foi pedida
        pass
    assert pedidas == [None]


def test_historico_tem_janela_de_60_dias():
    assert JANELAS == (7, 30, 60, 90)


# ============================================================================ vitrine
def _of(**kw):
    base = {"promocao_id": 1, "titulo": "Fone", "preco": 99.9, "link_id": 3, "categoria": "Áudio",
            "marketplace_nome": "Shopee", "marketplace_codigo": "shopee", "conferido_em": "2026-11-25T14:30:00+00:00",
            "imagem_url": "https://img/x.jpg"}
    base.update(kw)
    return base


def test_vitrine_mostra_hora_da_conferencia_e_aviso_amazon_so_com_amazon(tmp_path):
    g = GeradorVitrine("https://o.exemplo", "https://r", "X")
    g.gerar([_of()], [], tmp_path, AGORA)
    index = (tmp_path / "index.html").read_text()
    assert "preço conferido em 25/11 às 11:30; pode ter mudado na loja" in index and AVISO_AMAZON not in index
    assert index.count("<h1") == 1 and 'onerror="this.remove()"' in index and "#c2361a" in index and "focus-visible" in index
    g.gerar([_of(), _of(promocao_id=2, marketplace_codigo="amazon", marketplace_nome="Amazon", titulo="Kindle")], [], tmp_path / "b", AGORA)
    assert AVISO_AMAZON in (tmp_path / "b" / "index.html").read_text()
    assert AVISO_AMAZON in next((tmp_path / "b" / "oferta").glob("2-*.html")).read_text()
    assert AVISO_AMAZON not in next((tmp_path / "b" / "oferta").glob("1-*.html")).read_text()


def test_repositorio_em_memoria_espelha_os_filtros_de_preco_da_vitrine():
    repo, pub, _, cfg = cenario({})
    agora = repo.relogio()
    pr = repo.t["promocoes"][0]
    prod = repo._um("produtos", id=pr["produto_id"])
    pr.update(status="publicada", classificacao="boa", publicada_em=agora)
    prod.update(verificado_em=agora, preco_atual=pr["preco_promocional"])
    assert [o["promocao_id"] for o in repo.ofertas_vitrine()] == [pr["id"]]
    prod["verificado_em"] = agora - timedelta(hours=30)
    assert repo.ofertas_vitrine() == []
    prod.update(verificado_em=agora, preco_atual=float(pr["preco_promocional"]) * 1.1)
    assert repo.ofertas_vitrine() == []
