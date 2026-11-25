"""Auditoria complementar · P0 3, 4 e 5: atribuição de afiliado, redirecionamentos, SSRF e imagens."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.cliente_http import ClienteHTTP
from app.servicos.validacao import validar_link
from dubles import RespostaFalsa, SessaoFalsa

PUBLICO = lambda host: ["200.147.67.142"]          # resolvedor de DNS falso: tudo é público
SHOPEE = ["s.shopee.com.br", "shope.ee"]
DESTINO = ["shopee.com.br"]
PARAMS = ["utm_source", "mmp_pid"]


def cli(sessao):
    return ClienteHTTP("t", sessao=sessao, tentativas=1, dormir=lambda s: None)


def redir(para, status=302):
    return RespostaFalsa(status, headers={"Location": para})


def resolvedor(mapa):
    return lambda host: mapa.get(host, ["200.147.67.142"])


def validar(url, sessao, **kw):
    kw.setdefault("resolver", PUBLICO)
    kw.setdefault("dominios_destino", DESTINO)
    kw.setdefault("parametros_afiliado", PARAMS)
    return validar_link(url, SHOPEE, cli(sessao), **kw)


# ---------------------------------------------------------------- redirecionamentos e domínio final
def test_cadeia_valida_preserva_atribuicao():
    s = (SessaoFalsa().quando("GET", "s.shopee.com.br/AbC", redir("https://shopee.com.br/produto-i.1.2?utm_source=an_123&mmp_pid=an_123"))
         .quando("GET", "shopee.com.br/produto", RespostaFalsa(200)))
    r = validar("https://s.shopee.com.br/AbC", s, id_afiliado="123")
    assert r.valido, r.motivo
    assert len(s.chamadas) == 2 and all(c.get("allow_redirects") is False for c in s.chamadas)   # cada salto conferido


def test_redirecionamento_para_outro_dominio_e_recusado():
    s = SessaoFalsa().quando("GET", "s.shopee.com.br/AbC", redir("https://golpe-shopee.com/oferta"))
    r = validar("https://s.shopee.com.br/AbC", s)
    assert not r.valido and "golpe-shopee.com" in r.motivo
    assert not any("golpe" in c["url"] for c in s.chamadas)             # nem chegou a acessar


def test_redirecionamento_para_http_e_recusado():
    s = SessaoFalsa().quando("GET", "s.shopee.com.br/AbC", redir("http://shopee.com.br/produto?utm_source=an_1"))
    r = validar("https://s.shopee.com.br/AbC", s)
    assert not r.valido and "https" in r.motivo


def test_redirecionamento_para_endereco_interno_e_bloqueado_sem_acessar():
    s = SessaoFalsa().quando("GET", "s.shopee.com.br/AbC", redir("https://metadata.interno/latest"))
    r = validar("https://s.shopee.com.br/AbC", s, resolver=resolvedor({"metadata.interno": ["169.254.169.254"]}),
                dominios_destino=["metadata.interno"])
    assert not r.valido and ("privado" in r.motivo or "não é um site da loja" in r.motivo)
    assert not any("metadata.interno" in c["url"] for c in s.chamadas)
    s2 = SessaoFalsa().quando("GET", "s.shopee.com.br/AbC", redir("https://shopee.com.br/x"))   # domínio da loja apontando p/ IP interno
    r2 = validar("https://s.shopee.com.br/AbC", s2, resolver=resolvedor({"shopee.com.br": ["169.254.169.254"]}))
    assert not r2.valido and "privado" in r2.motivo and len(s2.chamadas) == 1


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "100.64.0.1", "::1", "fd00::1", "0.0.0.0"])
def test_dominio_de_afiliado_resolvendo_para_ip_privado_e_bloqueado(ip):
    s = SessaoFalsa()
    r = validar("https://s.shopee.com.br/AbC", s, resolver=resolvedor({"s.shopee.com.br": [ip]}))
    assert not r.valido and "privado" in r.motivo and not s.chamadas


def test_excesso_de_redirecionamentos_e_recusado():
    r = validar("https://s.shopee.com.br/AbC", SessaoFalsa().quando("GET", "s.shopee.com.br", redir("https://shopee.com.br/a"))
                .quando("GET", "shopee.com.br/a", lambda m, u, **k: redir(u + "a")))
    assert not r.valido and "redirecionamentos" in r.motivo


def test_url_com_usuario_senha_ou_porta_estranha_e_recusada():
    assert not validar("https://user:senha@s.shopee.com.br/AbC", SessaoFalsa()).valido
    assert not validar("https://s.shopee.com.br:8443/AbC", SessaoFalsa()).valido


def test_atribuicao_perdida_no_redirecionamento_e_recusada():
    s = (SessaoFalsa().quando("GET", "s.shopee.com.br/AbC", redir("https://shopee.com.br/produto-i.1.2"))
         .quando("GET", "shopee.com.br/produto", RespostaFalsa(200)))
    r = validar("https://s.shopee.com.br/AbC", s)
    assert not r.valido and "identificador de afiliado" in r.motivo


def test_link_de_afiliado_de_outra_pessoa_e_recusado():
    s = (SessaoFalsa().quando("GET", "s.shopee.com.br/AbC", redir("https://shopee.com.br/p?utm_source=an_999&mmp_pid=an_999"))
         .quando("GET", "shopee.com.br/p", RespostaFalsa(200)))
    r = validar("https://s.shopee.com.br/AbC", s, id_afiliado="123")
    assert not r.valido and "outra conta" in r.motivo


def test_resposta_http_invalida_e_recusada():
    s = SessaoFalsa().quando("GET", "s.shopee.com.br/AbC", RespostaFalsa(302))   # 3xx sem Location
    assert not validar("https://s.shopee.com.br/AbC", s).valido
    s2 = SessaoFalsa().quando("GET", "s.shopee.com.br/AbC", RespostaFalsa(999))
    assert not validar("https://s.shopee.com.br/AbC", s2).valido


def test_em_producao_loja_sem_identificador_configurado_nao_valida():
    s = (SessaoFalsa().quando("GET", "s.shopee.com.br/AbC", redir("https://shopee.com.br/p?utm_source=an_1"))
         .quando("GET", "shopee.com.br/p", RespostaFalsa(200)))
    r = validar("https://s.shopee.com.br/AbC", s, parametros_afiliado=[], exigir_atribuicao=True)
    assert not r.valido and "configur" in r.motivo


# ---------------------------------------------------------------- imagens
def jpeg(largura=800, altura=800) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (largura, altura), "red").save(buf, "JPEG")
    return buf.getvalue()


def test_imagem_valida_e_baixada():
    from app.rede import baixar_imagem
    s = SessaoFalsa().quando("GET", "cf.shopee.com.br/img.jpg", RespostaFalsa(200, conteudo=jpeg(), headers={"Content-Type": "image/jpeg"}))
    dados = baixar_imagem(cli(s), "https://cf.shopee.com.br/img.jpg", resolver=PUBLICO)
    assert Image.open(io.BytesIO(dados)).size == (800, 800)


def test_imagem_grande_demais_para_de_ler_no_limite():
    from app.rede import ImagemRecusada, baixar_imagem
    grande = RespostaFalsa(200, conteudo=b"\xff\xd8" + b"0" * (12 * 1024 * 1024), headers={"Content-Type": "image/jpeg"})
    s = SessaoFalsa().quando("GET", "cf.shopee.com.br/g.jpg", grande)
    with pytest.raises(ImagemRecusada, match="grande"):
        baixar_imagem(cli(s), "https://cf.shopee.com.br/g.jpg", resolver=PUBLICO, max_bytes=5 * 1024 * 1024)
    assert grande.lidos <= 5 * 1024 * 1024 + 65536          # parou de ler, não baixou os 12 MB


def test_imagem_com_content_length_gigante_nem_e_lida():
    from app.rede import ImagemRecusada, baixar_imagem
    r = RespostaFalsa(200, conteudo=jpeg(), headers={"Content-Type": "image/jpeg", "Content-Length": str(500 * 1024 * 1024)})
    with pytest.raises(ImagemRecusada):
        baixar_imagem(cli(SessaoFalsa().quando("GET", "x.jpg", r)), "https://cf.shopee.com.br/x.jpg", resolver=PUBLICO)
    assert r.lidos == 0


@pytest.mark.parametrize("tipo,conteudo", [("text/html", b"<html>"), ("image/svg+xml", b"<svg/>"), ("image/jpeg", b"nao e imagem")])
def test_formato_invalido_e_recusado(tipo, conteudo):
    from app.rede import ImagemRecusada, baixar_imagem
    s = SessaoFalsa().quando("GET", "x.jpg", RespostaFalsa(200, conteudo=conteudo, headers={"Content-Type": tipo}))
    with pytest.raises(ImagemRecusada):
        baixar_imagem(cli(s), "https://cf.shopee.com.br/x.jpg", resolver=PUBLICO)


def test_bomba_de_descompressao_e_recusada():
    from app.rede import ImagemRecusada, baixar_imagem
    buf = io.BytesIO()
    Image.new("1", (20000, 20000)).save(buf, "PNG")          # 400 milhões de pixels em poucos KB
    s = SessaoFalsa().quando("GET", "b.png", RespostaFalsa(200, conteudo=buf.getvalue(), headers={"Content-Type": "image/png"}))
    with pytest.raises(ImagemRecusada, match="pixels"):
        baixar_imagem(cli(s), "https://cf.shopee.com.br/b.png", resolver=PUBLICO)


def test_imagem_em_http_ou_endereco_privado_e_recusada():
    from app.rede import ImagemRecusada, baixar_imagem
    with pytest.raises(ImagemRecusada):
        baixar_imagem(cli(SessaoFalsa()), "http://cf.shopee.com.br/x.jpg", resolver=PUBLICO)
    with pytest.raises(ImagemRecusada):
        baixar_imagem(cli(SessaoFalsa()), "https://interno/x.jpg", resolver=lambda h: ["10.1.2.3"])


def test_download_lento_estoura_o_prazo():
    from app.rede import ImagemRecusada, baixar_imagem
    tempos = iter([0, 100, 100, 100])
    s = SessaoFalsa().quando("GET", "x.jpg", RespostaFalsa(200, conteudo=jpeg() * 3, headers={"Content-Type": "image/jpeg"}))
    with pytest.raises(ImagemRecusada, match="tempo"):
        baixar_imagem(cli(s), "https://cf.shopee.com.br/x.jpg", resolver=PUBLICO, prazo_s=15, cronometro=lambda: next(tempos))


def test_publicacao_e_motor_usam_o_download_seguro():
    import inspect
    from app.servicos import inteligencia, publicacao
    assert "baixar_imagem" in inspect.getsource(publicacao.ServicoPublicacao._foto)
    assert "baixar_imagem" in inspect.getsource(inteligencia.ServicoInteligencia.medir_imagem)
