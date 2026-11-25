"""Cliente HTTP com timeout, retentativas com espera crescente e erros padronizados.

Toda integração usa este cliente, então os testes trocam a "sessão" por um dublê
e nenhuma chamada real à internet acontece durante os testes.

Proteções (auditoria):
  * Disjuntor por servidor: depois de `limite_falhas` falhas temporárias seguidas no mesmo
    servidor, as próximas chamadas falham na hora ("fora do ar nesta rodada"). Uma API fora do
    ar não consome mais os 9 minutos do job.
  * Envio não repetível (`repetir=False`, usado para publicar posts): timeout ou queda depois de
    enviar NÃO é repetido, porque o canal pode ter publicado. O erro sai marcado como `incerto`.
  * Mensagens de erro nunca levam a URL (a do Telegram contém o token).
"""
from __future__ import annotations

import time
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from .erros import ErroIntegracao, EscritaBloqueada

TEMPORARIOS = {408, 425, 429, 500, 502, 503, 504}
NAVEGADOR = "Mozilla/5.0 (compatible; BotDeOfertas/2.0; +https://github.com)"


class ClienteHTTP:
    def __init__(self, servico: str, *, timeout: float = 20.0, tentativas: int = 3,
                 espera_inicial: float = 1.0, sessao: Any = None, dormir: Callable[[float], None] = time.sleep,
                 limite_falhas: int = 2):
        self.servico = servico
        self.timeout = timeout
        self.tentativas = max(1, tentativas)
        self.espera_inicial = espera_inicial
        self.sessao = sessao or requests.Session()
        self.dormir = dormir
        self.limite_falhas = max(1, limite_falhas)
        self.falhas_seguidas: dict[str, int] = {}

    def fora_do_ar(self, url: str) -> bool:
        return self.falhas_seguidas.get(_servidor(url), 0) >= self.limite_falhas

    def requisitar(self, metodo: str, url: str, *, ok_status: tuple[int, ...] = (), repetir: bool = True,
                   **kwargs) -> requests.Response:
        """Faz a requisição. Retenta erros temporários. Lança ErroIntegracao se não der certo."""
        servidor = _servidor(url)
        if self.fora_do_ar(url):
            raise ErroIntegracao(f"{self.servico}: {servidor} fora do ar nesta rodada (disjuntor aberto)",
                                 servico=self.servico, temporario=True)
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("headers", {}).setdefault("User-Agent", NAVEGADOR)
        ultimo: ErroIntegracao | None = None
        for tentativa in range(1, self.tentativas + 1):
            try:
                resp = self.sessao.request(metodo, url, **kwargs)
            except requests.ConnectTimeout:
                # não chegou a conectar: o pedido não foi enviado, pode repetir com segurança
                ultimo = ErroIntegracao(f"{self.servico}: sem resposta (ConnectTimeout)", servico=self.servico, temporario=True)
            except (requests.Timeout, requests.ConnectionError) as e:
                if not repetir:
                    self._falhou(servidor)
                    raise ErroIntegracao(f"{self.servico}: conexão perdida durante o envio ({type(e).__name__})",
                                         servico=self.servico, temporario=True, incerto=True) from None
                ultimo = ErroIntegracao(f"{self.servico}: sem resposta ({type(e).__name__})", servico=self.servico, temporario=True)
            except requests.RequestException as e:  # qualquer outra falha de rede: sem URL na mensagem
                raise ErroIntegracao(f"{self.servico}: falha de rede ({type(e).__name__})", servico=self.servico,
                                     temporario=False, incerto=not repetir) from None
            else:
                if resp.status_code < 400 or resp.status_code in ok_status:
                    self.falhas_seguidas[servidor] = 0
                    return resp
                temporario = resp.status_code in TEMPORARIOS
                ultimo = ErroIntegracao(f"{self.servico}: HTTP {resp.status_code} {resp.text[:300]}",
                                        servico=self.servico, temporario=temporario, status=resp.status_code,
                                        autenticacao=resp.status_code == 401, limite=resp.status_code == 429)
                if not temporario:
                    self.falhas_seguidas[servidor] = 0  # o servidor respondeu: está no ar
                    raise ultimo
                if not repetir and resp.status_code != 429:
                    # 5xx/408 num envio: o serviço pode ter publicado antes de falhar. Marca como INCERTO
                    # para o item nunca ser reenviado automaticamente (evita post duplicado).
                    ultimo.incerto = True
                    raise ultimo
            if tentativa < self.tentativas:
                self.dormir(self.espera_inicial * (2 ** (tentativa - 1)))
        assert ultimo is not None
        self._falhou(servidor)
        raise ultimo

    def _falhou(self, servidor: str) -> None:
        self.falhas_seguidas[servidor] = self.falhas_seguidas.get(servidor, 0) + 1

    def get(self, url, **kw): return self.requisitar("GET", url, **kw)
    def post(self, url, **kw): return self.requisitar("POST", url, **kw)
    def patch(self, url, **kw): return self.requisitar("PATCH", url, **kw)


