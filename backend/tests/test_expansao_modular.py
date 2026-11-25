"""Expansão modular: feature flags, adaptadores AliExpress/Amazon (dublês), vitrine, alertas de preço,
WhatsApp com consentimento e teto de gasto, canais por categoria, agendamento inteligente e workers.
Nenhum teste fala com a internet: integração real só é aprovada pelo validar-integracoes."""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.ambiente import Ambiente
from app.cliente_http import chamada_de_leitura
from app.config import Config
from app.erros import ErroBanco, ErroConfiguracao, ErroIntegracao
from app.integracoes import registro
from app.integracoes.base import REAL, FonteOfertas
from app.integracoes.canais.demo import DemoCanal
from app.integracoes.fontes.aliexpress import AliExpressFonte, assinar
from app.integracoes.fontes.amazon import AmazonFonte
from app.logs import Log
from app.modelos import OfertaEncontrada
from app.modulos import Modulos
from app.recursos import RECURSOS, ligado
from app.repositorio_memoria import MemoriaRepositorio
from app.servicos.agendamento import proximo_horario
from app.servicos.alertas import ServicoAlertas, combina, interpretar_alerta
from app.servicos.vitrine import GeradorVitrine
from app.servicos.whatsapp import ServicoWhatsApp
from app.worker import Worker
from dubles import RespostaFalsa, SessaoFalsa
from test_auditoria import TARDE, cenario, cliente, novo_log

AGORA = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)   # 12:00 em Recife


# ============================================================================ feature flags
def test_recursos_desligados_por_padrao_e_kill_switch_por_ambiente():
    repo = MemoriaRepositorio()
    assert not any(ligado(repo.configuracoes(), r, env={}) for r in RECURSOS)
    cfg = {"recursos.vitrine": True}
    assert ligado(cfg, "vitrine", env={}) and not ligado(cfg, "vitrine", env={"RECURSOS_DESLIGADOS": "whatsapp, vitrine"})
    with pytest.raises(ValueError):
        ligado(cfg, "nao_existe")


# ============================================================================ AliExpress
NO_ALI = {"product_id": 1005001, "product_title": "Fone Bluetooth TWS", "target_sale_price": "59.90",
          "target_original_price": "99.90", "promotion_link": "https://s.click.aliexpress.com/e/_abc",
          "commission_rate": "7.5%", "evaluate_rate": "96.5%", "lastest_volume": 1200, "shop_id": 42,
          "product_main_image_url": "https://ae01.alicdn.com/x.jpg"}


def _ali(resposta):
    sessao = SessaoFalsa().quando("POST", "api-sg.aliexpress.com", resposta)
    return AliExpressFonte("chave", "segredo", "meu_tracking", http=cliente(sessao), relogio=lambda: 1_700_000_000), sessao


def test_assinatura_aliexpress_ordenada_e_sem_o_campo_sign():
    a = assinar({"b": "2", "a": "1", "sign": "x"}, "s")
    assert a == assinar({"a": "1", "b": "2"}, "s") and a.isupper() and len(a) == 64


def test_aliexpress_busca_sem_inventar_nota_e_com_link_de_afiliado():
    f, sessao = _ali(RespostaFalsa(200, {"aliexpress_affiliate_product_query_response": {"resp_result": {
        "resp_code": 200, "result": {"products": {"product": [NO_ALI, dict(NO_ALI, product_id=2, target_original_price="10")]}}}}}))
    ofertas = f.buscar("fone")
    o = ofertas[0]
    assert o.preco == 59.9 and o.preco_original == 99.9 and o.comissao_pct == 7.5 and o.total_vendas == 1200
    assert o.nota is None                                   # "% positivas" não é nota de 0 a 5: sem dados
    assert ofertas[1].preco_original is None                # original menor que o preço: descartado
    enviado = sessao.chamadas[0]["data"]
    assert enviado["tracking_id"] == "meu_tracking" and enviado["sign"] and enviado["ship_to_country"] == "BR"


def test_aliexpress_erro_da_api_vira_erro_de_integracao():
    f, _ = _ali(RespostaFalsa(200, {"error_response": {"code": "IncompleteSignature", "msg": "assinatura inválida"}}))
    with pytest.raises(ErroIntegracao, match="assinatura"):
        f.buscar("fone")


