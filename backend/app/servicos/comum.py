"""Utilidades comuns aos serviços: leitura de configuração com padrão e hora local."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PADROES = {
    "publicacao.aprovacao_manual": False, "publicacao.horario_inicio": 0, "publicacao.horario_fim": 24,
    "publicacao.intervalo_minutos": 10, "publicacao.por_rodada": 2, "publicacao.max_por_dia": 60,
    "publicacao.feed_horario": 19, "filtros.desconto_minimo": 20, "filtros.nota_minima": 4.5,
    "filtros.repetir_apos_dias": 30, "monitor.ativo": True, "monitor.tolerancia_preco_pct": 5,
    "monitor.acompanhar_dias": 30, "monitor.verificar_links": True, "busca.palavras_por_execucao": 3,
    "busca.ofertas_por_palavra": 30, "sistema.fuso_horario": "America/Recife",
    "inteligencia.janela_repeticao_horas": 24, "inteligencia.verificar_imagem": True,
    "cupons.ativo": True, "cupons.max_stories_dia": 3, "cupons.publicar_telegram": True, "cupons.horario": 9,
    "cupons.buscar_campanhas": True, "cupons.sugerir_produtos": True, "cupons.max_sugestoes": 10,
}


def ajuste(cfg: dict, chave: str):
    valor = cfg.get(chave)
    return PADROES.get(chave) if valor is None else valor


def num(cfg: dict, chave: str) -> float:
    try:
        return float(ajuste(cfg, chave))
    except (TypeError, ValueError):
        return float(PADROES[chave])


def fuso(cfg: dict) -> ZoneInfo:
    try:
        return ZoneInfo(str(ajuste(cfg, "sistema.fuso_horario")))
    except Exception:
        return ZoneInfo("America/Recife")


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def inicio_do_dia(cfg: dict, agora: datetime) -> datetime:
    local = agora.astimezone(fuso(cfg))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def dentro_do_horario(cfg: dict, agora: datetime) -> bool:
    hora = agora.astimezone(fuso(cfg)).hour
    ini, fim = int(num(cfg, "publicacao.horario_inicio")), int(num(cfg, "publicacao.horario_fim"))
    return ini <= hora < fim if ini <= fim else (hora >= ini or hora < fim)
