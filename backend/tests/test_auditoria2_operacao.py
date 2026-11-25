"""Auditoria complementar · monitoramento externo, validação real (somente leitura) e telegram/instagram."""
import json

import pytest

from app.erros import ErroIntegracao
from app.logs import Log
from app.monitor_externo import Batimento
from app.repositorio_memoria import MemoriaRepositorio
from app.validacao_integracoes import validar
from app.integracoes import registro
from app.integracoes.fontes.demo import DemoFonte
from app.integracoes.canais.demo import DemoCanal
from app.integracoes.canais.telegram import TelegramCanal


class HttpGravador:
    def __init__(self, falhar=False):
        self.chamadas, self.falhar = [], falhar

    def post(self, url, data=None, timeout=None):
        if self.falhar:
            raise OSError("sem rede")
        self.chamadas.append(url)


def test_batimento_inicio_sucesso_falha():
    h = HttpGravador()
    b = Batimento("https://hc-ping.com/abc/", http=h)
    assert b.inicio() and b.sucesso("ok") and b.falha("erro")
    assert h.chamadas == ["https://hc-ping.com/abc/start", "https://hc-ping.com/abc", "https://hc-ping.com/abc/fail"]


@pytest.mark.parametrize("url", [None, "", "http://hc-ping.com/abc", "javascript:alert(1)"])
def test_batimento_ignora_url_ausente_ou_insegura(url):
    h = HttpGravador()
    assert Batimento(url, http=h).inicio() is False and h.chamadas == []


def test_falha_do_monitor_nunca_derruba_a_rodada():
    assert Batimento("https://hc-ping.com/x", http=HttpGravador(falhar=True)).sucesso() is False


def test_validacao_reprova_integracao_em_demo(tmp_path):
    """Teste simulado NÃO aprova integração real: modo demo = reprovado."""
    integ = registro.Integracoes(fontes={"demo": DemoFonte()}, canais={"telegram": DemoCanal("telegram")})
    saida = tmp_path / "v.json"
    assert validar(integ, MemoriaRepositorio(), Log(), saida=saida) == 1
    ev = json.loads(saida.read_text())
    assert ev["aprovado"] is False
    assert all(not i["aprovado"] for i in ev["itens"])
    assert any(i["integracao"] == "atribuicao_afiliado" for i in ev["itens"])


class TelegramFalso(TelegramCanal):
    def __init__(self, membro):
        self.chat, self.token, self.membro = "@canal", "123:SEGREDO", membro

    def _chamar(self, metodo, repetir=True, data=None, files=None):
        return {"getMe": {"id": 7, "username": "meubot"}, "getChat": {"title": "Achadinhos"},
                "getChatMember": self.membro}[metodo]


def test_telegram_exige_bot_administrador_que_publica_e_edita():
    assert "administrador" in TelegramFalso({"status": "administrator", "can_post_messages": True, "can_edit_messages": True}).testar()
    for membro in ({"status": "member"}, {"status": "administrator", "can_post_messages": False}):
        with pytest.raises(ErroIntegracao):
            TelegramFalso(membro).testar()


def test_evidencia_nao_contem_segredo(tmp_path):
    class Fonte(DemoFonte):
        modo = "real"
        codigo = "shopee"
        def testar(self):
            raise ErroIntegracao("token 123:SEGREDO recusado", servico="shopee")
    log = Log(segredos=["123:SEGREDO"])
    saida = tmp_path / "v.json"
    validar(registro.Integracoes(fontes={"shopee": Fonte()}, canais={}), MemoriaRepositorio(), log, saida=saida)
    assert "123:SEGREDO" not in saida.read_text()


def test_fila_atrasada_gera_alerta_critico():
    from datetime import timedelta
    from app.orquestrador import Orquestrador
    repo = MemoriaRepositorio()
    log = Log()
    integ = registro.Integracoes(fontes={"demo": DemoFonte()}, canais={})
    repo.t["fila"].append({"id": 999, "promocao_id": 1, "canal_id": 1, "status": "pendente",
                           "agendado_para": repo.relogio() - timedelta(hours=5)})
    status, resumo = Orquestrador(repo, integ, log, url_supabase="https://x.supabase.co").executar(origem="teste")
    assert resumo.alertas_criticos >= 1
    assert any("atrasadas" in (l.get("mensagem") or "") for l in repo.t["logs"])


def test_fila_atrasada_nao_alerta_com_parada_de_emergencia():
    from datetime import timedelta
    repo = MemoriaRepositorio()
    repo.t["fila"].append({"id": 1, "status": "pendente", "agendado_para": repo.relogio() - timedelta(hours=5)})
    assert repo.fila_atrasada(120) == 1 and repo.fila_atrasada(600) == 0


def test_instagram_publi_visivel_na_primeira_linha():
    from app.servicos.textos import legenda_instagram
    cfg = {"transparencia.marcar_publi": True, "textos.hashtags": "#achadinhos #promo",
           "transparencia.aviso_afiliado": "Link de afiliado: posso ganhar comissão."}
    leg = legenda_instagram({"titulo": "Fone", "preco_promocional": 50, "preco_referencia": 100, "desconto_pct": 50}, cfg, False)
    primeira = leg.splitlines()[0]
    assert "#publi" in primeira  # visível sem tocar em "mais" (CONAR 2026)
    assert leg.count("#publi") == 1
