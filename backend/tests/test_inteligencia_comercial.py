"""Inteligência comercial: histórico 7/30/90, menor preço, desconto artificial, equivalência entre lojas,
aprendizado, saturação, ranking por receita sem sacrificar qualidade, testes A/B e explicação das decisões.
Todos os dados são construídos no teste; nada é inventado pelo código (sem dados = "sem dados")."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest

from app.inteligencia import EntradaAnalise, PontoPreco, Regras, analisar
from app.inteligencia.aprendizado import aprender, faixa_de_preco, periodo_do_dia, potencial, receita_esperada
from app.inteligencia.equivalencia import ALTA, BAIXA, MEDIA, comparar, encontrar, gtin_valido, modelo_do_titulo
from app.inteligencia.experimentos import atribuir, avaliar as avaliar_ab
from app.inteligencia.precos import Registro, resumir, resumir_janela
from app.inteligencia.ranking import classificar
from app.inteligencia.saturacao import Saturacao, avaliar as avaliar_saturacao
from app.logs import Log
from app.repositorio_memoria import MemoriaRepositorio
from dubles import RespostaFalsa, SessaoFalsa
from test_auditoria import TARDE, cenario, cliente, fila

AGORA = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)


def r(preco, dias_atras, disponivel=True):
    return Registro(preco, AGORA - timedelta(days=dias_atras), disponivel)


# ============================================================================ 1–3. preços
def test_janela_usa_o_preco_que_ja_valia_antes_dela_ancora():
    # preço 100 desde 200 dias atrás, caiu para 80 há 3 dias: a janela de 30 dias está 100% coberta
    j = resumir_janela([r(100, 200), r(80, 3)], AGORA, 30)
    assert j.suficiente and j.cobertura_pct == 100 and j.minimo == 80 and j.maximo == 100
    assert 95 < j.medio < 100                       # média ponderada pelo tempo (80 vigorou só 3 dias)


def test_sem_ancora_a_janela_longa_fica_insuficiente_e_nao_gera_conclusao():
    res = resumir([r(100, 10), r(80, 1)], AGORA, 80)
    assert res.janelas[7].suficiente and not res.janelas[30].suficiente and not res.janelas[90].suficiente
    assert "sem conclusões" in res.janelas[90].explicacao
    assert any("desconto artificial" in s for s in res.sem_dados) is False or res.desconto_artificial == []


def test_periodo_indisponivel_nao_conta_como_preco():
    j = resumir_janela([r(50, 40, disponivel=False), r(100, 20)], AGORA, 30)
    assert j.minimo == 100                          # os R$ 50 eram de produto esgotado


def test_menor_preco_historico_com_periodo_monitorado():
    res = resumir([r(120, 60), r(99, 30), r(95, 1)], AGORA, 95)
    assert res.menor_preco == 95 and res.preco_atual_e_menor is True
    assert any("Menor preço desde que o bot monitora" in s for s in res.sinais)
    res = resumir([r(80, 60), r(95, 1)], AGORA, 95)
    assert res.preco_atual_e_menor is False and any("Já esteve mais barato" in s for s in res.sinais)


def test_menor_preco_nao_e_afirmado_com_menos_de_7_dias():
    res = resumir([r(100, 3), r(90, 1)], AGORA, 90)
    assert res.preco_atual_e_menor is None and any("monitorado há só" in s for s in res.sem_dados)


def test_sem_historico_nenhuma_afirmacao():
    res = resumir([], AGORA, 90, 150)
    assert res.menor_preco is None and res.preco_atual_e_menor is None and not res.desconto_artificial
    assert len(res.sem_dados) >= 2


def test_desconto_artificial_sobe_e_desce_e_bloqueante():
    # 90 dias a R$ 100, subiu para R$ 150 há 10 dias, "promoção" de R$ 150 por R$ 100
    res = resumir([r(100, 200), r(150, 10), r(100, 1)], AGORA, 100, preco_referencia=150)
    assert res.artificial_bloqueante and any("subiu" in a for a in res.desconto_artificial)


def test_preco_de_nunca_praticado_e_sinalizado_mas_nao_bloqueante():
    res = resumir([r(100, 200), r(90, 2)], AGORA, 90, preco_referencia=200)
    assert res.desconto_artificial and not res.artificial_bloqueante


def test_queda_real_nao_e_artificial():
    res = resumir([r(150, 200), r(100, 2)], AGORA, 100, preco_referencia=150)
    assert not res.desconto_artificial


def _entrada(historico, referencia):
    return EntradaAnalise(titulo="Fone Bluetooth XYZ com estojo", preco=100, disponivel=True, link_situacao="validado",
                          preco_referencia=referencia, origem_referencia="loja",
                          historico=[PontoPreco(x.preco, x.quando, x.disponivel) for x in historico])


def test_motor_bloqueia_desconto_artificial_e_explica():
    res = analisar(_entrada([r(100, 200), r(150, 10), r(100, 1)], 150), Regras(), AGORA)
    assert res.classificacao == "nao_recomendada" and any(b.startswith("Desconto artificial") for b in res.bloqueios)
    assert res.historico_precos["janelas"]["90"]["suficiente"] is True


def test_motor_sem_bloqueio_quando_regra_desligada():
    regras = Regras(bloquear_desconto_artificial=False)
    res = analisar(_entrada([r(100, 200), r(150, 10), r(100, 1)], 150), regras, AGORA)
    assert not any(b.startswith("Desconto artificial") for b in res.bloqueios)
    assert any("artificial" in a for a in res.alertas)


# ============================================================================ 5. equivalência
def test_gtin_digito_verificador():
    assert gtin_valido("4006381333931") and not gtin_valido("4006381333932") and not gtin_valido("00000000")


def test_equivalencia_por_gtin_e_alta_e_gtin_diferente_separa():
    a = {"id": 1, "marketplace": "shopee", "titulo": "Fone X", "gtin": "4006381333931"}
    b = {"id": 2, "marketplace": "mercado_livre", "titulo": "Outro nome", "gtin": "4006381333931"}
    assert comparar(a, b).confianca == ALTA
    c = dict(b, gtin="7891000315507", titulo="Fone X")
    assert comparar(a, c) is None                   # GTINs válidos e diferentes: produtos diferentes


def test_equivalencia_marca_modelo_media_e_modelos_diferentes_separam():
    a = {"id": 1, "marketplace": "shopee", "titulo": "Fone Sony WH-1000XM4 preto", "marca": "Sony"}
    b = {"id": 2, "marketplace": "mercado_livre", "titulo": "Headphone WH1000XM4 Sony", "marca": "sony"}
    assert comparar(a, b).confianca == MEDIA
    c = dict(b, titulo="Headphone WH-1000XM5 Sony")
    assert comparar(a, c) is None


def test_equivalencia_so_por_titulo_e_baixa_e_mesma_loja_nao_compara():
    a = {"id": 1, "marketplace": "shopee", "titulo": "Garrafa térmica inox 1 litro azul tampa rosca"}
    b = {"id": 2, "marketplace": "mercado_livre", "titulo": "Garrafa térmica inox 1 litro azul tampa rosca"}
    assert comparar(a, b).confianca == BAIXA
    assert comparar(a, dict(b, marketplace="shopee")) is None
    assert encontrar([a, b])[0].metodo == "titulo"


def test_medidas_nao_sao_modelo():
    assert modelo_do_titulo("Pendrive 128GB USB 3.0") is None
    assert modelo_do_titulo("Smartphone Galaxy A55 128GB") == "a55" or modelo_do_titulo("Smartphone Galaxy A55 128GB") is None


# ============================================================================ 6. aprendizado
def pub(canal="telegram", cliques=3, categoria="Eletrônicos", periodo="noite (18h–24h)", faixa="R$ 50–100",
        conv=0.0, com=0.0, horas_atras=48, cliques_2h=None, **extra):
    return {"canal": canal, "cliques": cliques, "cliques_2h": cliques if cliques_2h is None else cliques_2h,
            "categoria": categoria, "periodo": periodo, "faixa_preco": faixa, "conversoes_atribuidas": conv,
            "comissao_atribuida": com, "publicado_em": AGORA - timedelta(hours=horas_atras), **extra}


def test_aprendizado_sem_dados_diz_explicitamente():
    ap = aprender([])
    assert ap.publicacoes == 0 and ap.sem_dados and not ap.tem_cliques
    fator, textos = potencial(ap, {"categoria": "X"})
    assert fator is None and "sem histórico" in textos[0]


def test_segmento_abaixo_da_amostra_fica_neutro():
    linhas = [pub() for _ in range(25)] + [pub(categoria="Casa", cliques=30) for _ in range(5)]
    ap = aprender(linhas, amostra_minima=20)
    casa = ap.segmento("categoria", "Casa")
    assert not casa.suficiente and casa.fator_cliques == 1.0 and "insuficientes" in casa.explicacao


def test_segmento_com_amostra_usa_media_encolhida():
    linhas = [pub(cliques=2) for _ in range(40)] + [pub(categoria="Casa", cliques=6) for _ in range(40)]
    ap = aprender(linhas, amostra_minima=20)
    casa = ap.segmento("categoria", "Casa")
    assert casa.suficiente and 1.0 < casa.fator_cliques < 6 / ap.ctr_geral   # encolhido para a média
    fator, _ = potencial(ap, {"categoria": "Casa", "periodo": "noite (18h–24h)", "canal": "telegram",
                              "faixa_preco": "R$ 50–100"})
    assert fator > 1.0


def test_receita_esperada_so_com_conversoes_suficientes():
    linhas = [pub(cliques=4) for _ in range(30)]
    ap = aprender(linhas, amostra_minima=20, min_conversoes=10)
    valor, texto = receita_esperada(ap, 100, 10, 1.0)
    assert valor is None and "insuficientes" in texto
    linhas = [pub(cliques=4, conv=0.5, com=3) for _ in range(30)]          # 15 vendas em 120 cliques
    ap = aprender(linhas, amostra_minima=20, min_conversoes=10)
    valor, texto = receita_esperada(ap, 100, 10, 1.0)
    assert valor == pytest.approx(100 * 0.10 * (15 / 120) * 4) and "receita esperada" in texto


def test_faixas_e_periodos():
    assert faixa_de_preco(49.9) == "até R$ 50" and faixa_de_preco(None) is None and faixa_de_preco(999) == "acima de R$ 500"
    assert periodo_do_dia(5) == "madrugada (0h–6h)" and periodo_do_dia(20) == "noite (18h–24h)"


# ============================================================================ 8. saturação
def test_saturacao_sem_amostra_nao_limita():
    sat = avaliar_saturacao([pub(horas_atras=5)], AGORA)
    assert not sat.canal_saturado("telegram") and "insuficientes" in sat.canais["telegram"].explicacao


def test_canal_saturado_com_queda_forte_de_cliques():
    linhas = [pub(cliques_2h=10, horas_atras=24 * 5) for _ in range(30)] + [pub(cliques_2h=3, horas_atras=6) for _ in range(10)]
    sat = avaliar_saturacao(linhas, AGORA)
    assert sat.canal_saturado("telegram") and "SATURADO" in sat.canais["telegram"].explicacao


def test_categoria_cheia_na_janela():
    linhas = [pub(categoria="Casa", horas_atras=1) for _ in range(3)] + [pub(categoria="Beleza", horas_atras=1)]
    sat = avaliar_saturacao(linhas, AGORA, max_categoria=3, janela_categoria_h=6)
    assert sat.categorias_cheias == {"Casa": 3}


# ============================================================================ 4, 7, 12. ranking e explicação
def oferta(pid, classe="boa", pontuacao=60, preco=100.0, **kw):
    base = {"promocao_id": pid, "produto_id": pid * 10, "marketplace": "shopee", "titulo": f"Produto {pid}",
            "preco": preco, "comissao_pct": 8.0, "dados_confiaveis": True, "pontuacao": pontuacao, "classificacao": classe,
            "desconto_real": 40, "desconto_comprovado": True, "categoria": "Eletrônicos", "faixa_preco": faixa_de_preco(preco),
            "periodo": None, "canal": None, "loja_oficial": None, "estoque": None}
    base.update(kw)
    return base


def test_sem_dados_de_aprendizado_ranking_usa_qualidade_e_diz_o_que_falta():
    decisoes = classificar([oferta(1, pontuacao=70), oferta(2, pontuacao=55)], aprender([]), Saturacao(), [])
    assert [d.promocao_id for d in decisoes] == [1, 2]
    assert all(d.receita_esperada is None for d in decisoes)
    falta = " ".join(decisoes[0].sem_dados)
    assert "vendedor" in falta and "estoque" in falta and "receita esperada" in falta


def test_excelente_sempre_antes_de_boa_mesmo_com_receita_maior():
    linhas = [pub(cliques=4, conv=0.5, com=3) for _ in range(30)]
    ap = aprender(linhas, amostra_minima=20, min_conversoes=10)
    boa_cara = oferta(1, "boa", 60, preco=900.0, comissao_pct=12)
    excelente_barata = oferta(2, "excelente", 80, preco=30.0, comissao_pct=3)
    decisoes = classificar([boa_cara, excelente_barata], ap, Saturacao(), [])
    assert decisoes[0].promocao_id == 2                     # qualidade primeiro
    assert decisoes[1].receita_esperada > decisoes[0].receita_esperada


def test_receita_reordena_dentro_da_mesma_classificacao():
    linhas = [pub(cliques=4, conv=0.5, com=3) for _ in range(30)]
    ap = aprender(linhas, amostra_minima=20, min_conversoes=10)
    decisoes = classificar([oferta(1, "boa", 62, preco=40.0), oferta(2, "boa", 60, preco=400.0)], ap, Saturacao(), [])
    assert decisoes[0].promocao_id == 2 and any("receita esperada" in m for m in decisoes[0].motivos)


def test_produto_equivalente_mais_barato_em_outra_loja_recusa_o_mais_caro():
    from app.inteligencia.equivalencia import Equivalencia
    a = oferta(1, preco=120.0, marketplace="shopee")
    b = oferta(2, preco=100.0, marketplace="mercado_livre")
    eq = Equivalencia(10, 20, ALTA, "gtin", "mesmo GTIN 4006381333931", 1.0)
    d = {x.promocao_id: x for x in classificar([a, b], aprender([]), Saturacao(), [eq])}
    assert not d[1].selecionada and "mercado_livre" in d[1].recusa and "GTIN" in d[1].recusa
    assert d[2].selecionada and d[2].posicao == 1


def test_equivalencia_baixa_so_avisa():
    from app.inteligencia.equivalencia import Equivalencia
    eq = Equivalencia(10, 20, BAIXA, "titulo", "títulos 90% parecidos", 0.9)
    d = classificar([oferta(1, preco=120.0), oferta(2, preco=100.0, marketplace="mercado_livre")], aprender([]), Saturacao(), [eq])
    assert all(x.selecionada for x in d) and any(x.avisos for x in d)


def test_estoque_zero_e_categoria_saturada_sao_recusados_com_motivo():
    sat = Saturacao(categorias_cheias={"Casa": 4})
    d = {x.promocao_id: x for x in classificar([oferta(1, estoque=0), oferta(2, categoria="Casa"), oferta(3, estoque=3)],
                                                aprender([]), sat, [])}
    assert "estoque zerado" in d[1].recusa and "Casa" in d[2].recusa and d[3].selecionada
    assert any("risco de esgotar" in f.explicacao for f in d[3].fatores)


def test_desconto_nao_comprovado_vale_zero_e_comissao_nao_oficial_fica_sem_dados():
    d = classificar([oferta(1, desconto_comprovado=False, dados_confiaveis=False)], aprender([]), Saturacao(), [])[0]
    fatores = {f.nome: f for f in d.fatores}
    assert fatores["desconto comprovado"].nota == 0.0 and "comissão" not in fatores
    assert any("comissão" in s for s in d.sem_dados)


# ============================================================================ 9. A/B
def test_sorteio_ab_deterministico_e_equilibrado():
    assert atribuir(1, 42, "telegram") == atribuir(1, 42, "telegram")
    variantes = [atribuir(1, i, "telegram") for i in range(1000)]
    assert 430 < variantes.count("A") < 570


def test_ab_sem_amostra_nao_declara_vencedor():
    linhas = [{"variante": "A", "cliques": 10}] * 5 + [{"variante": "B", "cliques": 1}] * 5
    r = avaliar_ab({"id": 1, "tipo": "legenda"}, linhas, amostra_minima=30)
    assert r.vencedor is None and not r.conclusivo and "amostra mínima" in r.explicacao


def test_ab_diferenca_pequena_nao_e_conclusiva():
    linhas = [{"variante": "A", "cliques": 3}] * 40 + [{"variante": "B", "cliques": 3}] * 40
    r = avaliar_ab({"id": 1, "tipo": "legenda"}, linhas, amostra_minima=30)
    assert not r.conclusivo and r.p_valor > 0.05


def test_ab_vencedor_com_significancia():
    linhas = [{"variante": "A", "cliques": 2}] * 40 + [{"variante": "B", "cliques": 5}] * 40
    r = avaliar_ab({"id": 1, "tipo": "legenda"}, linhas, amostra_minima=30)
    assert r.conclusivo and r.vencedor == "B" and r.p_valor < 0.05


# ============================================================================ integração com o fluxo real
def test_enfileirar_grava_a_explicacao_de_cada_oferta():
    repo, pub_, _, cfg = cenario({})
    assert len(repo.t["selecoes"]) == 2
    s = repo.t["selecoes"][0]["detalhes"]
    assert s["resumo"] and s["fatores"] and s["sem_dados"]          # o que ficou sem dados é dito
    assert any(r["tipo"] == "aprendizado" for r in repo.t["resumos"])


def test_sem_vaga_e_registrado_como_motivo():
    repo = MemoriaRepositorio()
    repo.relogio = lambda: TARDE
    from test_auditoria import LojaReal, novo_log
    from app.servicos.busca import ServicoBusca
    from app.servicos.inteligencia import ServicoInteligencia
    from app.servicos.publicacao import ServicoPublicacao
    from app.integracoes.canais.demo import DemoCanal
    repo.definir_modo("marketplaces", "shopee", "real")
    repo.config.update({"busca.palavras_por_execucao": 99, "monitor.verificar_links": False, "publicacao.por_rodada": 1})
    cfg = repo.configuracoes()
    ServicoBusca(repo, {"shopee": LojaReal()}, novo_log()).executar(cfg)
    pub_ = ServicoPublicacao(repo, {c: DemoCanal(c) for c in ("telegram", "instagram_story", "instagram_feed")}, novo_log(),
                             url_supabase="https://abc.supabase.co", relogio=lambda: TARDE, http=cliente(SessaoFalsa(), tentativas=1))
    pub_.validar_links(cfg)
    ServicoInteligencia(repo, novo_log()).executar(cfg)
    pub_.enfileirar(cfg)
    recusadas = [s for s in repo.t["selecoes"] if not s["selecionada"]]
    assert len(recusadas) == 1 and "sem vaga" in recusadas[0]["detalhes"]["recusa"]


def test_falha_na_selecao_nao_impede_publicar():
    repo, pub_, _, cfg = cenario({})

    def quebra(*a, **k):
        raise RuntimeError("view ausente")
    pub_.selecao.ordenar = quebra
    for f in repo.t["fila"]:
        f["status"] = "concluida"
    for pr in repo.t["promocoes"]:
        pr["status"] = "aprovada"
    repo.t["historico"].clear()
    assert pub_.enfileirar(cfg) >= 1
    assert any("Seleção comercial indisponível" in l["mensagem"] for l in pub_.log.pendentes)


def test_ab_de_legenda_registra_variante_e_muda_a_legenda():
    from app.integracoes.canais.demo import DemoCanal
    repo, pub_, _, cfg = cenario({})
    repo.t["experimentos"].append({"id": 7, "nome": "Preço primeiro", "tipo": "legenda", "ativo": True, "amostra_minima": 30})
    pub_._experimento = False
    pub_.processar_fila(cfg)
    hist = [h for h in repo.t["historico"] if h["resultado"] == "sucesso"]
    assert hist and all(h["experimento_id"] == 7 and h["variante"] in ("A", "B") for h in hist)
    canal = pub_.canais["telegram"]
    for h, conteudo in zip([h for h in hist if repo._um("canais", id=h["canal_id"])["codigo"] == "telegram"], canal.publicados):
        primeira = [l for l in conteudo.legenda.splitlines() if l and "publi" not in l][0]
        assert primeira.startswith("💥 <b>Por") == (h["variante"] == "B")


def test_avaliacao_do_experimento_grava_resultado():
    repo, pub_, _, cfg = cenario({})
    repo.t["experimentos"].append({"id": 7, "nome": "Arte escura", "tipo": "arte", "ativo": True, "amostra_minima": 30})
    r = pub_.selecao.avaliar_experimento()
    assert r["conclusivo"] is False and repo.t["experimentos"][0]["resultado"]["explicacao"]


def test_comissao_importada_e_ligada_ao_post_e_entra_no_aprendizado():
    from test_auditoria_p0_v2 import CanalEspiao
    repo, pub_, _, cfg = cenario({"telegram": CanalEspiao()})   # post de demonstração não conta: canal real
    pub_.processar_fila(cfg)
    h = next(h for h in repo.t["historico"] if h["resultado"] == "sucesso")
    prod = repo._um("produtos", id=repo._um("promocoes", id=h["promocao_id"])["produto_id"])
    repo.registrar_clique_teste(h["id"])
    mkt = prod["marketplace_id"]
    repo.salvar_comissoes(mkt, [{"referencia_externa": "X-1", "valor_comissao": 6.0, "status": "pendente",
                                 "data_venda": TARDE.date().isoformat(), "id_externo_item": prod["id_externo"]}])
    c = repo.t["comissoes"][0]
    assert c["produto_id"] == prod["id"] and c["promocao_id"] == h["promocao_id"] and c["atribuicao"] == "oferta_publicada"
    linhas = repo.desempenho_publicacoes(TARDE - timedelta(days=1))
    minha = next(l for l in linhas if l["publicacao_id"] == h["id"])
    assert minha["cliques"] == 1 and minha["comissao_atribuida"] > 0


def test_canal_saturado_so_recebe_excelentes():
    repo, pub_, _, cfg = cenario({})
    pub_.selecao.canal_saturado = lambda cfg, canal: "telegram: SATURADO" if canal == "telegram" else None
    assert pub_._saturado({"id": 1, "classificacao": "boa"}, "telegram", cfg)
    assert not pub_._saturado({"id": 1, "classificacao": "excelente"}, "telegram", cfg)
    assert not pub_._saturado({"id": 1, "classificacao": "boa", "origem": "manual"}, "telegram", cfg)


# ============================================================================ integrações
def test_shopee_le_vendedor_e_refaz_sem_campos_se_a_api_recusar():
    from app.integracoes.fontes.shopee import ShopeeFonte
    nodes = {"data": {"productOfferV2": {"nodes": [{"itemId": 9, "productName": "Fone", "priceMin": "50", "offerLink":
             "https://s.shopee.com.br/x", "shopId": 77, "shopName": "Loja Oficial X", "shopType": [1]}]}}}
    s = ShopeeFonte("a", "b", http=cliente(SessaoFalsa().quando("POST", "graphql", RespostaFalsa(200, nodes))))
    o = s.buscar("fone")[0]
    assert o.loja_oficial is True and o.vendedor_id == "77" and o.vendedor_nome == "Loja Oficial X"
    chamadas = []

    def recusa(metodo, url, **kw):
        corpo = kw["data"].decode() if isinstance(kw.get("data"), bytes) else str(kw.get("data"))
        chamadas.append(corpo)
        if "shopType" in corpo:
            return RespostaFalsa(200, {"errors": [{"message": "Cannot query field \"shopType\""}]})
        return RespostaFalsa(200, {"data": {"productOfferV2": {"nodes": [{"itemId": 9, "productName": "Fone",
                                                                           "priceMin": "50", "offerLink": "https://s.shopee.com.br/x"}]}}})
    s = ShopeeFonte("a", "b", http=cliente(SessaoFalsa().quando("POST", "graphql", recusa)))
    o = s.buscar("fone")[0]
    assert o.loja_oficial is None and len(chamadas) == 2       # vendedor fica "sem dados", não presumido


def _repo(sessao):
    from app.repositorio import SupabaseRepositorio
    return SupabaseRepositorio("https://abc.supabase.co", "sb_secret_x", cliente(sessao, tentativas=1))


def test_supabase_historico_com_ancora_e_fallback_sem_019():
    sessao = SessaoFalsa().quando("POST", "rpc/historico_precos_janela", RespostaFalsa(200, [
        {"produto_id": 1, "preco": "100", "disponivel": True, "registrado_em": "2026-05-01T00:00:00+00:00", "ancora": True}]))
    h = _repo(sessao).historico_precos([1], AGORA - timedelta(days=90))
    assert h[1][0]["ancora"] is True
    sessao = (SessaoFalsa().quando("POST", "rpc/historico_precos_janela", RespostaFalsa(404, {"code": "PGRST202", "message": "x"}))
              .quando("GET", "historico_precos", RespostaFalsa(200, [{"produto_id": 1, "preco": "90", "disponivel": True,
                                                                     "registrado_em": "2026-11-20T00:00:00+00:00"}])))
    assert _repo(sessao).historico_precos([1], AGORA - timedelta(days=90))[1][0]["preco"] == 90


def test_supabase_sem_019_desempenho_vazio_e_experimento_nenhum():
    sessao = (SessaoFalsa().quando("GET", "vw_desempenho_publicacoes", RespostaFalsa(404, {"code": "PGRST205", "message": "x"}))
              .quando("GET", "experimentos", RespostaFalsa(404, {"code": "PGRST205", "message": "x"})))
    r = _repo(sessao)
    assert r.desempenho_publicacoes(AGORA) == [] and r.experimento_ativo() is None


def test_supabase_comissao_envia_codigo_do_item_e_tolera_banco_antigo():
    enviados = []

    def post(metodo, url, **kw):
        enviados.append([dict(x) for x in kw["json"]])
        if "id_externo_item" in kw["json"][0]:
            return RespostaFalsa(400, {"code": "PGRST204", "message": "column"})
        return RespostaFalsa(201, texto="")
    sessao = SessaoFalsa().quando("GET", "comissoes", RespostaFalsa(200, [])).quando("POST", "comissoes", post)
    _repo(sessao).salvar_comissoes(1, [{"referencia_externa": "A", "valor_comissao": 2, "data_venda": "2026-11-20",
                                        "id_externo_item": "999"}])
    assert enviados[0][0]["id_externo_item"] == "999" and "id_externo_item" not in enviados[1][0]
