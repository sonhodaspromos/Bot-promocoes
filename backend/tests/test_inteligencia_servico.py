"""Motor de inteligência integrado ao bot: coleta de dados reais, gravação, fila e prioridade."""
import io
from datetime import timedelta

from PIL import Image

from app.cliente_http import ClienteHTTP
from app.integracoes.canais.demo import DemoCanal
from app.integracoes.fontes.demo import DemoFonte
from app.logs import Log
from app.modelos import OfertaEncontrada, ResultadoValidacao
from app.repositorio import agora
from app.repositorio_memoria import MemoriaRepositorio
from app.servicos.busca import ServicoBusca
from app.servicos.inteligencia import ServicoInteligencia, normalizar_titulo, situacao_do_link
from app.servicos.publicacao import ServicoPublicacao
from dubles import RespostaFalsa, SessaoFalsa


def log():
    return Log(saida=io.StringIO())


def png(largura, altura):
    buf = io.BytesIO()
    Image.new("RGB", (largura, altura), "red").save(buf, "PNG")
    return buf.getvalue()


def http_imagens(mapa):
    s = SessaoFalsa()
    for trecho, (w, h) in mapa.items():
        s.quando("GET", trecho, RespostaFalsa(200, texto=""))
        s.regras[-1] = ("GET", trecho, _resp_bin(png(w, h)))
    return ClienteHTTP("img", sessao=s, tentativas=1, dormir=lambda x: None)


def _resp_bin(conteudo):
    return RespostaFalsa(200, conteudo=conteudo, headers={"Content-Type": "image/png"})


def oferta(id_externo="1", titulo="Fone de Ouvido Bluetooth com Estojo de Carga", preco=60.0, original=120.0, **kw):
    return OfertaEncontrada("shopee", id_externo, titulo, preco, f"https://s.shopee.com.br/{id_externo}", original,
                            kw.pop("nota", 4.8), kw.pop("vendas", 1500), imagem_url=kw.pop("imagem", None),
                            comissao_pct=kw.pop("comissao", 8.0), **kw)


def repo_real():
    """Repositório com a Shopee em modo REAL (dados da loja confiáveis)."""
    r = MemoriaRepositorio()
    r.definir_modo("marketplaces", "shopee", "real")
    return r


def cadastrar(r, o, validar=True, categoria=True):
    mkt = next(m for m in r.marketplaces() if m["codigo"] == o.marketplace)
    cat = r.categorias()[0]["id"] if categoria else None
    pid, _ = r.salvar_produto(o, mkt["id"], cat)
    promo = r.criar_promocao(pid, o, "aprovada", "automatica")
    link = r.salvar_link(pid, promo, o.link_afiliado)
    if validar:
        r.registrar_validacao(link, ResultadoValidacao(True, status_http=200))
    return pid, promo, link


def promo(r, pid):
    return r._um("promocoes", id=pid)


# ------------------------------------------------------------------ análise gravada
def test_analisa_grava_pontuacao_classificacao_e_explicacao():
    r = repo_real()
    _, p, _ = cadastrar(r, oferta())
    c = ServicoInteligencia(r, log()).executar(r.configuracoes())
    pr = promo(r, p)
    assert c["analisadas"] == 1 and pr["classificacao"] in ("excelente", "boa") and 0 <= pr["pontuacao"] <= 100
    assert pr["analise"]["resumo"] and pr["analise"]["componentes"] and pr["analisada_em"]
    assert r.promocoes_para_analisar(10) == []  # já analisada


def test_dados_da_loja_so_sao_confiaveis_com_api_real():
    r = MemoriaRepositorio()  # shopee em DEMO
    _, p, _ = cadastrar(r, oferta())
    ServicoInteligencia(r, log()).executar(r.configuracoes())
    comps = {c["nome"]: c for c in promo(r, p)["analise"]["componentes"]}
    assert comps["comissão"]["nota"] is None and comps["popularidade"]["nota"] is None


