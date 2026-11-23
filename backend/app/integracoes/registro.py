"""Registro de integrações: decide, para cada loja e canal, qual implementação usar.

Regra: com credenciais completas -> implementação REAL; sem credenciais -> DEMONSTRAÇÃO
(claramente identificada). Para incluir uma integração nova, adicione-a aqui.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import REAL, Config
from ..cliente_http import ClienteHTTP
from .base import CanalPublicacao, FonteOfertas
from .canais.demo import DemoCanal
from .canais.instagram import InstagramCanal
from .canais.telegram import TelegramCanal
from .fontes.demo import DemoFonte
from .fontes.manual import ManualFonte
from .fontes.shopee import ShopeeFonte
from .imagens.imgbb import ImgBB


@dataclass
class Integracoes:
    fontes: dict[str, FonteOfertas] = field(default_factory=dict)
    canais: dict[str, CanalPublicacao] = field(default_factory=dict)

    @property
    def modo_geral(self) -> str:
        """'real' se pelo menos um canal publica de verdade; senão 'demo'."""
        return "real" if any(c.modo == "real" for c in self.canais.values()) else "demo"


def montar(config: Config, ordenacao_shopee: int = 2) -> Integracoes:
    t = config.timeout_http
    integ = Integracoes()

    # ---- fontes
    if config.modo("shopee") == REAL:
        integ.fontes["shopee"] = ShopeeFonte(config.credencial("shopee", "APP_ID"), config.credencial("shopee", "SECRET"),
                                             ClienteHTTP("shopee", timeout=t), ordenacao=ordenacao_shopee)
    else:
        integ.fontes["demo"] = DemoFonte()
    integ.fontes["mercado_livre"] = ManualFonte("mercado_livre")

    # ---- canais
    if config.modo("telegram") == REAL:
        integ.canais["telegram"] = TelegramCanal(config.credencial("telegram", "TOKEN"),
                                                 config.credencial("telegram", "CANAL"), ClienteHTTP("telegram", timeout=t))
    else:
        integ.canais["telegram"] = DemoCanal("telegram")

    if config.modo("instagram") == REAL and config.modo("imgbb") == REAL:
        hospedagem = ImgBB(config.credencial("imgbb", "KEY"), ClienteHTTP("imgbb", timeout=t))
        for tipo in ("story", "feed"):
            integ.canais[f"instagram_{tipo}"] = InstagramCanal(
                tipo, config.credencial("instagram", "TOKEN"), config.credencial("instagram", "USER_ID"),
                hospedagem, ClienteHTTP("instagram", timeout=t))
    else:
        integ.canais["instagram_story"] = DemoCanal("instagram_story")
        integ.canais["instagram_feed"] = DemoCanal("instagram_feed")
    return integ
