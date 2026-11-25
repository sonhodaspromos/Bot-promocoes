"""Alertas personalizados de preço pelo bot do Telegram.

A própria pessoa inicia a conversa com o bot (ninguém recebe sem pedir):
  /start                      explica o serviço, os dados guardados e como sair
  /alerta fone bluetooth até 150   cria um alerta (termo + preço máximo opcional)
  /meusalertas                lista os alertas
  /apagar 2                   apaga o alerta nº 2
  /parar                      apaga TODOS os alertas e o identificador da conversa (LGPD)

Avisos: quando uma oferta REAL publicada nas últimas horas contém todas as palavras do termo e custa até o
preço máximo. Um aviso por alerta e oferta; no máximo `alertas.max_por_pessoa_dia` por pessoa.
Comandos lidos por getUpdates (sem webhook); o último update lido fica salvo para não repetir.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

from ..erros import ErroIntegracao
from .comum import agora_utc
from .textos import reais

AJUDA = ("Olá! Eu aviso quando aparecer oferta do que você procura.\n\n"
         "• /alerta fone bluetooth até 150 — cria um alerta\n• /meusalertas — lista os seus alertas\n"
         "• /apagar 2 — apaga o alerta nº 2\n• /parar — apaga tudo e para de receber\n\n"
         "Guardamos só o identificador desta conversa e os termos dos seus alertas, para enviar os avisos. "
         "Os avisos contêm links de afiliado (#publi).")


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def interpretar_alerta(texto: str) -> tuple[str, float | None] | None:
    """'/alerta fone bluetooth até 150,90' → ('fone bluetooth', 150.9). None se inválido."""
    corpo = re.sub(r"^/alerta(@\w+)?", "", texto.strip(), flags=re.I).strip()
    m = re.search(r"\s+(?:at[eé]|ate|<=?|por no m[aá]ximo)\s*r?\$?\s*([\d.]+(?:,\d{1,2})?)\s*$", corpo, flags=re.I)
    preco = None
    if m:
        preco = float(m.group(1).replace(".", "").replace(",", ".")) if "," in m.group(1) else float(m.group(1))
        corpo = corpo[:m.start()].strip()
    termo = re.sub(r"\s+", " ", corpo)[:60]
    if len(termo) < 3 or (preco is not None and preco <= 0):
        return None
    return termo, preco


def combina(termo: str, preco_max: float | None, titulo: str, preco: float) -> bool:
    palavras = [p for p in re.split(r"[^a-z0-9]+", _norm(termo)) if len(p) >= 2]
    alvo = _norm(titulo)
    return bool(palavras) and all(p in alvo for p in palavras) and (preco_max is None or preco <= preco_max)


class ServicoAlertas:
    def __init__(self, repo, bot, log, url_cliques: str, relogio=agora_utc):
        """bot: objeto com chamar(metodo, **dados) → dict (o TelegramCanal serve)."""
        self.repo, self.bot, self.log, self.url_cliques, self.relogio = repo, bot, log, url_cliques, relogio

    def _enviar(self, chat_id: int, texto: str) -> bool:
        try:
            self.bot.chamar("sendMessage", chat_id=chat_id, text=texto[:4000], disable_web_page_preview=True)
            return True
        except ErroIntegracao as e:
            if e.status == 403:   # a pessoa bloqueou o bot: apaga os dados dela
                self.repo.alertas_apagar_tudo(chat_id)
            self.log.aviso("alertas", "Não consegui enviar mensagem do alerta", erro=str(e))
            return False

    def processar_comandos(self, cfg: dict) -> int:
        offset = int(cfg.get("alertas.telegram_offset") or 0)
        updates = self.bot.chamar("getUpdates", offset=offset + 1 if offset else None, timeout=0,
                                  allowed_updates='["message"]') or []
        maior = offset
        for u in updates:
            maior = max(maior, int(u.get("update_id", 0)))
            msg = u.get("message") or {}
            chat = (msg.get("chat") or {})
            if chat.get("type") != "private" or not msg.get("text"):
                continue   # só conversa privada iniciada pela pessoa
            self._comando(int(chat["id"]), msg["text"].strip(), cfg)
        if maior != offset:
            self.repo.salvar_configuracao("alertas.telegram_offset", maior)
        return len(updates)

    def _comando(self, chat_id: int, texto: str, cfg: dict) -> None:
        cmd = texto.split()[0].lower().split("@")[0]
        if cmd == "/start" or cmd == "/ajuda":
            self._enviar(chat_id, AJUDA)
        elif cmd == "/alerta":
            r = interpretar_alerta(texto)
            if not r:
                self._enviar(chat_id, "Não entendi. Exemplo: /alerta fone bluetooth até 150")
                return
            ativos = self.repo.alertas_da_pessoa(chat_id)
            if len(ativos) >= int(cfg.get("alertas.max_alertas_por_pessoa") or 10):
                self._enviar(chat_id, "Você já tem o máximo de alertas. Apague algum com /apagar.")
                return
            self.repo.criar_alerta(chat_id, r[0], r[1])
            self._enviar(chat_id, f"✅ Alerta criado: {r[0]}" + (f" até {reais(r[1])}" if r[1] else ""))
        elif cmd == "/meusalertas":
            ativos = self.repo.alertas_da_pessoa(chat_id)
            self._enviar(chat_id, "\n".join(f"{i + 1}. {a['termo']}" + (f" até {reais(a['preco_maximo'])}" if a.get("preco_maximo") else "")
                                            for i, a in enumerate(ativos)) or "Você não tem alertas. Crie com /alerta.")
        elif cmd == "/apagar":
            ativos = self.repo.alertas_da_pessoa(chat_id)
            partes = texto.split()
            if len(partes) == 2 and partes[1].isdigit() and 1 <= int(partes[1]) <= len(ativos):
                self.repo.apagar_alerta(ativos[int(partes[1]) - 1]["id"])
                self._enviar(chat_id, "Alerta apagado.")
            else:
                self._enviar(chat_id, "Use /apagar e o número da lista de /meusalertas.")
        elif cmd in ("/parar", "/sair", "/stop"):
            self.repo.alertas_apagar_tudo(chat_id)
            self._enviar(chat_id, "Pronto: apaguei todos os seus alertas e não envio mais nada. Para voltar, use /start.")

    def avisar(self, cfg: dict, horas: float = 2) -> int:
        ofertas = self.repo.ofertas_publicadas_desde(self.relogio() - timedelta(hours=horas))
        if not ofertas:
            return 0
        limite = int(cfg.get("alertas.max_por_pessoa_dia") or 5)
        enviados = 0
        enviados_hoje = self.repo.avisos_hoje_por_pessoa()
        for a in self.repo.alertas_ativos():
            for o in ofertas:
                if enviados_hoje.get(a["telegram_chat_id"], 0) >= limite:
                    break
                if not combina(a["termo"], a.get("preco_maximo"), o["titulo"], float(o["preco"])):
                    continue
                if not self.repo.registrar_aviso(a["id"], o["promocao_id"]):
                    continue   # já avisado desta oferta
                link = f"{self.url_cliques}?l={int(o['link_id'])}&c=alerta"
                if self._enviar(a["telegram_chat_id"], f"🔔 Alerta \"{a['termo']}\": {o['titulo']}\n"
                                                         f"Por {reais(o['preco'])}\n{link}\n#publi · link de afiliado"):
                    enviados += 1
                    enviados_hoje[a["telegram_chat_id"]] = enviados_hoje.get(a["telegram_chat_id"], 0) + 1
        return enviados
