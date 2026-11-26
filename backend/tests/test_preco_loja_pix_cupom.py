"""Preço riscado lido da página (desconto comprovado), preço no Pix e cupom sem código."""
from datetime import datetime, timedelta, timezone

from app.inteligencia import Regras, analisar
from app.modelos import Cupom
from app.servicos import textos
from app.servicos.cupons import melhor_cupom
from app.servicos.pagina_produto import extrair_produto
from test_inteligencia import AGORA, boa_oferta, hist

PRODUCAO = Regras(exigir_desconto_comprovado=True)


def manual(**kw):
    base = dict(preco=318.72, preco_produto=318.72, preco_referencia=369.90, historico=[], dados_da_loja_confiaveis=False)
    return boa_oferta(**{**base, **kw})


def test_preco_riscado_da_pagina_conta_como_comprovado_em_producao():
    r = analisar(manual(origem_referencia="pagina"), PRODUCAO, AGORA)
    assert r.desconto_comprovado and not any("não comprovado" in b for b in r.bloqueios)
    assert round(r.desconto_real) == 14 and "página da loja" in r.base_desconto


def test_preco_antigo_digitado_continua_exigindo_historico():
    r = analisar(manual(origem_referencia="manual"), PRODUCAO, AGORA)
    assert not r.desconto_comprovado and any("não comprovado" in b for b in r.bloqueios)


def test_desligado_na_configuracao_volta_a_exigir_historico():
    r = analisar(manual(origem_referencia="pagina"), Regras(exigir_desconto_comprovado=True, aceitar_preco_pagina=False), AGORA)
    assert not r.desconto_comprovado


def test_historico_longo_desmente_o_preco_riscado_da_pagina():
    # o "de R$ 369,90" nunca apareceu em 40 dias: vale o maior preço visto, não o riscado
    e = manual(origem_referencia="pagina", historico=hist((320, 40), (321, 25), (322, 10), (318.72, 0)))
    r = analisar(e, PRODUCAO, AGORA)
    assert "maior preço visto" in r.base_desconto and r.desconto_real < 2


def test_regras_le_a_opcao_da_configuracao():
    assert Regras.de_config({}).aceitar_preco_pagina is True
    assert Regras.de_config({"inteligencia.aceitar_preco_pagina": False}).aceitar_preco_pagina is False


FIM = datetime.now(timezone.utc) + timedelta(days=1)


def test_cupom_sem_codigo_entra_no_calculo_e_campanha_nao():
    seguir = Cupom(1, "mercado_livre", "Cupom por seguir a loja", FIM, desconto_tipo="valor", desconto_valor=31.88)
    campanha = Cupom(2, "mercado_livre", "Campanha", FIM, tipo="campanha", desconto_tipo="valor", desconto_valor=80,
                     link_afiliado="https://meli.la/x")
    c, final = melhor_cupom([seguir, campanha], 318.72)
    assert c is seguir and final == 286.84


def legenda(**kw):
    d = {"promocao_id": 1, "titulo": "Perfume Power Of You 30ml", "preco_promocional": 318.72,
         "preco_referencia": 369.90, "desconto_pct": 14, "desconto_comprovado": True, **kw}
    return textos.legenda_telegram(d, "https://l", {})


def test_legenda_com_pix_e_cupom_sem_codigo():
    t = legenda(preco_no_pix=True, cupom_codigo=None, cupom_na_pagina=True, preco_com_cupom=286.84)
    assert "💥 <b>Por: R$ 318,72 no Pix</b>" in t
    assert "🎟 Com o cupom da loja: <b>R$ 286,84</b> (ative o cupom na página do produto)" in t
    assert "<code>" not in t


def test_legenda_sem_pix_nao_diz_pix():
    assert "Pix" not in legenda()


def test_legenda_do_cupom_sem_codigo():
    c = Cupom(1, "mercado_livre", "Cupom por seguir a loja", FIM, desconto_tipo="valor", desconto_valor=31.88,
              marketplace_nome="Mercado Livre")
    t = textos.legenda_cupom_telegram(c, {})
    assert "Sem código: ative o cupom na página do produto" in t and "Código:" not in t


def test_pix_na_pagina_do_mercado_livre():
    html = ('<meta property="og:title" content="Perfume - R$ 318,72">'
            '<span aria-label="Agora: 318 reais com 72 centavos">R$ 318,72</span><span>13% OFF</span> <span>no Pix</span>')
    assert extrair_produto(html)["preco_pix"] is True
    assert extrair_produto('<span aria-label="Agora: 10 reais">R$ 10</span>')["preco_pix"] is False


def test_arte_com_pix_e_cupom_da_loja_nao_quebra():
    from app.servicos import arte
    d = {"titulo": "Perfume", "preco_promocional": 318.72, "preco_referencia": 369.9, "desconto_pct": 14,
         "marketplace_nome": "Mercado Livre", "preco_no_pix": True, "cupom_na_pagina": True, "preco_com_cupom": 286.84}
    assert arte.criar_arte(d, arte.foto_padrao(), 1080, 1350, {})[:2] == b"\xff\xd8"
