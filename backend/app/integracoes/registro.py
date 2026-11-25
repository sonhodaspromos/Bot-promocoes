"""Registro de integrações: decide, para cada loja e canal, qual implementação usar.

Com AMBIENTE definido (o normal), quem decide é o ambiente (ver app/ambiente.py):
  demonstracao -> tudo simulado; homologacao -> lojas reais, canais simulados;
  producao -> só reais, e credencial ausente BLOQUEIA o bot (nunca vira demonstração).
Sem ambiente (só testes e `simular`): com credenciais -> REAL; sem -> DEMONSTRAÇÃO.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..ambiente import Ambiente
from ..config import REAL, Config
from ..erros import ErroConfiguracao
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


def montar(config: Config, ordenacao_shopee: int = 2, ambiente: Ambiente | None = None) -> Integracoes:
    if ambiente is not None:
        return _montar_por_ambiente(config, ordenacao_shopee, ambiente)
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


def _montar_por_ambiente(config: Config, ordenacao: int, amb: Ambiente) -> Integracoes:
    t = config.timeout_http
    integ = Integracoes()
    if amb.nome == "demonstracao":
        integ.fontes["demo"] = DemoFonte()
        integ.fontes["mercado_livre"] = ManualFonte("mercado_livre")
        integ.canais = {c: DemoCanal(c) for c in ("telegram", "instagram_story", "instagram_feed")}
        return integ
    if config.forcar_demo:
        raise ErroConfiguracao(f"Conflito: AMBIENTE={amb.nome} com MODO_DEMO ligado. Remova MODO_DEMO.")

    exigidas = {"shopee": ["shopee"], "telegram": ["telegram"], "instagram": ["instagram", "imgbb"]}
    faltando = [c for i in amb.ativas for c in exigidas[i] if config.modo(c) != REAL]
    if faltando:
        raise ErroConfiguracao(
            f"{amb.nome.upper()}: credenciais ausentes para {', '.join(sorted(set(faltando)))}. "
            "O bot foi BLOQUEADO (não troca para demonstração). Cadastre as credenciais ou tire a "
            "integração de INTEGRACOES_ATIVAS.")

    if "shopee" in amb.ativas:
        integ.fontes["shopee"] = ShopeeFonte(config.credencial("shopee", "APP_ID"), config.credencial("shopee", "SECRET"),
                                             ClienteHTTP("shopee", timeout=t), ordenacao=ordenacao)
    integ.fontes["mercado_livre"] = ManualFonte("mercado_livre")

    if amb.nome == "homologacao":  # nunca publica de verdade, nem com token configurado
        integ.canais = {c: DemoCanal(c) for c in ("telegram", "instagram_story", "instagram_feed")}
        return integ

    if "telegram" in amb.ativas:
        integ.canais["telegram"] = TelegramCanal(config.credencial("telegram", "TOKEN"), config.credencial("telegram", "CANAL"),
                                                 ClienteHTTP("telegram", timeout=t))
    if "instagram" in amb.ativas:
        hospedagem = ImgBB(config.credencial("imgbb", "KEY"), ClienteHTTP("imgbb", timeout=t))
        for tipo in ("story", "feed"):
            integ.canais[f"instagram_{tipo}"] = InstagramCanal(
                tipo, config.credencial("instagram", "TOKEN"), config.credencial("instagram", "USER_ID"),
                hospedagem, ClienteHTTP("instagram", timeout=t))
    return integ
