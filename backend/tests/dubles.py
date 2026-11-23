"""Dublês de teste: substituem a internet. Nenhum teste faz chamada real."""
from __future__ import annotations

import json as _json

import requests


class RespostaFalsa:
    def __init__(self, status=200, json=None, texto=None, url=None, headers=None):
        self.status_code = status
        self._json = json
        self.text = texto if texto is not None else (_json.dumps(json) if json is not None else "")
        self.url = url
        self.headers = headers or {}
        self.content = self.text.encode()

    def json(self):
        if self._json is None:
            return _json.loads(self.text or "null")
        return self._json

    def close(self):
        pass


class SessaoFalsa:
    """Responde a partir de uma lista de regras: (metodo, trecho_da_url) -> resposta ou função."""

    def __init__(self, regras=None):
        self.regras = list(regras or [])
        self.chamadas: list[dict] = []

    def quando(self, metodo, trecho, resposta):
        self.regras.append((metodo.upper(), trecho, resposta))
        return self

    def request(self, metodo, url, **kwargs):
        self.chamadas.append({"metodo": metodo.upper(), "url": url, **kwargs})
        resp = self._responder(metodo, url, **kwargs)
        if getattr(resp, "url", "") is None:  # como no requests: sem redirecionamento, url final = url pedida
            resp.url = url
        return resp

    def _responder(self, metodo, url, **kwargs):
        for m, trecho, resposta in self.regras:
            if m == metodo.upper() and trecho in url:
                if isinstance(resposta, Exception):
                    raise resposta
                if callable(resposta):
                    return resposta(metodo, url, **kwargs)
                if isinstance(resposta, list):  # sequência de respostas
                    item = resposta.pop(0) if len(resposta) > 1 else resposta[0]
                    if isinstance(item, Exception):
                        raise item
                    return item
                return resposta
        raise requests.ConnectionError(f"sem regra para {metodo} {url}")
