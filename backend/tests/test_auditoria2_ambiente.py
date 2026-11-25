"""Auditoria complementar · P0 2: isolamento demonstração / homologação / produção."""
from __future__ import annotations

import io

import pytest

from app.config import ler_config
from app.erros import ErroConfiguracao
from app.integracoes import registro
from app.integracoes.canais.demo import DemoCanal
from app.integracoes.fontes.demo import DemoFonte

BASE = {"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SERVICE_KEY": "sb_secret_x"}
TUDO = {"SHOPEE_APP_ID": "1", "SHOPEE_SECRET": "s", "TELEGRAM_TOKEN": "123456:AAA", "TELEGRAM_CANAL": "@c",
        "IG_TOKEN": "IGAAx", "IG_USER_ID": "9", "IMGBB_KEY": "k"}


def montar(**env):
    from app.ambiente import ler_ambiente
    e = {**BASE, **env}
    amb = ler_ambiente(e)
    return amb, registro.montar(ler_config(e), ambiente=amb)


def demos(integ):
    return [k for k, v in {**integ.fontes, **integ.canais}.items() if isinstance(v, (DemoFonte, DemoCanal))]


def test_ambiente_e_obrigatorio_para_rodar():
    from app.ambiente import ler_ambiente
    with pytest.raises(ErroConfiguracao, match="AMBIENTE"):
        ler_ambiente(BASE)
    with pytest.raises(ErroConfiguracao):
        ler_ambiente({**BASE, "AMBIENTE": "prod"})


def test_producao_sem_credencial_bloqueia_em_vez_de_virar_demo():
    env = dict(TUDO)
    env.pop("TELEGRAM_TOKEN")
    with pytest.raises(ErroConfiguracao, match="telegram"):
        montar(AMBIENTE="producao", INTEGRACOES_ATIVAS="shopee,telegram,instagram", **env)


def test_producao_nunca_usa_integracao_de_demonstracao():
    amb, integ = montar(AMBIENTE="producao", INTEGRACOES_ATIVAS="shopee,telegram", **TUDO)
    assert demos(integ) == []
    assert set(integ.canais) == {"telegram"} and "instagram_story" in amb.canais_desativados


def test_producao_com_modo_demo_ligado_e_conflito():
    with pytest.raises(ErroConfiguracao, match="MODO_DEMO"):
        montar(AMBIENTE="producao", MODO_DEMO="1", INTEGRACOES_ATIVAS="telegram", **TUDO)


def test_producao_exige_lista_explicita_de_integracoes():
    with pytest.raises(ErroConfiguracao, match="INTEGRACOES_ATIVAS"):
        montar(AMBIENTE="producao", **TUDO)


def test_homologacao_le_de_verdade_mas_nunca_publica():
    amb, integ = montar(AMBIENTE="homologacao", INTEGRACOES_ATIVAS="shopee,telegram,instagram", **TUDO)
    assert integ.fontes["shopee"].modo == "real"
    assert all(isinstance(c, DemoCanal) for c in integ.canais.values())


def test_demonstracao_ignora_credenciais_reais():
    amb, integ = montar(AMBIENTE="demonstracao", **TUDO)
    assert set(demos(integ)) >= {"telegram", "instagram_story", "instagram_feed"} and "shopee" not in integ.fontes


def test_banco_de_outro_ambiente_e_recusado():
    from app.ambiente import conferir_banco, ler_ambiente
    amb = ler_ambiente({**BASE, "AMBIENTE": "producao", "INTEGRACOES_ATIVAS": "telegram"})
    with pytest.raises(ErroConfiguracao, match="homologacao"):
        conferir_banco(amb, {"sistema.ambiente": "homologacao"})
    with pytest.raises(ErroConfiguracao, match="017"):
        conferir_banco(amb, {})
    conferir_banco(amb, {"sistema.ambiente": "producao"})


def test_producao_obriga_verificar_links_e_atribuicao():
    from app.ambiente import ler_ambiente
    amb = ler_ambiente({**BASE, "AMBIENTE": "producao", "INTEGRACOES_ATIVAS": "telegram"})
    cfg = amb.ajustar({"monitor.verificar_links": False})
    assert cfg["monitor.verificar_links"] is True and cfg["links.exigir_atribuicao"] is True


def test_rodar_em_producao_sem_credencial_sai_com_erro_e_alerta(monkeypatch, capsys):
    from app import main
    for k in list(TUDO) + ["AMBIENTE", "INTEGRACOES_ATIVAS", "MODO_DEMO"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SUPABASE_URL", BASE["SUPABASE_URL"])
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", BASE["SUPABASE_SERVICE_KEY"])
    monkeypatch.setenv("AMBIENTE", "producao")
    monkeypatch.setenv("INTEGRACOES_ATIVAS", "telegram")
    monkeypatch.setattr(main, "carregar_env", lambda: None)

    class RepoFalso:
        def __init__(self, *a, **k): pass
        def obter_credencial(self, *a): return None
        def configuracoes(self): return {"sistema.ambiente": "producao"}
    monkeypatch.setattr(main, "SupabaseRepositorio", RepoFalso)
    assert main.main(["rodar"]) == 2
    assert "CRITICO" in capsys.readouterr().out


def test_em_producao_link_so_valida_com_atribuicao_comprovada():
    """Fluxo completo: produção exige parâmetros de afiliado configurados e presentes no destino final."""
    from app.ambiente import ler_ambiente
    from app.cliente_http import ClienteHTTP
    from app.logs import Log
    from app.modelos import OfertaEncontrada
    from app.repositorio_memoria import MemoriaRepositorio
    from app.servicos.busca import ServicoBusca
    from app.servicos.publicacao import ServicoPublicacao
    from dubles import RespostaFalsa, SessaoFalsa
    from app.integracoes.base import REAL, FonteOfertas

    class Loja(FonteOfertas):
        codigo, modo = "shopee", REAL
        def buscar(self, palavra, limite=30):
            return [OfertaEncontrada(marketplace="shopee", id_externo="1", titulo="Fone Bluetooth sem fio",
                                     preco=50, preco_original=100, link_afiliado="https://s.shopee.com.br/A1")]

    sessao = (SessaoFalsa().quando("GET", "s.shopee.com.br/A1", RespostaFalsa(302, headers={"Location": "https://shopee.com.br/p?utm_source=an_777"}))
              .quando("GET", "shopee.com.br/p", RespostaFalsa(200)))
    repo = MemoriaRepositorio()
    repo.definir_modo("marketplaces", "shopee", "real")
    repo.config["busca.palavras_por_execucao"] = 1
    amb = ler_ambiente({"AMBIENTE": "producao", "INTEGRACOES_ATIVAS": "shopee,telegram"})
    cfg = amb.ajustar(repo.configuracoes())
    ServicoBusca(repo, {"shopee": Loja()}, Log(saida=io.StringIO())).executar(cfg)
    pub = ServicoPublicacao(repo, {}, Log(saida=io.StringIO()), url_supabase="https://x.supabase.co",
                            http=ClienteHTTP("t", sessao=sessao, tentativas=1, dormir=lambda s: None))
    assert pub.validar_links(cfg) == (0, 1)                        # sem parâmetros configurados: não valida
    mkt = repo._um("marketplaces", codigo="shopee")
    mkt.update(parametros_afiliado=["utm_source"], id_afiliado="777")
    repo.t["links"][0].update(status_http=None, motivo_invalido=None, validado=False)
    assert pub.validar_links(cfg) == (1, 0)
