"""Interfaces das integrações.

Para adicionar uma loja nova (Amazon, Magalu...) ou um canal novo (WhatsApp, X...):
  1. crie um arquivo em fontes/ ou canais/ com uma classe que herda daqui;
  2. registre a classe em registro.py.
Nenhum serviço precisa mudar.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..modelos import CampanhaEncontrada, ConteudoPublicacao, OfertaEncontrada

REAL, DEMO, MANUAL = "real", "demo", "manual"


class FonteOfertas(ABC):
    """Uma loja de onde vêm ofertas."""
    codigo: str = ""                 # igual ao código do marketplace no banco
    modo: str = DEMO                 # real | demo | manual
    suporta_busca: bool = True
    suporta_consulta: bool = False   # sabe consultar preço/disponibilidade de um item
    suporta_comissoes: bool = False  # sabe importar relatório de comissões
    suporta_campanhas: bool = False  # sabe listar campanhas/cupons oficiais da loja

    @abstractmethod
    def buscar(self, palavra: str, limite: int = 30) -> list[OfertaEncontrada]:
        """Ofertas para uma palavra-chave. Os links devolvidos já devem ser de afiliado."""

    def consultar(self, id_externo: str) -> OfertaEncontrada | None:
        """Situação atual do item (None = indisponível). Só se suporta_consulta."""
        raise NotImplementedError

    def gerar_link_afiliado(self, url: str) -> str | None:
        """Converte uma URL comum em link de afiliado, se a API permitir."""
        return None

    def comissoes(self, inicio: datetime, fim: datetime) -> list[dict]:
        """Itens de comissão: referencia_externa, valor_comissao, valor_venda, data_venda, status, id_externo."""
        raise NotImplementedError

    def campanhas(self, limite: int = 20) -> list[CampanhaEncontrada]:
        """Campanhas oficiais vigentes (com link de afiliado). Só se suporta_campanhas."""
        return []

    def testar(self) -> str:
        """Teste rápido da integração (usado no comando testar-integracoes)."""
        return f"{self.codigo}: modo {self.modo}"


class CanalPublicacao(ABC):
    """Um lugar onde as ofertas são publicadas."""
    codigo: str = ""                 # igual ao código do canal no banco
    modo: str = DEMO

    @abstractmethod
    def publicar(self, conteudo: ConteudoPublicacao) -> str:
        """Publica e devolve o ID da mensagem/post no serviço."""

    def encerrar(self, id_mensagem: str, titulo: str, motivo: str) -> None:
        """Marca uma publicação antiga como encerrada (se o serviço permitir)."""

    def testar(self) -> str:
        return f"{self.codigo}: modo {self.modo}"


class HospedagemImagem(ABC):
    @abstractmethod
    def enviar(self, imagem: bytes) -> str:
        """Hospeda a imagem e devolve uma URL pública (exigência do Instagram)."""
