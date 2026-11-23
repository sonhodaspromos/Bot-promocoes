"""Etapa 4 · serviços: validação de link, busca, publicação, monitor, comissões, arte e textos."""
from datetime import datetime, timedelta, timezone

import pytest
import requests

from app.cliente_http import ClienteHTTP
from app.integracoes.base import REAL, CanalPublicacao, FonteOfertas
from app.integracoes.canais.demo import DemoCanal
from app.integracoes.fontes.demo import DemoFonte
from app.integracoes.fontes.manual import ManualFonte
from app.logs import Log
from app.modelos import OfertaEncontrada, ResultadoValidacao
from app.repositorio_memoria import MemoriaRepositorio
from app.servicos import arte, textos
from app.servicos.busca import ServicoBusca
from app.servicos.comissoes import ServicoComissoes
from app.servicos.monitor import ServicoMonitor
from app.servicos.inteligencia import ServicoInteligencia
from app.servicos.publicacao import ServicoPublicacao
from app.servicos.validacao import LinkTemporariamenteIndisponivel, validar_link
from dubles import RespostaFalsa, SessaoFalsa

import io

SHOPEE = ["s.shopee.com.br", "shope.ee"]
# 22/11/2026 15:00 em Recife (UTC-3)
TARDE = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)
NOITE = datetime(2026, 11, 22, 23, 30, tzinfo=timezone.utc)   # 20:30 local
MADRUGADA = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)  # 03:00 local


def log():
    return Log(saida=io.StringIO())


def http(sessao):
    return ClienteHTTP("t", sessao=sessao, tentativas=1, dormir=lambda s: None)


class CanalRealFalso(CanalPublicacao):
    modo = REAL
    def __init__(self, codigo): self.codigo, self.publicados, self.encerrados = codigo, [], []
    def publicar(self, c): self.publicados.append(c); return f"msg-{len(self.publicados)}"
    def encerrar(self, i, t, m): self.encerrados.append((i, m))


# ================================================================ validação
def test_link_precisa_de_https_e_dominio_de_afiliado():
    assert not validar_link("http://s.shopee.com.br/a", SHOPEE, None).valido
    assert not validar_link("https://shopee.com.br/produto", SHOPEE, None).valido
    assert not validar_link("https://s.shopee.com.br.golpe.com/a", SHOPEE, None).valido
    assert validar_link("https://br.s.shopee.com.br/a", SHOPEE, None, verificar_http=False).valido


def test_link_demo_so_vale_para_oferta_demo():
    assert validar_link("https://demo.invalid/p/1", ["demo.invalid"], None, demo=True).valido
    assert not validar_link("https://demo.invalid/p/1", ["demo.invalid"], None, demo=False).valido


def test_link_quebrado_bloqueado_e_bloqueio_antirrobo_aceito():
    s = SessaoFalsa().quando("GET", "/quebrado", RespostaFalsa(404)).quando("GET", "/robo", RespostaFalsa(403))
    r = validar_link("https://s.shopee.com.br/quebrado", SHOPEE, http(s))
    assert not r.valido and r.status_http == 404
    assert validar_link("https://s.shopee.com.br/robo", SHOPEE, http(s)).valido


def test_loja_fora_do_ar_nao_invalida_tenta_depois():
    s = SessaoFalsa().quando("GET", "s.shopee", requests.Timeout())
    with pytest.raises(LinkTemporariamenteIndisponivel):
        validar_link("https://s.shopee.com.br/a", SHOPEE, http(s))


# ================================================================ busca
def test_busca_demo_cria_produtos_promocoes_e_links_sem_duplicar():
    repo, fonte = MemoriaRepositorio(), DemoFonte()
    repo.config["busca.palavras_por_execucao"] = 99  # todas as palavras nas duas rodadas
    busca = ServicoBusca(repo, {"demo": fonte}, log())
    cfg = repo.configuracoes()
    _, novas = busca.executar(cfg)
    assert novas > 0 and len(repo.t["promocoes"]) == novas == len(repo.t["links"])
    assert all(p["demo"] for p in repo.t["produtos"]) and all(pr["status"] == "aprovada" for pr in repo.t["promocoes"])
    _, novas2 = busca.executar(cfg)
    assert novas2 == 0 and len(repo.t["promocoes"]) == novas  # sem duplicidade


