"""Módulos da expansão (vitrine, alertas de preço, WhatsApp, canais extras), ligados por feature flag.

Cada módulo roda de duas formas:
  * dentro da rodada do bot (padrão), como uma etapa isolada: erro em um módulo não afeta os outros;
  * pela fila persistente de tarefas (recursos.workers ligado): a rodada só ENFILEIRA (com chave de
    idempotência por hora) e um worker independente executa (python -m app.main worker).
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from .integracoes.canais.demo import DemoCanal
from .integracoes.canais.telegram import TelegramCanal
from .recursos import ligado
from .servicos.alertas import ServicoAlertas
from .servicos.comum import agora_utc
from .servicos.vitrine import GeradorVitrine
from .servicos.whatsapp import ServicoWhatsApp

TIPOS = {"vitrine": "vitrine.gerar", "alertas_preco": "alertas.processar", "whatsapp": "whatsapp.enviar"}


class BotTelegram:
    """Adapta o TelegramCanal (mesmo token do canal) para conversas privadas dos alertas."""

    def __init__(self, canal: TelegramCanal):
        self.canal = canal

    def chamar(self, metodo: str, **dados):
        return self.canal._chamar(metodo, data={k: v for k, v in dados.items() if v is not None})


class Modulos:
    def __init__(self, repo, integ, log, url_supabase: str, ambiente=None, relogio=agora_utc):
        self.repo, self.integ, self.log, self.ambiente, self.relogio = repo, integ, log, ambiente, relogio
        self.url_cliques = url_supabase.rstrip("/") + "/functions/v1/r"

    @property
    def producao(self) -> bool:
        return bool(self.ambiente and self.ambiente.producao)

    # ------------------------------------------------------------------ canais extras por categoria
    def montar_canais_extras(self, cfg: dict) -> list[str]:
        """Cria os canais de Telegram extras cadastrados no banco (ex.: telegram_casa → @canalcasa)."""
        if not ligado(cfg, "canais_por_categoria"):
            return []
        principal = self.integ.canais.get("telegram")
        novos = []
        for c in self.repo.canais():
            if c.get("tipo") != "telegram" or c["codigo"] in self.integ.canais or not c.get("ativo"):
                continue
            if self.producao and isinstance(principal, TelegramCanal) and c.get("identificador_externo"):
                self.integ.canais[c["codigo"]] = TelegramCanal(principal.token, c["identificador_externo"], principal.http)
            else:
                self.integ.canais[c["codigo"]] = DemoCanal(c["codigo"])   # fora de produção nunca publica
            novos.append(c["codigo"])
        return novos

    # ------------------------------------------------------------------ execução (direta ou pela fila)
    def executar(self, cfg: dict) -> dict:
        resumo = {}
        for nome, tipo in TIPOS.items():
            if not ligado(cfg, nome):
                continue
            if ligado(cfg, "workers"):
                chave = f"{tipo}-{self.relogio().strftime('%Y-%m-%dT%H')}"
                resumo[nome] = {"enfileirada": self.repo.tarefa_enfileirar(tipo, {}, chave)}
            else:
                resumo[nome] = self.handlers(cfg)[tipo]({})
        return resumo

    def handlers(self, cfg: dict | None = None) -> dict:
        cfg_ = cfg if cfg is not None else None

        def config():
            return cfg_ if cfg_ is not None else self.repo.configuracoes()
        return {"vitrine.gerar": lambda p: self.vitrine(config()),
                "alertas.processar": lambda p: self.alertas(config()),
                "whatsapp.enviar": lambda p: self.whatsapp(config())}

    # ------------------------------------------------------------------ módulos
    def vitrine(self, cfg: dict) -> dict:
        if not ligado(cfg, "vitrine"):
            return {"pulado": "recurso desligado"}
        saida = Path(os.environ.get("VITRINE_SAIDA") or Path(__file__).resolve().parents[1] / "saida" / "vitrine")
        g = GeradorVitrine(str(cfg.get("vitrine.url_base") or "https://example.invalid"), self.url_cliques,
                           str(cfg.get("visual.nome_perfil") or "Ofertas"))
        r = g.gerar(self.repo.ofertas_vitrine(7), self.repo.cupons_vitrine(), saida, self.relogio())
        self.log.info("vitrine", f"Vitrine gerada: {r['ofertas']} ofertas, {r['paginas']} páginas", **r)
        return r

    def alertas(self, cfg: dict) -> dict:
        if not ligado(cfg, "alertas_preco"):
            return {"pulado": "recurso desligado"}
        canal = self.integ.canais.get("telegram")
        if not self.producao or not isinstance(canal, TelegramCanal):
            self.log.info("alertas", "Alertas de preço só funcionam em produção com o Telegram real")
            return {"pulado": "fora de produção ou sem Telegram real"}
        s = ServicoAlertas(self.repo, BotTelegram(canal), self.log, self.url_cliques, self.relogio)
        return {"comandos": s.processar_comandos(cfg), "avisos": s.avisar(cfg)}

    def whatsapp(self, cfg: dict) -> dict:
        if not ligado(cfg, "whatsapp"):
            return {"pulado": "recurso desligado"}
        if not self.producao or self.integ.whatsapp is None:
            self.log.info("whatsapp", "WhatsApp só envia em produção, com credenciais e INTEGRACOES_ATIVAS")
            return {"pulado": "fora de produção ou sem credenciais"}
        s = ServicoWhatsApp(self.repo, self.integ.whatsapp, self.log, self.url_cliques, self.relogio)
        total = {"enviados": 0, "falhas": 0, "custo_usd": 0.0, "bloqueado_orcamento": False}
        for o in self.repo.ofertas_publicadas_desde(self.relogio() - timedelta(hours=2)):
            r = s.enviar_oferta(o, cfg)
            for k in ("enviados", "falhas"):
                total[k] += r[k]
            total["custo_usd"] = round(total["custo_usd"] + r["custo_usd"], 4)
            if r["bloqueado_orcamento"]:
                total["bloqueado_orcamento"] = True
                break
        return total
