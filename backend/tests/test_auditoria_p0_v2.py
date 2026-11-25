"""Auditoria corretiva P0 (V2) · um teste de regressão para cada problema encontrado.

P1  validar-integracoes era incompatível com homologação (canais trocados por simulados → reprovava tudo)
P2  não havia modo de validação real SOMENTE LEITURA (nada impedia uma chamada de escrita)
P3  fonte manual do Mercado Livre reprovava a validação das integrações automáticas
P4  validação podia "aprovar" sem nenhuma integração obrigatória verificada
P5  _marcar_publicando() era permissivo: sem gravar o estado, o post saía assim mesmo
P6  cupons: nenhum estado gravado antes do envio; falha ao gravar o resultado levava a reenvio
P7  falha de comunicação DEPOIS do envio (5xx, erro inesperado, banco) podia gerar republicação
P8  não havia verificação de migrations/RPCs antes de publicar de verdade
P9  atribuição do link: ID por substring, página inicial com parâmetro, link curto não validado,
    bloqueio de robô no encurtador aceito como prova, comprovação sem validade
"""
from __future__ import annotations

import io
import json
from datetime import timedelta

import pytest

from app.cliente_http import ClienteHTTP, ClienteHTTPSomenteLeitura, chamada_de_leitura
from app.config import Config
from app.ambiente import Ambiente
from app.erros import ErroBanco, ErroConfiguracao, ErroIntegracao, EscritaBloqueada
from app.integracoes import registro
from app.integracoes.base import REAL, CanalPublicacao
from app.integracoes.canais.demo import DemoCanal
from app.integracoes.canais.telegram import TelegramCanal
from app.integracoes.fontes.manual import ManualFonte
from app.logs import Log
from app.modelos import ConteudoPublicacao, ResultadoValidacao
from app.repositorio_memoria import MemoriaRepositorio
from app.requisitos import faltando_para_publicar
from app.servicos.cupons import ServicoCupons
from app.servicos.validacao import contem_id, validar_link
from app.validacao_integracoes import validar
from dubles import RespostaFalsa, SessaoFalsa
from test_auditoria import TARDE, TOKEN_TG, cenario, cliente, fila, novo_log, tg_ok


def cfg_com(**credenciais) -> Config:
    return Config(supabase_url="https://x.supabase.co", supabase_chave="sb_secret_x", credenciais=credenciais)


CRED_TG = {"telegram": {"TOKEN": TOKEN_TG, "CANAL": "@canal"}}
CRED_SHOPEE = {"shopee": {"APP_ID": "app", "SECRET": "segredo"}}


class CanalEspiao(CanalPublicacao):
    """Canal 'real' que conta envios e pode falhar de vários jeitos."""
    modo = REAL

    def __init__(self, codigo="telegram", falha=None):
        self.codigo, self.falha, self.envios = codigo, falha, 0

    def publicar(self, conteudo: ConteudoPublicacao) -> str:
        self.envios += 1
        if self.falha:
            raise self.falha
        return f"msg-{self.envios}"


# ============================================================================ P1 · homologação × validar
def test_p1_validacao_em_homologacao_monta_cliente_real_e_execucao_continua_simulada():
    amb = Ambiente("homologacao", ["telegram"])
    alvos = {a.nome: a for a in registro.montar_validacao(cfg_com(**CRED_TG), amb)}
    assert isinstance(alvos["telegram"].cliente, TelegramCanal) and alvos["telegram"].categoria == "real"
    # a RODADA normal de homologação continua sem canal real (nunca publica)
    integ = registro.montar(cfg_com(**CRED_TG), ambiente=amb)
    assert isinstance(integ.canais["telegram"], DemoCanal)


def test_p1_validacao_recusa_ambiente_de_demonstracao():
    with pytest.raises(ErroConfiguracao, match="REAIS"):
        registro.montar_validacao(cfg_com(**CRED_TG), Ambiente("demonstracao", []))


