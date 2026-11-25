"""Auditoria complementar · P0 6, P1 19 e 20: desconto comprovado, variações, preço na hora de publicar, parada de emergência."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from app.cliente_http import ClienteHTTP
from app.integracoes.base import REAL, FonteOfertas
from app.integracoes.canais.telegram import TelegramCanal
from app.integracoes.fontes.shopee import ShopeeFonte
from app.inteligencia import EntradaAnalise, PontoPreco, Regras, analisar
from app.logs import Log
from app.modelos import OfertaEncontrada
from app.servicos import textos
from dubles import RespostaFalsa, SessaoFalsa
from test_auditoria import TARDE, TOKEN_TG, cenario, cliente, fila, tg_ok

AGORA = datetime(2026, 11, 25, 15, tzinfo=timezone.utc)
ESTRITO = Regras(exigir_desconto_comprovado=True)


def historico(*precos, dias=40):
    passo = dias / max(len(precos) - 1, 1)
    return [PontoPreco(p, AGORA - timedelta(days=dias - i * passo), True) for i, p in enumerate(precos)]


def base(**kw):
    d = dict(titulo="Fone Bluetooth sem fio", preco=60.0, disponivel=True, link_situacao="validado",
             preco_referencia=120.0, origem_referencia="loja")
    d.update(kw)
    return EntradaAnalise(**d)


# ---------------------------------------------------------------- desconto comprovado
def test_desconto_so_da_loja_nao_e_publicado_em_modo_estrito():
    r = analisar(base(), ESTRITO, AGORA)
    assert r.classificacao == "nao_recomendada" and not r.desconto_comprovado
    assert any("não comprovado" in b for b in r.bloqueios)


def test_desconto_confirmado_pelo_historico_e_comprovado():
    r = analisar(base(historico=historico(120, 119, 121, 60)), ESTRITO, AGORA)
    assert r.desconto_comprovado and r.classificacao != "nao_recomendada"


def test_preco_de_inflado_tambem_nao_passa():
    r = analisar(base(preco_referencia=300.0, historico=historico(80, 82, 79, 60)), ESTRITO, AGORA)
    assert r.desconto_real < 30 and r.desconto_comprovado            # calculado pelo maior preço visto, não pelo "de" inflado


def test_preco_de_derivado_da_taxa_da_shopee_nunca_e_comprovado():
    r = analisar(base(referencia_derivada=True, historico=historico(120, 118, 60, dias=3)), ESTRITO, AGORA)
    assert not r.desconto_comprovado


def test_sem_modo_estrito_continua_como_antes():
    assert analisar(base(), Regras(), AGORA).classificacao != "nao_recomendada"


def test_legenda_so_mostra_de_por_quando_comprovado():
    d = {"titulo": "Fone", "preco_promocional": 60, "preco_referencia": 120, "desconto_pct": 50,
         "marketplace_nome": "Shopee", "desconto_comprovado": False}
    cfg = {"transparencia.marcar_publi": True}
    sem = textos.legenda_telegram(d, "https://x.supabase.co/r?l=1", cfg)
    com = textos.legenda_telegram({**d, "desconto_comprovado": True}, "https://x.supabase.co/r?l=1", cfg)
    assert "120,00" not in sem and "50%" not in sem
    assert "120,00" in com and "50%" in com


# ---------------------------------------------------------------- variações
def test_preco_diferente_entre_variacoes_bloqueia():
    r = analisar(base(preco_maximo_variacao=95.0, historico=historico(120, 121, 60)), ESTRITO, AGORA)
    assert r.classificacao == "nao_recomendada" and any("variações" in b for b in r.bloqueios)
    ok = analisar(base(preco_maximo_variacao=61.0, historico=historico(120, 121, 60)), ESTRITO, AGORA)
    assert not any("variações" in b for b in ok.bloqueios)


def test_shopee_le_preco_maximo_e_marca_preco_de_derivado():
    s = SessaoFalsa().quando("POST", "graphql", RespostaFalsa(200, {"data": {"productOfferV2": {"nodes": [
        {"itemId": 7, "productName": "Camiseta básica algodão", "priceMin": "29.90", "priceMax": "59.90",
         "priceDiscountRate": 40, "offerLink": "https://s.shopee.com.br/c7"}]}}}))
    (o,) = ShopeeFonte("a", "b", http=cliente(s)).buscar("camiseta")
    assert o.preco == 29.9 and o.preco_maximo == 59.9 and o.referencia_derivada


# ---------------------------------------------------------------- preço na hora de publicar
class LojaQueMuda(FonteOfertas):
    codigo, modo, suporta_consulta = "shopee", REAL, True

    def __init__(self, loja):
        self.loja, self.consultas = loja, 0

    def buscar(self, p, limite=30):
        return self.loja.buscar(p, limite)

    def consultar(self, id_externo):
        self.consultas += 1
        return self.loja.consultar(id_externo)


def test_preco_e_reconferido_antes_de_publicar_e_aumento_cancela():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", tg_ok)
    repo, pub, loja, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    espia = LojaQueMuda(loja)
    pub.fontes = {"shopee": espia}
    loja.catalogo["111"].preco = 139.9                     # subiu entre a análise e a publicação
    pub.processar_fila(cfg)
    assert espia.consultas >= 1
    textos_enviados = " ".join(c["data"]["caption"] for c in sessao.chamadas if "sendPhoto" in c["url"])
    assert "Fone Bluetooth" not in textos_enviados          # o fone não saiu com o preço velho
    assert "Air Fryer" in textos_enviados                   # a outra oferta (preço igual) saiu


def test_produto_esgotado_na_hora_de_publicar_nao_sai():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", tg_ok)
    repo, pub, loja, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    pub.fontes = {"shopee": LojaQueMuda(loja)}
    loja.catalogo["222"].disponivel = False
    pub.processar_fila(cfg)
    assert not any("Air Fryer" in c["data"]["caption"] for c in sessao.chamadas if "sendPhoto" in c["url"])


def test_oferta_manual_com_preco_velho_nao_e_publicada():
    from app.servicos.publicacao import ServicoPublicacao
    sessao = SessaoFalsa().quando("POST", "sendPhoto", tg_ok)
    repo, pub, loja, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    for p in repo.t["produtos"]:
        p["verificado_em"] = TARDE - timedelta(hours=30)        # ninguém conferiu o preço há 30 h
    pub.fontes = {}                                             # loja sem consulta (ex.: Mercado Livre manual)
    pub.processar_fila(cfg)
    assert not any("sendPhoto" in c["url"] for c in sessao.chamadas)
    assert all(f["status"] == "pendente" for f in fila(repo, "telegram"))


# ---------------------------------------------------------------- parada de emergência
def test_parada_de_emergencia_impede_qualquer_envio_real():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", tg_ok)
    repo, pub, loja, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    repo.config["sistema.parada_emergencia"] = True
    pub.processar_fila(cfg)
    assert not sessao.chamadas
    assert all(f["status"] == "pendente" and f["tentativas"] == 0 for f in fila(repo, "telegram"))


def test_parada_ligada_no_meio_da_rodada_para_o_proximo_envio():
    repo_ref = {}

    def envia_e_liga(metodo, url, **kw):
        repo_ref["r"].config["sistema.parada_emergencia"] = True   # dono apertou o botão durante a rodada
        return RespostaFalsa(200, {"ok": True, "result": {"message_id": 1}})
    sessao = SessaoFalsa().quando("POST", "sendPhoto", envia_e_liga)
    repo, pub, loja, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    repo_ref["r"] = repo
    pub.processar_fila(cfg)
    assert len(sessao.chamadas) == 1                        # o que já estava saindo saiu; nada depois disso


def test_parada_tambem_para_os_cupons():
    from app.servicos.cupons import ServicoCupons
    sessao = SessaoFalsa().quando("POST", "sendPhoto", tg_ok)
    repo, pub, loja, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    repo.criar_cupom("shopee", "Cupom", TARDE + timedelta(days=1), codigo="PARADA")
    repo.config["sistema.parada_emergencia"] = True
    ServicoCupons(repo, {}, pub, Log(saida=io.StringIO()), relogio=lambda: TARDE).publicar(cfg)
    assert not sessao.chamadas


def test_analise_do_servico_usa_variacoes_salvas_no_banco():
    """Fluxo completo: a variação de preço lida da loja chega ao motor e bloqueia a oferta."""
    from app.repositorio_memoria import MemoriaRepositorio
    from app.servicos.busca import ServicoBusca
    from app.servicos.inteligencia import ServicoInteligencia

    class Loja(FonteOfertas):
        codigo, modo = "shopee", REAL
        def buscar(self, p, limite=30):
            return [OfertaEncontrada(marketplace="shopee", id_externo="9", titulo="Tênis de corrida leve", preco=99.0,
                                     preco_original=199.0, preco_maximo=189.0, link_afiliado="https://s.shopee.com.br/T9")]
    repo = MemoriaRepositorio()
    repo.definir_modo("marketplaces", "shopee", "real")
    repo.config["busca.palavras_por_execucao"] = 1
    ServicoBusca(repo, {"shopee": Loja()}, Log(saida=io.StringIO())).executar(repo.configuracoes())
    ServicoInteligencia(repo, Log(saida=io.StringIO())).executar(repo.configuracoes())
    pr = repo.t["promocoes"][0]
    assert pr["classificacao"] == "nao_recomendada" and any("variações" in b for b in pr["analise"]["bloqueios"])


def test_post_usa_o_desconto_comprovado_pela_analise_e_nao_o_da_loja():
    from app.servicos.publicacao import aplicar_desconto_da_analise
    d = {"preco_promocional": 60.0, "preco_referencia": 300.0, "desconto_pct": 80,
         "analise": {"desconto_comprovado": True, "desconto_real": 26.8, "base_desconto": "maior preço visto em 40 dias"}}
    aplicar_desconto_da_analise(d)
    assert d["desconto_pct"] == 27 and round(d["preco_referencia"]) == 82
    sem = {"preco_promocional": 60.0, "preco_referencia": 300.0, "desconto_pct": 80,
           "analise": {"desconto_comprovado": False, "desconto_real": 80, "base_desconto": "preço informado pela loja"}}
    aplicar_desconto_da_analise(sem)
    assert "300,00" not in textos.legenda_telegram({**sem, "titulo": "X", "marketplace_nome": "Shopee"}, "https://l", {})
