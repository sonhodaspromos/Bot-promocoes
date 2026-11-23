"""Etapa 3 · integrações: formato das chamadas e tratamento das respostas (sem internet)."""
import hashlib
import json

import pytest

from app.config import ler_config
from app.erros import ErroIntegracao
from app.cliente_http import ClienteHTTP
from app.integracoes import registro
from app.integracoes.base import HospedagemImagem
from app.integracoes.canais.demo import DemoCanal
from app.integracoes.canais.instagram import InstagramCanal
from app.integracoes.canais.telegram import TelegramCanal
from app.integracoes.fontes.demo import DemoFonte
from app.integracoes.fontes.manual import ManualFonte
from app.integracoes.fontes.shopee import ShopeeFonte
from app.integracoes.imagens.imgbb import ImgBB
from app.modelos import ConteudoPublicacao
from dubles import RespostaFalsa, SessaoFalsa


def http(sessao, servico="x"):
    return ClienteHTTP(servico, sessao=sessao, tentativas=1, dormir=lambda s: None)


def no(item_id=1, preco="80.00", desconto=20, link="https://s.shopee.com.br/x1", **kw):
    return {"itemId": item_id, "productName": f"Produto {item_id}", "priceMin": preco, "price": preco,
            "priceDiscountRate": desconto, "imageUrl": "https://cf.shopee.com.br/i.jpg", "offerLink": link,
            "ratingStar": "4.8", "sales": 120, "commissionRate": "0.08", **kw}


# ---------------------------------------------------------------- Shopee
def test_shopee_assina_a_requisicao_corretamente():
    s = SessaoFalsa().quando("POST", "shopee", RespostaFalsa(200, {"data": {"productOfferV2": {"nodes": []}}}))
    fonte = ShopeeFonte("123", "segredo", http(s), relogio=lambda: 1700000000)
    fonte.buscar("fone bluetooth", limite=5)
    c = s.chamadas[0]
    corpo = c["data"].decode()
    esperado = hashlib.sha256(("123" + "1700000000" + corpo + "segredo").encode()).hexdigest()
    assert c["headers"]["Authorization"] == f"SHA256 Credential=123, Timestamp=1700000000, Signature={esperado}"
    assert '\\"fone bluetooth\\"' in corpo and "sortType:2" in corpo and "limit:5" in corpo


def test_shopee_converte_ofertas():
    s = SessaoFalsa().quando("POST", "shopee", RespostaFalsa(200, {"data": {"productOfferV2": {"nodes": [
        no(1), no(2, link=""), no(3, preco=None)]}}}))
    ofertas = ShopeeFonte("1", "s", http(s)).buscar("x")
    assert len(ofertas) == 1  # sem link ou sem preço é descartada
    o = ofertas[0]
    assert (o.id_externo, o.preco, o.preco_original, o.comissao_pct, o.nota, o.demo) == ("1", 80.0, 100.0, 8.0, 4.8, False)
    assert o.desconto_pct == 20.0


def test_shopee_erro_da_api_vira_erro_de_integracao():
    s = SessaoFalsa().quando("POST", "shopee", RespostaFalsa(200, {"errors": [{"message": "Cannot query field x"}]}))
    with pytest.raises(ErroIntegracao, match="Cannot query field"):
        ShopeeFonte("1", "s", http(s)).buscar("x")


def test_shopee_consulta_item_e_indisponivel():
    s = SessaoFalsa().quando("POST", "shopee", [RespostaFalsa(200, {"data": {"productOfferV2": {"nodes": [no(9, preco="90")]}}}),
                                                RespostaFalsa(200, {"data": {"productOfferV2": {"nodes": []}}})])
    f = ShopeeFonte("1", "s", http(s))
    assert f.consultar("9").preco == 90.0
    assert f.consultar("9") is None
    with pytest.raises(ValueError):
        f.consultar("9 or 1=1")