def test_p1_homologacao_valida_telegram_real_e_aprova_com_evidencia(tmp_path):
    sessao = (SessaoFalsa()
              .quando("POST", "getMe", RespostaFalsa(200, {"ok": True, "result": {"id": 7, "username": "bot"}}))
              .quando("POST", "getChatMember", RespostaFalsa(200, {"ok": True, "result": {
                  "status": "administrator", "can_post_messages": True, "can_edit_messages": True}}))
              .quando("POST", "getChat", RespostaFalsa(200, {"ok": True, "result": {"title": "Achadinhos"}})))
    canal = TelegramCanal(TOKEN_TG, "@canal", http=ClienteHTTPSomenteLeitura("telegram", sessao=sessao, tentativas=1))
    alvos = [registro.AlvoValidacao("telegram", "real", True, canal),
             registro.AlvoValidacao("mercado_livre", "manual", False, ManualFonte(), "manual")]
    saida = tmp_path / "v.json"
    assert validar(alvos, MemoriaRepositorio(), Log(saida=io.StringIO()), saida=saida, ambiente="homologacao") == 0
    ev = json.loads(saida.read_text())
    assert ev["aprovado"] and ev["validadas_de_verdade"] == ["telegram", "esquema_banco"]
    assert TOKEN_TG not in saida.read_text()


# ============================================================================ P2 · somente leitura
@pytest.mark.parametrize("metodo,url,kw,leitura", [
    ("POST", "https://api.telegram.org/botX/getMe", {}, True),
    ("POST", "https://api.telegram.org/botX/getChatMember", {}, True),
    ("POST", "https://api.telegram.org/botX/sendPhoto", {}, False),
    ("POST", "https://api.telegram.org/botX/sendMessage", {}, False),
    ("POST", "https://api.telegram.org/botX/editMessageCaption", {}, False),
    ("GET", "https://graph.instagram.com/v21.0/me", {}, True),
    ("POST", "https://graph.instagram.com/v21.0/1/media", {}, False),
    ("POST", "https://graph.instagram.com/v21.0/1/media_publish", {}, False),
    ("POST", "https://open-api.affiliate.shopee.com.br/graphql", {"data": b'{"query":"{productOfferV2{nodes{itemId}}}"}'}, True),
    ("POST", "https://open-api.affiliate.shopee.com.br/graphql", {"data": b'{"query":"mutation{generateShortLink}"}'}, False),
    ("POST", "https://api.imgbb.com/1/upload", {}, False),
    ("DELETE", "https://graph.instagram.com/v21.0/1", {}, False),
])
def test_p2_classificacao_de_chamadas_de_leitura(metodo, url, kw, leitura):
    assert chamada_de_leitura(metodo, url, kw) is leitura


def test_p2_canal_de_validacao_nao_consegue_publicar_e_nada_sai_do_bot():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", tg_ok)
    alvos = {a.nome: a for a in registro.montar_validacao(cfg_com(**CRED_TG), Ambiente("producao", ["telegram"]))}
    canal = alvos["telegram"].cliente
    canal.http.sessao = sessao
    with pytest.raises(EscritaBloqueada):
        canal.publicar(ConteudoPublicacao(promocao_id=1, titulo="x", preco=1.0, loja="s", link_rastreado="https://a.b/c",
                                          legenda="x", imagem=b"jpg"))
    assert sessao.chamadas == []            # bloqueado ANTES de sair: nenhuma requisição


def test_p2_imgbb_so_envia_com_autorizacao_explicita():
    assert not chamada_de_leitura("POST", "https://api.imgbb.com/1/upload", {})
    assert chamada_de_leitura("POST", "https://api.imgbb.com/1/upload", {}, permitir_envio_imgbb=True)


# ============================================================================ P3 · categorias
def test_p3_categorias_real_manual_desativada():
    amb = Ambiente("homologacao", ["telegram"])
    alvos = {a.nome: a for a in registro.montar_validacao(cfg_com(**CRED_TG), amb)}
    assert alvos["mercado_livre"].categoria == "manual" and not alvos["mercado_livre"].obrigatoria
    assert alvos["shopee"].categoria == "desativada" and not alvos["shopee"].obrigatoria
    assert alvos["instagram"].categoria == "desativada"


