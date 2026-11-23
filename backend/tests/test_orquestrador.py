"""Etapa 5 · orquestrador e linha de comando: rodada completa em modo DEMO, isolamento de falhas."""
import io

from app import main as cli
from app.integracoes.canais.demo import DemoCanal
from app.integracoes.fontes.demo import DemoFonte
from app.integracoes.registro import Integracoes
from app.logs import Log
from app.orquestrador import Orquestrador
from app.repositorio_memoria import MemoriaRepositorio


def montar():
    repo = MemoriaRepositorio()
    repo.config["busca.palavras_por_execucao"] = 99  # determinístico
    integ = Integracoes(fontes={"demo": DemoFonte()},
                        canais={c: DemoCanal(c) for c in ("telegram", "instagram_story", "instagram_feed")})
    log = Log(saida=io.StringIO(), segredos=["segredo-que-nao-pode-vazar"])
    return repo, integ, log, Orquestrador(repo, integ, log, url_supabase="https://abc.supabase.co")


def test_rodada_completa_em_demo():
    repo, integ, log, orq = montar()
    status, resumo = orq.executar(origem="teste")
    assert status == "sucesso" and resumo.novas_promocoes > 0 and resumo.links_validados > 0
    assert resumo.publicadas == 4 and integ.canais["telegram"].publicados
    execucao = repo.t["execucoes"][0]
    assert execucao["status"] == "sucesso" and execucao["modo"] == "demo" and execucao["resumo"]["publicadas"] == 4
    assert repo.t["logs"] and all(l["execucao_id"] == execucao["id"] for l in repo.t["logs"])


def test_sincroniza_modo_dos_canais_no_banco():
    repo, integ, log, orq = montar()
    for c in repo.t["canais"]:
        c["modo"] = "real"
    orq.executar()
    assert all(c["modo"] == "demo" for c in repo.t["canais"])


def test_falha_em_uma_etapa_nao_derruba_as_outras():
    repo, integ, log, orq = montar()

    def quebra(cfg):
        raise RuntimeError("monitor caiu com segredo-que-nao-pode-vazar")
    orq.monitor.executar = quebra
    status, resumo = orq.executar()
    assert status == "parcial" and resumo.etapas_com_erro == ["monitor"] and resumo.publicadas > 0
    assert not any("segredo-que-nao-pode-vazar" in str(l) for l in repo.t["logs"])


def test_sem_configuracao_a_rodada_e_erro():
    repo, integ, log, orq = montar()

    def quebra():
        raise RuntimeError("banco fora")
    repo.configuracoes = quebra
    status, _ = orq.executar()
    assert status == "erro"


def test_cli_sem_variaveis_sai_com_codigo_2(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setattr(cli, "carregar_env", lambda: None)
    assert cli.main(["rodar"]) == 2


def test_cli_simular_funciona_sem_banco_e_sem_internet(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "SAIDA", tmp_path)
    assert cli.main(["simular"]) == 0
    assert list(tmp_path.glob("simulacao_telegram_*.jpg"))