def test_aliexpress_comissoes_mapeiam_status():
    f, _ = _ali(RespostaFalsa(200, {"aliexpress_affiliate_order_list_response": {"resp_result": {"resp_code": 200, "result": {
        "orders": {"order": [
            {"order_id": 1, "sub_order_id": 11, "product_id": 9, "order_status": "Order Settled", "estimated_paid_commission": "3.10",
             "paid_amount": "40", "created_time": "2026-11-20 10:00:00"},
            {"order_id": 2, "sub_order_id": 21, "product_id": 9, "order_status": "Invalid", "estimated_paid_commission": "1",
             "created_time": "2026-11-21 10:00:00"}]}}}}}))
    itens = f.comissoes(AGORA - timedelta(days=7), AGORA)
    assert [i["status"] for i in itens] == ["confirmada", "cancelada"] and itens[0]["id_externo"] == "9"


def test_validacao_somente_leitura_aceita_so_metodos_de_consulta_do_aliexpress():
    url = "https://api-sg.aliexpress.com/sync"
    assert chamada_de_leitura("POST", url, {"data": {"method": "aliexpress.affiliate.product.query"}})
    assert not chamada_de_leitura("POST", url, {"data": {"method": "aliexpress.affiliate.link.generate"}})
    assert not chamada_de_leitura("POST", "https://graph.facebook.com/v21.0/1/messages", {"json": {}})


# ============================================================================ Amazon
def _amazon(sessao):
    return AmazonFonte("id", "segredo", "minhatag-20", "https://auth.exemplo/token", "https://api.exemplo/v1",
                       http=cliente(sessao), relogio=lambda: 1000.0)


ITEM_AMZ = {"asin": "B0TESTE", "detailPageURL": "https://www.amazon.com.br/dp/B0TESTE?tag=minhatag-20",
            "itemInfo": {"title": {"displayValue": "Air Fryer 4L"}, "byLineInfo": {"brand": {"displayValue": "Marca"}}},
            "offersV2": {"listings": [{"price": {"money": {"amount": 299.9}, "savingBasis": {"money": {"amount": 399.9}}},
                                       "availability": {"type": "IN_STOCK"}}]}}


def test_amazon_exige_enderecos_oficiais_https():
    with pytest.raises(ErroIntegracao):
        AmazonFonte("id", "s", "tag", "", "https://api")


def test_amazon_busca_com_token_em_cache_e_sem_comissao_inventada():
    sessao = (SessaoFalsa().quando("POST", "auth.exemplo", RespostaFalsa(200, {"access_token": "T", "expires_in": 3600}))
              .quando("POST", "searchItems", RespostaFalsa(200, {"searchResult": {"items": [ITEM_AMZ]}})))
    f = _amazon(sessao)
    o = f.buscar("air fryer")[0]
    f.buscar("air fryer")
    assert o.preco == 299.9 and o.preco_original == 399.9 and o.comissao_pct is None and o.marca == "Marca"
    assert sum(1 for c in sessao.chamadas if "auth.exemplo" in c["url"]) == 1      # token reaproveitado
    corpo = next(c for c in sessao.chamadas if "searchItems" in c["url"])["json"]
    assert corpo["partnerTag"] == "minhatag-20" and corpo["marketplace"] == "www.amazon.com.br"


# ============================================================================ registro e ambientes
def cfg_com(**cred):
    return Config(supabase_url="https://x.supabase.co", supabase_chave="sb_secret_x", credenciais=cred)


def test_producao_bloqueia_aliexpress_ativa_sem_credencial():
    with pytest.raises(ErroConfiguracao, match="BLOQUEADO"):
        registro.montar(cfg_com(), ambiente=Ambiente("producao", ["aliexpress"]))


def test_validacao_lista_novas_fontes_com_categoria_correta():
    alvos = {a.nome: a for a in registro.montar_validacao(cfg_com(), Ambiente("homologacao", ["aliexpress"]))}
    assert alvos["aliexpress"].obrigatoria and alvos["aliexpress"].cliente is None
    assert alvos["amazon"].categoria == "desativada" and alvos["whatsapp"].categoria == "desativada"
    assert alvos["magalu"].categoria == "manual" and alvos["mercado_livre"].categoria == "manual"


class FonteFalsa(FonteOfertas):
    codigo, modo = "aliexpress", REAL

    def __init__(self):
        self.buscas = 0

    def buscar(self, palavra, limite=30):
        self.buscas += 1
        return []


