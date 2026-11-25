"""Histórico de preços (7, 30 e 90 dias), menor preço histórico e detecção de desconto artificial.

Como o histórico é gravado
--------------------------
`historico_precos` recebe uma linha a cada MUDANÇA de preço ou de disponibilidade (não uma por dia).
Por isso o preço é uma função em degraus: entre duas linhas, vale o preço da linha anterior. Para
saber o preço no começo de uma janela é preciso o último registro ANTES dela (a "âncora"); o banco
o devolve pela RPC `historico_precos_janela` (migration 019).

Regras (nada é inventado)
-------------------------
* Só entram períodos em que o produto estava DISPONÍVEL (preço de produto esgotado não é preço).
* Cobertura = quanto da janela o bot realmente observou. Abaixo de `cobertura_minima_pct` (padrão 80%)
  a janela é "insuficiente": mínimo/máximo aparecem, mas nenhuma afirmação ("menor preço em 30 dias")
  é feita sobre ela.
* Menor preço histórico = menor preço observado desde o 1º registro (no máximo 90 dias de consulta),
  sempre dito junto com o período monitorado.
* Desconto artificial (só com histórico suficiente; sem dados, a análise diz "não dá para avaliar"):
    1. "preço de" acima de tudo que foi observado na janela de 90 dias;
    2. preço SUBIU recentemente (últimos 30 dias) acima do típico de 90 dias e o "desconto" é calculado
       sobre o preço inflado, sem o preço atual ficar abaixo do típico ("sobe e desce");
    3. preço atual acima do menor preço dos últimos 30 dias por mais que a tolerância ("já esteve mais barato").
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

JANELAS = (7, 30, 60, 90)


@dataclass
class Registro:
    preco: float
    quando: datetime
    disponivel: bool = True


@dataclass
class ResumoJanela:
    dias: int
    suficiente: bool
    cobertura_pct: float          # % da janela observada pelo bot
    registros: int                # mudanças de preço dentro da janela
    minimo: float | None = None
    maximo: float | None = None
    medio: float | None = None    # média ponderada pelo tempo em que cada preço vigorou
    explicacao: str = ""


@dataclass
class ResumoPrecos:
    janelas: dict[int, ResumoJanela]
    monitorado_desde: datetime | None
    dias_monitorados: float
    menor_preco: float | None
    menor_preco_em: datetime | None
    preco_atual_e_menor: bool | None      # None = não dá para afirmar
    desconto_artificial: list[str] = field(default_factory=list)
    artificial_bloqueante: bool = False   # "sobe e desce": nem o histórico salva o desconto
    sinais: list[str] = field(default_factory=list)          # observações (não bloqueiam)
    sem_dados: list[str] = field(default_factory=list)       # o que não pôde ser avaliado e por quê

    def como_dict(self) -> dict:
        d = asdict(self)
        d["janelas"] = {str(k): asdict(v) for k, v in self.janelas.items()}
        d["monitorado_desde"] = self.monitorado_desde.isoformat() if self.monitorado_desde else None
        d["menor_preco_em"] = self.menor_preco_em.isoformat() if self.menor_preco_em else None
        return d


def _reais(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _degraus(registros: list[Registro], inicio: datetime, fim: datetime):
    """Trechos (preço, disponível, de, até) que valeram entre inicio e fim. Antes do 1º registro: nada."""
    ordem = sorted((r for r in registros if r.preco and r.preco > 0), key=lambda r: r.quando)
    trechos = []
    for i, r in enumerate(ordem):
        de = max(r.quando, inicio)
        ate = min(ordem[i + 1].quando if i + 1 < len(ordem) else fim, fim)
        if ate > de:
            trechos.append((r.preco, r.disponivel, de, ate))
    return trechos


def resumir_janela(registros: list[Registro], agora: datetime, dias: int, cobertura_minima_pct: float = 80) -> ResumoJanela:
    inicio = agora - timedelta(days=dias)
    trechos = _degraus(registros, inicio, agora)
    disponiveis = [t for t in trechos if t[1]]
    total = (agora - inicio).total_seconds()
    observado = sum((t[3] - t[2]).total_seconds() for t in trechos)
    cobertura = round(100 * observado / total, 1) if total else 0.0
    mudancas = sum(1 for r in registros if inicio <= r.quando <= agora)
    if not disponiveis:
        return ResumoJanela(dias, False, cobertura, mudancas,
                            explicacao=f"Sem preço observado com o produto disponível nos últimos {dias} dias")
    seg = sum((t[3] - t[2]).total_seconds() for t in disponiveis)
    medio = sum(t[0] * (t[3] - t[2]).total_seconds() for t in disponiveis) / seg if seg else None
    suficiente = cobertura >= cobertura_minima_pct
    minimo, maximo = min(t[0] for t in disponiveis), max(t[0] for t in disponiveis)
    texto = (f"{dias} dias: mínimo {_reais(minimo)}, máximo {_reais(maximo)}, médio {_reais(medio)} "
             f"({cobertura:.0f}% do período observado)")
    if not suficiente:
        texto += f" — histórico insuficiente (mínimo {cobertura_minima_pct:.0f}% do período): sem conclusões"
    return ResumoJanela(dias, suficiente, cobertura, mudancas, round(minimo, 2), round(maximo, 2),
                        round(medio, 2) if medio else None, texto)


def _media_periodo(registros, inicio, fim, cobertura_minima_pct) -> float | None:
    """Preço típico (média ponderada pelo tempo, produto disponível) entre inicio e fim; None se mal coberto."""
    trechos = _degraus(registros, inicio, fim)
    total = (fim - inicio).total_seconds()
    if not total or sum((t[3] - t[2]).total_seconds() for t in trechos) / total * 100 < cobertura_minima_pct:
        return None
    disp = [t for t in trechos if t[1]]
    seg = sum((t[3] - t[2]).total_seconds() for t in disp)
    return sum(t[0] * (t[3] - t[2]).total_seconds() for t in disp) / seg if seg else None


def resumir(registros: list[Registro], agora: datetime, preco_atual: float | None,
            preco_referencia: float | None = None, *, cobertura_minima_pct: float = 80,
            tolerancia_pct: float = 1.0, alta_artificial_pct: float = 15.0) -> ResumoPrecos:
    janelas = {d: resumir_janela(registros, agora, d, cobertura_minima_pct) for d in JANELAS}
    validos = sorted((r for r in registros if r.preco and r.preco > 0), key=lambda r: r.quando)
    desde = validos[0].quando if validos else None
    dias_mon = round((agora - desde).total_seconds() / 86400, 1) if desde else 0.0
    sem_dados: list[str] = []
    sinais: list[str] = []
    artificial: list[str] = []
    bloqueante = False

    # ---- menor preço histórico (observado, com o produto disponível)
    disponiveis = [r for r in validos if r.disponivel]
    menor = min(disponiveis, key=lambda r: (r.preco, r.quando)) if disponiveis else None
    e_menor = None
    if menor is None:
        sem_dados.append("menor preço histórico: nenhum preço observado com o produto disponível")
    elif preco_atual:
        if dias_mon < 7:
            sem_dados.append(f"menor preço histórico: produto monitorado há só {dias_mon:.0f} dia(s) "
                             "(mínimo 7 para afirmar)")
        else:
            e_menor = preco_atual <= menor.preco * (1 + tolerancia_pct / 100)
            if e_menor:
                sinais.append(f"Menor preço desde que o bot monitora o produto ({dias_mon:.0f} dias, desde "
                              f"{desde.strftime('%d/%m/%Y')})")
            else:
                sinais.append(f"Já esteve mais barato: {_reais(menor.preco)} em {menor.quando.strftime('%d/%m/%Y')}")

    # ---- desconto artificial (só com histórico suficiente)
    j30, j90 = janelas[30], janelas[90]
    if not j90.suficiente and not j30.suficiente:
        sem_dados.append("desconto artificial: histórico de 30/90 dias insuficiente para avaliar")
    if preco_referencia and preco_atual and preco_referencia > preco_atual:
        base = j90 if j90.suficiente else (j30 if j30.suficiente else None)
        if base and base.maximo and preco_referencia > base.maximo * (1 + tolerancia_pct / 100 + 0.04):
            artificial.append(f"\"Preço de\" {_reais(preco_referencia)} nunca foi praticado nos últimos {base.dias} dias "
                              f"(maior preço observado: {_reais(base.maximo)})")
    tipico = _media_periodo(registros, agora - timedelta(days=90), agora - timedelta(days=30), cobertura_minima_pct)
    if tipico and j30.maximo and preco_atual and preco_referencia:
        subiu = j30.maximo >= tipico * (1 + alta_artificial_pct / 100)
        sobre_inflado = preco_referencia >= j30.maximo * 0.97
        nao_abaixo_tipico = preco_atual >= tipico * (1 - tolerancia_pct / 100)
        if subiu and sobre_inflado and nao_abaixo_tipico:
            bloqueante = True
            artificial.append(f"Preço subiu para {_reais(j30.maximo)} nos últimos 30 dias (típico entre 90 e 30 dias "
                              f"atrás: {_reais(tipico)}) e o desconto é calculado sobre o preço inflado; o preço atual "
                              f"({_reais(preco_atual)}) não está abaixo do típico")
    if j30.suficiente and j30.minimo and preco_atual and preco_atual > j30.minimo * (1 + max(tolerancia_pct, 5) / 100):
        sinais.append(f"Preço atual acima do menor dos últimos 30 dias ({_reais(j30.minimo)})")

    return ResumoPrecos(janelas, desde, dias_mon, round(menor.preco, 2) if menor else None,
                        menor.quando if menor else None, e_menor, artificial, bloqueante, sinais, sem_dados)