def test_busca_respeita_filtros_e_aprovacao_manual():
    repo = MemoriaRepositorio()
    repo.config.update({"filtros.desconto_minimo": 95, "publicacao.aprovacao_manual": True})
    _, novas = ServicoBusca(repo, {"demo": DemoFonte()}, log()).executar(repo.configuracoes())
    assert novas == 0
    repo.config["filtros.desconto_minimo"] = 0
    ServicoBusca(repo, {"demo": DemoFonte()}, log()).executar(repo.configuracoes())
    assert repo.t["promocoes"] and all(pr["status"] == "encontrada" for pr in repo.t["promocoes"])


class FonteLinkComum(FonteOfertas):
    codigo, modo = "shopee", REAL
    def __init__(self, gera): self.gera = gera
    def buscar(self, palavra, limite=30):
        return [OfertaEncontrada("shopee", f"{palavra}-1", "Produto", 50, "https://shopee.com.br/produto-1", 100, 4.9)]
    def gerar_link_afiliado(self, url): return "https://s.shopee.com.br/curto1" if self.gera else None


def test_busca_converte_link_comum_em_afiliado_ou_descarta():
    repo = MemoriaRepositorio()
    ServicoBusca(repo, {"shopee": FonteLinkComum(gera=False)}, log()).executar(repo.configuracoes())
    assert repo.t["promocoes"] == [] and repo.t["links"] == []
    ServicoBusca(repo, {"shopee": FonteLinkComum(gera=True)}, log()).executar(repo.configuracoes())
    assert repo.t["links"] and all(l["url"] == "https://s.shopee.com.br/curto1" for l in repo.t["links"])


def test_busca_ignora_marketplace_desativado():
    repo = MemoriaRepositorio()
    repo.definir_modo("marketplaces", "demo", "desativado")
    assert ServicoBusca(repo, {"demo": DemoFonte()}, log()).executar(repo.configuracoes()) == (0, 0)


def test_produto_publicado_pode_voltar_depois_do_prazo():
    repo, fonte = MemoriaRepositorio(), DemoFonte()
    repo.config["busca.palavras_por_execucao"] = 99
    ServicoBusca(repo, {"demo": fonte}, log()).executar(repo.configuracoes())
    pr = repo.t["promocoes"][0]
    pr.update(status="publicada", publicada_em=TARDE - timedelta(days=40))
    ServicoBusca(repo, {"demo": fonte}, log(), relogio=lambda: TARDE).executar(repo.configuracoes())
    assert pr["status"] == "expirada"
    assert sum(1 for p in repo.t["promocoes"] if p["produto_id"] == pr["produto_id"]) == 2


# ================================================================ publicação
def preparar(canais=None, relogio=TARDE):
    repo, fonte = MemoriaRepositorio(), DemoFonte()
    repo.config["busca.palavras_por_execucao"] = 99  # determinístico
    ServicoBusca(repo, {"demo": fonte}, log()).executar(repo.configuracoes())
    canais = canais or {c: DemoCanal(c) for c in ("telegram", "instagram_story", "instagram_feed")}
    pub = ServicoPublicacao(repo, canais, log(), url_supabase="https://abc.supabase.co", relogio=lambda: relogio)
    return repo, fonte, canais, pub


def validar_e_analisar(pub, repo, cfg):
    pub.validar_links(cfg)
    return ServicoInteligencia(repo, log()).executar(cfg)


def test_valida_links_e_publica_em_demo():
    repo, _, canais, pub = preparar()
    cfg = repo.configuracoes()
    ok, rec = pub.validar_links(cfg)
    assert ok == len(repo.t["links"]) and rec == 0
    ServicoInteligencia(repo, log()).executar(cfg)
    n = pub.enfileirar(cfg)
    assert n == 2 * 2  # 2 ofertas por rodada x (telegram + story)
    publicados, falhas = pub.processar_fila(cfg)
    assert publicados == 4 and falhas == 0
    c = canais["telegram"].publicados[0]
    assert c.link_rastreado.startswith("https://abc.supabase.co/functions/v1/r?l=") and c.imagem[:2] == b"\xff\xd8"
    assert "DEMONSTRAÇÃO" in c.legenda and "#publi" in c.legenda
    assert canais["instagram_story"].publicados[0].imagem_story
    # publicação demo de oferta demo marca a promoção como publicada (só dentro do modo demo)
    assert sum(1 for p in repo.t["promocoes"] if p["status"] == "publicada") == 2