def _servidor(url: str) -> str:
    return (urlparse(url).hostname or "?").lower()


# ------------------------------------------------------------------------ validação somente leitura
TELEGRAM_LEITURA = {"getMe", "getChat", "getChatMember", "getChatAdministrators"}
ALIEXPRESS_LEITURA = {"aliexpress.affiliate.product.query", "aliexpress.affiliate.productdetail.get",
                      "aliexpress.affiliate.order.list", "aliexpress.affiliate.category.get"}


def chamada_de_leitura(metodo: str, url: str, kwargs: dict, permitir_envio_imgbb: bool = False) -> bool:
    """True se a chamada só LÊ dados. Tudo que não está nesta lista é tratado como escrita."""
    metodo = metodo.upper()
    host = (urlparse(url).hostname or "").lower()
    caminho = urlparse(url).path
    if metodo in ("GET", "HEAD"):
        return True
    if metodo != "POST":
        return False
    if host == "api.telegram.org":
        return caminho.rsplit("/", 1)[-1] in TELEGRAM_LEITURA
    if host.endswith("shopee.com.br") and caminho.endswith("/graphql"):
        corpo = kwargs.get("data") or kwargs.get("json") or b""
        if isinstance(corpo, (bytes, bytearray)):
            corpo = corpo.decode("utf-8", "ignore")
        texto = corpo if isinstance(corpo, str) else str(corpo)
        try:
            import json as _json
            consulta = (_json.loads(texto) or {}).get("query", "") if texto.strip().startswith("{") else texto
        except ValueError:
            consulta = texto
        return not str(consulta).lstrip().lower().startswith("mutation")
    if host == "api-sg.aliexpress.com":   # gateway oficial: só os métodos de LEITURA do programa de afiliados
        corpo = kwargs.get("data") or {}
        return isinstance(corpo, dict) and corpo.get("method") in ALIEXPRESS_LEITURA
    if caminho.endswith(("/searchItems", "/getItems")):   # Amazon Creators API: consultas
        return True
    corpo = kwargs.get("data")
    if isinstance(corpo, dict) and corpo.get("grant_type") == "client_credentials":   # token OAuth (não altera nada)
        return True
    if host == "api.imgbb.com":
        return permitir_envio_imgbb  # não existe consulta de leitura no ImgBB: só um envio de teste autorizado
    return False


class ClienteHTTPSomenteLeitura(ClienteHTTP):
    """Cliente usado pelo `validar-integracoes`: qualquer chamada de escrita (publicar, criar mídia,
    gerar link, editar mensagem) é BLOQUEADA antes de sair do bot."""

    def __init__(self, servico: str, *, permitir_envio_imgbb: bool = False, **kw):
        super().__init__(servico, **kw)
        self.permitir_envio_imgbb = permitir_envio_imgbb
        self.chamadas: list[str] = []

    def requisitar(self, metodo, url, **kwargs):
        if not chamada_de_leitura(metodo, url, kwargs, self.permitir_envio_imgbb):
            caminho = urlparse(url).path.rsplit("/", 1)[-1]
            raise EscritaBloqueada(f"{self.servico}: chamada de escrita bloqueada no modo somente leitura "
                                   f"({metodo.upper()} {urlparse(url).hostname}/…/{caminho})")
        self.chamadas.append(f"{metodo.upper()} {urlparse(url).hostname}")
        return super().requisitar(metodo, url, **kwargs)