def test_busca_so_usa_fonte_nova_com_a_flag_ligada():
    from app.servicos.busca import ServicoBusca
    repo = MemoriaRepositorio()
    repo.definir_modo("marketplaces", "aliexpress", "real")
    f = FonteFalsa()
    ServicoBusca(repo, {"aliexpress": f}, novo_log()).executar(repo.configuracoes())
    assert f.buscas == 0
    repo.config["recursos.fonte_aliexpress"] = True
    ServicoBusca(repo, {"aliexpress": f}, novo_log()).executar(repo.configuracoes())
    assert f.buscas > 0


# ============================================================================ vitrine
def oferta_v(i, **kw):
    base = {"promocao_id": i, "titulo": f"Produto {i}", "preco": 100.0 + i, "preco_referencia": 200.0, "desconto_pct": 50,
            "desconto_comprovado": True, "imagem_url": "https://img/x.jpg", "marca": "M", "categoria": "Casa",
            "marketplace_nome": "Shopee", "link_id": 10 + i}
    base.update(kw)
    return base


def test_vitrine_gera_paginas_com_seo_e_links_rastreados(tmp_path):
    g = GeradorVitrine("https://ofertas.exemplo", "https://x.supabase.co/functions/v1/r", "Sonho das Promos")
    r = g.gerar([oferta_v(1), oferta_v(2, categoria="Beleza", desconto_comprovado=False)],
                [{"titulo": "10% OFF", "codigo": "PROMO10", "fim_em": "2026-12-01", "marketplace_nome": "Shopee"}], tmp_path, AGORA)
    assert r["ofertas"] == 2 and r["categorias"] == 2
    index = (tmp_path / "index.html").read_text()
    assert '<link rel="canonical" href="https://ofertas.exemplo/">' in index and "application/ld+json" in index
    assert "?l=11&amp;c=vitrine" in index and 'rel="sponsored nofollow noopener"' in index and "#publi" in index
    pag = next((tmp_path / "oferta").glob("1-*.html")).read_text()
    ld = json.loads(pag.split('<script type="application/ld+json">')[1].split("</script>")[0])
    assert ld["@type"] == "Product" and ld["offers"]["price"] == "101.00" and ld["offers"]["priceCurrency"] == "BRL"
    beleza = (tmp_path / "categoria" / "beleza.html").read_text()
    assert "OFF" not in beleza.split("<main>")[1].split("</main>")[0].replace("% OFF</span>", "")  # sem desconto comprovado
    assert "PROMO10" in (tmp_path / "cupons.html").read_text()
    sitemap = (tmp_path / "sitemap.xml").read_text()
    assert "oferta/1-produto-1.html" in sitemap and "categoria/casa.html" in sitemap
    assert "Sitemap: https://ofertas.exemplo/sitemap.xml" in (tmp_path / "robots.txt").read_text()


def test_vitrine_escapa_html_e_ignora_demo_e_imagem_insegura(tmp_path):
    g = GeradorVitrine("https://ofertas.exemplo", "https://r", "X")
    g.gerar([oferta_v(1, titulo='Fone <script>alert(1)</script>', imagem_url="javascript:alert(1)"),
             oferta_v(2, demo=True)], [], tmp_path, AGORA)
    index = (tmp_path / "index.html").read_text()
    assert "<script>alert(1)</script>" not in index and "&lt;script&gt;" in index
    assert "javascript:alert" not in index and "Produto 2" not in index


def test_vitrine_vazia_tem_mensagem(tmp_path):
    GeradorVitrine("https://o.exemplo", "https://r").gerar([], [], tmp_path, AGORA)
    assert "Nenhuma oferta no momento" in (tmp_path / "index.html").read_text()


# ============================================================================ alertas de preço
def test_interpretacao_do_comando_de_alerta():
    assert interpretar_alerta("/alerta fone bluetooth até 150") == ("fone bluetooth", 150.0)
    assert interpretar_alerta("/alerta air fryer ate R$ 299,90") == ("air fryer", 299.9)
    assert interpretar_alerta("/alerta tênis") == ("tênis", None)
    assert interpretar_alerta("/alerta ab") is None
    assert combina("fone bluetooth", 150, "Fone de ouvido Bluetooth JBL", 149.9)
    assert not combina("fone bluetooth", 100, "Fone de ouvido Bluetooth JBL", 149.9)
    assert not combina("fone bluetooth", None, "Caixa de som JBL", 50)


class BotFalso:
    def __init__(self, updates=None, falhar_403=False):
        self.updates, self.enviadas, self.falhar_403 = updates or [], [], falhar_403

    def chamar(self, metodo, **dados):
        if metodo == "getUpdates":
            return self.updates
        if self.falhar_403:
            raise ErroIntegracao("bot bloqueado", servico="telegram", status=403)
        self.enviadas.append(dados)
        return {}


