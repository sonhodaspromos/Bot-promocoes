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
        """Envia a oferta a quem consentiu. Cada envio passa por UMA reserva atômica no banco
        (whatsapp_reservar_envio): descadastro, repetição, limite por pessoa e teto diário são conferidos
        com as linhas travadas, então dois workers simultâneos nunca estouram o orçamento nem o limite."""
        custo = float(cfg.get("whatsapp.custo_mensagem_usd") or 0.0625)
        teto = float(cfg.get("whatsapp.orcamento_diario_usd") or 0)
        max_pessoa = int(cfg.get("whatsapp.max_por_inscrito_dia") or 3)
        resumo = {"enviados": 0, "falhas": 0, "bloqueado_orcamento": False, "custo_usd": 0.0}
        link = f"{self.url_cliques}?l={int(oferta['link_id'])}&c=whatsapp"
        for p in self.repo.whatsapp_destinatarios(oferta.get("categoria_id"), max_pessoa, oferta["promocao_id"]):
            situacao = self.repo.whatsapp_reservar_envio(p["id"], oferta["promocao_id"], custo, teto, max_pessoa)
            if situacao == "orcamento":
                resumo["bloqueado_orcamento"] = True
                self.log.aviso("whatsapp", f"Teto diário de US$ {teto:.2f} atingido: envio interrompido")
                break
            if situacao != "ok":
                continue   # repetido, limite da pessoa ou descadastrado (conferido com a linha travada)
            try:
                self.cliente.enviar_template(p["telefone"], [oferta["titulo"][:200], reais(oferta["preco"]), link])
                resumo["enviados"] += 1
                resumo["custo_usd"] = round(resumo["custo_usd"] + custo, 4)
                self.repo.whatsapp_evento(p["id"], "envio", custo, f"oferta {oferta['promocao_id']}")
            except ErroIntegracao as e:
                resumo["falhas"] += 1
                if e.incerto:
                    # pode ter sido entregue: o custo reservado fica e a reserva impede reenvio
                    resumo["custo_usd"] = round(resumo["custo_usd"] + custo, 4)
                else:
                    self.repo.orcamento_estornar("whatsapp", custo)   # recusa certa: não houve cobrança
                self.repo.whatsapp_evento(p["id"], "falha", custo if e.incerto else None, str(e)[:300])
        self._alertar_consumo()
        return resumo

    def _alertar_consumo(self) -> None:
        try:
            s = self.repo.orcamento_situacao("whatsapp")
        except Exception:  # noqa: BLE001 — alerta nunca derruba o envio
            return
        if s.get("alertar"):
            self.log.erro("whatsapp", f"Consumo do WhatsApp em {s['pct']:.0f}% do teto diário "
                                      f"(US$ {s['gasto_usd']:.2f} de US$ {s['teto_usd']:.2f})")


PALAVRAS_SAIR = {"sair", "parar", "stop", "cancelar", "descadastrar"}
PALAVRAS_ENTRAR = {"quero ofertas", "quero", "entrar", "sim quero"}