def test_shopee_gera_link_curto_e_le_comissoes():
    s = SessaoFalsa().quando("POST", "shopee", [
        RespostaFalsa(200, {"data": {"generateShortLink": {"shortLink": "https://s.shopee.com.br/curto"}}}),
        RespostaFalsa(200, {"data": {"conversionReport": {"nodes": [{"conversionId": 7, "purchaseTime": 1795300000, "orders": [
            {"orderId": "A", "orderStatus": "COMPLETED", "items": [{"itemId": 1, "itemName": "Fone", "itemPrice": "100", "itemTotalCommission": "8"}]},
            {"orderId": "B", "orderStatus": "CANCELLED", "items": [{"itemId": 2, "itemName": "X", "itemTotalCommission": "3"}]}]}]}}})])
    f = ShopeeFonte("1", "s", http(s))
    assert f.gerar_link_afiliado("https://shopee.com.br/p") == "https://s.shopee.com.br/curto"
    from datetime import datetime, timezone
    itens = f.comissoes(datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 22, tzinfo=timezone.utc))
    assert [(i["referencia_externa"], i["status"], i["valor_comissao"]) for i in itens] == [
        ("7-A-1", "confirmada", 8.0), ("7-B-2", "cancelada", 3.0)]


# ---------------------------------------------------------------- Demo e Manual
def test_demo_e_claramente_identificada_e_deterministica():
    a, b = DemoFonte().buscar("air fryer"), DemoFonte().buscar("air fryer")
    assert [o.id_externo for o in a] == [o.id_externo for o in b]
    assert all(o.titulo.startswith("[DEMO]") and o.demo and o.link_afiliado.startswith("https://demo.invalid/") for o in a)


def test_demo_simula_mudanca_de_preco_e_esgotado():
    f = DemoFonte()
    o = f.buscar("fone")[0]
    f.simular_preco(o.id_externo, 999.0)
    assert f.consultar(o.id_externo).preco == 999.0
    f.simular_esgotado(o.id_externo)
    assert f.consultar(o.id_externo) is None


def test_manual_nao_busca_nada():
    f = ManualFonte()
    assert f.buscar("qualquer") == [] and not f.suporta_busca and f.modo == "manual"


# ---------------------------------------------------------------- Telegram
def conteudo(**kw):
    base = dict(promocao_id=1, titulo="Fone", preco=50.0, loja="Shopee", legenda="<b>Fone</b>",
                link_rastreado="https://abc.supabase.co/functions/v1/r?l=5&c=telegram", imagem=b"jpg", imagem_story=b"story")
    base.update(kw)
    return ConteudoPublicacao(**base)


def test_telegram_publica_foto_com_botao_de_link_rastreado():
    s = SessaoFalsa().quando("POST", "sendPhoto", RespostaFalsa(200, {"ok": True, "result": {"message_id": 42}}))
    assert TelegramCanal("123:ABC", "@canal", http(s)).publicar(conteudo()) == "42"
    c = s.chamadas[0]
    assert c["data"]["chat_id"] == "@canal" and c["data"]["parse_mode"] == "HTML"
    assert json.loads(c["data"]["reply_markup"])["inline_keyboard"][0][0]["url"].endswith("r?l=5&c=telegram")
    assert c["files"]["photo"][1] == b"jpg"


def test_telegram_recusa_link_sem_https_e_trata_erro_da_api():
    s = SessaoFalsa().quando("POST", "sendPhoto", RespostaFalsa(403, {"ok": False, "description": "bot was kicked"}))
    canal = TelegramCanal("123:ABC", "@canal", http(s))
    with pytest.raises(ErroIntegracao):
        canal.publicar(conteudo(link_rastreado="http://x"))
    with pytest.raises(ErroIntegracao, match="bot was kicked"):
        canal.publicar(conteudo())