def msg(uid, texto, chat=7, tipo="private"):
    return {"update_id": uid, "message": {"chat": {"id": chat, "type": tipo}, "text": texto}}


def test_comandos_do_bot_criam_listam_apagam_e_param():
    repo = MemoriaRepositorio()
    bot = BotFalso([msg(1, "/start"), msg(2, "/alerta fone bluetooth até 150"), msg(3, "/alerta air fryer"),
                    msg(4, "/meusalertas"), msg(5, "/apagar 1"), msg(6, "/alerta oi grupo", chat=-100, tipo="group")])
    s = ServicoAlertas(repo, bot, novo_log(), "https://r")
    s.processar_comandos(repo.configuracoes())
    assert [a["termo"] for a in repo.alertas_da_pessoa(7)] == ["air fryer"]
    assert repo.alertas_da_pessoa(-100) == []                       # grupo é ignorado
    assert repo.config["alertas.telegram_offset"] == 6
    assert "Guardamos só o identificador" in bot.enviadas[0]["text"]
    bot.updates = [msg(7, "/parar")]
    s.processar_comandos(repo.configuracoes())
    assert repo.alertas_da_pessoa(7) == []


def _publicar_oferta(repo, titulo="Fone de ouvido Bluetooth JBL", preco=120.0):
    from test_auditoria_p0_v2 import CanalEspiao
    repo2, pub, _, cfg = cenario({"telegram": CanalEspiao()})
    return repo2, pub, cfg


def test_aviso_de_preco_uma_vez_por_oferta_e_com_limite_diario():
    repo, pub, cfg = _publicar_oferta(None)
    pub.processar_fila(cfg)
    titulo = next(p["titulo"] for p in repo.t["produtos"] if "Fone" in p["titulo"])
    repo.criar_alerta(7, "fone bluetooth", None)
    repo.relogio = lambda: TARDE + timedelta(minutes=5)
    bot = BotFalso()
    s = ServicoAlertas(repo, bot, novo_log(), "https://r", relogio=lambda: TARDE + timedelta(minutes=5))
    assert s.avisar(cfg) == 1 and s.avisar(cfg) == 0                 # não repete a mesma oferta
    assert "&c=alerta" in bot.enviadas[0]["text"] and "#publi" in bot.enviadas[0]["text"] and titulo in bot.enviadas[0]["text"]


def test_pessoa_que_bloqueou_o_bot_tem_os_dados_apagados():
    repo = MemoriaRepositorio()
    repo.criar_alerta(9, "fone", None)
    ServicoAlertas(repo, BotFalso(falhar_403=True), novo_log(), "https://r")._enviar(9, "oi")
    assert repo.alertas_da_pessoa(9) == []


# ============================================================================ WhatsApp
class WhatsFalso:
    def __init__(self, falha=None):
        self.enviadas, self.falha = [], falha

    def enviar_template(self, telefone, parametros):
        self.enviadas.append((telefone, parametros))
        if self.falha:
            raise self.falha
        return "wamid.1"


def _wa(teto=5.0, falha=None):
    repo = MemoriaRepositorio()
    repo.relogio = lambda: AGORA
    cfg = dict(repo.configuracoes(), **{"whatsapp.orcamento_diario_usd": teto, "whatsapp.custo_mensagem_usd": 0.0625,
                                        "whatsapp.max_por_inscrito_dia": 3})
    cli = WhatsFalso(falha)
    return repo, cfg, cli, ServicoWhatsApp(repo, cli, novo_log(), "https://r", relogio=lambda: AGORA)


OFERTA = {"promocao_id": 1, "titulo": "Air Fryer", "preco": 299.9, "link_id": 5, "categoria_id": 3}


def test_whatsapp_so_para_quem_consentiu_e_respeita_categoria_e_descadastro():
    repo, cfg, cli, s = _wa()
    repo.whatsapp_inscrever("+5581911112222", "mensagem_recebida", "Quero receber ofertas pelo WhatsApp")
    repo.whatsapp_inscrever("+5581933334444", "formulario", "Quero ofertas de Beleza", [9])
    repo.whatsapp_inscrever("+5581955556666", "formulario", "Quero ofertas de tudo")
    repo.whatsapp_descadastrar("+5581955556666")
    r = s.enviar_oferta(OFERTA, cfg)
    assert r["enviados"] == 1 and cli.enviadas[0][0] == "+5581911112222" and "&c=whatsapp" in cli.enviadas[0][1][2]
    assert s.enviar_oferta(OFERTA, cfg)["enviados"] == 0              # mesma oferta não é reenviada


