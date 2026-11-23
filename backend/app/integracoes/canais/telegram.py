"""Canal do Telegram · Bot API oficial (https://core.telegram.org/bots/api)."""
from __future__ import annotations

import html
import json

from ...erros import ErroIntegracao
from ...cliente_http import ClienteHTTP
from ...modelos import ConteudoPublicacao
from ..base import REAL, CanalPublicacao

API = "https://api.telegram.org/bot{token}/{metodo}"


class TelegramCanal(CanalPublicacao):
    codigo = "telegram"
    modo = REAL

    def __init__(self, token: str, chat: str, http: ClienteHTTP | None = None):
        self.token, self.chat = token, chat
        self.http = http or ClienteHTTP("telegram")

    def _chamar(self, metodo: str, repetir: bool = True, **kwargs) -> dict:
        try:
            resp = self.http.post(API.format(token=self.token, metodo=metodo), ok_status=(400, 401, 403, 429),
                                  repetir=repetir, **kwargs)
            dados = resp.json()
        except ValueError:
            raise ErroIntegracao(f"Telegram ({metodo}): resposta inválida", servico="telegram", incerto=not repetir) from None
        if not dados.get("ok"):
            codigo = int(dados.get("error_code") or resp.status_code)
            espera = int((dados.get("parameters") or {}).get("retry_after") or 60)
            raise ErroIntegracao(
                f"Telegram ({metodo}): {dados.get('description', 'erro')}", servico="telegram", status=codigo,
                # 401 = token inválido/revogado; 403 = bot removido do canal ou sem permissão de postar
                autenticacao=codigo in (401, 403), limite=codigo == 429, esperar_min=max(1, -(-espera // 60)))
        return dados["result"]

    def publicar(self, conteudo: ConteudoPublicacao) -> str:
        dados = {"chat_id": self.chat, "caption": conteudo.legenda[:1024], "parse_mode": "HTML"}
        if conteudo.link_rastreado:  # cupom sem link só mostra o código; oferta sempre tem link (checado antes)
            if not conteudo.link_rastreado.startswith("https://"):
                raise ErroIntegracao("Link inválido para publicação", servico="telegram")
            texto_botao = "🎟 Pegar cupom" if getattr(conteudo, "promocao_id", 1) == 0 else "🛒 Comprar agora"
            dados["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": texto_botao, "url": conteudo.link_rastreado}]]})
        if conteudo.imagem:
            r = self._chamar("sendPhoto", repetir=False, data=dados, files={"photo": ("oferta.jpg", conteudo.imagem, "image/jpeg")})
        else:
            dados["text"] = dados.pop("caption")
            r = self._chamar("sendMessage", repetir=False, data=dados)
        return str(r["message_id"])

    def encerrar(self, id_mensagem, titulo, motivo):
        texto = (f"❌ <b>OFERTA ENCERRADA</b> ({html.escape(motivo)})\n\n<s>{html.escape(titulo)}</s>\n\n"
                 "Fique de olho no canal para as próximas promoções!")
        self._chamar("editMessageCaption", data={"chat_id": self.chat, "message_id": id_mensagem, "caption": texto,
                                                 "parse_mode": "HTML", "reply_markup": json.dumps({"inline_keyboard": []})})

    def testar(self):
        eu = self._chamar("getMe")
        chat = self._chamar("getChat", data={"chat_id": self.chat})
        return f"telegram: REAL ✔ bot @{eu.get('username')} no canal {chat.get('title') or self.chat}"
