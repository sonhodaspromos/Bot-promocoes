"""Saturação da audiência.

Dois sinais, ambos com dados reais e amostra mínima (sem amostra = "sem dados", nada é limitado):

1. Queda de engajamento por canal: cliques nas 2 primeiras horas de cada publicação (`cliques_2h`).
   Recente = publicações das últimas 24 h que já têm 2 h de vida; base = publicações de 2 a 14 dias atrás.
   Se a média recente cair mais que `queda_pct` (padrão 50%) em relação à base, com pelo menos
   `amostra_recente` (10) e `amostra_base` (30) publicações, o canal está SATURADO:
   nesta rodada, só ofertas "excelentes" entram nele.
2. Repetição de categoria: mais de `max_categoria` (3) publicações da mesma categoria nas últimas
   `janela_categoria_h` (6) horas, somando os canais → a categoria espera.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta


@dataclass
class EstadoCanal:
    canal: str
    saturado: bool
    media_recente: float | None
    media_base: float | None
    publicacoes_recentes: int
    publicacoes_base: int
    explicacao: str


@dataclass
class Saturacao:
    canais: dict[str, EstadoCanal] = field(default_factory=dict)
    categorias_cheias: dict[str, int] = field(default_factory=dict)
    janela_categoria_h: float = 6
    max_categoria: int = 3

    def canal_saturado(self, canal: str) -> bool:
        e = self.canais.get(canal)
        return bool(e and e.saturado)

    def como_dict(self) -> dict:
        return asdict(self)


def _quando(v) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if isinstance(v, str) else v


def avaliar(publicacoes: list[dict], agora: datetime, *, queda_pct: float = 50, amostra_recente: int = 10,
            amostra_base: int = 30, max_categoria: int = 3, janela_categoria_h: float = 6) -> Saturacao:
    sat = Saturacao(janela_categoria_h=janela_categoria_h, max_categoria=max_categoria)
    por_canal: dict[str, list[dict]] = {}
    for p in publicacoes:
        por_canal.setdefault(p.get("canal") or "?", []).append(p)
    for canal, ps in por_canal.items():
        recentes = [p for p in ps if agora - timedelta(hours=24) <= _quando(p["publicado_em"]) <= agora - timedelta(hours=2)]
        base = [p for p in ps if agora - timedelta(days=14) <= _quando(p["publicado_em"]) < agora - timedelta(days=2)]
        mr = sum(int(p.get("cliques_2h") or 0) for p in recentes) / len(recentes) if recentes else None
        mb = sum(int(p.get("cliques_2h") or 0) for p in base) / len(base) if base else None
        if len(recentes) < amostra_recente or len(base) < amostra_base or not mb:
            texto = (f"{canal}: dados insuficientes para medir saturação ({len(recentes)} publicações recentes de "
                     f"{amostra_recente}, {len(base)} de base de {amostra_base})")
            sat.canais[canal] = EstadoCanal(canal, False, mr, mb, len(recentes), len(base), texto)
            continue
        queda = (1 - mr / mb) * 100
        saturado = queda >= queda_pct
        texto = (f"{canal}: {mr:.1f} cliques nas 2 primeiras horas (últimas 24 h) contra {mb:.1f} na base de 2–14 dias "
                 f"({'queda' if queda > 0 else 'alta'} de {abs(queda):.0f}%)")
        texto += " — SATURADO: só ofertas excelentes nesta rodada" if saturado else " — normal"
        sat.canais[canal] = EstadoCanal(canal, saturado, round(mr, 2), round(mb, 2), len(recentes), len(base), texto)
    limite = agora - timedelta(hours=janela_categoria_h)
    contagem: dict[str, int] = {}
    for p in publicacoes:
        if p.get("categoria") and _quando(p["publicado_em"]) >= limite:
            contagem[p["categoria"]] = contagem.get(p["categoria"], 0) + 1
    sat.categorias_cheias = {c: n for c, n in contagem.items() if n >= max_categoria}
    return sat