def test_telegram_encerra_oferta_editando_a_mensagem():
    s = SessaoFalsa().quando("POST", "editMessageCaption", RespostaFalsa(200, {"ok": True, "result": True}))
    TelegramCanal("123:ABC", "@canal", http(s)).encerrar("42", "Fone <top>", "preço subiu")
    d = s.chamadas[0]["data"]
    assert "OFERTA ENCERRADA" in d["caption"] and "&lt;top&gt;" in d["caption"]
    assert json.loads(d["reply_markup"]) == {"inline_keyboard": []}


# ---------------------------------------------------------------- Instagram e ImgBB
class HospedagemFalsa(HospedagemImagem):
    def __init__(self): self.enviadas = []
    def enviar(self, imagem):
        self.enviadas.append(imagem)
        return "https://i.ibb.co/x.jpg"


def test_instagram_story_usa_arte_vertical_e_espera_processar():
    s = (SessaoFalsa()
         .quando("POST", "/media_publish", RespostaFalsa(200, {"id": "999"}))
         .quando("POST", "/media", RespostaFalsa(200, {"id": "C1"}))
         .quando("GET", "/C1", [RespostaFalsa(200, {"status_code": "IN_PROGRESS"}), RespostaFalsa(200, {"status_code": "FINISHED"})]))
    hosp = HospedagemFalsa()
    canal = InstagramCanal("story", "tok", "77", hosp, http(s), dormir=lambda s: None)
    assert canal.publicar(conteudo()) == "999"
    criar = [c for c in s.chamadas if c["url"].endswith("/77/media")][0]["data"]
    assert criar["media_type"] == "STORIES" and "caption" not in criar and hosp.enviadas == [b"story"]


def test_instagram_feed_leva_legenda_e_falha_se_imagem_der_erro():
    s = (SessaoFalsa().quando("POST", "/media", RespostaFalsa(200, {"id": "C2"}))
         .quando("GET", "/C2", RespostaFalsa(200, {"status_code": "ERROR"})))
    canal = InstagramCanal("feed", "tok", "77", HospedagemFalsa(), http(s), dormir=lambda s: None)
    with pytest.raises(ErroIntegracao, match="ERROR"):
        canal.publicar(conteudo(legenda="Oferta do dia"))
    assert s.chamadas[0]["data"]["caption"] == "Oferta do dia"


def test_imgbb_devolve_url_ou_erro():
    s = SessaoFalsa().quando("POST", "imgbb", [RespostaFalsa(200, {"data": {"url": "https://i.ibb.co/a.jpg"}}),
                                               RespostaFalsa(200, {"data": {}})])
    h = ImgBB("chave", http(s))
    assert h.enviar(b"img") == "https://i.ibb.co/a.jpg"
    with pytest.raises(ErroIntegracao):
        h.enviar(b"img")


# ---------------------------------------------------------------- registro
BASE = {"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SERVICE_KEY": "sb_secret_x"}


def test_sem_credenciais_tudo_vira_demonstracao():
    integ = registro.montar(ler_config(BASE))
    assert set(integ.fontes) == {"demo", "mercado_livre"} and "shopee" not in integ.fontes
    assert all(isinstance(c, DemoCanal) for c in integ.canais.values()) and integ.modo_geral == "demo"


def test_com_credenciais_usa_integracoes_reais():
    env = {**BASE, "SHOPEE_APP_ID": "1", "SHOPEE_SECRET": "s", "TELEGRAM_TOKEN": "1:a", "TELEGRAM_CANAL": "@c",
           "IG_TOKEN": "t", "IG_USER_ID": "9", "IMGBB_KEY": "k"}
    integ = registro.montar(ler_config(env))
    assert isinstance(integ.fontes["shopee"], ShopeeFonte) and "demo" not in integ.fontes
    assert isinstance(integ.canais["telegram"], TelegramCanal)
    assert isinstance(integ.canais["instagram_feed"], InstagramCanal) and integ.modo_geral == "real"


def test_instagram_sem_imgbb_fica_em_demo():
    env = {**BASE, "IG_TOKEN": "t", "IG_USER_ID": "9"}
    assert isinstance(registro.montar(ler_config(env)).canais["instagram_story"], DemoCanal)
