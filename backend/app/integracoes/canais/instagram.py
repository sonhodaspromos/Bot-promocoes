"""Instagram (feed e stories) · Instagram Graph API, login do Instagram (conta profissional).

Limitações da própria API: não publica figurinha (sticker) de link nos stories e links na
legenda do feed não são clicáveis. A imagem precisa estar numa URL pública (ver imgbb.py).
"""
from __future__ import annotations

import time

from ...erros import ErroIntegracao
from ...cliente_http import ClienteHTTP
from ...modelos import ConteudoPublicacao
from ..base import REAL, CanalPublicacao, HospedagemImagem

API = "https://graph.instagram.com/v25.0"


class InstagramCanal(CanalPublicacao):
    modo = REAL

    def __init__(self, tipo: str, token: str, user_id: str, hospedagem: HospedagemImagem,
                 http: ClienteHTTP | None = None, dormir=time.sleep, espera_max: int = 20):
        if tipo not in ("feed", "story"):
            raise ValueError("tipo deve ser feed ou story")
        self.tipo = tipo
        self.codigo = f"instagram_{tipo}"
        self.token, self.user_id, self.hospedagem = token, user_id, hospedagem
        self.http = http or ClienteHTTP("instagram")
        self.dormir, self.espera_max = dormir, espera_max

    def _req(self, metodo, caminho, repetir=True, **params):
        params["access_token"] = self.token
        kw = {"data": params} if metodo == "POST" else {"params": params}
        try:
            return self.http.requisitar(metodo, f"{API}/{caminho}", repetir=repetir, **kw).json()
        except ErroIntegracao as e:
            raise _classificar(e) from None

    def cota_restante(self) -> int | None:
        """Quantas publicações a API ainda aceita nas próximas 24 h (None se não deu para consultar)."""
        try:
            dados = self._req("GET", f"{self.user_id}/content_publishing_limit", fields="quota_usage,config")
            item = (dados.get("data") or [{}])[0]
            return int((item.get("config") or {}).get("quota_total", 50)) - int(item.get("quota_usage", 0))
        except (ErroIntegracao, ValueError, TypeError, IndexError):
            return None

    def publicar(self, conteudo: ConteudoPublicacao) -> str:
        imagem = conteudo.imagem_story if self.tipo == "story" else conteudo.imagem
        if not imagem:
            raise ErroIntegracao("Instagram exige imagem", servico="instagram")
        params = {"image_url": self.hospedagem.enviar(imagem)}
        if self.tipo == "story":
            params["media_type"] = "STORIES"
        else:
            params["caption"] = conteudo.legenda[:2200]
        container = self._req("POST", f"{self.user_id}/media", **params)["id"]
        for _ in range(self.espera_max):
            status = self._req("GET", container, fields="status_code").get("status_code")
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                raise ErroIntegracao(f"Instagram não processou a imagem ({status})", servico="instagram")
            self.dormir(3)
        else:
            raise ErroIntegracao("Instagram demorou demais para processar", servico="instagram", temporario=True)
        # publicar NÃO é repetido automaticamente: um timeout aqui pode já ter publicado
        return str(self._req("POST", f"{self.user_id}/media_publish", repetir=False, creation_id=container)["id"])

    def testar(self):
        eu = self._req("GET", "me", fields="username,account_type")
        return f"{self.codigo}: REAL ✔ conta @{eu.get('username')} ({eu.get('account_type')})"


LIMITE = {4, 9, 17, 32, 613}          # códigos de excesso de chamadas / cota de publicação
AUTENTICACAO = {102, 190, 10, 200}    # token inválido/vencido ou permissão retirada


def _classificar(e: ErroIntegracao) -> ErroIntegracao:
    """Lê o erro da Graph API ({"error": {"code": 190, ...}}) e marca token vencido ou limite."""
    import json as _json
    codigo = sub = None
    msg = str(e)
    try:
        corpo = _json.loads(msg.split(" ", 3)[3]) if msg.count(" ") >= 3 else {}
        erro = corpo.get("error") or {}
        codigo, sub, msg_api = erro.get("code"), erro.get("error_subcode"), erro.get("message")
        if msg_api:
            msg = f"instagram: {msg_api} (código {codigo})"
    except (ValueError, AttributeError, IndexError):
        pass
    e.args = (msg,)
    if codigo in AUTENTICACAO or e.status == 401:
        e.autenticacao = True
    if codigo in LIMITE or sub == 2207042 or e.status == 429:
        e.limite, e.esperar_min = True, 60
    return e
