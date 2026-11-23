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
    canais_bloqueados: dict = field(default_factory=dict)
    alertas_criticos: int = 0

    def como_dict(self) -> dict:
        return dict(self.__dict__)