def test_sem_link_validado_nada_entra_na_fila():
    repo, _, _, pub = preparar()
    assert pub.enfileirar(repo.configuracoes()) == 0


def test_link_invalidado_depois_de_enfileirar_bloqueia_a_publicacao():
    repo, _, canais, pub = preparar()
    cfg = repo.configuracoes()
    validar_e_analisar(pub, repo, cfg)
    pub.enfileirar(cfg)
    for l in repo.t["links"]:
        l["validado"] = False  # simula invalidação sem passar pelas regras do banco
    ok, falhas = pub.processar_fila(cfg)
    assert ok == 0 and falhas > 0 and canais["telegram"].publicados == []
    assert all(h["resultado"] == "erro" and "bloqueada" in h["erro"] for h in repo.t["historico"])


def test_oferta_demo_nunca_vai_para_canal_real():
    reais = {c: CanalRealFalso(c) for c in ("telegram", "instagram_story", "instagram_feed")}
    repo, _, canais, pub = preparar(canais=reais)
    for c in repo.t["canais"]:
        c["modo"] = "real"
    cfg = repo.configuracoes()
    validar_e_analisar(pub, repo, cfg)
    assert pub.enfileirar(cfg) == 0 and all(not c.publicados for c in reais.values())


def test_fora_do_horario_so_sai_oferta_manual():
    repo, _, _, pub = preparar(relogio=MADRUGADA)
    repo.config.update({"publicacao.horario_inicio": 7, "publicacao.horario_fim": 23})
    cfg = repo.configuracoes()
    validar_e_analisar(pub, repo, cfg)
    assert pub.enfileirar(cfg) == 0
    repo.t["promocoes"][0]["origem"] = "manual"
    assert pub.enfileirar(cfg) == 2


def test_limite_diario_e_intervalo_minimo():
    repo, _, _, pub = preparar()
    cfg = repo.configuracoes()
    validar_e_analisar(pub, repo, cfg)
    pub.enfileirar(cfg); pub.processar_fila(cfg)
    for h in repo.t["historico"]:
        h.update(demo=False, publicado_em=TARDE)  # publicações reais, agora
    assert pub.enfileirar(cfg) == 0  # intervalo mínimo de 10 min
    for h in repo.t["historico"]:
        h["publicado_em"] -= timedelta(minutes=30)
    assert pub.enfileirar({**cfg, "publicacao.max_por_dia": 2}) == 0  # já publicou 2 hoje
    assert pub.enfileirar({**cfg, "publicacao.max_por_dia": 3}) == 2  # cabe mais 1 oferta (telegram + story)


def test_post_do_dia_no_feed_uma_vez_so():
    repo, _, canais, pub = preparar(relogio=NOITE)
    cfg = repo.configuracoes()
    validar_e_analisar(pub, repo, cfg); pub.enfileirar(cfg); pub.processar_fila(cfg)
    assert len(canais["instagram_feed"].publicados) == 0  # feed só depois que existir oferta publicada
    pub.enfileirar(cfg); pub.processar_fila(cfg)
    feed = canais["instagram_feed"].publicados
    assert len(feed) == 1 and feed[0].destaque and "IMPERDÍVEL" in feed[0].legenda
    for h in repo.t["historico"]:
        h["demo"] = False
    pub.enfileirar(cfg); pub.processar_fila(cfg)
    assert len(canais["instagram_feed"].publicados) == 1


# ================================================================ monitor
def test_monitor_pausa_quando_preco_sobe_ou_esgota_e_encerra_mensagem():
    reais = {c: CanalRealFalso(c) for c in ("telegram", "instagram_story", "instagram_feed")}
    repo, fonte, _, pub = preparar(canais=reais)
    for p in repo.t["produtos"] + repo.t["promocoes"]:
        p["demo"] = False
    for c in repo.t["canais"]:
        c["modo"] = "real"
    cfg = repo.configuracoes()
    inicio = datetime.now(timezone.utc)
    validar_e_analisar(pub, repo, cfg)
    for l in repo.t["links"]:
        l.update(validado=True, status_http=200, motivo_invalido=None)
    for pr in repo.t["promocoes"]:
        repo._invalidar_analise(pr["produto_id"])
    ServicoInteligencia(repo, log()).executar(cfg)
    pub.enfileirar(cfg); pub.processar_fila(cfg)
    publicadas = [p for p in repo.t["promocoes"] if p["status"] == "publicada"]
    assert len(publicadas) == 2
    a, b = (repo._um("produtos", id=p["produto_id"]) for p in publicadas)
    fonte.simular_preco(a["id_externo"], a["preco_atual"] * 2)
    fonte.simular_esgotado(b["id_externo"])
    ServicoMonitor(repo, {"demo": fonte}, log()).executar(cfg)
    assert [p["status"] for p in publicadas] == ["pausada", "pausada"]
    assert "Preço subiu" in publicadas[0]["motivo_status"] and publicadas[1]["motivo_status"] == "Produto indisponível"
    assert pub.encerrar_pausadas(inicio) >= 2 and len(reais["telegram"].encerrados) == 2


