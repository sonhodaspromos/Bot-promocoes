"""Feature flags dos módulos novos (expansão modular).

* Cada módulo é ligado pela configuração `recursos.<nome>` no banco (painel → Ajustes). Padrão: DESLIGADO.
* Botão de desligar de emergência por ambiente: RECURSOS_DESLIGADOS=vitrine,whatsapp (vale sobre o banco).
* Integração externa (Amazon, AliExpress, WhatsApp) só funciona de verdade quando, ALÉM da flag, tem
  credenciais e foi aprovada pelo `validar-integracoes` com as credenciais reais. Ligar a flag não aprova nada.
"""
from __future__ import annotations

import os

RECURSOS = ("fonte_amazon", "fonte_aliexpress", "whatsapp", "vitrine", "alertas_preco",
            "canais_por_categoria", "agendamento_inteligente", "workers")


def desligados_por_ambiente(env=None) -> set[str]:
    texto = (env if env is not None else os.environ).get("RECURSOS_DESLIGADOS", "")
    return {p.strip() for p in texto.split(",") if p.strip()}


def ligado(cfg: dict, nome: str, env=None) -> bool:
    if nome not in RECURSOS:
        raise ValueError(f"Recurso desconhecido: {nome}")
    if nome in desligados_por_ambiente(env):
        return False
    return cfg.get(f"recursos.{nome}") is True


def situacao(cfg: dict, env=None) -> dict[str, bool]:
    return {n: ligado(cfg, n, env) for n in RECURSOS}
