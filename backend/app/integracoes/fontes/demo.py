"""Fonte de DEMONSTRAÇÃO — claramente identificada.

Usada quando a API de uma loja não está configurada. Gera ofertas fictícias e determinísticas:
  * título começa com "[DEMO]";
  * marketplace "demo" e links em https://demo.invalid/ (domínio reservado, que não existe);
  * no banco ficam marcadas como demo e NUNCA podem ir para canais reais.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace

from datetime import datetime, timedelta, timezone

from ...modelos import CampanhaEncontrada, OfertaEncontrada
from ..base import DEMO, FonteOfertas


class DemoFonte(FonteOfertas):
    codigo = "demo"
    modo = DEMO
    suporta_consulta = True
    suporta_campanhas = True

    def __init__(self, ofertas_por_busca: int = 4):
        self.ofertas_por_busca = ofertas_por_busca
        self.catalogo: dict[str, OfertaEncontrada] = {}

    def _gerar(self, palavra: str, i: int) -> OfertaEncontrada:
        semente = int(hashlib.sha256(f"{palavra}-{i}".encode()).hexdigest()[:8], 16)
        original = float(50 + semente % 450)
        desconto = 15 + semente % 50
        id_externo = f"DEMO-{semente % 10_000_000}"
        if id_externo not in self.catalogo:
            self.catalogo[id_externo] = OfertaEncontrada(
                marketplace=self.codigo, id_externo=id_externo,
                titulo=f"[DEMO] {palavra.title()} modelo {semente % 900 + 100}",
                preco=round(original * (1 - desconto / 100), 2), preco_original=original,
                nota=round(4 + (semente % 10) / 10, 1), total_vendas=semente % 5000,
                link_afiliado=f"https://demo.invalid/p/{id_externo}", comissao_pct=float(3 + semente % 10), demo=True)
        return self.catalogo[id_externo]

    def buscar(self, palavra, limite=30):
        return [replace(self._gerar(palavra, i)) for i in range(min(limite, self.ofertas_por_busca))]

    def consultar(self, id_externo):
        oferta = self.catalogo.get(id_externo)
        return replace(oferta) if oferta and oferta.disponivel else None

    def campanhas(self, limite=20):
        fim = datetime.now(timezone.utc) + timedelta(days=2)
        return [CampanhaEncontrada(marketplace=self.codigo, id_externo="camp-demo-1", titulo="[DEMO] Semana do Frete Grátis",
                                   link_afiliado="https://demo.invalid/campanha/frete", fim_em=fim, demo=True)][:limite]

    # usado em testes e na simulação para imitar mudanças na loja
    def simular_preco(self, id_externo: str, preco: float) -> None:
        self.catalogo[id_externo].preco = preco

    def simular_esgotado(self, id_externo: str) -> None:
        self.catalogo[id_externo].disponivel = False

    def testar(self):
        return "demo: MODO DEMONSTRAÇÃO (ofertas fictícias, nada é publicado de verdade)"