def test_whatsapp_para_no_teto_de_gasto_diario():
    repo, cfg, cli, s = _wa(teto=0.13)                                # cabem 2 mensagens de US$ 0,0625
    for i in range(5):
        repo.whatsapp_inscrever(f"+55819111100{i:02d}", "formulario", "Quero receber ofertas pelo WhatsApp")
    r = s.enviar_oferta(OFERTA, cfg)
    assert r["enviados"] == 2 and r["bloqueado_orcamento"] and r["custo_usd"] == 0.125
    assert any(e["tipo"] == "bloqueio_orcamento" for e in repo.t["wa_eventos"])


def test_whatsapp_resultado_incerto_conta_custo_e_nao_reenvia():
    repo, cfg, cli, s = _wa(falha=ErroIntegracao("timeout", servico="whatsapp", incerto=True))
    repo.whatsapp_inscrever("+5581911112222", "formulario", "Quero receber ofertas pelo WhatsApp")
    s.enviar_oferta(OFERTA, cfg)
    cli.falha = None
    s.enviar_oferta(OFERTA, cfg)
    assert len(cli.enviadas) == 1 and repo.whatsapp_gasto_hoje() == 0.0625


def test_modulo_whatsapp_nao_envia_fora_de_producao():
    repo = MemoriaRepositorio()
    repo.config["recursos.whatsapp"] = True
    integ = registro.Integracoes(whatsapp=WhatsFalso())
    r = Modulos(repo, integ, novo_log(), "https://x.supabase.co", Ambiente("homologacao", [])).whatsapp(repo.configuracoes())
    assert "fora de produção" in r["pulado"] and integ.whatsapp.enviadas == []


# ============================================================================ canais por categoria
def test_canal_extra_recebe_so_a_sua_categoria():
    repo, pub, _, cfg = cenario({})
    fone = next(p for p in repo.t["produtos"] if "Fone" in p["titulo"])
    cat_casa = repo.t["categorias"][1]["id"]
    repo.t["canais"].append({"id": 99, "codigo": "telegram_casa", "tipo": "telegram", "nome": "Casa", "modo": "demo",
                             "ativo": True, "identificador_externo": "@casa"})
    repo.t["canais_categorias"].append({"canal_id": 99, "categoria_id": cat_casa})
    for f in repo.t["fila"]:
        f["status"] = "concluida"
    repo.t["historico"].clear()
    m = Modulos(repo, pub.repo and registro.Integracoes(canais=pub.canais), novo_log(), "https://x.supabase.co")
    cfg = dict(cfg, **{"recursos.canais_por_categoria": True})
    assert m.montar_canais_extras(cfg) == ["telegram_casa"] and isinstance(pub.canais["telegram_casa"], DemoCanal)
    for pr in repo.t["promocoes"]:
        pr["status"] = "aprovada"
    pub.enfileirar(cfg)
    na_casa = [f for f in repo.t["fila"] if f["canal_id"] == 99 and f["status"] == "pendente"]
    esperado = [pr["id"] for pr in repo.t["promocoes"]
                if repo._um("produtos", id=pr["produto_id"]).get("categoria_id") == cat_casa]
    assert sorted(f["promocao_id"] for f in na_casa) == sorted(esperado)


def test_sem_a_flag_os_canais_sao_os_de_sempre():
    repo, pub, _, cfg = cenario({})
    canais = {c["codigo"]: c for c in repo.t["canais"]}
    alvos, regras = pub._canais_alvo(cfg, canais)
    assert alvos == ["telegram", "instagram_story"] and regras == {}


# ============================================================================ agendamento inteligente
CFG_AG = {"publicacao.horario_inicio": 8, "publicacao.horario_fim": 22, "agendamento.min_intervalo_canal_min": 30,
          "geral.fuso_horario": "America/Recife"}


def test_agendamento_sem_dados_espaca_e_respeita_o_horario():
    from app.inteligencia.aprendizado import aprender
    h = proximo_horario(CFG_AG, AGORA, AGORA + timedelta(minutes=10), aprender([]))
    assert h.quando == AGORA + timedelta(minutes=40) and "sem dados" in h.explicacao
    noite = datetime(2026, 11, 26, 2, 0, tzinfo=timezone.utc)        # 23h em Recife: fora do horário
    h = proximo_horario(CFG_AG, noite, None, None)
    assert h.quando.astimezone(timezone(timedelta(hours=-3))).hour == 8


