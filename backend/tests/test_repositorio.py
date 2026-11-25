"""Etapa 2 · repositório: chamadas ao Supabase (com dublê) e regras do repositório em memória."""
import pytest

from app.erros import ErroBanco, RegraViolada
from app.cliente_http import ClienteHTTP
from app.modelos import ItemFila, OfertaEncontrada, ResultadoValidacao
from app.repositorio import SupabaseRepositorio
from app.repositorio_memoria import MemoriaRepositorio
from dubles import RespostaFalsa, SessaoFalsa

URL = "https://abc.supabase.co"


def repo(sessao, chave="sb_secret_xyz"):
    return SupabaseRepositorio(URL, chave, ClienteHTTP("supabase", sessao=sessao, tentativas=1, dormir=lambda s: None))


def oferta(**kw):
    base = dict(marketplace="shopee", id_externo="123", titulo="Fone Bluetooth", preco=50.0, preco_original=100.0,
                link_afiliado="https://s.shopee.com.br/abc", nota=4.8)
    base.update(kw)
    return OfertaEncontrada(**base)


# ---------------------------------------------------------------- Supabase (formato das chamadas)
def test_chave_secreta_nova_vai_so_no_apikey():
    s = SessaoFalsa().quando("GET", "configuracoes", RespostaFalsa(200, [{"chave": "a.b", "valor": 1}]))
    assert repo(s).configuracoes() == {"a.b": 1}
    cab = s.chamadas[0]["headers"]
    assert cab["apikey"] == "sb_secret_xyz" and "Authorization" not in cab


def test_chave_antiga_jwt_vai_no_authorization():
    s = SessaoFalsa().quando("GET", "configuracoes", RespostaFalsa(200, []))
    repo(s, chave="eyJhbGciOi.xxx.yyy").configuracoes()
    assert s.chamadas[0]["headers"]["Authorization"] == "Bearer eyJhbGciOi.xxx.yyy"


def test_regra_do_banco_vira_regra_violada():
    s = SessaoFalsa().quando("POST", "historico_publicacoes",
                             RespostaFalsa(400, {"code": "P0010", "message": "link não validado"}))
    with pytest.raises(RegraViolada) as e:
        repo(s).registrar_publicacao(ItemFila(1, 2, 3, 4), True, "99", None)
    assert e.value.codigo == "P0010" and e.value.descricao == "link de afiliado não validado"


def test_promocao_duplicada_devolve_none():
    s = SessaoFalsa().quando("POST", "promocoes", RespostaFalsa(409, {"code": "23505", "message": "duplicate"}))
    assert repo(s).criar_promocao(1, oferta(), "aprovada", "automatica") is None


def test_enfileirar_recusado_pelo_banco_devolve_false():
    s = SessaoFalsa().quando("POST", "fila_publicacoes", RespostaFalsa(400, {"code": "P0015", "message": "demo"}))
    assert repo(s).enfileirar(1, 2, 3) is False


def test_produto_existente_e_atualizado_e_novo_e_inserido():
    s = (SessaoFalsa()
         .quando("GET", "produtos", [RespostaFalsa(200, [{"id": 7}]), RespostaFalsa(200, [])])
         .quando("PATCH", "produtos", RespostaFalsa(204, texto=""))
         .quando("POST", "produtos", RespostaFalsa(201, [{"id": 8}])))
    r = repo(s)
    assert r.salvar_produto(oferta(), 1, 2) == (7, False)
    assert r.salvar_produto(oferta(id_externo="999"), 1, 2) == (8, True)
    novo = [c for c in s.chamadas if c["metodo"] == "POST"][0]["json"]
    assert novo["marketplace_id"] == 1 and novo["id_externo"] == "999" and novo["categoria_id"] == 2


def test_pegar_fila_usa_a_funcao_do_banco():
    s = SessaoFalsa().quando("POST", "rpc/pegar_proximas_publicacoes", RespostaFalsa(200, [
        {"id": 5, "promocao_id": 6, "canal_id": 7, "link_afiliado_id": 8, "tentativas": 1}]))
    itens = repo(s).pegar_fila(3)
    assert itens[0].id == 5 and s.chamadas[0]["json"] == {"p_limite": 3, "p_bloqueio_min": 10}


def test_falha_de_rede_vira_erro_de_banco():
    s = SessaoFalsa().quando("GET", "canais", RespostaFalsa(503, texto="indisponível"))
    with pytest.raises(ErroBanco):
        repo(s).canais()


def test_comissoes_repetidas_sao_atualizadas_nao_duplicadas():
    # (auditoria complementar) antes: "ignore-duplicates" descartava cancelamentos e mudanças de status
    s = (SessaoFalsa().quando("GET", "comissoes", RespostaFalsa(200, []))
         .quando("POST", "comissoes", RespostaFalsa(201, texto="")))
    repo(s).salvar_comissoes(1, [{"referencia_externa": "A", "valor_comissao": 2, "data_venda": "2026-11-20"}])
    c = next(x for x in s.chamadas if x["metodo"] == "POST")
    assert "merge-duplicates" in c["headers"]["Prefer"] and c["params"]["on_conflict"] == "marketplace_id,referencia_externa"
    assert c["json"][0]["origem"] == "api"


# ---------------------------------------------------------------- memória (regras essenciais)
def _cenario():
    r = MemoriaRepositorio()
    shopee = next(m for m in r.marketplaces() if m["codigo"] == "shopee")
    pid, novo = r.salvar_produto(oferta(), shopee["id"], None)
    promo = r.criar_promocao(pid, oferta(), "aprovada", "automatica")
    link = r.salvar_link(pid, promo, "https://s.shopee.com.br/abc")
    return r, pid, promo, link


def test_memoria_sem_promocao_duplicada():
    r, pid, promo, _ = _cenario()
    assert promo and r.criar_promocao(pid, oferta(), "aprovada", "automatica") is None


def test_memoria_link_fora_do_dominio_nao_valida():
    r, pid, _, _ = _cenario()
    outro = r.salvar_link(pid, None, "https://golpe.com/x")
    with pytest.raises(RegraViolada):
        r.registrar_validacao(outro, ResultadoValidacao(True, status_http=200))


def test_memoria_fila_exige_link_validado():
    r, _, promo, link = _cenario()
    canal = r.canais()[0]["id"]
    assert r.enfileirar(promo, canal, link) is False
    r.registrar_validacao(link, ResultadoValidacao(True, status_http=200))
    assert r.enfileirar(promo, canal, link) is False  # sem análise do motor
    r.salvar_analise(promo, 80, "excelente", 90, {"resumo": "teste"})
    assert r.enfileirar(promo, canal, link) is True
    assert r.enfileirar(promo, canal, link) is False  # duplicada


def test_memoria_preco_subiu_pausa():
    r, pid, promo, _ = _cenario()
    r.atualizar_produto(pid, 80.0, True, None)
    assert r._um("promocoes", id=promo)["status"] == "pausada"