def test_p3_fonte_manual_nao_reprova_integracao_automatica(tmp_path):
    class Ok:
        def testar(self): return "telegram: REAL ✔"
    alvos = [registro.AlvoValidacao("telegram", "real", True, Ok()),
             registro.AlvoValidacao("mercado_livre", "manual", False, ManualFonte(), "manual"),
             registro.AlvoValidacao("shopee", "desativada", False, None, "sem credencial")]
    saida = tmp_path / "v.json"
    assert validar(alvos, MemoriaRepositorio(), Log(saida=io.StringIO()), saida=saida) == 0
    itens = {i["integracao"]: i for i in json.loads(saida.read_text())["itens"]}
    assert itens["mercado_livre"]["status"] == "ignorado" and itens["shopee"]["status"] == "ignorado"


# ============================================================================ P4 · nada validado = reprovado
def test_p4_sem_integracao_obrigatoria_nunca_aprova(tmp_path):
    alvos = registro.montar_validacao(cfg_com(**CRED_TG), Ambiente("homologacao", []))   # nada em INTEGRACOES_ATIVAS
    alvos = [a for a in alvos if a.nome != "telegram"]                                   # sem nenhuma chamada de rede
    saida = tmp_path / "v.json"
    assert validar(alvos, MemoriaRepositorio(), Log(saida=io.StringIO()), saida=saida) == 1
    assert any("nenhuma integração obrigatória" in m for m in json.loads(saida.read_text())["motivos_reprovacao"])


def test_p4_obrigatoria_sem_credencial_reprova():
    alvos = {a.nome: a for a in registro.montar_validacao(cfg_com(), Ambiente("homologacao", ["shopee"]))}
    assert alvos["shopee"].obrigatoria and alvos["shopee"].cliente is None and "ausente" in alvos["shopee"].motivo


def test_p4_imgbb_nao_verificado_reprova_instagram_obrigatorio(tmp_path):
    cred = {"instagram": {"TOKEN": "IGT", "USER_ID": "1"}, "imgbb": {"KEY": "K"}}
    alvos = {a.nome: a for a in registro.montar_validacao(cfg_com(**cred), Ambiente("homologacao", ["instagram"]))}
    assert alvos["imgbb"].cliente is None and alvos["imgbb"].obrigatoria

    class Ok:
        def testar(self): return "instagram: REAL ✔"
    lista = [registro.AlvoValidacao("instagram", "real", True, Ok()), alvos["imgbb"]]
    saida = tmp_path / "v.json"
    assert validar(lista, MemoriaRepositorio(), Log(saida=io.StringIO()), saida=saida) == 1
    assert "imgbb: nao_verificado" in json.loads(saida.read_text())["motivos_reprovacao"]


def test_p4_esquema_do_banco_incompleto_reprova(tmp_path):
    class Ok:
        def testar(self): return "ok"
    repo = MemoriaRepositorio()
    repo.esquema["rpcs"] = []
    saida = tmp_path / "v.json"
    assert validar([registro.AlvoValidacao("telegram", "real", True, Ok())], repo, Log(saida=io.StringIO()), saida=saida) == 1


# ============================================================================ P5 · reserva obrigatória
def test_p5_reserva_nao_gravada_por_erro_do_banco_nada_e_enviado():
    repo, pub, _, cfg = cenario({"telegram": CanalEspiao()})
    canal = pub.canais["telegram"]

    def falha(_):
        raise ErroBanco("banco fora", status=503)
    repo.reservar_envio = falha
    pub.processar_fila(cfg)
    assert canal.envios == 0


