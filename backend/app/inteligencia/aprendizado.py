"""Aprendizado de desempenho por categoria, horário, canal e faixa de preço.

Fonte: `vw_desempenho_publicacoes` (migration 019) — uma linha por publicação REAL bem-sucedida, com os
cliques únicos (os suspeitos ficam fora, em cliques_suspeitos) e as comissões atribuídas.

Regras
------
* Só dados reais. Publicação de demonstração não entra. Sem publicações: "sem dados".
* Atribuição de comissão: a venda é ligada ao produto pelo código do item no relatório oficial e à oferta
  publicada antes da venda; se a oferta saiu em vários canais, a comissão é dividida igualmente entre
  essas publicações (regra simples e auditável; não é atribuição por clique, porque o relatório da loja
  não diz em qual canal a pessoa clicou).
* Amostra mínima por segmento (padrão 20 publicações). Abaixo dela o segmento aparece como
  "dados insuficientes" e o fator fica NEUTRO (1,0) na decisão.
* Encolhimento (média bayesiana): estimativa = (soma do segmento + k × média geral) / (n + k), com k igual à
  amostra mínima. Segmento com poucos dados fica perto da média geral; não salta por acaso.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

DIMENSOES = ("categoria", "periodo", "canal", "faixa_preco")
FAIXAS = ((0, 50, "até R$ 50"), (50, 100, "R$ 50–100"), (100, 250, "R$ 100–250"), (250, 500, "R$ 250–500"),
          (500, math.inf, "acima de R$ 500"))
PERIODOS = ((0, 6, "madrugada (0h–6h)"), (6, 12, "manhã (6h–12h)"), (12, 18, "tarde (12h–18h)"), (18, 24, "noite (18h–24h)"))


def faixa_de_preco(preco: float | None) -> str | None:
    if preco is None or preco <= 0:
        return None
    return next(nome for de, ate, nome in FAIXAS if de <= preco < ate)


def periodo_do_dia(hora: int | None) -> str | None:
    if hora is None:
        return None
    return next(nome for de, ate, nome in PERIODOS if de <= hora < ate)


@dataclass
class Segmento:
    dimensao: str
    valor: str
    publicacoes: int
    cliques: int
    conversoes: float
    comissao: float
    ctr: float | None                  # cliques por publicação (bruto)
    conversao_por_clique: float | None
    receita_por_publicacao: float | None
    suficiente: bool
    fator_cliques: float = 1.0         # estimativa encolhida / média geral (1,0 = neutro)
    explicacao: str = ""


@dataclass
class Aprendizado:
    publicacoes: int
    cliques: int
    conversoes: float
    comissao: float
    ctr_geral: float | None
    conversao_por_clique: float | None
    amostra_minima: int
    segmentos: dict[str, list[Segmento]] = field(default_factory=dict)
    sem_dados: list[str] = field(default_factory=list)

    @property
    def tem_cliques(self) -> bool:
        return self.publicacoes >= self.amostra_minima and self.ctr_geral is not None

    def segmento(self, dimensao: str, valor: str | None) -> Segmento | None:
        return next((s for s in self.segmentos.get(dimensao, []) if s.valor == valor), None)

    def como_dict(self) -> dict:
        return asdict(self)


def _div(a, b):
    return a / b if b else None


def aprender(linhas: list[dict], amostra_minima: int = 20, min_conversoes: int = 10) -> Aprendizado:
    n = len(linhas)
    cliques = sum(int(l.get("cliques") or 0) for l in linhas)
    conv = sum(float(l.get("conversoes_atribuidas") or 0) for l in linhas)
    com = sum(float(l.get("comissao_atribuida") or 0) for l in linhas)
    ctr = _div(cliques, n)
    cpc = _div(conv, cliques) if conv >= min_conversoes else None
    ap = Aprendizado(n, cliques, round(conv, 2), round(com, 2), round(ctr, 4) if ctr is not None else None,
                     round(cpc, 5) if cpc is not None else None, amostra_minima)
    if n == 0:
        ap.sem_dados.append("Nenhuma publicação real registrada: não há o que aprender ainda")
        return ap
    if n < amostra_minima:
        ap.sem_dados.append(f"Só {n} publicação(ões) real(is); o aprendizado começa com {amostra_minima}")
    if conv < min_conversoes:
        ap.sem_dados.append(f"Só {conv:.0f} venda(s) atribuída(s); a taxa de conversão só é estimada com "
                            f"{min_conversoes} ou mais (receita esperada indisponível)")
    k = amostra_minima
    for dim in DIMENSOES:
        grupos: dict[str, list[dict]] = {}
        for l in linhas:
            v = l.get(dim)
            if v:
                grupos.setdefault(str(v), []).append(l)
        segs = []
        for valor, ls in sorted(grupos.items()):
            sn = len(ls)
            sc = sum(int(l.get("cliques") or 0) for l in ls)
            sv = sum(float(l.get("conversoes_atribuidas") or 0) for l in ls)
            sm = sum(float(l.get("comissao_atribuida") or 0) for l in ls)
            suficiente = sn >= amostra_minima
            fator = 1.0
            if ap.tem_cliques and ctr:
                est = (sc + k * ctr) / (sn + k)
                fator = round(est / ctr, 3)
            texto = (f"{valor}: {sn} publicações, {sc} cliques ({_div(sc, sn):.2f} por publicação)"
                     if sn else f"{valor}: sem publicações")
            if not suficiente:
                texto += f" — dados insuficientes (mínimo {amostra_minima}); fator neutro"
                fator = 1.0
            segs.append(Segmento(dim, valor, sn, sc, round(sv, 2), round(sm, 2),
                                 round(_div(sc, sn), 4) if sn else None,
                                 round(_div(sv, sc), 5) if sc and sv >= min_conversoes else None,
                                 round(_div(sm, sn), 4) if sn and sv >= min_conversoes else None,
                                 suficiente, fator, texto))
        ap.segmentos[dim] = segs
    return ap


def potencial(ap: Aprendizado, oferta: dict) -> tuple[float | None, list[str]]:
    """Fator de cliques esperado da oferta (1,0 = média) a partir dos segmentos com dados suficientes."""
    if not ap.tem_cliques:
        return None, ["potencial de conversão: sem histórico suficiente de cliques"]
    fatores, textos = [], []
    for dim in DIMENSOES:
        s = ap.segmento(dim, oferta.get(dim))
        if s and s.suficiente:
            fatores.append(s.fator_cliques)
            textos.append(f"{dim.replace('_', ' ')} \"{s.valor}\": {s.fator_cliques:.2f}× a média de cliques")
    if not fatores:
        return None, ["potencial de conversão: nenhum segmento desta oferta tem dados suficientes"]
    return round(math.prod(fatores) ** (1 / len(fatores)), 3), textos


def receita_esperada(ap: Aprendizado, preco: float | None, comissao_pct: float | None,
                     fator: float | None) -> tuple[float | None, str]:
    """R$ esperado por publicação = preço × comissão × conversão por clique × cliques por publicação × fator."""
    if not preco or comissao_pct is None:
        return None, "receita esperada: sem preço ou sem comissão oficial"
    if ap.conversao_por_clique is None or not ap.tem_cliques:
        return None, "receita esperada: vendas atribuídas insuficientes para estimar a conversão"
    cliques = ap.ctr_geral * (fator or 1.0)
    valor = preco * comissao_pct / 100 * ap.conversao_por_clique * cliques
    return round(valor, 4), (f"receita esperada ≈ {_reais(valor)} por publicação ({cliques:.1f} cliques × "
                             f"{ap.conversao_por_clique * 100:.2f}% de conversão × comissão de "
                             f"{_reais(preco * comissao_pct / 100)} por venda)")


def _reais(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
