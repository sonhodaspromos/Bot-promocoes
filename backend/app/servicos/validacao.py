"""Validação de link de afiliado — nenhuma oferta é publicada sem passar por aqui.

Um link só é válido se:
  1. usa HTTPS;
  2. o domínio é um dos domínios de afiliado cadastrados para a loja (subdomínios aceitos;
     domínios disfarçados, como afiliado.loja.com.br.golpe.com, recusados);
  3. segue o formato de link de afiliado da loja (`padrao`, ex.: mercadolivre.com/sec/…), para
     não aceitar um link comum da loja sem atribuição de afiliado;
  4. abre (sem 404/410, sem falha de conexão e sem cair na página inicial da loja, que é o que
     acontece com link de afiliado expirado) — exceto links de demonstração, que nunca
     são acessados e só podem ir para canais de demonstração.
O banco repete a checagem de domínio (trigger), então mesmo um erro aqui não passaria.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlparse

from ..erros import ErroIntegracao
from ..cliente_http import ClienteHTTP
from ..modelos import ResultadoValidacao
from ..rede import UrlRecusada, host_permitido, seguir

DOMINIO_DEMO = "demo.invalid"
QUEBRADO = {404, 410}
BLOQUEIO_ROBO = {401, 403, 429}


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def dominio_permitido(url: str, dominios: list[str]) -> bool:
    h = host(url)
    return bool(h) and any(h == d.lower() or h.endswith("." + d.lower()) for d in dominios or [])


class LinkTemporariamenteIndisponivel(Exception):
    """Não deu para verificar agora (timeout, 5xx). Tentar de novo na próxima rodada."""


def segue_padrao(url: str, padrao: str | None) -> bool:
    if not padrao:
        return True
    try:
        return re.match(padrao, url) is not None
    except re.error:
        return False


def validar_link(url: str, dominios: list[str], http: ClienteHTTP | None, *, demo: bool = False,
                 verificar_http: bool = True, padrao: str | None = None, dominios_destino: list[str] | None = None,
                 parametros_afiliado: list[str] | None = None, id_afiliado: str | None = None,
                 exigir_atribuicao: bool = False, resolver=None) -> ResultadoValidacao:
    if not url or not url.lower().startswith("https://") or any(c.isspace() for c in url):
        return ResultadoValidacao(False, "o link precisa começar com https:// e não ter espaços")
    if not dominio_permitido(url, dominios):
        return ResultadoValidacao(False, f"o domínio {host(url) or '?'} não é um domínio de afiliado desta loja")
    if not segue_padrao(url, padrao):
        return ResultadoValidacao(False, "o link não tem a atribuição de afiliado da loja (formato de link de afiliado não reconhecido)")
    if host(url) == DOMINIO_DEMO or host(url).endswith("." + DOMINIO_DEMO):
        if demo:
            return ResultadoValidacao(True, "link de demonstração (não acessado)")
        return ResultadoValidacao(False, "link de demonstração em oferta real")
    params = [p.lower() for p in (parametros_afiliado or []) if p]
    if exigir_atribuicao and not params:
        return ResultadoValidacao(False, "configure os identificadores de afiliado da loja antes de publicar "
                                         "(marketplaces.parametros_afiliado): sem isso não dá para provar a comissão")
    if not verificar_http or http is None:
        if exigir_atribuicao:
            return ResultadoValidacao(False, "em produção o link precisa ser aberto para provar a atribuição")
        return ResultadoValidacao(True, "domínio conferido (verificação de acesso desligada)")
    try:
        permitidos = None if dominios_destino is None else list(dominios) + list(dominios_destino)
        resp, cadeia = seguir(http, url, resolver=resolver, hosts_permitidos=permitidos)
    except UrlRecusada as e:
        return ResultadoValidacao(False, f"link recusado: {e}")
    except ErroIntegracao as e:
        if e.temporario:
            raise LinkTemporariamenteIndisponivel(str(e)) from e
        return ResultadoValidacao(False, f"link não abre: {e}")
    status = resp.status_code
    final = cadeia[-1]
    try:
        resp.close()
    except Exception:
        pass
    if status in QUEBRADO:
        return ResultadoValidacao(False, f"link quebrado (HTTP {status})", status)
    if status >= 500:
        raise LinkTemporariamenteIndisponivel(f"loja respondeu HTTP {status}")
    if not (200 <= status < 300 or status in BLOQUEIO_ROBO):
        return ResultadoValidacao(False, f"resposta HTTP inesperada ({status})", status)
    destinos = dominios_destino if dominios_destino is not None else None
    if destinos is not None and not host_permitido(host(final), list(destinos) + list(dominios)):
        return ResultadoValidacao(False, f"o link termina em {host(final)}, que não é um site da loja", status)
    if len(cadeia) > 1 and urlparse(final).path in ("", "/") and not urlparse(final).query:
        return ResultadoValidacao(False, "o link leva para a página inicial da loja (afiliação expirada ou inválida)", status)
    if params:
        valores = {k.lower(): v for k, v in parse_qsl(urlparse(final).query, keep_blank_values=True)}
        achados = {p: valores[p] for p in params if p in valores}
        if not achados:
            return ResultadoValidacao(False, "o link perdeu o identificador de afiliado no caminho "
                                             f"(esperado um destes parâmetros: {', '.join(params)})", status)
        if id_afiliado and not any(str(id_afiliado).lower() in v.lower() for v in achados.values()):
            return ResultadoValidacao(False, "o identificador de afiliado do link é de outra conta: a comissão não seria sua", status)
    # 2xx = abriu. 401/403/429 = a loja respondeu, mas bloqueia robôs: o link existe.
    return ResultadoValidacao(True, None if 200 <= status < 300 else f"loja bloqueou o robô (HTTP {status})", status)