def test_p5_rpc_de_reserva_ausente_nada_e_enviado():
    repo, pub, _, cfg = cenario({"telegram": CanalEspiao()})

    def ausente(_):
        raise ErroBanco("function not found", codigo="PGRST202", status=404)
    repo.reservar_envio = ausente
    pub.processar_fila(cfg)
    assert pub.canais["telegram"].envios == 0


def test_p5_reserva_recusada_trava_vencida_nada_e_enviado():
    repo, pub, _, cfg = cenario({"telegram": CanalEspiao()})
    repo.reservar_envio = lambda _: False
    pub.processar_fila(cfg)
    assert pub.canais["telegram"].envios == 0


def test_p5_memoria_recusa_reserva_com_trava_vencida_e_reserva_dupla():
    repo, pub, _, cfg = cenario({"telegram": CanalEspiao()})
    item = repo.pegar_fila(1)[0]
    assert repo.reservar_envio(item.id) is True and repo.reservar_envio(item.id) is False
    item2 = repo.pegar_fila(1)[0]
    repo._um("fila", id=item2.id)["bloqueado_ate"] = TARDE - timedelta(seconds=1)
    assert repo.reservar_envio(item2.id) is False


# ============================================================================ P6 · cupons
def _cupons(canal_real):
    repo, pub, _, cfg = cenario({"telegram": canal_real})
    repo.config.update({"cupons.ativo": True, "cupons.horario": 0, "cupons.publicar_telegram": True})
    repo.criar_cupom("shopee", "Cupom de teste", TARDE + timedelta(days=1), codigo="TESTE10")
    cfg = repo.configuracoes()
    return repo, pub, cfg, ServicoCupons(repo, {}, pub, Log(saida=io.StringIO()), relogio=lambda: TARDE)


def test_p6_cupom_sem_reserva_gravada_nao_e_enviado():
    canal = CanalEspiao()
    repo, pub, cfg, svc = _cupons(canal)

    def falha(*_):
        raise ErroBanco("banco fora", status=503)
    repo.reservar_envio_cupom = falha
    svc.publicar(cfg)
    assert canal.envios == 0


def test_p6_cupom_incerto_nao_e_reenviado_nem_na_proxima_rodada():
    canal = CanalEspiao(falha=ErroIntegracao("conexão caiu", servico="telegram", incerto=True))
    repo, pub, cfg, svc = _cupons(canal)
    svc.publicar(cfg)
    canal.falha = None
    svc.publicar(cfg)                       # próxima rodada, canal normal
    assert canal.envios == 1
    assert not any(p["resultado"] == "erro" for p in repo.t["cupons_pub"])   # incerto não vira "erro" (que liberaria)


def test_p6_cupom_enviado_com_falha_ao_gravar_resultado_nao_e_reenviado():
    canal = CanalEspiao()
    repo, pub, cfg, svc = _cupons(canal)

    def falha_registro(*a, **k):
        raise ErroBanco("timeout", status=504)
    original = repo.registrar_publicacao_cupom
    repo.registrar_publicacao_cupom = falha_registro
    svc.publicar(cfg)
    repo.registrar_publicacao_cupom = original
    svc.publicar(cfg)
    assert canal.envios == 1


def test_p6_cupom_erro_definitivo_permite_nova_tentativa():
    canal = CanalEspiao(falha=ErroIntegracao("HTTP 400 imagem inválida", servico="telegram", status=400))
    repo, pub, cfg, svc = _cupons(canal)
    svc.publicar(cfg)
    canal.falha = None
    assert svc.publicar(cfg) == 1 and canal.envios == 2


# ============================================================================ P7 · falha após o envio
def test_p7_5xx_num_envio_sem_repeticao_e_incerto():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", RespostaFalsa(502, texto="bad gateway"))
    with pytest.raises(ErroIntegracao) as e:
        cliente(sessao).post("https://api.telegram.org/botX/sendPhoto", repetir=False)
    assert e.value.incerto and len(sessao.chamadas) == 1


