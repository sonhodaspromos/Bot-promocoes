"""Auditoria final · cenários de falha exigidos (e alguns achados extras da auditoria).

Cada teste reproduz uma falha real de produção usando dublês (nenhuma chamada à internet):
canal "real" falando HTTP com um servidor falso, loja "real" com API falsa, banco em memória
que imita as regras do Supabase.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import requests

from app.cliente_http import ClienteHTTP
from app.erros import ErroBanco, ErroIntegracao
from app.integracoes.base import REAL, CanalPublicacao, FonteOfertas, HospedagemImagem
from app.integracoes.canais.demo import DemoCanal
from app.integracoes.canais.instagram import InstagramCanal
from app.integracoes.canais.telegram import TelegramCanal
from app.integracoes.fontes.shopee import ShopeeFonte
from app.inteligencia import EntradaAnalise, analisar
from app.logs import Log
from app.modelos import OfertaEncontrada
from app.orquestrador import Orquestrador
from app.integracoes.registro import Integracoes
from app.repositorio_memoria import MemoriaRepositorio
from app.servicos.busca import ServicoBusca
from app.servicos.inteligencia import ServicoInteligencia
from app.servicos.monitor import ServicoMonitor
from app.servicos.publicacao import ServicoPublicacao
from app.servicos.validacao import validar_link
from dubles import RespostaFalsa, SessaoFalsa

TARDE = datetime(2026, 11, 22, 18, 0, tzinfo=timezone.utc)  # 15:00 em Recife
TOKEN_TG = "123456789:AAH-segredo_do_telegram_0123456789abcdef"
ML = ["mercadolivre.com"]
PADRAO_ML = r"^https://mercadolivre\.com/sec/[A-Za-z0-9]+"


def novo_log():
    return Log(saida=io.StringIO())


def cliente(sessao, servico="t", tentativas=3):
    return ClienteHTTP(servico, sessao=sessao, tentativas=tentativas, dormir=lambda s: None)


# ---------------------------------------------------------------- dublês de loja e canais "reais"
class LojaReal(FonteOfertas):
    """Imita a API oficial da Shopee já convertida em ofertas."""
    codigo, modo, suporta_consulta = "shopee", REAL, True

    def __init__(self):
        self.catalogo = {
            "111": OfertaEncontrada(marketplace="shopee", id_externo="111", titulo="Fone Bluetooth com cancelamento de ruído",
                                    preco=99.9, preco_original=249.9, link_afiliado="https://s.shopee.com.br/AbC111",
                                    nota=4.8, total_vendas=3200, comissao_pct=8.0),
            "222": OfertaEncontrada(marketplace="shopee", id_externo="222", titulo="Air Fryer 4 litros digital",
                                    preco=279.9, preco_original=599.9, link_afiliado="https://s.shopee.com.br/AbC222",
                                    nota=4.7, total_vendas=1500, comissao_pct=6.0),
        }

    def buscar(self, palavra, limite=30):
        return [o for o in self.catalogo.values()] if palavra == "fone bluetooth" else []

    def consultar(self, id_externo):
        o = self.catalogo.get(id_externo)
        return o if o and o.disponivel else None


class Hospedagem(HospedagemImagem):
    def __init__(self): self.enviadas = []
    def enviar(self, imagem): self.enviadas.append(imagem); return f"https://i.ibb.co/{len(self.enviadas)}.jpg"


def cenario(canais_reais: dict, relogio=TARDE):
    """Loja real + canais reais: busca, valida, analisa e enfileira. Devolve (repo, pub, loja, cfg)."""
    repo, loja = MemoriaRepositorio(), LojaReal()
    repo.relogio = lambda: relogio          # relógio fixo: o teste não depende do dia em que roda
    repo.definir_modo("marketplaces", "shopee", "real")
    for codigo in ("telegram", "instagram_story", "instagram_feed"):
        repo.definir_modo("canais", codigo, "real" if codigo in canais_reais else "demo")
    repo.config.update({"busca.palavras_por_execucao": 99, "monitor.verificar_links": False})
    cfg = repo.configuracoes()
    ServicoBusca(repo, {"shopee": loja}, novo_log()).executar(cfg)
    canais = {c: DemoCanal(c) for c in ("telegram", "instagram_story", "instagram_feed")}
    canais.update(canais_reais)
    pub = ServicoPublicacao(repo, canais, novo_log(), url_supabase="https://abc.supabase.co", relogio=lambda: relogio,
                            http=cliente(SessaoFalsa(), tentativas=1))
    pub.validar_links(cfg)
    ServicoInteligencia(repo, novo_log()).executar(cfg)
    assert pub.enfileirar(cfg) == 4, "2 ofertas × (telegram + story)"
    return repo, pub, loja, cfg


def fila(repo, codigo):
    canal = next(c for c in repo.t["canais"] if c["codigo"] == codigo)
    return [f for f in repo.t["fila"] if f["canal_id"] == canal["id"]]


def tg_ok(metodo, url, **kw):
    return RespostaFalsa(200, {"ok": True, "result": {"message_id": 77}})


# ================================================================ 1. produto sem preço
def test_produto_sem_preco_e_ignorado_sem_derrubar_a_busca():
    shopee = ShopeeFonte("app", "segredo", http=cliente(SessaoFalsa().quando("POST", "graphql", RespostaFalsa(200, {
        "data": {"productOfferV2": {"nodes": [
            {"itemId": 1, "productName": "Sem preço", "offerLink": "https://s.shopee.com.br/x"},
            {"itemId": 2, "productName": "Com preço", "priceMin": "49.90", "priceDiscountRate": 50,
             "offerLink": "https://s.shopee.com.br/y"}]}}}))))
    ofertas = shopee.buscar("fone")
    assert [o.id_externo for o in ofertas] == ["2"]          # o item sem preço nem vira oferta
    sem_preco = analisar(EntradaAnalise(titulo="Produto qualquer", preco=0, disponivel=True, link_situacao="validado",
                                        preco_referencia=100, origem_referencia="loja"))
    assert sem_preco.classificacao == "nao_recomendada" and sem_preco.pontuacao == 0
    assert any("preço" in b.lower() for b in sem_preco.bloqueios)
    repo = MemoriaRepositorio()
    loja = LojaReal()
    loja.catalogo["111"].preco = 0                            # API devolveu preço zerado
    repo.config["busca.palavras_por_execucao"] = 99
    _, novas = ServicoBusca(repo, {"shopee": loja}, novo_log()).executar(repo.configuracoes())
    assert novas == 1 and len(repo.t["promocoes"]) == 1      # só o produto com preço foi salvo


# ================================================================ 2. produto sem imagem
def test_produto_sem_imagem_publica_com_arte_gerada_e_avisa_na_analise():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", tg_ok)
    repo, pub, _, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    promo = repo.t["promocoes"][0]
    assert any("imagem" in a.lower() for a in promo["analise"]["alertas"] + [c["explicacao"] for c in promo["analise"]["componentes"]])
    assert promo["classificacao"] in ("excelente", "boa")    # sem imagem não bloqueia (exigir_imagem = falso)
    ok, falhas = pub.processar_fila(cfg)
    assert falhas == 0 and ok == 4
    envio = next(c for c in sessao.chamadas if "sendPhoto" in c["url"])
    assert envio["files"]["photo"][1][:2] == b"\xff\xd8"     # foi uma arte JPEG gerada pelo bot
    regra = analisar(EntradaAnalise(titulo="Produto sem foto", preco=50, disponivel=True, link_situacao="validado",
                                    preco_referencia=100, origem_referencia="loja"), __import__("app.inteligencia", fromlist=["Regras"]).Regras(exigir_imagem=True))
    assert regra.classificacao == "nao_recomendada"          # se o dono exigir imagem, bloqueia


# ================================================================ 3. link inválido
def test_link_invalido_nunca_entra_na_fila():
    sessao = SessaoFalsa().quando("GET", "AbC111", RespostaFalsa(404)).quando("GET", "AbC222", RespostaFalsa(200))
    r = validar_link("https://s.shopee.com.br/AbC111", ["s.shopee.com.br"], cliente(sessao, tentativas=1))
    assert not r.valido and "404" in r.motivo
    repo, loja = MemoriaRepositorio(), LojaReal()
    repo.definir_modo("marketplaces", "shopee", "real")
    repo.config["busca.palavras_por_execucao"] = 99
    cfg = repo.configuracoes()
    ServicoBusca(repo, {"shopee": loja}, novo_log()).executar(cfg)
    pub = ServicoPublicacao(repo, {c: DemoCanal(c) for c in ("telegram", "instagram_story")}, novo_log(),
                            url_supabase="https://abc.supabase.co", relogio=lambda: TARDE, http=cliente(sessao, tentativas=1))
    assert pub.validar_links(cfg) == (1, 1)
    ServicoInteligencia(repo, novo_log()).executar(cfg)
    pub.enfileirar(cfg)
    ids = {f["promocao_id"] for f in repo.t["fila"]}
    ruim = next(p for p in repo.t["promocoes"] if repo._um("produtos", id=p["produto_id"])["id_externo"] == "111")
    assert ruim["id"] not in ids and ruim["classificacao"] == "nao_recomendada"


# ================================================================ 4. link sem atribuição de afiliado
def test_link_no_dominio_da_loja_mas_sem_atribuicao_e_recusado():
    # domínio certo, mas não é um link de afiliado (sem /sec/ do programa de afiliados)
    assert not validar_link("https://www.mercadolivre.com/ofertas", ML, None, verificar_http=False, padrao=PADRAO_ML).valido
    assert not validar_link("https://mercadolivre.com/p/MLB123", ML, None, verificar_http=False, padrao=PADRAO_ML).valido
    assert validar_link("https://mercadolivre.com/sec/1a2B3c", ML, None, verificar_http=False, padrao=PADRAO_ML).valido


def test_link_que_redireciona_para_a_pagina_inicial_perdeu_a_atribuicao():
    sessao = (SessaoFalsa().quando("GET", "AbC999", RespostaFalsa(302, headers={"Location": "https://shopee.com.br/"}))
              .quando("GET", "shopee.com.br/", RespostaFalsa(200)))
    r = validar_link("https://s.shopee.com.br/AbC999", ["s.shopee.com.br"], cliente(sessao, tentativas=1))
    assert not r.valido and "página inicial" in r.motivo


def test_padrao_de_afiliado_do_marketplace_e_usado_na_validacao():
    repo = MemoriaRepositorio()
    ml = next(m for m in repo.t["marketplaces"] if m["codigo"] == "mercado_livre")
    assert ml.get("padrao_afiliado")
    repo.t["produtos"].append({"id": 900, "marketplace_id": ml["id"], "id_externo": "MLB1", "titulo": "Produto ML",
                               "preco": 50.0, "disponivel": True, "demo": False, "categoria_id": None})
    repo.t["promocoes"].append({"id": 901, "produto_id": 900, "status": "aprovada"})
    repo.salvar_link(900, None, "https://www.mercadolivre.com/qualquer-coisa")
    pub = ServicoPublicacao(repo, {}, novo_log(), url_supabase="https://abc.supabase.co")
    assert pub.validar_links({"monitor.verificar_links": False}) == (0, 1)


# ================================================================ 5. oferta duplicada
def test_oferta_duplicada_nao_gera_segunda_promocao_nem_segunda_publicacao():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", tg_ok)
    repo, pub, loja, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    ServicoBusca(repo, {"shopee": loja}, novo_log()).executar(cfg)    # a loja devolve as mesmas ofertas de novo
    assert len(repo.t["promocoes"]) == 2
    assert pub.enfileirar(cfg) == 0                                   # não enfileira de novo
    pub.processar_fila(cfg)
    assert sum("sendPhoto" in c["url"] for c in sessao.chamadas) == 2   # 1 post por oferta
    repo.t["promocoes"][0]["status"] = "aprovada"                     # mesmo que alguém reaprove
    repo.relogio = pub.relogio = lambda: TARDE + timedelta(hours=1)    # depois do intervalo entre rodadas
    assert pub.enfileirar(cfg) == 1                                   # só o story (demo); o Telegram respeita a janela de 24 h
    tg = next(c for c in repo.t["canais"] if c["codigo"] == "telegram")
    assert not any(f["canal_id"] == tg["id"] and f["status"] == "pendente" for f in repo.t["fila"])
    copia = analisar(EntradaAnalise(titulo="Fone Bluetooth com cancelamento de ruído", preco=99.9, disponivel=True,
                                    link_situacao="validado", preco_referencia=249.9, origem_referencia="loja", duplicada=True))
    assert copia.classificacao == "nao_recomendada" and any("duplicad" in b.lower() for b in copia.bloqueios)


# ================================================================ 6. preço alterado
def test_preco_alterado_pausa_cancela_fila_apaga_analise_e_encerra_a_mensagem():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", tg_ok).quando("POST", "editMessageCaption", tg_ok)
    repo, pub, loja, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    pub.processar_fila(cfg)
    loja.catalogo["111"].preco = 149.9                                   # +50%: acima da tolerância de 5%
    ServicoMonitor(repo, {"shopee": loja}, novo_log()).executar(cfg)
    promo = next(p for p in repo.t["promocoes"] if repo._um("produtos", id=p["produto_id"])["id_externo"] == "111")
    assert promo["status"] == "pausada" and "preço" in promo["motivo_status"].lower()
    assert promo["classificacao"] is None                                # análise apagada
    assert all(f["status"] not in ("pendente", "processando") for f in repo.t["fila"] if f["promocao_id"] == promo["id"])
    pub.encerrar_pausadas(TARDE - timedelta(hours=1))
    assert any("editMessageCaption" in c["url"] and "ENCERRADA" in c["data"]["caption"] for c in sessao.chamadas)


# ================================================================ 7. produto esgotado
def test_produto_esgotado_pausa_a_oferta():
    repo, pub, loja, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(SessaoFalsa().quando("POST", "sendPhoto", tg_ok)))})
    pub.processar_fila(cfg)
    loja.catalogo["222"].disponivel = False
    ServicoMonitor(repo, {"shopee": loja}, novo_log()).executar(cfg)
    promo = next(p for p in repo.t["promocoes"] if repo._um("produtos", id=p["produto_id"])["id_externo"] == "222")
    assert promo["status"] == "pausada"
    resultado = analisar(EntradaAnalise(titulo="Produto esgotado", preco=50, disponivel=False, link_situacao="validado",
                                        preco_referencia=100, origem_referencia="loja"))
    assert resultado.classificacao == "nao_recomendada"


# ================================================================ 8. API indisponível
def test_api_fora_do_ar_nao_trava_a_rodada():
    sessao = SessaoFalsa().quando("POST", "graphql", requests.ConnectionError("Name or service not known"))
    shopee = ShopeeFonte("app", "segredo", http=cliente(sessao, "shopee", tentativas=3))
    repo = MemoriaRepositorio()
    repo.definir_modo("marketplaces", "shopee", "real")
    repo.config["busca.palavras_por_execucao"] = 5
    log = novo_log()
    ServicoBusca(repo, {"shopee": shopee}, log).executar(repo.configuracoes())
    # 5 palavras × 3 tentativas = 15 chamadas de ~20 s cada sem disjuntor (estoura o limite de 9 min do job)
    assert len(sessao.chamadas) <= 6, f"{len(sessao.chamadas)} chamadas à API fora do ar"
    assert any("fora do ar" in l["mensagem"] or "fora do ar" in str(l["contexto"]) for l in log.pendentes)


def test_rodada_respeita_o_prazo_e_publica_mesmo_atrasada():
    repo, pub, loja, cfg = cenario({})
    tempos = iter([0] + [10_000] * 50)                # depois da 1ª leitura, o relógio já passou do prazo
    orq = Orquestrador(repo, Integracoes(fontes={"shopee": loja}, canais=pub.canais), novo_log(),
                       url_supabase="https://abc.supabase.co", publicacao=pub, cronometro=lambda: next(tempos))
    status, resumo = orq.executar()
    assert "busca" in resumo.etapas_puladas and resumo.publicadas == 4   # pulou o opcional, publicou a fila


# ================================================================ 9. token expirado
def test_token_do_telegram_expirado_pausa_o_canal_sem_perder_ofertas():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", RespostaFalsa(401, {"ok": False, "error_code": 401, "description": "Unauthorized"}))
    log = novo_log()
    repo, pub, _, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    pub.log = log
    ok, falhas = pub.processar_fila(cfg)
    assert ok == 2                                                   # os stories continuam saindo
    assert sum("sendPhoto" in c["url"] for c in sessao.chamadas) == 1  # 1 tentativa e o canal é pausado na rodada
    for f in fila(repo, "telegram"):
        assert f["status"] == "pendente" and f["tentativas"] == 0    # nada se perde: volta para a fila sem gastar tentativa
    assert log.contagem["CRITICO"] >= 1 and "telegram" in pub.canais_bloqueados
    assert TOKEN_TG not in log.saida.getvalue()


def test_token_do_instagram_expirado_codigo_190():
    erro190 = RespostaFalsa(400, {"error": {"message": "Error validating access token", "type": "OAuthException", "code": 190}})
    sessao = SessaoFalsa().quando("POST", "/media", erro190)
    ig = InstagramCanal("story", "IGAAtokenexpirado123", "999", Hospedagem(), http=cliente(sessao), dormir=lambda s: None)
    try:
        ig.publicar(type("C", (), {"imagem_story": b"jpg", "imagem": b"jpg", "legenda": "x"})())
        assert False, "deveria falhar"
    except ErroIntegracao as e:
        assert e.autenticacao and "IGAAtokenexpirado123" not in str(e)


# ================================================================ 10. erro no Telegram
def test_erro_permanente_do_telegram_vira_falha_com_nova_tentativa():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", RespostaFalsa(400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}))
    repo, pub, _, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    pub.processar_fila(cfg)
    itens = fila(repo, "telegram")
    assert all(f["status"] == "pendente" and f["tentativas"] == 1 for f in itens)
    assert any("chat not found" in (h["erro"] or "") for h in repo.t["historico"])


def test_timeout_no_envio_do_telegram_nao_repete_para_nao_duplicar_post():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", requests.ReadTimeout("read timed out"))
    repo, pub, _, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    pub.processar_fila(cfg)
    envios = sum("sendPhoto" in c["url"] for c in sessao.chamadas)
    itens = fila(repo, "telegram")
    assert envios == len(itens) == 2, f"{envios} envios para 2 ofertas: o bot repetiu um post que pode ter saído"
    assert all(f["status"] == "falhou" and "incerto" in f["ultimo_erro"] for f in itens)
    repo.relogio = lambda: TARDE + timedelta(hours=1)
    pub.processar_fila(cfg)                                          # rodadas seguintes também não repetem
    assert sum("sendPhoto" in c["url"] for c in sessao.chamadas) == 2


def test_limite_do_telegram_429_devolve_para_a_fila():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", RespostaFalsa(429, {"ok": False, "error_code": 429,
                                  "description": "Too Many Requests: retry after 30", "parameters": {"retry_after": 30}}))
    repo, pub, _, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao, tentativas=1))})
    pub.processar_fila(cfg)
    assert all(f["status"] == "pendente" and f["tentativas"] == 0 for f in fila(repo, "telegram"))


# ================================================================ 11. erro no Instagram
def _ig(sessao):
    return InstagramCanal("story", "IGAAtoken", "999", Hospedagem(), http=cliente(sessao), dormir=lambda s: None)


def test_instagram_nao_processou_a_imagem_registra_falha():
    sessao = (SessaoFalsa().quando("POST", "/media", RespostaFalsa(200, {"id": "c1"}))
              .quando("GET", "c1", RespostaFalsa(200, {"status_code": "ERROR"})))
    repo, pub, _, cfg = cenario({"instagram_story": _ig(sessao)})
    ok, falhas = pub.processar_fila(cfg)
    assert ok == 2 and falhas == 2
    assert all(f["status"] == "pendente" for f in fila(repo, "instagram_story"))


def test_timeout_no_media_publish_do_instagram_nao_repete():
    sessao = (SessaoFalsa().quando("POST", "media_publish", requests.ReadTimeout("read timed out"))
              .quando("POST", "/media", RespostaFalsa(200, {"id": "c1"}))
              .quando("GET", "c1", RespostaFalsa(200, {"status_code": "FINISHED"})))
    repo, pub, _, cfg = cenario({"instagram_story": _ig(sessao)})
    pub.processar_fila(cfg)
    itens = fila(repo, "instagram_story")
    assert sum("media_publish" in c["url"] for c in sessao.chamadas) == len(itens) == 2   # 1 envio por oferta
    assert all(f["status"] == "falhou" and "incerto" in f["ultimo_erro"] for f in itens)


def test_limite_de_publicacoes_do_instagram_devolve_para_a_fila():
    limite = RespostaFalsa(400, {"error": {"message": "Application request limit reached", "code": 9, "error_subcode": 2207042}})
    sessao = (SessaoFalsa().quando("POST", "media_publish", limite)
              .quando("POST", "/media", RespostaFalsa(200, {"id": "c1"}))
              .quando("GET", "c1", RespostaFalsa(200, {"status_code": "FINISHED"})))
    repo, pub, _, cfg = cenario({"instagram_story": _ig(sessao)})
    pub.processar_fila(cfg)
    assert all(f["status"] == "pendente" and f["tentativas"] == 0 for f in fila(repo, "instagram_story"))
    assert "instagram_story" in pub.canais_bloqueados


def test_cota_do_instagram_esgotada_nao_tenta_publicar():
    sessao = (SessaoFalsa().quando("GET", "content_publishing_limit", RespostaFalsa(200, {"data": [{"quota_usage": 49, "config": {"quota_total": 50}}]}))
              .quando("POST", "/media", RespostaFalsa(200, {"id": "c1"})))
    repo, pub, _, cfg = cenario({"instagram_story": _ig(sessao)})
    pub.processar_fila(cfg)
    assert not any("/media" in c["url"] for c in sessao.chamadas if c["metodo"] == "POST")
    assert all(f["status"] == "pendente" and f["tentativas"] == 0 for f in fila(repo, "instagram_story"))


def test_limite_diario_dos_stories_cabe_na_cota_da_api_do_instagram():
    repo = MemoriaRepositorio()
    story = next(c for c in repo.t["canais"] if c["codigo"] == "instagram_story")
    feed = next(c for c in repo.t["canais"] if c["codigo"] == "instagram_feed")
    assert story["limite_diario"] + feed["limite_diario"] <= 50    # cota oficial: 50 publicações / 24 h


# ================================================================ 12. queda do servidor
class BancoCaiDepoisDePublicar(MemoriaRepositorio):
    cair = True

    def registrar_publicacao(self, item, sucesso, id_mensagem, erro):
        if self.cair:
            raise ErroBanco("supabase: sem resposta (ConnectionError)")
        return super().registrar_publicacao(item, sucesso, id_mensagem, erro)


def test_queda_do_banco_logo_apos_publicar_nao_duplica_o_post():
    repo, pub, _, cfg = cenario({})
    repo.__class__ = BancoCaiDepoisDePublicar
    canal = pub.canais["telegram"]
    ok, falhas = pub.processar_fila(cfg)               # não pode estourar exceção
    publicados_antes = len(canal.publicados) + len(pub.canais["instagram_story"].publicados)
    assert publicados_antes == 1                       # parou de publicar ao perceber o banco fora
    repo.cair = False
    repo.relogio = lambda: TARDE + timedelta(minutes=30)   # trava de 10 min venceu
    pub.processar_fila(cfg)
    total = len(canal.publicados) + len(pub.canais["instagram_story"].publicados)
    assert total == 4                                  # os outros 3 saem; o incerto NÃO é repetido
    incertos = [f for f in repo.t["fila"] if f["status"] == "falhou"]
    assert len(incertos) == 1 and "incerto" in incertos[0]["ultimo_erro"]


def test_servidor_cai_antes_de_publicar_o_item_volta_sozinho():
    repo, pub, _, cfg = cenario({})
    repo.pegar_fila(10)                                # o job pegou os itens e morreu (sem publicar)
    assert pub.processar_fila(cfg) == (0, 0)           # trava de 10 min ainda vale: ninguém pega
    repo.relogio = lambda: TARDE + timedelta(minutes=11)
    assert pub.processar_fila(cfg)[0] == 4             # depois da trava, publica normalmente


def test_logs_sao_gravados_a_cada_etapa():
    repo, pub, loja, cfg = cenario({})
    vistos = {}

    class PublicacaoEspia(ServicoPublicacao):
        def processar_fila(self, cfg, limite=10):
            vistos["logs_antes_de_publicar"] = len(repo.t["logs"])
            return super().processar_fila(cfg, limite)

    espia = PublicacaoEspia(repo, pub.canais, pub.log, url_supabase="https://abc.supabase.co", relogio=lambda: TARDE)
    log = novo_log()
    espia.log = log
    Orquestrador(repo, Integracoes(fontes={"shopee": loja}, canais=pub.canais), log,
                 url_supabase="https://abc.supabase.co", publicacao=espia).executar()
    assert vistos["logs_antes_de_publicar"] > 0        # se o job morrer na publicação, o resto já está salvo


# ================================================================ 13. reinicialização
def test_reinicio_fecha_rodada_interrompida_e_retoma_a_fila():
    repo, pub, loja, cfg = cenario({})
    orfa = repo.iniciar_execucao("agendada", "real", "2.0")
    repo._um("execucoes", id=orfa)["iniciada_em"] = TARDE - timedelta(hours=2)
    # processo novo: objetos novos, mesmo banco
    novo = ServicoPublicacao(repo, pub.canais, novo_log(), url_supabase="https://abc.supabase.co", relogio=lambda: TARDE)
    status, resumo = Orquestrador(repo, Integracoes(fontes={"shopee": loja}, canais=pub.canais), novo_log(),
                                  url_supabase="https://abc.supabase.co", publicacao=novo).executar()
    e = repo._um("execucoes", id=orfa)
    assert e["status"] == "erro" and e["resumo"].get("interrompida")
    assert resumo.publicadas == 4 and len(repo.t["historico"]) == 4


# ================================================================ extras de segurança
def test_erro_de_rede_inesperado_nao_vaza_token_da_url():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", requests.exceptions.ChunkedEncodingError(
        f"Connection broken for https://api.telegram.org/bot{TOKEN_TG}/sendPhoto"))
    tg = TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))
    try:
        tg.publicar(type("C", (), {"link_rastreado": "https://x", "legenda": "x", "imagem": b"j"})())
        assert False
    except ErroIntegracao as e:
        assert TOKEN_TG not in str(e)


def test_codigos_de_erro_do_banco_conhecidos_pelo_backend():
    from app.erros import CODIGOS_REGRA
    for codigo in ("P0018", "P0019", "P0023", "P0024"):
        assert codigo in CODIGOS_REGRA


# ================================================================ implantação: backend novo com banco ainda sem a migration 015
def test_backend_funciona_antes_da_migration_015():
    from app.repositorio import SupabaseRepositorio

    def links(metodo, url, **kw):
        if "padrao_afiliado" in kw["params"]["select"]:
            return RespostaFalsa(400, {"code": "42703", "message": "column marketplaces_1.padrao_afiliado does not exist"})
        return RespostaFalsa(200, [{"id": 1, "url": "https://s.shopee.com.br/x", "produto_id": 2,
                                    "produtos": {"demo": False, "marketplaces": {"codigo": "shopee", "dominios_afiliado": ["s.shopee.com.br"]}}}])
    sessao = (SessaoFalsa().quando("GET", "links_afiliados", links)
              .quando("POST", "rpc/fila_reservar_envio", RespostaFalsa(404, {"code": "PGRST202", "message": "function not found"})))
    repo = SupabaseRepositorio("https://abc.supabase.co", "sb_secret_x", cliente(sessao, tentativas=1))
    assert repo.links_pendentes(5)[0]["padrao"] is None       # leitura continua funcionando sem as migrations novas
    log = novo_log()
    pub = ServicoPublicacao(repo, {}, log, url_supabase="https://abc.supabase.co")
    # (auditoria corretiva P0) antes: sem a RPC de proteção o bot só AVISAVA e enviava assim mesmo.
    # Agora: sem gravar a reserva, nada é enviado e a publicação para.
    import pytest
    from app.erros import ErroBanco
    with pytest.raises(ErroBanco):
        pub._reservar_envio(type("I", (), {"id": 1, "promocao_id": 9})(), None, "telegram")
    assert any("NADA foi enviado" in l["mensagem"] and l["nivel"] == "CRITICO" for l in log.pendentes)
