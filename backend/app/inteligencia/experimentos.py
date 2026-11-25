"""Testes A/B de artes e legendas.

* Um experimento ativo por vez (tipo "legenda" ou "arte"), cadastrado no painel.
* Sorteio determinístico e equilibrado: SHA-256 de (experimento, oferta, canal) → A ou B. A mesma oferta
  no mesmo canal sempre recebe a mesma variante (reenvio não troca a variante).
* Métrica: cliques únicos por publicação (os suspeitos não contam). Vendas por variante quase nunca
  têm volume para teste; aparecem só como informação.
* Teste estatístico: com N cliques no total, se A e B fossem iguais os cliques de A seguiriam uma
  Binomial(N, pubs_A / (pubs_A + pubs_B)). p-valor bilateral exato (aproximação normal acima de 1.000
  cliques). Vencedor só quando: cada variante tem ≥ `amostra_minima` publicações E p < 0,05.
  Caso contrário: "sem resultado conclusivo" — nunca se declara vencedor por diferença pequena.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass

VARIANTES = ("A", "B")
DESCRICAO = {
    ("legenda", "A"): "legenda atual (título primeiro)",
    ("legenda", "B"): "legenda com o preço em destaque antes do título",
    ("arte", "A"): "arte atual (cor principal da marca)",
    ("arte", "B"): "arte com fundo escuro",
}


def atribuir(experimento_id: int, promocao_id: int, canal: str) -> str:
    h = hashlib.sha256(f"{experimento_id}:{promocao_id}:{canal}".encode()).digest()
    return VARIANTES[h[0] % 2]


def _binomial_bilateral(x: int, n: int, p: float) -> float:
    if n == 0 or p <= 0 or p >= 1:
        return 1.0
    if n > 1000:
        media, dp = n * p, math.sqrt(n * p * (1 - p))
        z = abs(x - media) / dp if dp else 0
        return math.erfc(z / math.sqrt(2))
    def pmf(k):
        return math.comb(n, k) * p ** k * (1 - p) ** (n - k)
    px = pmf(x)
    return min(1.0, sum(pmf(k) for k in range(n + 1) if pmf(k) <= px * (1 + 1e-9)))


@dataclass
class ResultadoExperimento:
    experimento_id: int
    tipo: str
    publicacoes: dict[str, int]
    cliques: dict[str, int]
    cliques_por_publicacao: dict[str, float | None]
    vendas: dict[str, float]
    p_valor: float | None
    vencedor: str | None
    conclusivo: bool
    explicacao: str

    def como_dict(self) -> dict:
        return asdict(self)


def avaliar(experimento: dict, linhas: list[dict], amostra_minima: int = 30) -> ResultadoExperimento:
    """linhas: publicações do experimento com 'variante', 'cliques', 'conversoes_atribuidas'."""
    pubs = {v: 0 for v in VARIANTES}
    cli = {v: 0 for v in VARIANTES}
    ven = {v: 0.0 for v in VARIANTES}
    for l in linhas:
        v = l.get("variante")
        if v in pubs:
            pubs[v] += 1
            cli[v] += int(l.get("cliques") or 0)
            ven[v] += float(l.get("conversoes_atribuidas") or 0)
    taxa = {v: (round(cli[v] / pubs[v], 3) if pubs[v] else None) for v in VARIANTES}
    exp_id, tipo = experimento["id"], experimento.get("tipo", "?")
    falta = [v for v in VARIANTES if pubs[v] < amostra_minima]
    total = cli["A"] + cli["B"]
    p = None
    if pubs["A"] and pubs["B"]:
        p = round(_binomial_bilateral(cli["A"], total, pubs["A"] / (pubs["A"] + pubs["B"])), 4)
    if falta:
        texto = (f"Sem resultado conclusivo: amostra mínima de {amostra_minima} publicações por variante "
                 f"(A: {pubs['A']}, B: {pubs['B']})")
        return ResultadoExperimento(exp_id, tipo, pubs, cli, taxa, ven, p, None, False, texto)
    if p is None or p >= 0.05:
        texto = (f"Sem resultado conclusivo: A {taxa['A']} × B {taxa['B']} cliques por publicação "
                 f"(p = {p}); a diferença pode ser acaso")
        return ResultadoExperimento(exp_id, tipo, pubs, cli, taxa, ven, p, None, False, texto)
    vencedor = "A" if (taxa["A"] or 0) > (taxa["B"] or 0) else "B"
    texto = (f"Variante {vencedor} venceu ({DESCRICAO.get((tipo, vencedor), vencedor)}): "
             f"A {taxa['A']} × B {taxa['B']} cliques por publicação, p = {p}")
    return ResultadoExperimento(exp_id, tipo, pubs, cli, taxa, ven, p, vencedor, True, texto)