def test_p7_telegram_5xx_ao_publicar_nao_republica():
    sessao = SessaoFalsa().quando("POST", "sendPhoto", RespostaFalsa(502, texto="bad gateway"))
    repo, pub, _, cfg = cenario({"telegram": TelegramCanal(TOKEN_TG, "@canal", http=cliente(sessao))})
    pub.processar_fila(cfg)
    enviados = len([c for c in sessao.chamadas if "sendPhoto" in c["url"]])
    for f in fila(repo, "telegram"):
        f["bloqueado_ate"] = TARDE - timedelta(minutes=1)
    pub.processar_fila(cfg)
    assert len([c for c in sessao.chamadas if "sendPhoto" in c["url"]]) == enviados
    assert all(f["status"] == "falhou" for f in fila(repo, "telegram"))


def test_p7_erro_inesperado_do_canal_vira_incerto():
    canal = CanalEspiao(falha=KeyError("message_id"))
    repo, pub, _, cfg = cenario({"telegram": canal})
    pub.processar_fila(cfg)
    assert all(f["status"] == "falhou" and "incerto" in (f.get("ultimo_erro") or "").lower() for f in fila(repo, "telegram"))
    envios = canal.envios
    pub.processar_fila(cfg)
    assert canal.envios == envios


def test_p7_post_enviado_e_banco_cai_ao_gravar_nao_republica():
    canal = CanalEspiao()
    repo, pub, _, cfg = cenario({"telegram": canal})

    def cai(*a, **k):
        raise ErroBanco("timeout", status=504)
    original = repo.registrar_publicacao
    repo.registrar_publicacao = cai
    pub.processar_fila(cfg)
    assert canal.envios == 1
    assert any("ENVIADO" in l["mensagem"] and l["nivel"] == "CRITICO" for l in pub.log.pendentes)
    repo.registrar_publicacao = original
    repo.relogio = lambda: TARDE + timedelta(minutes=30)       # trava venceu
    pub.processar_fila(cfg)
    item = next(f for f in fila(repo, "telegram") if f.get("publicando_desde"))
    assert item["status"] == "falhou" and canal.envios == 2     # o 2º envio é da OUTRA oferta, não repetição
    assert len({f["promocao_id"] for f in fila(repo, "telegram")}) == 2


# ============================================================================ P8 · requisitos do banco
def test_p8_sem_rpcs_exigidas_nenhum_envio_real_e_alerta_critico():
    canal = CanalEspiao()
    repo, pub, _, cfg = cenario({"telegram": canal})
    repo.esquema = {"versao": 17, "rpcs": ["pegar_proximas_publicacoes"], "configuracoes": [], "colunas": []}
    pub.processar_fila(cfg)
    assert canal.envios == 0
    assert any("bloqueadas" in l["mensagem"] and l["nivel"] == "CRITICO" for l in pub.log.pendentes)
    assert all(f["status"] == "pendente" for f in fila(repo, "telegram"))


def test_p8_rpc_de_esquema_ausente_bloqueia():
    repo = MemoriaRepositorio()

    def ausente():
        raise ErroBanco("function not found", codigo="PGRST202", status=404)
    repo.esquema_publicacao = ausente
    assert faltando_para_publicar(repo) and "migration 018" in faltando_para_publicar(repo)[0]


def test_p8_canal_simulado_nao_depende_dos_requisitos():
    repo, pub, _, cfg = cenario({})
    repo.esquema = {"versao": 0}
    ok, _ = pub.processar_fila(cfg)
    assert ok >= 1                                   # demonstração continua funcionando


def test_p8_cupons_reais_bloqueados_sem_requisitos():
    canal = CanalEspiao()
    repo, pub, cfg, svc = _cupons(canal)
    repo.esquema = {"versao": 17}
    svc.publicar(cfg)
    assert canal.envios == 0


# ============================================================================ P9 · atribuição
LOJA = ["shopee.com.br"]
AFIL = ["s.shopee.com.br"]