def test_agendamento_com_dados_prefere_periodo_de_melhor_desempenho():
    from app.inteligencia.aprendizado import aprender
    linhas = ([{"cliques": 1, "periodo": "manhã (6h–12h)", "publicado_em": AGORA} for _ in range(30)]
              + [{"cliques": 6, "periodo": "noite (18h–24h)", "publicado_em": AGORA} for _ in range(30)])
    h = proximo_horario(CFG_AG, AGORA, None, aprender(linhas, amostra_minima=20))
    assert h.quando.astimezone(timezone(timedelta(hours=-3))).hour == 18 and "melhor desempenho" in h.explicacao


def test_flag_de_agendamento_grava_horario_na_fila_e_manual_sai_na_hora():
    repo, pub, _, cfg = cenario({})
    for f in repo.t["fila"]:
        f["status"] = "concluida"
    repo.t["historico"].clear()
    for pr in repo.t["promocoes"]:
        pr["status"] = "aprovada"
    cfg = dict(cfg, **{"recursos.agendamento_inteligente": True, "agendamento.min_intervalo_canal_min": 30})
    pub.enfileirar(cfg)
    tg = sorted(f["agendado_para"] for f in repo.t["fila"] if f["status"] == "pendente" and f["canal_id"] == repo.t["canais"][0]["id"])
    assert len(tg) == 2 and tg[1] - tg[0] >= timedelta(minutes=30)


# ============================================================================ workers e fila persistente
def test_worker_processa_retenta_com_espera_e_idempotencia():
    repo = MemoriaRepositorio()
    repo.relogio = lambda: AGORA
    assert repo.tarefa_enfileirar("vitrine.gerar", {}, "vitrine-h1") and repo.tarefa_enfileirar("vitrine.gerar", {}, "vitrine-h1") is None
    chamadas = []

    def instavel(p):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise RuntimeError("rede caiu")
        return {"ok": True}
    w = Worker(repo, {"vitrine.gerar": instavel}, novo_log(), nome="w1")
    assert w.rodar()["falhas"] == 1
    t = repo.t["tarefas"][0]
    assert t["status"] == "pendente" and t["agendada_para"] > AGORA        # espera crescente
    repo.relogio = lambda: AGORA + timedelta(hours=1)
    assert Worker(repo, {"vitrine.gerar": instavel}, novo_log(), nome="w1").rodar()["concluidas"] == 1
    assert repo.t["tarefas"][0]["status"] == "concluida"


def test_worker_descarta_resultado_quando_outro_assumiu():
    repo = MemoriaRepositorio()
    repo.relogio = lambda: AGORA
    repo.tarefa_enfileirar("alertas.processar", {})

    def lento(p):
        repo._um("tarefas", tipo="alertas.processar")["worker"] = "w2"    # trava venceu e w2 assumiu
        return {}
    r = Worker(repo, {"alertas.processar": lento}, novo_log(), nome="w1").rodar()
    assert r["descartadas"] == 1 and r["concluidas"] == 0


def test_banco_fora_para_o_worker():
    repo = MemoriaRepositorio()
    repo.tarefa_enfileirar("vitrine.gerar", {})

    def cai(p):
        raise ErroBanco("fora", status=503)
    with pytest.raises(ErroBanco):
        Worker(repo, {"vitrine.gerar": cai}, novo_log()).rodar()


def test_com_workers_ligado_a_rodada_so_enfileira_uma_vez_por_hora():
    repo = MemoriaRepositorio()
    repo.relogio = lambda: AGORA
    cfg = dict(repo.configuracoes(), **{"recursos.workers": True, "recursos.vitrine": True})
    m = Modulos(repo, registro.Integracoes(), novo_log(), "https://x.supabase.co", relogio=lambda: AGORA)
    m.executar(cfg)
    m.executar(cfg)
    assert [t["tipo"] for t in repo.t["tarefas"]] == ["vitrine.gerar"]


def test_modulo_vitrine_gera_pelo_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("VITRINE_SAIDA", str(tmp_path))
    repo, pub, _, cfg = cenario({})
    repo.config["recursos.vitrine"] = True
    repo.tarefa_enfileirar("vitrine.gerar", {})
    m = Modulos(repo, registro.Integracoes(), novo_log(), "https://x.supabase.co")
    r = Worker(repo, m.handlers(), novo_log()).rodar()
    assert r["concluidas"] == 1 and (tmp_path / "index.html").exists()
