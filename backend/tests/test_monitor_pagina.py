"""Monitor diário do preço na página do produto (Mercado Livre, sem API)."""
import io
from datetime import datetime, timedelta, timezone

from app.cliente_http import ClienteHTTP
from app.integracoes.fontes.manual import ManualFonte
from app.logs import Log
from app.modelos import OfertaEncontrada, ResultadoValidacao
from app.repositorio_memoria import MemoriaRepositorio
from app.servicos.monitor import ServicoMonitor
from app.servicos.pagina_produto import extrair_produto, numero_preco
from dubles import RespostaFalsa, SessaoFalsa

AGORA = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
PUBLICO = lambda host: ["200.147.67.142"]  # noqa: E731  (DNS falso: endereço público)


def pagina(preco="276,44", antigo="349 reais com 99 centavos", extra=""):
    return (f'<meta property="og:title" content="Tênis Jump Hook adidas 35 - R$ {preco}">'
            f'<meta property="og:image" content="https://http2.mlstatic.com/D_NQ_NP_638350-O.webp">'
            f'<s aria-label="Antes: {antigo}">x</s>{extra}')


def cenario(preco_publicado=276.44, verificado=None):
    repo = MemoriaRepositorio()
    ml = next(m for m in repo.marketplaces() if m["codigo"] == "mercado_livre")
    o = OfertaEncontrada("mercado_livre", "MLB4657663257", "Tênis", preco_publicado, "https://meli.la/2FFbrsZ", 349.99)
    pid, _ = repo.salvar_produto(o, ml["id"], None)
    promo = repo.criar_promocao(pid, o, "aprovada", "manual")
    link = repo.salvar_link(pid, promo, o.link_afiliado)
    repo.registrar_validacao(link, ResultadoValidacao(True, status_http=200))
    repo._um("promocoes", id=promo).update(status="publicada", publicada_em=AGORA - timedelta(days=1))
    repo._um("produtos", id=pid)["verificado_em"] = verificado or (AGORA - timedelta(hours=30))
    return repo, pid, promo, link


def monitor(repo, sessao):
    http = ClienteHTTP("monitor", sessao=sessao, tentativas=1, dormir=lambda s: None)
    return ServicoMonitor(repo, {"mercado_livre": ManualFonte()}, Log(saida=io.StringIO()), http,
                          resolver=PUBLICO, relogio=lambda: AGORA)


def redireciona_para_produto():
    return (SessaoFalsa()
            .quando("GET", "meli.la", RespostaFalsa(302, headers={"Location": "https://www.mercadolivre.com.br/p/MLB4657663257"}))
            .quando("GET", "mercadolivre.com.br", RespostaFalsa(200, texto=pagina("320,00"))))


def test_preco_subiu_na_pagina_pausa_a_oferta():
    repo, pid, promo, _ = cenario()
    monitor(repo, redireciona_para_produto()).executar(repo.configuracoes())
    assert repo._um("produtos", id=pid)["preco_atual"] == 320.0
    p = repo._um("promocoes", id=promo)
    assert p["status"] == "pausada" and "preço" in p["motivo_status"].lower()


def test_preco_igual_mantem_publicada_e_marca_a_conferencia():
    repo, pid, promo, _ = cenario()
    s = (SessaoFalsa().quando("GET", "meli.la", RespostaFalsa(302, headers={"Location": "https://www.mercadolivre.com.br/p/MLB1"}))
         .quando("GET", "mercadolivre.com.br", RespostaFalsa(200, texto=pagina("276,44"))))
    monitor(repo, s).executar(repo.configuracoes())
    assert repo._um("promocoes", id=promo)["status"] == "publicada"
    assert repo._um("produtos", id=pid)["preco_atual"] == 276.44


def test_so_le_a_pagina_uma_vez_por_dia():
    repo, pid, promo, _ = cenario(verificado=AGORA - timedelta(hours=3))  # intervalo padrão: 6 h
    s = redireciona_para_produto()
    monitor(repo, s).executar(repo.configuracoes())
    assert repo._um("produtos", id=pid)["preco_atual"] == 276.44          # não atualizou (conferido há 3 h, intervalo de 6 h)
    assert repo._um("promocoes", id=promo)["status"] == "publicada"


def test_redirecionamento_para_fora_da_loja_nao_e_seguido():
    repo, pid, promo, _ = cenario()  # _ = id do link
    s = (SessaoFalsa().quando("GET", "meli.la", RespostaFalsa(302, headers={"Location": "https://golpe.example.com/p"}))
         .quando("GET", "golpe.example.com", RespostaFalsa(200, texto=pagina("1,00"))))
    monitor(repo, s).executar(repo.configuracoes())
    assert not any("golpe.example.com" in c["url"] for c in s.chamadas)   # nem a leitura nem a conferência abrem
    assert repo._um("produtos", id=pid)["preco_atual"] == 276.44
    assert repo._um("links", id=_)["validado"] is False                     # link que sai da loja é invalidado


def test_bloqueio_de_robo_nao_muda_preco_e_cai_na_conferencia_do_link():
    repo, pid, promo, _ = cenario()
    s = SessaoFalsa().quando("GET", "meli.la", RespostaFalsa(200, texto="<html>captcha: verifique que você não é um robô</html>"))
    monitor(repo, s).executar(repo.configuracoes())
    assert repo._um("produtos", id=pid)["preco_atual"] == 276.44
    assert repo._um("promocoes", id=promo)["status"] == "publicada"


def test_esgotado_na_pagina_pausa():
    repo, pid, promo, _ = cenario()
    ld = ('<script type="application/ld+json">{"@type":"Product","name":"Tênis","offers":{"price":276.44,'
          '"availability":"https://schema.org/OutOfStock"}}</script>')
    s = SessaoFalsa().quando("GET", "meli.la", RespostaFalsa(200, texto=ld))
    monitor(repo, s).executar(repo.configuracoes())
    assert repo._um("promocoes", id=promo)["status"] == "pausada"


def test_desligado_na_configuracao_volta_a_so_conferir_o_link():
    repo, pid, promo, _ = cenario()
    s = redireciona_para_produto()
    monitor(repo, s).executar({**repo.configuracoes(), "monitor.ler_preco_pagina": False})
    assert repo._um("produtos", id=pid)["preco_atual"] == 276.44


def test_extracao_igual_ao_painel():
    d = extrair_produto(pagina(), "https://www.mercadolivre.com.br/p/MLB4657663257")
    assert d["titulo"] == "Tênis Jump Hook adidas 35" and d["preco"] == 276.44 and d["preco_antigo"] == 349.99
    assert d["id_externo"] == "MLB4657663257" and d["disponivel"] is True
    assert numero_preco("1.299") == 1299 and numero_preco("2.199,90") == 2199.9 and numero_preco(True) is None
