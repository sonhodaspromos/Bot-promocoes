"""Cupons de desconto e campanhas: regras do cupom, campanhas da Shopee, sugestões, stories diários."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import requests
from PIL import Image

from app.cliente_http import ClienteHTTP
from app.erros import ErroBanco
from app.integracoes.base import REAL, FonteOfertas
from app.integracoes.canais.demo import DemoCanal
from app.integracoes.canais.telegram import TelegramCanal
from app.integracoes.fontes.demo import DemoFonte
from app.integracoes.fontes.shopee import ShopeeFonte, campanha_de_no
from app.integracoes.registro import Integracoes
from app.logs import Log
from app.modelos import CampanhaEncontrada, Cupom, OfertaEncontrada
from app.orquestrador import Orquestrador
from app.repositorio_memoria import MemoriaRepositorio
from app.servicos import arte
from app.servicos.busca import ServicoBusca
from app.servicos.cupons import ServicoCupons, melhor_cupom
from app.servicos.inteligencia import ServicoInteligencia
from app.servicos.publicacao import ServicoPublicacao
from dubles import RespostaFalsa, SessaoFalsa

TARDE = datetime(2026, 11, 23, 18, 0, tzinfo=timezone.utc)   # 15:00 em Recife
CEDO = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)    # 07:00 em Recife (antes das 9h)
TOKEN = "123456789:AAH-segredo_do_telegram_0123456789abcdef"


def log():
    return Log(saida=io.StringIO())


def cliente(sessao, tentativas=1):
    return ClienteHTTP("t", sessao=sessao, tentativas=tentativas, dormir=lambda s: None)


def cupom(**kw):
    base = dict(id=1, marketplace="shopee", titulo="Cupom", fim_em=TARDE + timedelta(days=1), codigo="ACHADO10",
                desconto_tipo="percentual", desconto_valor=10, valor_minimo=50, desconto_maximo=30)
    base.update(kw)
    return Cupom(**base)


class LojaReal(FonteOfertas):
    codigo, modo, suporta_consulta = "shopee", REAL, True

    def __init__(self):
        self.itens = [
            OfertaEncontrada(marketplace="shopee", id_externo="1", titulo="Fone Bluetooth sem fio com estojo", preco=99.9,
                             preco_original=249.9, link_afiliado="https://s.shopee.com.br/A1", nota=4.8, total_vendas=3000, comissao_pct=8),
            OfertaEncontrada(marketplace="shopee", id_externo="2", titulo="Air Fryer 4 litros digital", preco=279.9,
                             preco_original=599.9, link_afiliado="https://s.shopee.com.br/A2", nota=4.7, total_vendas=900, comissao_pct=6),
            OfertaEncontrada(marketplace="shopee", id_externo="3", titulo="Fone de ouvido com fio simples", preco=19.9,
                             preco_original=49.9, link_afiliado="https://s.shopee.com.br/A3", nota=4.6, total_vendas=500, comissao_pct=5),
        ]

    def buscar(self, palavra, limite=30):
        return list(self.itens)  # qualquer palavra: o mesmo catálogo (sem duplicar no banco)

    def consultar(self, id_externo):
        return next((o for o in self.itens if o.id_externo == id_externo), None)


def ambiente(canais_reais: dict | None = None, relogio=TARDE):
    """Loja real com 3 ofertas analisadas + canais (demo por padrão)."""
    repo, loja = MemoriaRepositorio(), LojaReal()
    repo.relogio = lambda: relogio
    repo.definir_modo("marketplaces", "shopee", "real")
    canais_reais = canais_reais or {}
    for codigo in ("telegram", "instagram_story", "instagram_feed"):
        repo.definir_modo("canais", codigo, "real" if codigo in canais_reais else "demo")
    repo.config.update({"busca.palavras_por_execucao": 99, "monitor.verificar_links": False})
    cfg = repo.configuracoes()
    ServicoBusca(repo, {"shopee": loja}, log()).executar(cfg)
    canais = {c: DemoCanal(c) for c in ("telegram", "instagram_story", "instagram_feed")}
    canais.update(canais_reais)
    pub = ServicoPublicacao(repo, canais, log(), url_supabase="https://abc.supabase.co", relogio=lambda: relogio,
                            http=cliente(SessaoFalsa()))
    pub.validar_links(cfg)
    ServicoInteligencia(repo, log(), relogio=lambda: relogio).executar(cfg)
    srv = ServicoCupons(repo, {"shopee": loja}, pub, log(), relogio=lambda: relogio)
    return repo, pub, srv, cfg


def promo_por_titulo(repo, trecho):
    for pr in repo.t["promocoes"]:
        if trecho in repo._um("produtos", id=pr["produto_id"])["titulo"]:
            return pr
    raise AssertionError(trecho)


# ================================================================ regras do cupom
def test_desconto_do_cupom_respeita_minimo_e_teto():
    c = cupom()
    assert c.preco_com_cupom(100) == 90.0          # 10%
    assert c.preco_com_cupom(400) == 370.0         # 10% seria 40, teto de R$ 30
    assert c.preco_com_cupom(49.9) is None         # abaixo do mínimo: não vale
    assert cupom(desconto_tipo="valor", desconto_valor=20, valor_minimo=None, desconto_maximo=None).preco_com_cupom(15) == 0.01
    assert cupom(desconto_tipo=None, desconto_valor=None).preco_com_cupom(100) is None   # desconto não informado: não inventa
    assert c.descricao() == "10% OFF (até R$ 30,00) acima de R$ 50,00"


def test_melhor_cupom_escolhe_o_mais_barato_e_ignora_campanha_sem_codigo():
    a = cupom(id=1, codigo="DEZ")
    b = cupom(id=2, codigo="VINTE", desconto_tipo="valor", desconto_valor=20, desconto_maximo=None)
    campanha = cupom(id=3, codigo=None, tipo="campanha", desconto_tipo="valor", desconto_valor=50)
    escolhido, preco = melhor_cupom([a, b, campanha], 150)
    assert escolhido.codigo == "VINTE" and preco == 130.0
    assert melhor_cupom([a], 10) is None


# ================================================================ campanhas oficiais da Shopee
def test_campanha_da_api_e_convertida_sem_inventar_dados():
    agora = TARDE
    ok = campanha_de_no({"offerName": "Semana do Frete Grátis", "offerLink": "https://s.shopee.com.br/Camp1",
                         "collectionId": 777, "periodStartTime": int((agora - timedelta(days=1)).timestamp()),
                         "periodEndTime": int((agora + timedelta(days=3)).timestamp()) * 1000, "commissionRate": "0.07"}, agora)
    assert ok.id_externo == "camp-777" and ok.fim_em == datetime.fromtimestamp(int((agora + timedelta(days=3)).timestamp()), timezone.utc)
    assert round(ok.comissao_pct, 2) == 7.0
    assert campanha_de_no({"offerName": "Sem link"}, agora) is None
    assert campanha_de_no({"offerName": "Vencida", "offerLink": "https://s.shopee.com.br/x",
                           "periodEndTime": int((agora - timedelta(hours=1)).timestamp())}, agora) is None


def test_shopee_consulta_campanhas_pela_api_oficial():
    resposta = {"data": {"shopeeOfferV2": {"nodes": [{"offerName": "Ofertas Relâmpago", "offerLink": "https://s.shopee.com.br/R1",
                                                         "periodEndTime": int((TARDE + timedelta(days=1)).timestamp())}]}}}
    sessao = SessaoFalsa().quando("POST", "graphql", RespostaFalsa(200, resposta))
    shopee = ShopeeFonte("app", "segredo", http=cliente(sessao), relogio=lambda: TARDE.timestamp())
    campanhas = shopee.campanhas(5)
    assert [c.titulo for c in campanhas] == ["Ofertas Relâmpago"]
    assert "shopeeOfferV2" in sessao.chamadas[0]["data"].decode()


def test_campanhas_sao_sincronizadas_uma_vez_por_dia():
    repo, pub, srv, cfg = ambiente()

    class Fonte(LojaReal):
        suporta_campanhas = True
        chamadas = 0

        def campanhas(self, limite=20):
            Fonte.chamadas += 1
            return [CampanhaEncontrada("shopee", "camp-1", "Semana do Frete", "https://s.shopee.com.br/F1", TARDE + timedelta(days=2)),
                    CampanhaEncontrada("shopee", "camp-2", "Link sem afiliado", "https://shopee.com.br/promo", TARDE + timedelta(days=2))]
    srv.fontes = {"shopee": Fonte()}
    assert srv.sincronizar_campanhas(cfg) == 1                 # a sem link de afiliado é recusada
    assert srv.sincronizar_campanhas(cfg) == 0 and Fonte.chamadas == 1
    assert any("Link sem afiliado" in l["mensagem"] for l in srv.log.pendentes)
    assert [c.titulo for c in repo.cupons_vigentes()] == ["Semana do Frete"]


def test_status_escolhido_pelo_dono_nao_e_sobrescrito_pela_sincronizacao():
    repo, pub, srv, cfg = ambiente()
    camp = CampanhaEncontrada("shopee", "camp-9", "Campanha", "https://s.shopee.com.br/C9", TARDE + timedelta(days=2))
    repo.salvar_campanha(camp)
    repo._um("cupons", id_externo="camp-9")["status"] = "pausado"
    repo.salvar_campanha(camp)
    assert repo._um("cupons", id_externo="camp-9")["status"] == "pausado"


# ================================================================ sugestões de produtos
def test_sugestoes_combinam_palavra_chave_respeitam_minimo_e_esperam_aprovacao():
    repo, pub, srv, cfg = ambiente()
    cid = repo.criar_cupom("shopee", "Cupom de fones", TARDE + timedelta(days=2), codigo="FONE10",
                           desconto_tipo="percentual", desconto_valor=10, valor_minimo=50, palavras_chave=["fone"])
    assert srv.sugerir_produtos(cfg) == 1                     # o fone de R$ 19,90 fica fora (mínimo R$ 50)
    (sug,) = [l for l in repo.t["cupons_produtos"] if l["cupom_id"] == cid]
    assert sug["origem"] == "sugestao" and sug["aprovado"] is False and "fone" in sug["motivo"]
    assert repo.cupons_da_promocao(sug["promocao_id"]) == []  # sem aprovação, não vale para publicação
    assert srv.sugerir_produtos(cfg) == 0                     # não repete a mesma sugestão


def test_sem_palavra_chave_nem_categoria_o_bot_nao_adivinha():
    repo, pub, srv, cfg = ambiente()
    repo.criar_cupom("shopee", "Cupom geral", TARDE + timedelta(days=2), codigo="GERAL")
    assert srv.sugerir_produtos(cfg) == 0


# ================================================================ publicação diária
def test_cupom_publica_no_story_e_no_telegram_uma_vez_por_dia():
    repo, pub, srv, cfg = ambiente()
    repo.criar_cupom("shopee", "Cupom da semana", TARDE + timedelta(days=3), codigo="SEMANA15",
                     desconto_tipo="percentual", desconto_valor=15, link_afiliado="https://s.shopee.com.br/Cup1")
    assert srv.publicar(cfg) == 2
    assert srv.publicar(cfg) == 0                              # mesmo dia: não repete
    story = pub.canais["instagram_story"].publicados[-1]
    assert Image.open(io.BytesIO(story.imagem_story)).size == (1080, 1920)
    tg = pub.canais["telegram"].publicados[-1]
    assert "SEMANA15" in tg.legenda and "#publi" in tg.legenda and tg.link_rastreado == "https://s.shopee.com.br/Cup1"
    amanha = TARDE + timedelta(days=1)
    repo.relogio = srv.relogio = lambda: amanha
    assert srv.publicar(cfg) == 2                              # dia seguinte: publica de novo


def test_antes_do_horario_cupom_vencido_e_pausado_nao_publicam():
    repo, pub, srv, cfg = ambiente(relogio=CEDO)
    repo.criar_cupom("shopee", "Cupom", CEDO + timedelta(days=1), codigo="CEDO10")
    assert srv.publicar(cfg) == 0                              # antes das 9h
    repo2, pub2, srv2, cfg2 = ambiente()
    vencido = repo2.criar_cupom("shopee", "Vencido", TARDE - timedelta(minutes=1), codigo="VELHO",
                                inicio_em=TARDE - timedelta(days=2))
    pausado = repo2.criar_cupom("shopee", "Pausado", TARDE + timedelta(days=1), codigo="PAUSA")
    repo2._um("cupons", id=pausado)["status"] = "pausado"
    assert repo2._um("cupons", id=vencido)["status"] == "expirado"
    assert srv2.publicar(cfg2) == 0


def test_limite_de_stories_de_cupom_por_dia():
    repo, pub, srv, cfg = ambiente()
    cfg["cupons.max_stories_dia"] = 2
    cfg["cupons.publicar_telegram"] = False
    for i in range(4):
        repo.criar_cupom("shopee", f"Cupom {i}", TARDE + timedelta(days=1 + i), codigo=f"CUPOM{i}")
    assert srv.publicar(cfg) == 2
    assert [c.titulo for c in pub.canais["instagram_story"].publicados] == ["Cupom 0", "Cupom 1"]   # vence primeiro, sai primeiro


def test_cupom_de_demonstracao_nao_vai_para_canal_real():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", RespostaFalsa(200, {"ok": True, "result": {"message_id": 5}}))
    repo, pub, srv, cfg = ambiente({"telegram": TelegramCanal(TOKEN, "@canal", http=cliente(sessao))})
    repo.criar_cupom("demo", "[DEMO] Cupom", TARDE + timedelta(days=1), codigo="DEMO10")
    assert srv.publicar(cfg) == 1                              # só o story (canal demo)
    assert not sessao.chamadas


def test_cupom_sem_link_vai_para_o_telegram_sem_botao():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", RespostaFalsa(200, {"ok": True, "result": {"message_id": 5}}))
    repo, pub, srv, cfg = ambiente({"telegram": TelegramCanal(TOKEN, "@canal", http=cliente(sessao))})
    repo.criar_cupom("shopee", "Cupom no app", TARDE + timedelta(days=1), codigo="APP5")
    srv.publicar(cfg)
    envio = sessao.chamadas[0]
    assert "reply_markup" not in envio["data"] and "APP5" in envio["data"]["caption"]


def test_campanha_com_link_ganha_botao_pegar_cupom():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", RespostaFalsa(200, {"ok": True, "result": {"message_id": 5}}))
    repo, pub, srv, cfg = ambiente({"telegram": TelegramCanal(TOKEN, "@canal", http=cliente(sessao))})
    repo.salvar_campanha(CampanhaEncontrada("shopee", "camp-1", "Frete Grátis", "https://s.shopee.com.br/F1", TARDE + timedelta(days=1)))
    srv.publicar(cfg)
    assert "Pegar cupom" in sessao.chamadas[0]["data"]["reply_markup"]


def test_timeout_no_envio_do_cupom_nao_repete_no_mesmo_dia():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", requests.ReadTimeout("timeout"))
    repo, pub, srv, cfg = ambiente({"telegram": TelegramCanal(TOKEN, "@canal", http=cliente(sessao, 3))})
    repo.criar_cupom("shopee", "Cupom", TARDE + timedelta(days=1), codigo="INCERTO")
    srv.publicar(cfg)
    srv.publicar(cfg)
    assert len(sessao.chamadas) == 1


def test_token_vencido_pausa_cupons_no_canal():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", RespostaFalsa(401, {"ok": False, "error_code": 401, "description": "Unauthorized"}))
    repo, pub, srv, cfg = ambiente({"telegram": TelegramCanal(TOKEN, "@canal", http=cliente(sessao))})
    srv.log = log()
    for i in range(3):
        repo.criar_cupom("shopee", f"Cupom {i}", TARDE + timedelta(days=1), codigo=f"TOKEN{i}")
    srv.publicar(cfg)
    assert len(sessao.chamadas) == 1 and "telegram" in pub.canais_bloqueados
    assert srv.log.contagem["CRITICO"] == 1


# ================================================================ produtos com cupom
def test_produto_com_cupom_aprovado_mostra_codigo_e_preco_com_cupom():
    repo, pub, srv, cfg = ambiente()
    fone = promo_por_titulo(repo, "Fone Bluetooth")
    cid = repo.criar_cupom("shopee", "Cupom", TARDE + timedelta(days=1), codigo="FONE10", desconto_tipo="valor", desconto_valor=10)
    repo.vincular_produto_cupom(cid, fone["id"])                 # adicionado pelo dono = aprovado
    pub.enfileirar(cfg)
    pub.processar_fila(cfg)
    tg = next(c for c in pub.canais["telegram"].publicados if c.promocao_id == fone["id"])
    assert "FONE10" in tg.legenda and "R$ 89,90" in tg.legenda


def test_sugestao_nao_aprovada_nao_aparece_no_post():
    repo, pub, srv, cfg = ambiente()
    fone = promo_por_titulo(repo, "Fone Bluetooth")
    cid = repo.criar_cupom("shopee", "Cupom", TARDE + timedelta(days=1), codigo="FONE10", desconto_tipo="valor", desconto_valor=10)
    repo.sugerir_produto_cupom(cid, fone["id"], "título contém: fone")
    pub.enfileirar(cfg)
    pub.processar_fila(cfg)
    tg = next(c for c in pub.canais["telegram"].publicados if c.promocao_id == fone["id"])
    assert "FONE10" not in tg.legenda


def test_cupom_conta_no_desconto_real_da_analise():
    repo, pub, srv, cfg = ambiente()
    loja_manual = promo_por_titulo(repo, "Air Fryer")
    loja_manual["preco_referencia"] = None                       # sem "preço de": sem cupom, seria bloqueada
    repo._um("produtos", id=loja_manual["produto_id"])["preco_original"] = None
    cid = repo.criar_cupom("shopee", "Cupom cozinha", TARDE + timedelta(days=1), codigo="COZINHA30",
                           desconto_tipo="percentual", desconto_valor=30, valor_minimo=100)
    repo.vincular_produto_cupom(cid, loja_manual["id"])
    loja_manual.update(analisada_em=None, classificacao=None, pontuacao=None, confianca=None, analise=None)
    ServicoInteligencia(repo, log(), relogio=lambda: TARDE).executar(cfg)
    assert loja_manual["classificacao"] in ("excelente", "boa")
    assert any("COZINHA30" in m for m in loja_manual["analise"]["motivos"])


def test_sem_tabela_de_cupons_a_publicacao_continua():
    repo, pub, srv, cfg = ambiente()

    def quebrado(promocao_id):
        raise ErroBanco("relation cupons_produtos does not exist", codigo="42P01")
    repo.cupons_da_promocao = quebrado
    pub.enfileirar(cfg)
    assert pub.processar_fila(cfg)[0] > 0


# ================================================================ rodada completa
def test_rodada_completa_demo_publica_campanha_demo_nos_canais_demo():
    repo = MemoriaRepositorio()
    repo.relogio = lambda: TARDE
    integ = Integracoes(fontes={"demo": DemoFonte()}, canais={c: DemoCanal(c) for c in ("telegram", "instagram_story", "instagram_feed")})
    pub = ServicoPublicacao(repo, integ.canais, log(), url_supabase="https://abc.supabase.co", relogio=lambda: TARDE)
    orq = Orquestrador(repo, integ, log(), url_supabase="https://abc.supabase.co", publicacao=pub,
                       cupons=ServicoCupons(repo, integ.fontes, pub, log(), relogio=lambda: TARDE))
    status, resumo = orq.executar()
    assert resumo.campanhas == 1 and resumo.cupons_publicados == 2
    story = [c for c in integ.canais["instagram_story"].publicados if c.promocao_id == 0][0]
    assert story.demo and "[DEMO]" in story.titulo


def test_arte_do_cupom_tem_codigo_publi_e_tamanho_certo():
    c = cupom(regras="Válido em produtos selecionados", marketplace_nome="Shopee")
    for largura, altura in ((1080, 1920), (1080, 1350)):
        img = Image.open(io.BytesIO(arte.criar_arte_cupom(c, None, largura, altura, {})))
        assert img.size == (largura, altura) and img.format == "JPEG"


def test_condicoes_do_cupom_nunca_sao_cortadas_na_arte():
    from PIL import ImageDraw
    c = cupom(desconto_maximo=30, valor_minimo=79, regras="Válido em produtos selecionados da loja oficial, limitado a um uso por pessoa")
    principal, condicoes = arte._desconto_e_condicoes(c)
    assert principal == "10% OFF" and "R$ 30,00" in condicoes and "acima de R$ 79,00" in condicoes
    dr = ImageDraw.Draw(Image.new("RGB", (1080, 1920)))
    linhas = arte._quebrar_sem_cortar(dr, condicoes + " " + c.regras, arte.fonte(40, True), 840)
    assert " ".join(linhas) == condicoes + " " + c.regras and not any(l.endswith("...") for l in linhas)
