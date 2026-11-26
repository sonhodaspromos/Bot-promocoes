"""Ranking e seleção das ofertas aprovadas pelo motor.

O motor (motor.py) continua sendo o FILTRO DE QUALIDADE: só ofertas "excelente" ou "boa" chegam aqui.
Este módulo decide a ORDEM e explica cada decisão. Fatores (0 a 1; ausente = "sem dados", fica fora da
conta e aparece na explicação):

  qualidade               nota do motor (0–100)                                       sempre
  desconto comprovado     desconto real ÷ meta, só se comprovado pelo histórico/cupom  senão 0 (dito)
  comissão                % LÍQUIDA ÷ meta: % oficial × (1 − taxa de cancelamento da loja nas comissões
                          decididas dos últimos 180 dias, com amostra mínima); sem amostra, a bruta (dito)
                                                                                       sem dados se não oficial
  vendedor                loja oficial = 1; vendedor comum = 0,5                       sem dados se a loja não informa
  estoque                 ≥ 10 un. = 1; 1–9 = 0,5 (risco de esgotar); 0 = recusa      sem dados se não informado
  potencial de conversão  fator de cliques do aprendizado (1,0 = média) ÷ 2            sem dados abaixo da amostra

Prioridade = média ponderada dos fatores com dados (pesos configuráveis).
Receita esperada (R$ por publicação) entra com `peso_receita` (padrão 30%) SOMENTE quando há dados
suficientes de conversão; é normalizada pela maior receita da rodada.

Qualidade nunca é sacrificada: a ordenação é por classificação primeiro (toda "excelente" vem antes de
toda "boa"); a receita só reordena DENTRO da mesma classificação.

Recusas (com motivo): estoque zerado; produto equivalente (confiança alta/média) mais barato em outro
marketplace; categoria saturada na janela. Equivalência de confiança baixa só gera aviso.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .aprendizado import Aprendizado, potencial, receita_esperada
from .equivalencia import ALTA, BAIXA, MEDIA, Equivalencia
from .saturacao import Saturacao

ORDEM_CLASSE = {"excelente": 1, "boa": 0}


@dataclass(frozen=True)
class PesosRanking:
    qualidade: float = 30
    desconto: float = 20
    comissao: float = 15
    vendedor: float = 10
    estoque: float = 5
    potencial: float = 20
    receita: float = 30          # % da prioridade final quando há receita esperada
    desconto_meta: float = 60
    comissao_meta: float = 10

    @classmethod
    def de_config(cls, cfg: dict) -> "PesosRanking":
        def n(chave, padrao):
            try:
                v = cfg.get(chave)
                return padrao if v is None else float(v)
            except (TypeError, ValueError):
                return padrao
        return cls(receita=max(0.0, min(n("inteligencia.peso_receita", 30), 70)),
                   desconto_meta=n("inteligencia.desconto_meta", 60), comissao_meta=n("inteligencia.comissao_meta", 10))


@dataclass
class Fator:
    nome: str
    nota: float | None
    peso: float
    explicacao: str


@dataclass
class Decisao:
    promocao_id: int
    selecionada: bool
    classificacao: str
    prioridade: float
    receita_esperada: float | None
    posicao: int | None = None
    fatores: list[Fator] = field(default_factory=list)
    motivos: list[str] = field(default_factory=list)
    recusa: str | None = None
    avisos: list[str] = field(default_factory=list)
    sem_dados: list[str] = field(default_factory=list)

    def resumo(self) -> str:
        if self.recusa:
            return f"Recusada: {self.recusa}"
        base = f"Prioridade {self.prioridade:.0f}/100 ({self.classificacao})"
        if self.posicao:
            base = f"{self.posicao}º lugar · " + base
        return base + (": " + "; ".join(self.motivos[:3]) if self.motivos else "")

    def como_dict(self) -> dict:
        d = asdict(self)
        d["resumo"] = self.resumo()
        return d


def _fatores(o: dict, ap: Aprendizado, p: PesosRanking) -> tuple[list[Fator], list[str], float | None, list[str]]:
    fatores, sem_dados, motivos = [], [], []
    q = float(o.get("pontuacao") or 0)
    fatores.append(Fator("qualidade", q / 100, p.qualidade, f"nota {q:.0f}/100 no motor de inteligência"))
    if o.get("desconto_comprovado") and o.get("desconto_real") is not None:
        d = float(o["desconto_real"])
        fatores.append(Fator("desconto comprovado", min(d / p.desconto_meta, 1.0) if p.desconto_meta else 0, p.desconto,
                             f"{d:.0f}% de desconto comprovado pelo histórico ou por cupom"))
        if d >= p.desconto_meta * 0.5:
            motivos.append(f"desconto comprovado de {d:.0f}%")
    else:
        fatores.append(Fator("desconto comprovado", 0.0, p.desconto, "desconto NÃO comprovado pelo histórico (vale 0)"))
    if o.get("comissao_pct") is not None and o.get("dados_confiaveis"):
        bruta = float(o["comissao_pct"])
        canc = o.get("cancelamento") or {}
        if canc.get("taxa") is not None:
            c = bruta * (1 - canc["taxa"])
            texto = (f"comissão líquida de {c:.1f}% (oficial {bruta:.1f}% − {100 * canc['taxa']:.0f}% de cancelamento "
                     f"em {canc['amostra']} comissões decididas da loja)")
        else:
            c = bruta
            texto = f"comissão oficial de {bruta:.1f}%"
            if canc:
                sem_dados.append(f"comissão líquida: só {canc.get('amostra', 0)} comissões decididas da loja "
                                 f"(mínimo {canc.get('minimo')}); usada a comissão bruta")
        o["comissao_liquida_pct"] = c
        fatores.append(Fator("comissão", min(c / p.comissao_meta, 1.0) if p.comissao_meta else 0, p.comissao, texto))
        if c >= p.comissao_meta * 0.7:
            motivos.append(f"comissão de {c:.1f}%")
    else:
        sem_dados.append("comissão: não informada pela API oficial da loja")
    if o.get("loja_oficial") is True:
        fatores.append(Fator("vendedor", 1.0, p.vendedor, "loja oficial"))
        motivos.append("vendida por loja oficial")
    elif o.get("loja_oficial") is False:
        fatores.append(Fator("vendedor", 0.5, p.vendedor, "vendedor comum (não é loja oficial)"))
    else:
        sem_dados.append("vendedor: a loja não informou se é loja oficial")
    est = o.get("estoque")
    if est is None:
        sem_dados.append("estoque: a loja não informa a quantidade (só se está disponível)")
    elif est > 0:
        fatores.append(Fator("estoque", 1.0 if est >= 10 else 0.5, p.estoque,
                             f"{est} unidade(s) em estoque" + (" — risco de esgotar" if est < 10 else "")))
    fator, textos = potencial(ap, o)
    if fator is None:
        sem_dados.extend(textos)
    else:
        fatores.append(Fator("potencial de conversão", min(fator / 2, 1.0), p.potencial, "; ".join(textos)))
        if fator >= 1.2:
            motivos.append(f"segmento com {fator:.1f}× a média de cliques")
    return fatores, sem_dados, fator, motivos


def classificar(ofertas: list[dict], ap: Aprendizado, sat: Saturacao, equivalencias: list[Equivalencia],
                pesos: PesosRanking | None = None) -> list[Decisao]:
    """Devolve uma Decisao por oferta, na ordem final (selecionadas primeiro)."""
    p = pesos or PesosRanking()
    por_produto = {o["produto_id"]: o for o in ofertas}
    decisoes: list[Decisao] = []
    for o in ofertas:
        fatores, sem_dados, fator, motivos = _fatores(o, ap, p)
        com_dados = [f for f in fatores if f.nota is not None]
        peso_total = sum(f.peso for f in com_dados)
        base = 100 * sum(f.peso * f.nota for f in com_dados) / peso_total if peso_total else 0.0
        receita, texto_receita = receita_esperada(ap, o.get("preco"), (o.get("comissao_liquida_pct") or o.get("comissao_pct"))
                                                  if o.get("dados_confiaveis") else None, fator)
        if receita is None:
            sem_dados.append(texto_receita)
        d = Decisao(o["promocao_id"], True, o.get("classificacao") or "boa", round(base, 2), receita,
                    fatores=fatores, motivos=motivos, sem_dados=sem_dados)
        if receita is not None:
            d.motivos.append(texto_receita)
        # ---- recusas
        if o.get("estoque") == 0:
            d.selecionada, d.recusa = False, "estoque zerado informado pela loja"
        cat = o.get("categoria")
        if d.selecionada and cat and cat in sat.categorias_cheias:
            d.selecionada = False
            d.recusa = (f"categoria \"{cat}\" já teve {sat.categorias_cheias[cat]} posts nas últimas "
                        f"{sat.janela_categoria_h:.0f} h (saturação da audiência)")
        for e in equivalencias:
            if o["produto_id"] not in (e.produto_a, e.produto_b):
                continue
            outro = por_produto.get(e.produto_b if e.produto_a == o["produto_id"] else e.produto_a)
            if not outro:
                continue
            if e.confianca == BAIXA:
                d.avisos.append(f"possível produto repetido em {outro.get('marketplace')} ({e.evidencia}); "
                                "confiança baixa: as duas ofertas seguem")
                continue
            meu, dele = float(o.get("preco") or 0), float(outro.get("preco") or 0)
            if d.selecionada and (dele < meu or (dele == meu and outro["promocao_id"] < o["promocao_id"])):
                d.selecionada = False
                d.recusa = (f"mesmo produto ({e.evidencia}, confiança {e.confianca}) mais barato ou igual em "
                            f"{outro.get('marketplace')}: R$ {dele:.2f} × R$ {meu:.2f}".replace(".", ","))
        decisoes.append(d)
    # ---- receita normalizada, só onde existe
    maior = max((d.receita_esperada for d in decisoes if d.receita_esperada), default=None)
    for d in decisoes:
        if maior and d.receita_esperada is not None and p.receita > 0:
            d.prioridade = round((1 - p.receita / 100) * d.prioridade + p.receita * d.receita_esperada / maior, 2)
    ordem = sorted(decisoes, key=lambda d: (d.selecionada, ORDEM_CLASSE.get(d.classificacao, 0), d.prioridade), reverse=True)
    pos = 0
    for d in ordem:
        if d.selecionada:
            pos += 1
            d.posicao = pos
    return ordem