def test_sem_link_ou_link_invalido_nao_e_recomendada():
    r = repo_real()
    _, p, link = cadastrar(r, oferta(), validar=False)
    r.registrar_validacao(link, ResultadoValidacao(False, "link quebrado (HTTP 404)", 404))
    ServicoInteligencia(r, log()).executar(r.configuracoes())
    pr = promo(r, p)
    assert pr["classificacao"] == "nao_recomendada" and "link quebrado" in pr["analise"]["resumo"]
    assert situacao_do_link(None) == ("invalido", "oferta sem link de afiliado")
    assert situacao_do_link({"validado": False, "status_http": None, "motivo_invalido": None})[0] == "pendente"


def test_produto_indisponivel_nao_e_recomendado():
    r = repo_real()
    pid, p, _ = cadastrar(r, oferta())
    r.atualizar_produto(pid, 60.0, False, None)
    r._um("promocoes", id=p)["status"] = "aprovada"  # (o banco pausaria; aqui forçamos para testar o motor)
    ServicoInteligencia(r, log()).executar(r.configuracoes())
    assert promo(r, p)["classificacao"] == "nao_recomendada" and "indisponível" in promo(r, p)["analise"]["resumo"]


def test_duplicidade_entre_produtos_com_mesmo_titulo():
    r = repo_real()
    _, p1, _ = cadastrar(r, oferta("1", titulo="Air Fryer Mondial 4 Litros Preta"))
    _, p2, _ = cadastrar(r, oferta("2", titulo="AIR FRYER Mondial - 4 litros, preta!"))
    ServicoInteligencia(r, log()).executar(r.configuracoes())
    assert promo(r, p1)["classificacao"] != "nao_recomendada"
    assert promo(r, p2)["classificacao"] == "nao_recomendada" and "duplicada" in promo(r, p2)["analise"]["resumo"]
    assert normalizar_titulo("[DEMO] Fritadeira Elétrica — 4L!") == "fritadeira eletrica 4l"


def test_mede_a_imagem_de_verdade_e_nao_inventa_quando_falha():
    r = repo_real()
    _, p1, _ = cadastrar(r, oferta("1", imagem="https://cf.shopee.com.br/grande.jpg"))
    _, p2, _ = cadastrar(r, oferta("2", titulo="Smartwatch Esportivo à Prova d'Água", imagem="https://cf.shopee.com.br/quebrada.jpg"))
    servico = ServicoInteligencia(r, log(), http=http_imagens({"grande": (900, 900)}))
    servico.executar(r.configuracoes())
    q1 = next(c for c in promo(r, p1)["analise"]["componentes"] if c["nome"].startswith("qualidade"))
    q2 = next(c for c in promo(r, p2)["analise"]["componentes"] if c["nome"].startswith("qualidade"))
    assert "900×900" in q1["explicacao"] and "não verificado" in q2["explicacao"]


def test_mudanca_de_preco_exige_nova_analise_com_historico():
    r = repo_real()
    pid, p, _ = cadastrar(r, oferta(preco=90, original=120))
    ServicoInteligencia(r, log()).executar(r.configuracoes())
    r.atualizar_produto(pid, 60.0, True, None)  # preço caiu
    assert promo(r, p)["classificacao"] is None and r.promocoes_para_analisar(10)


# ------------------------------------------------------------------ fila e prioridade
def preparar_publicacao(r):
    canais = {c: DemoCanal(c) for c in ("telegram", "instagram_story", "instagram_feed")}
    return ServicoPublicacao(r, canais, log(), url_supabase="https://abc.supabase.co"), canais


def test_nao_recomendada_nao_entra_na_fila():
    r = repo_real()
    _, p, _ = cadastrar(r, oferta(preco=110, original=120))  # 8% de desconto
    ServicoInteligencia(r, log()).executar(r.configuracoes())
    pub, _ = preparar_publicacao(r)
    assert promo(r, p)["classificacao"] == "nao_recomendada" and pub.enfileirar(r.configuracoes()) == 0


