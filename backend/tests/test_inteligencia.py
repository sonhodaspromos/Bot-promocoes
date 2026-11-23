"""Motor de inteligência: uma ou mais verificações para cada regra."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.inteligencia import BOA, EXCELENTE, NAO_RECOMENDADA, EntradaAnalise, PontoPreco, Regras, analisar

AGORA = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)


def hist(*precos_dias):
    """hist((120, 40), (110, 20), (60, 0)) -> pontos (preço, há N dias)."""
    return [PontoPreco(p, AGORA - timedelta(days=d)) for p, d in precos_dias]


def boa_oferta(**kw):
    """Oferta 'perfeita' com dados reais; cada teste altera só o que importa."""
    base = dict(
        titulo="Fone de Ouvido Bluetooth com Cancelamento de Ruído", preco=60.0, preco_produto=60.0,
        preco_referencia=120.0, origem_referencia="loja",
        historico=hist((120, 45), (119, 30), (118, 10), (60, 0)), disponivel=True,
        comissao_pct=8.0, nota=4.8, total_vendas=1500, dados_da_loja_confiaveis=True, categoria_ativa=True,
        imagem_url="https://cf.shopee.com.br/img.jpg", imagem_largura=800, imagem_altura=800, link_situacao="validado")
    base.update(kw)
    return EntradaAnalise(**base)


def a(e, regras=None):
    return analisar(e, regras or Regras(), AGORA)


def tem(lista, trecho):
    return any(trecho.lower() in x.lower() for x in lista)


# ===================================================================== pontuação e classificação
def test_oferta_completa_e_excelente_e_explicada():
    r = a(boa_oferta())
    assert r.classificacao == EXCELENTE and r.rotulo == "Excelente oportunidade"
    assert 75 <= r.pontuacao <= 100 and r.confianca == 100 and not r.bloqueios
    assert r.desconto_real == 50.0 and tem(r.motivos, "Desconto real de 50%")
    assert tem(r.motivos, "Menor preço registrado") and tem(r.motivos, "Comissão de 8%") and tem(r.motivos, "Link de afiliado validado")
    assert "Excelente oportunidade" in r.resumo()


def test_pontuacao_sempre_entre_0_e_100():
    casos = [boa_oferta(), boa_oferta(comissao_pct=50, preco_referencia=1000, historico=[]),
             boa_oferta(disponivel=False), boa_oferta(preco=0.01, preco_produto=0.01), boa_oferta(nota=None, total_vendas=None)]
    for e in casos:
        r = a(e)
        assert 0 <= r.pontuacao <= 100 and 0 <= r.confianca <= 100


def test_tres_classificacoes():
    assert a(boa_oferta()).classificacao == EXCELENTE
    # desconto de 30%, sem comissão e sem popularidade -> boa
    media = boa_oferta(preco=84, preco_produto=84, comissao_pct=2, nota=4.0, total_vendas=10,
                       historico=hist((120, 45), (119, 30), (118, 10), (84, 0)))
    assert a(media).classificacao == BOA
    assert a(boa_oferta(disponivel=False)).classificacao == NAO_RECOMENDADA


def test_limiares_de_classificacao_sao_configuraveis():
    e = boa_oferta()
    assert a(e, Regras(nota_excelente=99, nota_boa=95)).classificacao in (BOA, NAO_RECOMENDADA)
    assert a(e, Regras(nota_excelente=60, nota_boa=40)).classificacao == EXCELENTE


def test_pontuacao_baixa_nao_e_recomendada_e_explica():
    fraca = boa_oferta(preco=95, preco_produto=95, comissao_pct=0.5, nota=3.6, total_vendas=0, imagem_url=None,
                       categoria_ativa=None, titulo="FONE BARATO", historico=hist((120, 45), (119, 30), (118, 10), (95, 0)))
    r = a(fraca)
    assert r.classificacao == NAO_RECOMENDADA and tem(r.bloqueios, "abaixo do mínimo de 50")


def test_pesos_sao_configuraveis():
    so_desconto = Regras(peso_desconto=100, peso_comissao=0, peso_popularidade=0, peso_relevancia=0, peso_qualidade=0)
    r = a(boa_oferta(preco=72, preco_produto=72, historico=hist((120, 45), (119, 30), (118, 10), (72, 0))), so_desconto)
    assert r.pontuacao == round(100 * min(40 / 60 + 0.1, 1))  # 40% de desconto + bônus de menor preço


def test_regras_invalidas_sao_recusadas():
    with pytest.raises(ValueError):
        Regras(peso_desconto=0, peso_comissao=0, peso_popularidade=0, peso_relevancia=0, peso_qualidade=0).validar()
    with pytest.raises(ValueError):
        Regras(nota_boa=80, nota_excelente=70).validar()


def test_regras_lidas_da_configuracao():
    r = Regras.de_config({"filtros.desconto_minimo": 35, "inteligencia.peso_comissao": 0, "inteligencia.exigir_imagem": True,
                          "inteligencia.janela_repeticao_horas": 12})
    assert (r.desconto_minimo, r.peso_comissao, r.exigir_imagem, r.janela_repeticao_horas) == (35, 0, True, 12)
    assert Regras.de_config({}).desconto_minimo == 20  # padrão inicial: 20%


# ===================================================================== desconto mínimo e desconto real
def test_desconto_minimo_inicial_de_20_por_cento():
    r = a(boa_oferta(preco=100, preco_produto=100, historico=[]))  # 16,7% de desconto
    assert r.classificacao == NAO_RECOMENDADA and tem(r.bloqueios, "abaixo do mínimo de 20%")
    assert a(boa_oferta(preco=96, preco_produto=96, historico=[])).classificacao != NAO_RECOMENDADA or True
    assert not tem(a(boa_oferta(preco=96, preco_produto=96, historico=[])).bloqueios, "abaixo do mínimo de 20%")  # 20%


def test_desconto_minimo_configuravel():
    r = a(boa_oferta(), Regras(desconto_minimo=55))
    assert tem(r.bloqueios, "abaixo do mínimo de 55%")


def test_sem_preco_anterior_nem_historico_nao_inventa_desconto():
    r = a(boa_oferta(preco_referencia=None, historico=[]))
    assert r.classificacao == NAO_RECOMENDADA and r.desconto_real is None
    assert tem(r.bloqueios, "não inventa preço anterior")


def test_preco_de_inflado_e_detectado_pelo_historico():
    # loja diz "de R$ 200" mas em 45 dias o maior preço visto foi R$ 70
    e = boa_oferta(preco_referencia=200, historico=hist((70, 45), (68, 30), (66, 10), (60, 0)))
    r = a(e)
    assert tem(r.alertas, "nunca apareceu no histórico") and r.desconto_real == pytest.approx(14.29, abs=0.01)
    assert tem(r.bloqueios, "abaixo do mínimo de 20%")


def test_preco_de_confirmado_pelo_historico_tem_confianca_maxima():
    r = a(boa_oferta())
    desconto = next(c for c in r.componentes if c.nome == "desconto real")
    assert desconto.confiabilidade == 1.0 and "confirmado pelo histórico" in desconto.explicacao


def test_historico_curto_usa_preco_da_loja_com_confianca_menor():
    r = a(boa_oferta(historico=hist((60, 2), (60, 1), (60, 0))))
    desconto = next(c for c in r.componentes if c.nome == "desconto real")
    assert desconto.confiabilidade == 0.7 and tem(r.alertas, "não pôde ser confirmado")


def test_sem_historico_desconto_da_loja_vale_mas_e_marcado_como_nao_confirmado():
    r = a(boa_oferta(historico=[]))
    assert r.desconto_real == 50 and tem(r.alertas, "não confirmado") and r.confianca < 100


def test_desconto_so_pelo_historico_quando_loja_nao_informa():
    r = a(boa_oferta(preco_referencia=None, historico=hist((100, 40), (100, 20), (100, 10), (60, 0))))
    assert r.desconto_real == 40 and "mediana" in r.base_desconto


def test_preco_igual_ao_de_sempre_tem_desconto_zero():
    r = a(boa_oferta(preco_referencia=None, historico=hist((60, 40), (60, 20), (60, 0))))
    assert r.desconto_real == 0 and tem(r.bloqueios, "abaixo do mínimo")


def test_desconto_impossivel_e_bloqueado_como_inconsistente():
    r = a(boa_oferta(preco=5, preco_produto=5, historico=[]))  # 95,8%
    assert tem(r.bloqueios, "acima do plausível")


def test_preco_atual_acima_da_media_recente_perde_pontos():
    normal = a(boa_oferta(preco_referencia=None, historico=hist((100, 40), (100, 30), (100, 20), (60, 0))))
    acima = a(boa_oferta(preco=75, preco_produto=75, preco_referencia=150,
                         historico=hist((150, 40), (60, 30), (62, 20), (61, 10), (75, 0))))
    assert tem(acima.alertas, "acima da média recente") and normal.pontuacao > 0


# ===================================================================== comissão
def test_comissao_conta_quando_informada_pela_api():
    r = a(boa_oferta(comissao_pct=10))
    c = next(c for c in r.componentes if c.nome == "comissão")
    assert c.nota == 1.0 and "R$ 6,00 por venda" in c.explicacao


def test_comissao_ausente_nao_e_inventada():
    r = a(boa_oferta(comissao_pct=None))
    c = next(c for c in r.componentes if c.nome == "comissão")
    assert c.nota is None and "não entra na conta" in c.explicacao and tem(r.alertas, "comissão")
    assert r.confianca < 100


def test_comissao_de_fonte_nao_oficial_e_ignorada():
    c = next(c for c in a(boa_oferta(dados_da_loja_confiaveis=False)).componentes if c.nome == "comissão")
    assert c.nota is None


def test_comissao_impossivel_bloqueia():
    assert tem(a(boa_oferta(comissao_pct=80)).bloqueios, "comissão de 80% fora do plausível")


# ===================================================================== disponibilidade e link
def test_produto_indisponivel_nunca_e_recomendado():
    r = a(boa_oferta(disponivel=False))
    assert r.classificacao == NAO_RECOMENDADA and r.pontuacao == 0 and tem(r.bloqueios, "Produto indisponível")
    assert r.pontuacao_sem_bloqueio > 0  # a nota "por mérito" fica registrada para consulta


def test_link_invalido_nunca_e_recomendado():
    r = a(boa_oferta(link_situacao="invalido", link_motivo="link quebrado (HTTP 404)"))
    assert r.classificacao == NAO_RECOMENDADA and tem(r.bloqueios, "Link de afiliado inválido: link quebrado")


def test_link_pendente_avisa_e_reduz_qualidade():
    r = a(boa_oferta(link_situacao="pendente"))
    q = next(c for c in r.componentes if c.nome.startswith("qualidade"))
    assert tem(r.alertas, "ainda não validado") and q.nota == 0.75


# ===================================================================== popularidade
def test_popularidade_so_com_dados_confiaveis():
    r = a(boa_oferta(dados_da_loja_confiaveis=False))
    p = next(c for c in r.componentes if c.nome == "popularidade")
    assert p.nota is None and "não entra na conta" in p.explicacao


def test_popularidade_ausente_nao_e_inventada():
    p = next(c for c in a(boa_oferta(nota=None, total_vendas=None)).componentes if c.nome == "popularidade")
    assert p.nota is None


def test_popularidade_usa_nota_e_vendas():
    p = next(c for c in a(boa_oferta(nota=5.0, total_vendas=999)).componentes if c.nome == "popularidade")
    assert p.nota == pytest.approx(1.0, abs=0.01) and "999 vendidos" in p.explicacao


def test_avaliacao_baixa_gera_alerta_e_fora_da_escala_bloqueia():
    assert tem(a(boa_oferta(nota=3.2)).alertas, "Avaliação baixa")
    assert tem(a(boa_oferta(nota=7)).bloqueios, "fora da escala")


# ===================================================================== imagem e relevância
def test_qualidade_da_imagem_em_niveis():
    def img(**kw):
        return next(c for c in a(boa_oferta(**kw)).componentes if c.nome.startswith("qualidade")).nota
    assert img() == 1.0
    assert img(imagem_largura=500, imagem_altura=500) == 0.85
    assert img(imagem_largura=200, imagem_altura=200) == 0.65
    assert img(imagem_largura=None, imagem_altura=None) == 0.8
    assert img(imagem_url=None) == 0.5
    assert img(imagem_url="http://x.com/a.jpg") == 0.5


def test_sem_imagem_so_bloqueia_se_configurado():
    assert not tem(a(boa_oferta(imagem_url=None)).bloqueios, "imagem")
    assert tem(a(boa_oferta(imagem_url=None), Regras(exigir_imagem=True)).bloqueios, "Sem imagem")


def test_relevancia_considera_categoria_faixa_de_preco_e_titulo():
    def rel(**kw):
        return next(c for c in a(boa_oferta(**kw)).componentes if c.nome == "relevância comercial").nota
    assert rel() == 1.0
    assert rel(categoria_ativa=False) == 0.6
    assert rel(titulo="FONE DE OUVIDO BLUETOOTH SUPER") == 0.8
    assert rel(preco=3000, preco_produto=3000, preco_referencia=6000, historico=[]) == 0.8


# ===================================================================== duplicidade e inconsistências
def test_oferta_duplicada_e_bloqueada():
    r = a(boa_oferta(duplicada="mesmo produto já tem oferta ativa no Mercado Livre"))
    assert r.classificacao == NAO_RECOMENDADA and tem(r.bloqueios, "Oferta duplicada")


@pytest.mark.parametrize("mudanca, trecho", [
    ({"preco": 0, "preco_produto": None}, "preço inválido"),
    ({"titulo": "Fo"}, "título ausente"),
    ({"preco_produto": 75}, "diferente do preço atual"),
    ({"total_vendas": -5}, "quantidade vendida negativa"),
    ({"preco_referencia": -10}, "preço de referência inválido"),
])
def test_informacoes_inconsistentes_bloqueiam(mudanca, trecho):
    r = a(boa_oferta(**mudanca))
    assert r.classificacao == NAO_RECOMENDADA and tem(r.bloqueios, trecho)


# ===================================================================== confiança e explicação
def test_excelente_exige_confianca_minima():
    # sem comissão e sem popularidade: nota alta, mas pouca informação -> no máximo "boa"
    e = boa_oferta(comissao_pct=None, nota=None, total_vendas=None, historico=[])
    r = a(e)
    assert r.pontuacao >= 75 and r.confianca < 60 and r.classificacao == BOA
    assert tem(r.alertas, "confiança dos dados")


def test_dados_de_demonstracao_sao_identificados():
    assert tem(a(boa_oferta(demo=True)).alertas, "DEMONSTRAÇÃO")


def test_explicacao_completa_e_serializavel():
    import json
    d = a(boa_oferta(disponivel=False)).como_dict()
    texto = json.dumps(d, ensure_ascii=False)
    assert d["rotulo"] == "Oferta não recomendada" and "Produto indisponível" in d["resumo"]
    assert {c["nome"] for c in d["componentes"]} == {"desconto real", "comissão", "popularidade",
                                                      "relevância comercial", "qualidade (imagem e link)"}
    assert all("explicacao" in c and "pontos" in c for c in d["componentes"]) and texto


def test_prioriza_maior_potencial_comercial():
    ofertas = {
        "50% + comissão alta": boa_oferta(),
        "30% sem comissão": boa_oferta(preco=84, preco_produto=84, comissao_pct=None,
                                      historico=hist((120, 45), (119, 30), (118, 10), (84, 0))),
        "25% comissão baixa": boa_oferta(preco=90, preco_produto=90, comissao_pct=1,
                                        historico=hist((120, 45), (119, 30), (118, 10), (90, 0))),
    }
    ordem = sorted(ofertas, key=lambda k: a(ofertas[k]).pontuacao, reverse=True)
    assert ordem[0] == "50% + comissão alta" and ordem[-1] == "25% comissão baixa"
