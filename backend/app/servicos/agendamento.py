"""Agendamento inteligente de publicações.

Para cada canal, o próximo horário é escolhido assim:
  1. nunca antes do último post agendado no canal + `agendamento.min_intervalo_canal_min` (padrão 20 min);
  2. sempre dentro do horário de publicação (publicacao.horario_inicio–horario_fim, fuso do bot);
  3. com aprendizado suficiente por horário (amostra mínima), prefere o 1º período com desempenho
     ACIMA da média nas próximas 24 h; sem dados, distribui em intervalos regulares (e diz isso).
Ofertas manuais continuam saindo na hora (não são reagendadas).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..inteligencia.aprendizado import Aprendizado, periodo_do_dia
from .comum import fuso, num


@dataclass
class Horario:
    quando: datetime
    explicacao: str


def proximo_horario(cfg: dict, agora: datetime, ultimo_no_canal: datetime | None, ap: Aprendizado | None) -> Horario:
    tz = fuso(cfg)
    intervalo = timedelta(minutes=float(cfg.get("agendamento.min_intervalo_canal_min") or 20))
    inicio_h, fim_h = int(num(cfg, "publicacao.horario_inicio")), int(num(cfg, "publicacao.horario_fim"))
    base = max(agora, (ultimo_no_canal + intervalo) if ultimo_no_canal else agora)

    def dentro(t):
        h = t.astimezone(tz).hour
        return inicio_h <= h < fim_h

    def ajustar(t):
        passos = 0
        while not dentro(t) and passos < 200:
            t += timedelta(minutes=15)
            passos += 1
        return t

    candidato = ajustar(base)
    if ap and ap.tem_cliques:
        bons = {s.valor for s in ap.segmentos.get("periodo", []) if s.suficiente and s.fator_cliques > 1.0}
        if bons:
            t = candidato
            limite = candidato + timedelta(hours=24)
            while t <= limite:
                if dentro(t) and periodo_do_dia(t.astimezone(tz).hour) in bons:
                    return Horario(t, f"período de melhor desempenho ({periodo_do_dia(t.astimezone(tz).hour)})")
                t += timedelta(minutes=15)
        return Horario(candidato, "sem período acima da média com dados suficientes: próximo horário livre")
    return Horario(candidato, "sem dados suficientes de horário: distribuição em intervalos regulares")