def test_fila_prioriza_maior_pontuacao():
    r = repo_real()
    r.config["publicacao.por_rodada"] = 1
    _, fraca, _ = cadastrar(r, oferta("1", titulo="Garrafa Térmica Inox 500ml Simples", preco=90, original=120, comissao=1, nota=4.0, vendas=5))
    _, forte, _ = cadastrar(r, oferta("2", titulo="Fone Bluetooth com Cancelamento de Ruído", preco=50, original=120, comissao=10))
    ServicoInteligencia(r, log()).executar(r.configuracoes())
    assert promo(r, forte)["pontuacao"] > promo(r, fraca)["pontuacao"]
    pub, _ = preparar_publicacao(r)
    pub.enfileirar(r.configuracoes())
    assert {f["promocao_id"] for f in r.t["fila"]} == {forte}


def test_nao_repete_a_mesma_oferta_no_mesmo_canal_em_24h():
    r = repo_real()
    pid, p, link = cadastrar(r, oferta())
    ServicoInteligencia(r, log()).executar(r.configuracoes())
    pub, canais = preparar_publicacao(r)
    cfg = r.configuracoes()
    pub.enfileirar(cfg); pub.processar_fila(cfg)
    for h in r.t["historico"]:
        h["demo"] = False  # publicação real
    # a oferta acaba, mas o mesmo produto volta com promoção nova logo depois
    r.expirar_promocao(p, "teste")
    novo = r.criar_promocao(pid, oferta(), "aprovada", "automatica")
    r.salvar_analise(novo, 90, "excelente", 90, {"resumo": "teste"})
    cfg["publicacao.intervalo_minutos"] = 0.0001
    assert pub.enfileirar(cfg) == 0
    for h in r.t["historico"]:
        h["publicado_em"] = agora() - timedelta(hours=25)
    assert pub.enfileirar(cfg) == 2  # depois de 24 h pode voltar (telegram + story)


def test_janela_de_repeticao_configuravel():
    r = repo_real()
    pid, p, _ = cadastrar(r, oferta())
    r.salvar_analise(p, 90, "excelente", 90, {"resumo": "teste"})
    canal = r.canais()[0]["id"]
    r.t["historico"].append({"id": 999, "fila_id": None, "promocao_id": p, "canal_id": canal, "resultado": "sucesso",
                             "id_mensagem_externa": "1", "erro": None, "demo": False,
                             "publicado_em": agora() - timedelta(hours=5)})
    assert r.publicado_no_canal_recentemente(pid, canal, 24) and not r.publicado_no_canal_recentemente(pid, canal, 4)


def test_publicacao_bloqueia_oferta_que_perdeu_a_aprovacao():
    r = repo_real()
    pid, p, _ = cadastrar(r, oferta())
    ServicoInteligencia(r, log()).executar(r.configuracoes())
    pub, canais = preparar_publicacao(r)
    pub.enfileirar(r.configuracoes())
    promo(r, p).update(classificacao="nao_recomendada")  # reanalisada depois de entrar na fila
    ok, falhas = pub.processar_fila(r.configuracoes())
    assert ok == 0 and falhas > 0 and "motor de inteligência" in r.t["historico"][0]["erro"]


def test_rodada_completa_demo_com_motor():
    from app.integracoes.registro import Integracoes
    from app.orquestrador import Orquestrador
    r = MemoriaRepositorio()
    r.config["busca.palavras_por_execucao"] = 99
    integ = Integracoes(fontes={"demo": DemoFonte()}, canais={c: DemoCanal(c) for c in ("telegram", "instagram_story", "instagram_feed")})
    status, resumo = Orquestrador(r, integ, log(), url_supabase="https://abc.supabase.co").executar()
    assert status == "sucesso" and resumo.analisadas == resumo.novas_promocoes > 0
    assert resumo.excelentes + resumo.boas + resumo.nao_recomendadas == resumo.analisadas
    publicadas = {c.promocao_id for c in integ.canais["telegram"].publicados if c.promocao_id}  # 0 = post de cupom
    assert all(promo(r, i)["classificacao"] in ("excelente", "boa") for i in publicadas)