def _seguir(*saltos):
    """Sessão falsa: cada URL redireciona para a próxima; a última responde `saltos[-1]` (status)."""
    sessao = SessaoFalsa()
    urls, status = list(saltos[:-1]), saltos[-1]
    for atual, prox in zip(urls, urls[1:]):
        sessao.quando("GET", atual, RespostaFalsa(302, headers={"Location": prox}))
    sessao.quando("GET", urls[-1], RespostaFalsa(status, texto="ok"))
    return cliente(sessao, tentativas=1)


def _validar(http, url, **kw):
    base = dict(dominios_destino=LOJA, parametros_afiliado=["utm_source"], id_afiliado="12345", exigir_atribuicao=True)
    base.update(kw)
    return validar_link(url, AFIL, http, **base)


def test_p9_id_de_afiliado_por_token_e_nao_por_substring():
    assert contem_id("an_12345", "12345") and not contem_id("an_3123456", "12345")
    http = _seguir("https://s.shopee.com.br/A", "https://shopee.com.br/produto-i.1.2?utm_source=an_3123456", 200)
    r = _validar(http, "https://s.shopee.com.br/A")
    assert not r.valido and "outra conta" in r.motivo


def test_p9_pagina_inicial_com_parametro_nao_comprova_atribuicao():
    http = _seguir("https://s.shopee.com.br/A", "https://shopee.com.br/?utm_source=an_12345", 200)
    assert not _validar(http, "https://s.shopee.com.br/A").valido


def test_p9_bloqueio_de_robo_no_encurtador_nao_e_prova():
    http = _seguir("https://s.shopee.com.br/A", 403)
    r = _validar(http, "https://s.shopee.com.br/A")
    assert not r.valido and not r.atribuicao and r.status_http == 403


def test_p9_sem_dominio_de_destino_ou_sem_id_nao_comprova():
    http = _seguir("https://s.shopee.com.br/A", "https://shopee.com.br/p-i.1.2?utm_source=an_12345", 200)
    assert not _validar(http, "https://s.shopee.com.br/A", dominios_destino=None).valido
    http = _seguir("https://s.shopee.com.br/A", "https://shopee.com.br/p-i.1.2?utm_source=an_12345", 200)
    assert not _validar(http, "https://s.shopee.com.br/A", id_afiliado=None).valido


def test_p9_atribuicao_comprovada_marca_resultado():
    http = _seguir("https://s.shopee.com.br/A", "https://shopee.com.br/p-i.1.2?utm_source=an_12345", 200)
    r = _validar(http, "https://s.shopee.com.br/A")
    assert r.valido and r.atribuicao


def test_p9_link_curto_e_o_que_e_validado():
    repo = MemoriaRepositorio()
    repo.config["busca.palavras_por_execucao"] = 99
    repo, pub, _, cfg = cenario({})
    link = repo.t["links"][0]
    link.update(validado=False, status_http=None, url_curta="https://s.shopee.com.br/CURTO")
    pend = next(p for p in repo.links_pendentes(10) if p["link_id"] == link["id"])
    assert pend["url"] == "https://s.shopee.com.br/CURTO"


def test_p9_producao_nao_publica_sem_atribuicao_comprovada_e_revalida():
    canal = CanalEspiao()
    repo, pub, _, cfg = cenario({"telegram": canal})
    cfg = dict(cfg, **{"links.exigir_atribuicao": True, "links.validade_atribuicao_horas": 72})
    pub.processar_fila(cfg)
    assert canal.envios == 0                                     # nenhum link tinha atribuição comprovada
    assert len(repo.links_pendentes(10, revalidar_atribuicao_horas=72)) >= 2   # voltam para conferência


