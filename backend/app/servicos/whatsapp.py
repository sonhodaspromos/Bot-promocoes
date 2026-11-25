"""WhatsApp Business (Cloud API oficial da Meta) — somente para quem consentiu.

* Opt-in: a pessoa manda "QUERO OFERTAS" para o número (webhook registra o consentimento com o texto aceito).
  Também aceita cadastro manual pelo dono com consentimento comprovado.
* Opt-out: "SAIR", "PARAR" ou "STOP" a qualquer momento → descadastro imediato; o telefone é apagado em 30 dias.
* Mensagens de marketing só por MODELO (template) aprovado pela Meta; cobradas por mensagem entregue.
* Teto de gasto diário (`whatsapp.orcamento_diario_usd`) e máximo de mensagens por pessoa por dia.
  Bateu o teto, para e registra 'bloqueio_orcamento'. Cada envio grava o custo estimado.
* Filtra por categorias escolhidas pela pessoa (vazio = todas).

NÃO validado com credenciais reais: precisa de número verificado, template aprovado e validar-integracoes.
"""
from __future__ import annotations

from ..cliente_http import ClienteHTTP
from ..erros import ErroIntegracao
from .comum import agora_utc
from .textos import reais

API = "https://graph.facebook.com/v21.0"


class ClienteWhatsApp:
    def __init__(self, token: str, phone_id: str, template: str, http: ClienteHTTP | None = None, idioma: str = "pt_BR"):
        self.token, self.phone_id, self.template, self.idioma = token, phone_id, template, idioma
        self.http = http or ClienteHTTP("whatsapp")

    def enviar_template(self, telefone: str, parametros: list[str]) -> str:
        r = self.http.post(f"{API}/{self.phone_id}/messages", repetir=False, headers={"Authorization": f"Bearer {self.token}"},
                           json={"messaging_product": "whatsapp", "to": telefone, "type": "template",
                                 "template": {"name": self.template, "language": {"code": self.idioma},
                                              "components": [{"type": "body", "parameters": [
                                                  {"type": "text", "text": p[:1000]} for p in parametros]}]}}).json()
        ids = r.get("messages") or []
        if not ids:
            raise ErroIntegracao(f"WhatsApp recusou: {(r.get('error') or {}).get('message') or r}", servico="whatsapp")
        return ids[0].get("id", "")

    def testar(self) -> str:
        r = self.http.get(f"{API}/{self.phone_id}", headers={"Authorization": f"Bearer {self.token}"},
                          params={"fields": "display_phone_number,verified_name,quality_rating"}).json()
        if not r.get("display_phone_number"):
            raise ErroIntegracao("WhatsApp: número não encontrado para este token", servico="whatsapp")
        return f"whatsapp: REAL ✔ número {r['display_phone_number']} ({r.get('verified_name')}), qualidade {r.get('quality_rating')}"


class ServicoWhatsApp:
    def __init__(self, repo, cliente: ClienteWhatsApp, log, url_cliques: str, relogio=agora_utc):
        self.repo, self.cliente, self.log, self.url_cliques, self.relogio = repo, cliente, log, url_cliques, relogio

    def enviar_oferta(self, oferta: dict, cfg: dict) -> dict:
        custo = float(cfg.get("whatsapp.custo_mensagem_usd") or 0.0625)
        teto = float(cfg.get("whatsapp.orcamento_diario_usd") or 0)
        max_pessoa = int(cfg.get("whatsapp.max_por_inscrito_dia") or 3)
        gasto = self.repo.whatsapp_gasto_hoje()
        resumo = {"enviados": 0, "falhas": 0, "bloqueado_orcamento": False, "custo_usd": 0.0}
        link = f"{self.url_cliques}?l={int(oferta['link_id'])}&c=whatsapp"
        for p in self.repo.whatsapp_destinatarios(oferta.get("categoria_id"), max_pessoa, oferta["promocao_id"]):
            if gasto + custo > teto:
                resumo["bloqueado_orcamento"] = True
                self.repo.whatsapp_evento(None, "bloqueio_orcamento", None, f"teto de US$ {teto:.2f} atingido")
                self.log.aviso("whatsapp", f"Teto diário de US$ {teto:.2f} atingido: envio interrompido")
                break
            if not self.repo.whatsapp_reservar(p["id"], oferta["promocao_id"]):
                continue   # já enviado (ou reservado) para esta pessoa
            try:
                self.cliente.enviar_template(p["telefone"], [oferta["titulo"][:200], reais(oferta["preco"]), link])
                gasto += custo
                resumo["enviados"] += 1
                resumo["custo_usd"] = round(resumo["custo_usd"] + custo, 4)
                self.repo.whatsapp_evento(p["id"], "envio", custo, f"oferta {oferta['promocao_id']}")
            except ErroIntegracao as e:
                resumo["falhas"] += 1
                # incerto: pode ter sido entregue → conta o custo e não reenvia (a reserva fica)
                if e.incerto:
                    gasto += custo
                self.repo.whatsapp_evento(p["id"], "falha", custo if e.incerto else None, str(e)[:300])
        return resumo


PALAVRAS_SAIR = {"sair", "parar", "stop", "cancelar", "descadastrar"}
PALAVRAS_ENTRAR = {"quero ofertas", "quero", "entrar", "sim quero"}
