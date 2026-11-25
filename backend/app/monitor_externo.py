"""Monitoramento externo (ex.: healthchecks.io, Better Stack, UptimeRobot heartbeat).

Defina o segredo HEALTHCHECK_URL (https). O bot avisa no início (/start), no sucesso (URL pura)
e na falha (/fail). Se nenhum aviso chegar no prazo configurado lá (ex.: 30 min), o serviço
externo manda alerta por e-mail/Telegram: cobre "o bot parou de rodar" (GitHub desligou o cron,
projeto do Supabase pausado, segredo removido), que o próprio bot não consegue avisar.
Falha no aviso nunca derruba a rodada.
"""
from __future__ import annotations

from urllib.parse import urlparse

import requests


class Batimento:
    def __init__(self, url: str | None, http=None, timeout: float = 5):
        u = (url or "").strip().rstrip("/")
        self.url = u if u and urlparse(u).scheme == "https" and urlparse(u).hostname else None
        self.http, self.timeout = http or requests, timeout

    def _enviar(self, sufixo: str, texto: str = "") -> bool:
        if not self.url:
            return False
        try:
            self.http.post(self.url + sufixo, data=texto[:10000].encode(), timeout=self.timeout)
            return True
        except Exception:
            return False

    def inicio(self):
        return self._enviar("/start")

    def sucesso(self, resumo: str = ""):
        return self._enviar("", resumo)

    def falha(self, resumo: str = ""):
        return self._enviar("/fail", resumo)