def test_p9_atribuicao_vencida_bloqueia_e_recente_libera():
    canal = CanalEspiao()
    repo, pub, _, cfg = cenario({"telegram": canal})
    cfg = dict(cfg, **{"links.exigir_atribuicao": True, "links.validade_atribuicao_horas": 72})
    for l in repo.t["links"]:
        l["atribuicao_verificada_em"] = TARDE - timedelta(hours=100)
    pub.processar_fila(cfg)
    assert canal.envios == 0
    for f in fila(repo, "telegram"):
        f["agendado_para"] = TARDE - timedelta(minutes=1)
    for l in repo.t["links"]:
        l["atribuicao_verificada_em"] = TARDE - timedelta(hours=1)
    pub.processar_fila(cfg)
    assert canal.envios >= 1


def test_p9_registrar_validacao_grava_comprovacao_so_quando_ha_atribuicao():
    repo, pub, _, cfg = cenario({})
    l = repo.t["links"][0]
    repo.registrar_validacao(l["id"], ResultadoValidacao(True, None, 200, atribuicao=False))
    assert l["atribuicao_verificada_em"] is None
    repo.registrar_validacao(l["id"], ResultadoValidacao(True, None, 200, atribuicao=True))
    assert l["atribuicao_verificada_em"] == TARDE


# ============================================================================ Supabase (contratos HTTP das RPCs novas)
def _repo(sessao):
    from app.repositorio import SupabaseRepositorio
    return SupabaseRepositorio("https://abc.supabase.co", "sb_secret_x", cliente(sessao, tentativas=1))


@pytest.mark.parametrize("resposta,esperado", [(True, True), (False, False), (None, False)])
def test_supabase_reserva_so_vale_com_true_explicito(resposta, esperado):
    sessao = SessaoFalsa().quando("POST", "rpc/fila_reservar_envio", RespostaFalsa(200, resposta) if resposta is not None
                                  else RespostaFalsa(200, texto=""))
    assert _repo(sessao).reservar_envio(5) is esperado
    assert sessao.chamadas[0]["json"] == {"p_id": 5}


def test_supabase_reserva_sem_rpc_lanca_erro_sem_fallback():
    sessao = SessaoFalsa().quando("POST", "rpc/fila_reservar_envio",
                                  RespostaFalsa(404, {"code": "PGRST202", "message": "not found"}))
    with pytest.raises(ErroBanco):
        _repo(sessao).reservar_envio(5)


def test_supabase_reserva_de_cupom_e_esquema():
    sessao = (SessaoFalsa().quando("POST", "rpc/cupom_reservar_envio", RespostaFalsa(200, 42))
              .quando("POST", "rpc/esquema_publicacao", RespostaFalsa(200, {"versao": 18})))
    r = _repo(sessao)
    assert r.reservar_envio_cupom(1, 2) == 42 and r.esquema_publicacao() == {"versao": 18}


def test_supabase_registrar_validacao_grava_comprovacao_e_tolera_banco_antigo():
    chamadas = []

    def patch(metodo, url, **kw):
        chamadas.append(dict(kw["json"]))
        if "atribuicao_verificada_em" in kw["json"]:
            return RespostaFalsa(400, {"code": "PGRST204", "message": "column not found"})
        return RespostaFalsa(204, texto="")
    _repo(SessaoFalsa().quando("PATCH", "links_afiliados", patch)).registrar_validacao(
        1, ResultadoValidacao(True, None, 200, atribuicao=True))
    assert chamadas[0]["atribuicao_verificada_em"] and "atribuicao_verificada_em" not in chamadas[1]


def test_supabase_links_pendentes_valida_url_curta_e_revalida_atribuicao():
    def get(metodo, url, **kw):
        get.params = kw["params"]
        return RespostaFalsa(200, [{"id": 1, "url": "https://s.shopee.com.br/longo", "url_curta": "https://s.shopee.com.br/C",
                                    "produto_id": 2, "produtos": {"demo": False, "marketplaces": {
                                        "codigo": "shopee", "dominios_afiliado": AFIL}}}])
    pend = _repo(SessaoFalsa().quando("GET", "links_afiliados", get)).links_pendentes(5, revalidar_atribuicao_horas=72)
    assert pend[0]["url"] == "https://s.shopee.com.br/C"
    assert "atribuicao_verificada_em" in get.params["or"]
