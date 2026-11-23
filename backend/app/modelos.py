"""Estruturas de dados trocadas entre integrações, serviços e repositório."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OfertaEncontrada:
    """O que uma fonte (Shopee, Demo...) devolve. Ainda não está no banco."""
    marketplace: str            # código do marketplace (shopee, demo...)
    id_externo: str
    titulo: str
    preco: float
    link_afiliado: str
    preco_original: float | None = None
    nota: float | None = None
    total_vendas: int | None = None
    imagem_url: str | None = None
    url_produto: str | None = None
    comissao_pct: float | None = None
    categoria_slug: str | None = None
    disponivel: bool = True
    demo: bool = False

    @property
    def desconto_pct(self) -> float:
        if self.preco_original and self.preco_original > self.preco:
            return round((1 - self.preco / self.preco_original) * 100, 2)
        return 0.0


@dataclass
class ResultadoValidacao:
    valido: bool
    motivo: str | None = None
    status_http: int | None = None


@dataclass
class ConteudoPublicacao:
    """Tudo que um canal precisa para publicar uma oferta."""
    promocao_id: int
    titulo: str
    preco: float
    loja: str
    link_rastreado: str          # link que passa pela função de cliques
    preco_original: float | None = None
    desconto_pct: float = 0.0
    imagem: bytes | None = None  # arte já gerada
    imagem_story: bytes | None = None
    destaque: bool = False       # oferta imperdível do dia
    demo: bool = False
    legenda: str = ""            # texto já montado para o canal (cada canal tem o seu)


@dataclass
class ItemFila:
    id: int
    promocao_id: int
    canal_id: int
    link_afiliado_id: int
    tentativas: int = 0


@dataclass
class ResumoExecucao:
    encontradas: int = 0
    novas_promocoes: int = 0
    links_validados: int = 0
    links_recusados: int = 0
    analisadas: int = 0
    excelentes: int = 0
    boas: int = 0
    nao_recomendadas: int = 0
    enfileiradas: int = 0
    publicadas: int = 0
    falhas_publicacao: int = 0
    verificadas: int = 0
    comissoes: int = 0
    etapas_com_erro: list[str] = field(default_factory=list)
    etapas_puladas: list[str] = field(default_factory=list)
    campanhas: int = 0
    sugestoes_cupom: int = 0
    cupons_publicados: int = 0
    canais_bloqueados: dict = field(default_factory=dict)
    alertas_criticos: int = 0

    def como_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Cupom:
    """Cupom (código digitado no checkout) ou campanha da loja (só link). Dados vêm do cadastro ou da API oficial."""
    id: int
    marketplace: str
    titulo: str
    fim_em: datetime
    tipo: str = "cupom"                       # cupom | campanha
    codigo: str | None = None
    desconto_tipo: str | None = None          # percentual | valor
    desconto_valor: float | None = None
    valor_minimo: float | None = None
    desconto_maximo: float | None = None
    link_afiliado: str | None = None
    imagem_url: str | None = None
    regras: str | None = None
    marketplace_nome: str = ""
    palavras_chave: list[str] = field(default_factory=list)
    categoria_id: int | None = None
    origem: str = "manual"
    demo: bool = False

    def desconto_em(self, preco: float) -> float | None:
        """Quanto o cupom desconta neste preço (None = não se aplica ou desconto não informado)."""
        if not self.desconto_tipo or not self.desconto_valor or preco <= 0:
            return None
        if self.valor_minimo and preco < self.valor_minimo:
            return None
        if self.desconto_tipo == "percentual":
            valor = preco * self.desconto_valor / 100
            if self.desconto_maximo:
                valor = min(valor, self.desconto_maximo)
        else:
            valor = self.desconto_valor
        return round(min(valor, preco - 0.01), 2) if valor > 0 else None

    def preco_com_cupom(self, preco: float) -> float | None:
        desconto = self.desconto_em(preco)
        return round(preco - desconto, 2) if desconto else None

    def descricao(self) -> str:
        """Ex.: '10% OFF (até R$ 30,00) acima de R$ 79,00'. Só o que foi informado."""
        from .servicos.textos import reais
        if not self.desconto_tipo:
            partes = []
        elif self.desconto_tipo == "percentual":
            partes = [f"{self.desconto_valor:g}% OFF".replace(".", ",")]
            if self.desconto_maximo:
                partes.append(f"(até {reais(self.desconto_maximo)})")
        else:
            partes = [f"{reais(self.desconto_valor)} OFF"]
        if self.valor_minimo:
            partes.append(f"acima de {reais(self.valor_minimo)}")
        return " ".join(partes)

    @classmethod
    def de_linha(cls, l: dict) -> "Cupom":
        def n(v):
            return float(v) if v is not None else None
        fim = l["fim_em"]
        if isinstance(fim, str):
            fim = datetime.fromisoformat(fim.replace("Z", "+00:00"))
        return cls(id=l["id"], marketplace=l.get("marketplace_codigo") or l.get("marketplace", ""), titulo=l["titulo"],
                   fim_em=fim, tipo=l.get("tipo", "cupom"), codigo=l.get("codigo"), desconto_tipo=l.get("desconto_tipo"),
                   desconto_valor=n(l.get("desconto_valor")), valor_minimo=n(l.get("valor_minimo")),
                   desconto_maximo=n(l.get("desconto_maximo")), link_afiliado=l.get("link_afiliado"),
                   imagem_url=l.get("imagem_url"), regras=l.get("regras"), marketplace_nome=l.get("marketplace_nome") or "",
                   palavras_chave=list(l.get("palavras_chave") or []), categoria_id=l.get("categoria_id"),
                   origem=l.get("origem", "manual"), demo=bool(l.get("demo")))


@dataclass
class CampanhaEncontrada:
    """Campanha oficial devolvida pela API da loja (ex.: shopeeOfferV2)."""
    marketplace: str
    id_externo: str
    titulo: str
    link_afiliado: str
    fim_em: datetime
    inicio_em: datetime | None = None
    imagem_url: str | None = None
    comissao_pct: float | None = None
    demo: bool = False
