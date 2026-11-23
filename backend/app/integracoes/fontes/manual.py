"""Fonte MANUAL (Mercado Livre e lojas sem API de afiliados).

Não há busca automática nem scraping: as ofertas são cadastradas pelo painel, já com o link
de afiliado. O backend só valida o link (domínio de afiliado + se abre) antes de publicar e,
depois, confere se o link continua abrindo. Preço e estoque não são consultados.
"""
from __future__ import annotations

from ..base import MANUAL, FonteOfertas


class ManualFonte(FonteOfertas):
    modo = MANUAL
    suporta_busca = False

    def __init__(self, codigo: str = "mercado_livre"):
        self.codigo = codigo

    def buscar(self, palavra, limite=30):
        return []

    def testar(self):
        return f"{self.codigo}: MANUAL (ofertas cadastradas pelo painel; sem API e sem scraping)"
