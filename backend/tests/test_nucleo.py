"""Etapa 1 · núcleo: configuração, modo demo, máscara de segredos, logs e HTTP."""
import io

import pytest
import requests

from app.config import DEMO, REAL, ler_config
from app.erros import ErroConfiguracao, ErroIntegracao
from app.cliente_http import ClienteHTTP
from app.logs import Log, mascarar
from dubles import RespostaFalsa, SessaoFalsa

BASE = {"SUPABASE_URL": "https://abc.supabase.co/", "SUPABASE_SERVICE_KEY": "sb_secret_chave_teste_123456"}


# ---------------------------------------------------------------- config
def test_config_exige_supabase():
    with pytest.raises(ErroConfiguracao, match="SUPABASE_URL"):
        ler_config({})


def test_config_recusa_chave_publica():
    with pytest.raises(ErroConfiguracao, match="PÚBLICA"):
        ler_config({**BASE, "SUPABASE_SERVICE_KEY": "sb_publishable_xyz"})


def test_config_recusa_url_sem_https():
    with pytest.raises(ErroConfiguracao, match="https"):
        ler_config({**BASE, "SUPABASE_URL": "http://abc.supabase.co"})


def test_sem_credenciais_tudo_em_demo():
    c = ler_config(BASE)
    assert c.supabase_url == "https://abc.supabase.co"
    assert all(m == DEMO for m in c.resumo_seguro()["integracoes"].values())


def test_credencial_parcial_continua_demo():
    c = ler_config({**BASE, "SHOPEE_APP_ID": "123"})  # falta o SECRET
    assert c.modo("shopee") == DEMO


def test_credenciais_completas_viram_real():
    c = ler_config({**BASE, "TELEGRAM_TOKEN": "123:abc", "TELEGRAM_CANAL": "@canal"})
    assert c.modo("telegram") == REAL
    assert c.modo("instagram") == DEMO


def test_modo_demo_forcado_vence():
    c = ler_config({**BASE, "TELEGRAM_TOKEN": "123:abc", "TELEGRAM_CANAL": "@canal", "MODO_DEMO": "sim"})
    assert c.modo("telegram") == DEMO


def test_credencial_vem_do_cofre_quando_env_nao_tem():
    cofre = {("shopee", "APP_ID"): "999", ("shopee", "SECRET"): "segredo-do-cofre"}
    c = ler_config(BASE, buscar_no_cofre=lambda i, n: cofre.get((i, n)))
    assert c.modo("shopee") == REAL
    assert c.credencial("shopee", "SECRET") == "segredo-do-cofre"


def test_resumo_seguro_nao_tem_segredos():
    c = ler_config({**BASE, "SHOPEE_APP_ID": "123", "SHOPEE_SECRET": "super-secreto-abc"})
    texto = str(c.resumo_seguro())
    assert "super-secreto-abc" not in texto and "sb_secret" not in texto


# ---------------------------------------------------------------- máscara e log
@pytest.mark.parametrize("entrada, proibido", [
    ("https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/sendPhoto", "AAHdqTcv"),
    ("chave sb_secret_abcdefghijklmnop", "abcdefghijklmnop"),
    ("graph?access_token=IGQVJabc123&x=1", "IGQVJabc123"),
    ('{"token": "valor-secreto"}', "valor-secreto"),
    ("Authorization: Bearer eyJhbGciOi.eyJzdWIiOiIxMjM0NTY3ODkw.SflKxwRJSMeKKF2QT4fwpMeJf36", "SflKxwRJ"),
    ("SHA256 Credential=12345, Timestamp=1, Signature=abcdef0123456789abcdef", "abcdef0123456789"),
])
def test_mascara_padroes(entrada, proibido):
    assert proibido not in mascarar(entrada)


def test_mascara_valor_exato_cadastrado():
    assert "minha-senha-qualquer" not in mascarar("erro com minha-senha-qualquer", ["minha-senha-qualquer"])


def test_log_mascara_mensagem_e_contexto_e_guarda_pendentes():
    saida = io.StringIO()
    log = Log(segredos=["segredo-xyz-123"], saida=saida)
    log.erro("telegram", "falhou com segredo-xyz-123", url="https://x/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/", token="abc")
    log.debug("teste", "não vai para o banco")
    texto = saida.getvalue()
    assert "segredo-xyz-123" not in texto and "AAHdqTcv" not in texto and '"token": "***"' in texto
    pend = log.retirar_pendentes()
    assert len(pend) == 1 and pend[0]["nivel"] == "ERRO" and pend[0]["contexto"]["token"] == "***"
    assert log.retirar_pendentes() == []


def test_log_recusa_nivel_invalido():
    with pytest.raises(ValueError):
        Log(saida=io.StringIO()).registrar("FATAL", "x", "y")


# ---------------------------------------------------------------- HTTP
def test_http_retenta_erro_temporario_e_depois_funciona():
    sessao = SessaoFalsa().quando("GET", "api", [RespostaFalsa(503), RespostaFalsa(429), RespostaFalsa(200, {"ok": 1})])
    esperas = []
    r = ClienteHTTP("teste", sessao=sessao, tentativas=3, dormir=esperas.append).get("https://api.test/x")
    assert r.json() == {"ok": 1} and len(sessao.chamadas) == 3 and esperas == [1.0, 2.0]


def test_http_nao_retenta_erro_definitivo():
    sessao = SessaoFalsa().quando("GET", "api", RespostaFalsa(401, texto="unauthorized"))
    with pytest.raises(ErroIntegracao) as e:
        ClienteHTTP("teste", sessao=sessao, dormir=lambda s: None).get("https://api.test/x")
    assert e.value.status == 401 and not e.value.temporario and len(sessao.chamadas) == 1


def test_http_desiste_depois_das_tentativas_em_timeout():
    sessao = SessaoFalsa().quando("GET", "api", requests.Timeout())
    with pytest.raises(ErroIntegracao) as e:
        ClienteHTTP("teste", sessao=sessao, tentativas=2, dormir=lambda s: None).get("https://api.test/x")
    assert e.value.temporario and len(sessao.chamadas) == 2


def test_http_aceita_status_permitido():
    sessao = SessaoFalsa().quando("GET", "api", RespostaFalsa(409))
    r = ClienteHTTP("teste", sessao=sessao).get("https://api.test/x", ok_status=(409,))
    assert r.status_code == 409