def test_monitor_sem_api_invalida_link_quebrado():
    repo = MemoriaRepositorio()
    ml = next(m for m in repo.marketplaces() if m["codigo"] == "mercado_livre")
    o = OfertaEncontrada("mercado_livre", "MLB1", "Air Fryer", 200, "https://mercadolivre.com/sec/abc", 300)
    pid, _ = repo.salvar_produto(o, ml["id"], None)
    promo = repo.criar_promocao(pid, o, "aprovada", "manual")
    link = repo.salvar_link(pid, promo, o.link_afiliado)
    repo.registrar_validacao(link, ResultadoValidacao(True, status_http=200))
    repo._um("promocoes", id=promo).update(status="publicada", publicada_em=TARDE)
    s = SessaoFalsa().quando("GET", "mercadolivre.com", RespostaFalsa(404))
    ServicoMonitor(repo, {"mercado_livre": ManualFonte()}, log(), http(s)).executar(repo.configuracoes())
    assert repo._um("links", id=link)["validado"] is False
    assert repo._um("promocoes", id=promo)["status"] == "pausada"


# ================================================================ comissões
class FonteComissoes(FonteOfertas):
    codigo, modo, suporta_comissoes = "shopee", REAL, True
    def buscar(self, *a, **k): return []
    def comissoes(self, i, f):
        return [{"referencia_externa": "1-A-9", "id_externo": "9", "valor_comissao": 4.5, "data_venda": "2026-11-21", "status": "confirmada"}]


def test_comissoes_so_na_janela_das_6h_ou_forcado():
    repo = MemoriaRepositorio()
    seis = datetime(2026, 11, 22, 9, 5, tzinfo=timezone.utc)  # 06:05 local
    assert ServicoComissoes(repo, {"shopee": FonteComissoes()}, log(), relogio=lambda: TARDE).executar(repo.configuracoes()) == 0
    assert ServicoComissoes(repo, {"shopee": FonteComissoes()}, log(), relogio=lambda: seis).executar(repo.configuracoes()) == 1
    ServicoComissoes(repo, {"shopee": FonteComissoes()}, log()).executar(repo.configuracoes(), forcar=True)
    assert len(repo.t["comissoes"]) == 1  # repetida ignorada


def test_comissoes_ignora_fonte_demo():
    repo = MemoriaRepositorio()
    assert ServicoComissoes(repo, {"demo": DemoFonte()}, log()).executar(repo.configuracoes(), forcar=True) == 0


# ================================================================ arte e textos
DADOS = {"titulo": "Fone <b>Bluetooth</b> com cancelamento de ruído e estojo", "preco_promocional": 59.9,
         "preco_referencia": 149.9, "desconto_pct": 60, "marketplace_nome": "Shopee", "demo": True}


def test_arte_gera_jpeg_nos_dois_formatos():
    from PIL import Image
    cfg = MemoriaRepositorio().configuracoes()
    for largura, altura in ((1080, 1350), (1080, 1920)):
        img = Image.open(io.BytesIO(arte.criar_arte(DADOS, arte.foto_padrao(), largura, altura, cfg)))
        assert img.size == (largura, altura) and img.format == "JPEG"


def test_textos_identificam_publicidade_demo_e_escapam_html():
    cfg = MemoriaRepositorio().configuracoes()
    t = textos.legenda_telegram(DADOS, "https://x/r?l=1", cfg)
    assert "&lt;b&gt;Bluetooth" in t and "#publi" in t and "DEMONSTRAÇÃO" in t and "comissão" in t
    ig = textos.legenda_instagram({**DADOS, "demo": False}, cfg, destaque=True)
    assert "IMPERDÍVEL" in ig and "#publi" in ig and "DEMONSTRAÇÃO" not in ig
