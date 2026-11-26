"""Motor de inteligência de ofertas.

Função pura: recebe os DADOS REAIS de uma oferta (`EntradaAnalise`) e as regras, e devolve
pontuação (0–100), classificação, confiança e a explicação de cada decisão.

Princípios:
  * Nunca inventa dados. Preço anterior, vendas, avaliação ou comissão ausentes ficam "sem dados":
    o quesito sai da conta (os pesos são redistribuídos) e a CONFIANÇA da análise cai.
  * Bloqueios são absolutos: indisponível, link inválido, duplicada, desconto abaixo do mínimo,
    desconto impossível de calcular ou informações inconsistentes => "Oferta não recomendada".
  * Tudo é explicado em português, para aparecer no painel.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from .precos import resumir as resumir_precos
from .regras import Regras

EXCELENTE, BOA, NAO_RECOMENDADA = "excelente", "boa", "nao_recomendada"
ROTULOS = {EXCELENTE: "Excelente oportunidade", BOA: "Boa oportunidade", NAO_RECOMENDADA: "Oferta não recomendada"}


def _reais(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _cap(t: str) -> str:
    return t[:1].upper() + t[1:] if t else t


def _pct(v: float) -> str:
    return f"{v:.0f}%" if abs(v - round(v)) < 0.05 else f"{v:.1f}%".replace(".", ",")


@dataclass
class PontoPreco:
    preco: float
    quando: datetime
    disponivel: bool = True


@dataclass
class EntradaAnalise:
    titulo: str
    preco: float                              # preço da oferta
    disponivel: bool
    link_situacao: str                        # validado | pendente | invalido
    preco_produto: float | None = None        # preço atual do produto (conferência de consistência)
    preco_referencia: float | None = None     # "preço de" informado
    origem_referencia: str | None = None      # loja | manual | pagina (riscado lido pelo bot na página) | None
    historico: list[PontoPreco] = field(default_factory=list)
    comissao_pct: float | None = None
    nota: float | None = None
    total_vendas: int | None = None
    dados_da_loja_confiaveis: bool = False    # nota/vendas/comissão vieram de API oficial (não demo/manual)
    categoria_ativa: bool | None = None       # None = sem categoria
    imagem_url: str | None = None
    imagem_largura: int | None = None
    imagem_altura: int | None = None
    link_motivo: str | None = None
    duplicada: str | None = None              # motivo, se já existe oferta igual
    demo: bool = False
    preco_maximo_variacao: float | None = None  # maior preço entre as variações do produto
    referencia_derivada: bool = False           # "preço de" calculado de uma taxa (não foi visto)
    desconto_por_cupom: bool = False            # o desconto vem de um cupom aprovado (regras conhecidas)


@dataclass
class Componente:
    nome: str
    peso: float
    nota: float | None          # 0..1; None = sem dados (não entra na conta)
    confiabilidade: float       # 0..1: quão confiáveis são os dados deste quesito
    explicacao: str

    @property
    def pontos(self) -> float:
        return 0.0 if self.nota is None else self.peso * self.nota


@dataclass
class ResultadoAnalise:
    pontuacao: int
    classificacao: str
    confianca: int
    desconto_real: float | None
    base_desconto: str
    componentes: list[Componente]
    bloqueios: list[str]
    alertas: list[str]
    motivos: list[str]
    pontuacao_sem_bloqueio: int
    analisado_em: str
    desconto_comprovado: bool = False
    historico_precos: dict | None = None       # 7/30/90 dias, menor preço, desconto artificial, sem dados

    @property
    def rotulo(self) -> str:
        return ROTULOS[self.classificacao]

    @property
    def aprovada(self) -> bool:
        return self.classificacao in (EXCELENTE, BOA)

    def resumo(self) -> str:
        if self.bloqueios:
            return f"{self.rotulo}: " + "; ".join(self.bloqueios)
        principais = "; ".join(self.motivos[:3]) or "sem destaques"
        return f"{self.rotulo} ({self.pontuacao}/100, confiança {self.confianca}%): {principais}"

    def como_dict(self) -> dict:
        d = asdict(self)
        d["rotulo"] = self.rotulo
        d["resumo"] = self.resumo()
        for c, comp in zip(d["componentes"], self.componentes):
            c["pontos"] = round(comp.pontos, 2)
        return d


# =========================================================================== análise
def analisar(e: EntradaAnalise, regras: Regras | None = None, agora: datetime | None = None) -> ResultadoAnalise:
    r = regras or Regras()
    agora = agora or datetime.now(timezone.utc)
    bloqueios: list[str] = []
    alertas: list[str] = []
    motivos: list[str] = []

    # ------------------------------------------------ 1. consistência das informações
    if e.preco is None or e.preco <= 0:
        bloqueios.append("Informação inconsistente: preço inválido")
    if not e.titulo or len(e.titulo.strip()) < 5:
        bloqueios.append("Informação inconsistente: título ausente ou curto demais")
    if e.preco and e.preco > 0 and e.preco_produto and e.preco_produto > 0:
        diferenca = abs(e.preco - e.preco_produto) / e.preco_produto * 100
        if diferenca > r.tolerancia_preco_produto_pct:
            bloqueios.append(f"Informação inconsistente: preço da oferta ({_reais(e.preco)}) diferente do preço atual "
                             f"do produto ({_reais(e.preco_produto)})")
    if e.nota is not None and not (0 <= e.nota <= 5):
        bloqueios.append(f"Informação inconsistente: avaliação {e.nota} fora da escala de 0 a 5")
    if e.total_vendas is not None and e.total_vendas < 0:
        bloqueios.append("Informação inconsistente: quantidade vendida negativa")
    if e.comissao_pct is not None and not (0 <= e.comissao_pct <= r.comissao_maxima_plausivel):
        bloqueios.append(f"Informação inconsistente: comissão de {_pct(e.comissao_pct)} fora do plausível "
                         f"(máximo {_pct(r.comissao_maxima_plausivel)})")
    if e.preco_referencia is not None and e.preco_referencia <= 0:
        bloqueios.append("Informação inconsistente: preço de referência inválido")

    # ------------------------------------------------ 2. disponibilidade, link, duplicidade
    if not e.disponivel:
        bloqueios.append("Produto indisponível")
    if e.link_situacao == "invalido":
        bloqueios.append("Link de afiliado inválido" + (f": {e.link_motivo}" if e.link_motivo else ""))
    elif e.link_situacao == "pendente":
        alertas.append("Link de afiliado ainda não validado: a oferta só é publicada depois da validação")
    if e.duplicada:
        bloqueios.append(f"Oferta duplicada: {e.duplicada}")
    if e.preco and e.preco > 0 and e.preco_maximo_variacao and \
            e.preco_maximo_variacao > e.preco * (1 + r.variacao_preco_max_pct / 100):
        bloqueios.append(f"Preço diferente entre as variações do produto ({_reais(e.preco)} a "
                         f"{_reais(e.preco_maximo_variacao)}): o post mostraria um preço que nem todas as opções têm")

    # ------------------------------------------------ 3. histórico 7/30/90, menor preço, desconto artificial
    resumo = resumir_precos(e.historico, agora, e.preco if e.preco and e.preco > 0 else None,
                            e.preco_referencia if not e.desconto_por_cupom else None,
                            cobertura_minima_pct=r.historico_cobertura_minima_pct,
                            alta_artificial_pct=r.alta_artificial_pct)
    for texto in resumo.desconto_artificial:
        if resumo.artificial_bloqueante and r.bloquear_desconto_artificial and "subiu" in texto:
            bloqueios.append("Desconto artificial: " + texto)
        else:
            alertas.append("Desconto anunciado pela loja é artificial: " + texto
                           + " (o desconto divulgado é recalculado pelo histórico)")
    # ------------------------------------------------ 4. desconto real
    comp_desc, desconto_real, base = _desconto(e, r, agora, bloqueios, alertas, motivos)
    for s in resumo.sinais:
        if s.startswith("Menor preço"):
            if not any(m.startswith("Menor preço") for m in motivos):   # sem repetir o motivo do quesito desconto
                motivos.append(s)
        elif not any(s.split(" (")[0] in a for a in alertas):
            alertas.append(s)
    comprovado = desconto_real is not None and (comp_desc.confiabilidade >= 0.9 or e.desconto_por_cupom)
    if desconto_real is not None and r.exigir_desconto_comprovado and not comprovado:
        bloqueios.append(f"Desconto não comprovado ({base}): só divulgamos desconto confirmado pelo histórico de "
                         "preços que o próprio bot registrou ou por cupom aprovado")

    componentes = [
        comp_desc,
        _comissao(e, r, motivos, alertas),
        _popularidade(e, r, motivos, alertas),
        _relevancia(e, r, motivos, alertas),
        _qualidade(e, r, motivos, alertas, bloqueios),
    ]

    # ------------------------------------------------ 4. pontuação e confiança
    peso_total = sum(c.peso for c in componentes)
    peso_com_dados = sum(c.peso for c in componentes if c.nota is not None)
    if peso_com_dados > 0:
        pontuacao = round(100 * sum(c.pontos for c in componentes) / peso_com_dados)
    else:
        pontuacao = 0
    confianca = round(100 * sum(c.peso * c.confiabilidade for c in componentes if c.nota is not None) / peso_total) if peso_total else 0
    pontuacao = max(0, min(100, pontuacao))
    sem_dados = [c.nome for c in componentes if c.nota is None and c.peso > 0]
    if sem_dados:
        alertas.append("Sem dados confiáveis para: " + ", ".join(sem_dados) + " (fora da conta; confiança menor)")
    if e.demo:
        alertas.append("Oferta de DEMONSTRAÇÃO: dados fictícios, só pode ir para canais de demonstração")

    # ------------------------------------------------ 5. classificação
    pontuacao_sem_bloqueio = pontuacao
    if bloqueios:
        classificacao, pontuacao = NAO_RECOMENDADA, 0
    elif pontuacao >= r.nota_excelente and confianca >= r.confianca_minima_excelente:
        classificacao = EXCELENTE
    elif pontuacao >= r.nota_excelente:
        classificacao = BOA
        alertas.append(f"Pontuação de excelente, mas a confiança dos dados ({confianca}%) é menor que "
                       f"{r.confianca_minima_excelente:.0f}%: classificada como boa")
    elif pontuacao >= r.nota_boa:
        classificacao = BOA
    else:
        classificacao = NAO_RECOMENDADA
        bloqueios.append(f"Pontuação {pontuacao}/100 abaixo do mínimo de {r.nota_boa:.0f} para recomendação")

    return ResultadoAnalise(
        pontuacao=pontuacao, classificacao=classificacao, confianca=max(0, min(100, confianca)),
        desconto_real=round(desconto_real, 2) if desconto_real is not None else None, base_desconto=base,
        componentes=componentes, bloqueios=bloqueios, alertas=alertas, motivos=motivos,
        pontuacao_sem_bloqueio=pontuacao_sem_bloqueio, analisado_em=agora.isoformat(), desconto_comprovado=comprovado,
        historico_precos=resumo.como_dict())


# =========================================================================== quesitos
def _desconto(e, r, agora, bloqueios, alertas, motivos):
    peso = r.peso_desconto
    limite = agora - timedelta(days=r.historico_dias)
    pontos = sorted((p for p in e.historico if p.quando >= limite and p.preco and p.preco > 0 and p.disponivel),
                    key=lambda p: p.quando)
    precos = [p.preco for p in pontos]
    suficiente = len(precos) >= r.historico_minimo_registros
    cobertura = (pontos[-1].quando - pontos[0].quando).days if len(pontos) >= 2 else 0
    ref = e.preco_referencia if e.preco_referencia and e.preco and e.preco_referencia > e.preco else None
    desconto, base, conf = None, "sem base", 0.0

    if ref and suficiente:
        maior = max(precos)
        if maior >= ref * (1 - r.tolerancia_inflacao_pct / 100):
            desconto, conf = (ref - e.preco) / ref * 100, 1.0
            base = f"preço de {_reais(ref)} confirmado pelo histórico ({len(precos)} registros)"
        elif cobertura >= r.historico_cobertura_dias:
            desconto, conf = (maior - e.preco) / maior * 100, 1.0
            base = f"maior preço visto em {cobertura} dias ({_reais(maior)})"
            alertas.append(f"\"Preço de\" {_reais(ref)} nunca apareceu no histórico de {cobertura} dias "
                           f"(maior preço visto: {_reais(maior)}): desconto calculado pelo histórico")
        elif e.origem_referencia == "pagina" and r.aceitar_preco_pagina:
            desconto, conf = (ref - e.preco) / ref * 100, 0.9
            base = "preço riscado exibido na página da loja (lido pelo bot)"
        else:
            desconto, conf = (ref - e.preco) / ref * 100, 0.7
            base = "preço informado pela loja (histórico ainda curto para confirmar)"
            alertas.append(f"Histórico cobre só {cobertura} dia(s): o desconto informado ainda não pôde ser confirmado")
    elif ref and e.origem_referencia == "pagina" and r.aceitar_preco_pagina and not e.desconto_por_cupom:
        # a própria loja mostra o "de R$ X" riscado e o bot leu da página (não foi digitado): conta como comprovado.
        # Com histórico suficiente, o ramo de cima confere se esse "preço de" é real.
        desconto, conf = (ref - e.preco) / ref * 100, 0.9
        base = "preço riscado exibido na página da loja (lido pelo bot)"
    elif ref:
        desconto = (ref - e.preco) / ref * 100
        conf = 0.7 if e.origem_referencia == "loja" else 0.5
        if e.desconto_por_cupom:
            conf, base = 1.0, "desconto do cupom aprovado sobre o preço atual da loja"
        base = "preço informado pela loja" if e.origem_referencia == "loja" else "preço anterior informado no cadastro"
        alertas.append(f"Histórico de preço insuficiente ({len(precos)} registro(s)): desconto não confirmado")
    elif suficiente:
        tipico = statistics.median(precos)
        desconto, conf = max((tipico - e.preco) / tipico * 100, 0.0), 0.9
        base = f"mediana de {len(precos)} preços registrados ({_reais(tipico)})"

    if ref and e.referencia_derivada and conf >= 0.9 and not e.desconto_por_cupom \
            and not (suficiente and cobertura >= r.historico_cobertura_dias):
        conf = 0.7  # "preço de" calculado de uma taxa: só conta como comprovado com histórico longo
        base += " (preço de calculado pela taxa de desconto da loja)"

    if desconto is None:
        bloqueios.append("Sem preço de referência confiável: não dá para calcular o desconto "
                         "(o sistema não inventa preço anterior)")
        return Componente("desconto real", peso, None, 0.0, "Sem preço anterior nem histórico suficiente"), None, base

    if desconto > r.desconto_maximo_plausivel:
        bloqueios.append(f"Informação inconsistente: desconto de {_pct(desconto)} acima do plausível "
                         f"(máximo {_pct(r.desconto_maximo_plausivel)}); possível erro de preço")
    elif desconto < r.desconto_minimo:
        bloqueios.append(f"Desconto real de {_pct(max(desconto, 0))} abaixo do mínimo de {_pct(r.desconto_minimo)}")

    nota = max(0.0, min(desconto / r.desconto_meta, 1.0)) if r.desconto_meta > 0 else 0.0
    explicacao = f"{_pct(max(desconto, 0))} de desconto real (base: {base})"
    if suficiente and e.preco <= min(precos) * 1.001:
        nota = min(nota + 0.1, 1.0)
        motivos.append(f"Menor preço registrado nos últimos {r.historico_dias} dias")
        explicacao += "; menor preço do período (+10%)"
    elif suficiente and e.preco > statistics.median(precos) * 1.05:
        nota = max(nota - 0.2, 0.0)
        alertas.append("Preço atual acima da média recente do próprio produto")
        explicacao += "; acima da média recente (−20%)"
    if desconto >= r.desconto_minimo and desconto <= r.desconto_maximo_plausivel:
        motivos.insert(0, f"Desconto real de {_pct(desconto)} ({base})")
    return Componente("desconto real", peso, round(nota, 4), conf, explicacao), desconto, base


def _comissao(e, r, motivos, alertas):
    peso = r.peso_comissao
    if e.comissao_pct is None or not e.dados_da_loja_confiaveis:
        motivo = "comissão não informada pela loja" if e.comissao_pct is None else "comissão de fonte não oficial (ignorada)"
        return Componente("comissão", peso, None, 0.0, _cap(motivo) + ": não entra na conta")
    nota = max(0.0, min(e.comissao_pct / r.comissao_meta, 1.0)) if r.comissao_meta > 0 else 0.0
    estimada = e.preco * e.comissao_pct / 100 if e.preco else 0
    texto = f"Comissão de {_pct(e.comissao_pct)} (≈ {_reais(estimada)} por venda, estimativa sobre o preço atual)"
    if e.comissao_pct >= r.comissao_meta * 0.5:
        motivos.append(texto)
    elif e.comissao_pct < 2:
        alertas.append(f"Comissão baixa ({_pct(e.comissao_pct)})")
    return Componente("comissão", peso, round(nota, 4), 1.0, texto)


def _popularidade(e, r, motivos, alertas):
    peso = r.peso_popularidade
    if not e.dados_da_loja_confiaveis or (e.nota is None and e.total_vendas is None):
        return Componente("popularidade", peso, None, 0.0,
                          "Sem dados confiáveis de avaliação ou vendas: não entra na conta")
    partes, textos = [], []
    if e.nota is not None and 0 <= e.nota <= 5:
        partes.append(max(0.0, min((e.nota - 3.5) / 1.5, 1.0)))
        textos.append(f"avaliação {e.nota:.1f}")
        if e.nota < 4.0:
            alertas.append(f"Avaliação baixa na loja ({e.nota:.1f})")
    if e.total_vendas is not None and e.total_vendas >= 0:
        partes.append(min(math.log10(e.total_vendas + 1) / 3, 1.0))  # 1.000 vendas = nota máxima
        textos.append(f"{e.total_vendas:,} vendidos".replace(",", "."))
    if not partes:
        return Componente("popularidade", peso, None, 0.0, "Dados de popularidade inválidos: não entram na conta")
    nota = sum(partes) / len(partes)
    texto = "Na loja: " + ", ".join(textos)
    if nota >= 0.7:
        motivos.append(texto)
    return Componente("popularidade", peso, round(nota, 4), 1.0, texto)


def _relevancia(e, r, motivos, alertas):
    peso = r.peso_relevancia
    nota, textos = 0.0, []
    if e.categoria_ativa is True:
        nota += 0.4
        textos.append("categoria ativa")
    elif e.categoria_ativa is False:
        textos.append("categoria desativada")
        alertas.append("Produto de categoria desativada")
    else:
        textos.append("sem categoria")
    if e.preco and r.faixa_preco_min <= e.preco <= r.faixa_preco_max:
        nota += 0.3
        textos.append(f"preço na faixa de maior apelo ({_reais(r.faixa_preco_min)} a {_reais(r.faixa_preco_max)})")
    else:
        nota += 0.1
        textos.append("preço fora da faixa de maior apelo")
    titulo = (e.titulo or "").strip()
    letras = [c for c in titulo if c.isalpha()]
    caixa_alta = letras and sum(c.isupper() for c in letras) / len(letras) > 0.7
    if 15 <= len(titulo) <= 200 and not caixa_alta:
        nota += 0.3
        textos.append("título informativo")
    else:
        nota += 0.1
        textos.append("título fraco (muito curto, longo ou em CAIXA ALTA)")
    return Componente("relevância comercial", peso, round(nota, 4), 1.0, _cap("; ".join(textos)))


def _qualidade(e, r, motivos, alertas, bloqueios):
    peso = r.peso_qualidade
    url = (e.imagem_url or "").strip()
    if not url:
        img, txt_img = 0.0, "sem imagem"
        if r.exigir_imagem:
            bloqueios.append("Sem imagem do produto (regra: exigir imagem)")
        else:
            alertas.append("Oferta sem imagem do produto: a arte usa uma imagem genérica")
    elif not url.lower().startswith("https://"):
        img, txt_img = 0.0, "imagem sem HTTPS (não usada)"
        alertas.append("Imagem do produto sem HTTPS: não será usada")
    elif e.imagem_largura and e.imagem_altura:
        menor = min(e.imagem_largura, e.imagem_altura)
        if menor >= 600:
            img, txt_img = 1.0, f"imagem boa ({e.imagem_largura}×{e.imagem_altura})"
        elif menor >= 400:
            img, txt_img = 0.7, f"imagem média ({e.imagem_largura}×{e.imagem_altura})"
        else:
            img, txt_img = 0.3, f"imagem pequena ({e.imagem_largura}×{e.imagem_altura})"
            alertas.append(f"Imagem pequena ({e.imagem_largura}×{e.imagem_altura}): pode ficar borrada")
    else:
        img, txt_img = 0.6, "imagem presente (tamanho não verificado)"
    link = {"validado": 1.0, "pendente": 0.5}.get(e.link_situacao, 0.0)
    txt_link = {"validado": "link de afiliado validado", "pendente": "link aguardando validação"}.get(e.link_situacao, "link inválido")
    if e.link_situacao == "validado":
        motivos.append("Link de afiliado validado (domínio oficial e abre normalmente)")
    nota = 0.5 * img + 0.5 * link
    return Componente("qualidade (imagem e link)", peso, round(nota, 4), 1.0, f"{_cap(txt_img)}; {txt_link}")
