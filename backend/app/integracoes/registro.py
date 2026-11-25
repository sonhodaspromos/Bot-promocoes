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
    whatsapp: object | None = None          # ClienteWhatsApp (só produção, com credenciais e INTEGRACOES_ATIVAS)

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

    exigidas = {"shopee": ["shopee"], "telegram": ["telegram"], "instagram": ["instagram", "imgbb"],
                "aliexpress": ["aliexpress"], "amazon": ["amazon"], "whatsapp": ["whatsapp"]}
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
    integ.fontes["magalu"] = ManualFonte("magalu")   # sem API pública de afiliados: cadastro pelo painel
    if "aliexpress" in amb.ativas:
        from .fontes.aliexpress import AliExpressFonte
        integ.fontes["aliexpress"] = AliExpressFonte(
            config.credencial("aliexpress", "APP_KEY"), config.credencial("aliexpress", "APP_SECRET"),
            config.credencial("aliexpress", "TRACKING_ID"), ClienteHTTP("aliexpress", timeout=t))
    if "amazon" in amb.ativas:
        from .fontes.amazon import AmazonFonte
        integ.fontes["amazon"] = AmazonFonte(
            config.credencial("amazon", "CREDENTIAL_ID"), config.credencial("amazon", "CREDENTIAL_SECRET"),
            config.credencial("amazon", "PARTNER_TAG"), config.credencial("amazon", "TOKEN_URL"),
            config.credencial("amazon", "API_URL"), ClienteHTTP("amazon", timeout=t))

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
    if "whatsapp" in amb.ativas:
        from ..servicos.whatsapp import ClienteWhatsApp
        integ.whatsapp = ClienteWhatsApp(config.credencial("whatsapp", "TOKEN"), config.credencial("whatsapp", "PHONE_ID"),
                                         config.credencial("whatsapp", "TEMPLATE"), ClienteHTTP("whatsapp", timeout=t))
    return integ


# ============================================================================ validação somente leitura
CATEGORIAS = ("real", "manual", "simulada", "desativada")


@dataclass
class AlvoValidacao:
    """Uma integração a validar. categoria: real | manual | simulada | desativada."""
    nome: str
    categoria: str
    obrigatoria: bool
    cliente: object | None = None   # objeto com .testar(); None = não há o que chamar
    motivo: str = ""


def montar_validacao(config: Config, amb: Ambiente, ordenacao: int = 2, *,
                     permitir_envio_imgbb: bool = False) -> list[AlvoValidacao]:
    """Monta clientes REAIS para validar credenciais, permissões e cotas, com HTTP somente leitura:
    qualquer chamada que publicaria ou alteraria algo é barrada (EscritaBloqueada) antes de sair.

    Funciona em homologação e produção. Obrigatórias = INTEGRACOES_ATIVAS. Integração sem credencial e
    fora da lista fica 'desativada' (não reprova); dentro da lista, reprova. A fonte manual do Mercado
    Livre é 'manual': aparece no relatório, mas nunca aprova nem reprova.
    """
    from ..cliente_http import ClienteHTTPSomenteLeitura

    if amb.nome == "demonstracao":
        raise ErroConfiguracao("validar-integracoes confere integrações REAIS: use AMBIENTE=homologacao "
                               "(ou producao). Em demonstração tudo é simulado e nada seria validado.")
    if config.forcar_demo:
        raise ErroConfiguracao(f"Conflito: AMBIENTE={amb.nome} com MODO_DEMO ligado. Remova MODO_DEMO.")
    t = config.timeout_http
    ro = lambda servico, **kw: ClienteHTTPSomenteLeitura(servico, timeout=t, tentativas=2, **kw)  # noqa: E731
    alvos: list[AlvoValidacao] = []

    def sem_credencial(nome, obrigatoria, faltam):
        if obrigatoria:
            return AlvoValidacao(nome, "real", True, None, f"credencial ausente: {', '.join(faltam)}")
        return AlvoValidacao(nome, "desativada", False, None, "sem credencial e fora de INTEGRACOES_ATIVAS")

    # Shopee
    obrig = "shopee" in amb.ativas
    if config.modo("shopee") == REAL:
        alvos.append(AlvoValidacao("shopee", "real", obrig, ShopeeFonte(
            config.credencial("shopee", "APP_ID"), config.credencial("shopee", "SECRET"), ro("shopee"), ordenacao=ordenacao),
            "" if obrig else "credencial presente, mas fora de INTEGRACOES_ATIVAS (validada só para informação)"))
    else:
        alvos.append(sem_credencial("shopee", obrig, ["SHOPEE_APP_ID", "SHOPEE_SECRET"]))

    # Telegram
    obrig = "telegram" in amb.ativas
    if config.modo("telegram") == REAL:
        alvos.append(AlvoValidacao("telegram", "real", obrig, TelegramCanal(
            config.credencial("telegram", "TOKEN"), config.credencial("telegram", "CANAL"), ro("telegram"))))
    else:
        alvos.append(sem_credencial("telegram", obrig, ["TELEGRAM_TOKEN", "TELEGRAM_CANAL"]))

    # Instagram (+ ImgBB, que hospeda as imagens que o Instagram baixa)
    obrig = "instagram" in amb.ativas
    if config.modo("instagram") == REAL:
        hospedagem = ImgBB(config.credencial("imgbb", "KEY"), ro("imgbb", permitir_envio_imgbb=permitir_envio_imgbb)) \
            if config.modo("imgbb") == REAL else None
        alvos.append(AlvoValidacao("instagram", "real", obrig, InstagramCanal(
            "feed", config.credencial("instagram", "TOKEN"), config.credencial("instagram", "USER_ID"),
            hospedagem, ro("instagram"))))
        if hospedagem is None:
            alvos.append(sem_credencial("imgbb", obrig, ["IMGBB_KEY"]))
        elif permitir_envio_imgbb:
            alvos.append(AlvoValidacao("imgbb", "real", obrig, hospedagem,
                                       "envio de teste AUTORIZADO (1 pixel, expira em 60 s)"))
        else:
            alvos.append(AlvoValidacao("imgbb", "real", obrig, None,
                                       "o ImgBB não tem consulta somente leitura: sem --imgbb (envio de teste de 1 pixel "
                                       "que expira em 60 s) a chave NÃO é verificada"))
    else:
        alvos.append(sem_credencial("instagram", obrig, ["IG_TOKEN", "IG_USER_ID"]))
        if obrig and config.modo("imgbb") != REAL:
            alvos.append(sem_credencial("imgbb", True, ["IMGBB_KEY"]))

    # AliExpress e Amazon (APIs oficiais de afiliados) e WhatsApp (Cloud API) — somente leitura
    from .fontes.aliexpress import AliExpressFonte
    from .fontes.amazon import AmazonFonte
    from ..servicos.whatsapp import ClienteWhatsApp
    fabricas = {
        "aliexpress": (["ALIEXPRESS_APP_KEY", "ALIEXPRESS_APP_SECRET", "ALIEXPRESS_TRACKING_ID"], lambda: AliExpressFonte(
            config.credencial("aliexpress", "APP_KEY"), config.credencial("aliexpress", "APP_SECRET"),
            config.credencial("aliexpress", "TRACKING_ID"), ro("aliexpress"))),
        "amazon": (["AMAZON_CREDENTIAL_ID", "AMAZON_CREDENTIAL_SECRET", "AMAZON_PARTNER_TAG", "AMAZON_TOKEN_URL",
                    "AMAZON_API_URL"], lambda: AmazonFonte(
            config.credencial("amazon", "CREDENTIAL_ID"), config.credencial("amazon", "CREDENTIAL_SECRET"),
            config.credencial("amazon", "PARTNER_TAG"), config.credencial("amazon", "TOKEN_URL"),
            config.credencial("amazon", "API_URL"), ro("amazon"))),
        "whatsapp": (["WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_TEMPLATE"], lambda: ClienteWhatsApp(
            config.credencial("whatsapp", "TOKEN"), config.credencial("whatsapp", "PHONE_ID"),
            config.credencial("whatsapp", "TEMPLATE"), ro("whatsapp"))),
    }
    for nome, (vars_, fabrica) in fabricas.items():
        obrig = nome in amb.ativas
        if config.modo(nome) == REAL:
            try:
                alvos.append(AlvoValidacao(nome, "real", obrig, fabrica()))
            except ErroConfiguracao as e:
                alvos.append(AlvoValidacao(nome, "real", obrig, None, str(e)))
            except Exception as e:  # ex.: URL da Amazon que não é https
                alvos.append(AlvoValidacao(nome, "real", obrig, None, f"configuração inválida: {e}"))
        else:
            alvos.append(sem_credencial(nome, obrig, vars_))

    alvos.append(AlvoValidacao("mercado_livre", "manual", False, ManualFonte("mercado_livre"),
                               "fonte manual: ofertas cadastradas no painel (sem API de afiliados); não entra na aprovação"))
    alvos.append(AlvoValidacao("magalu", "manual", False, ManualFonte("magalu"),
                               "fonte manual: o programa Parceiro Magalu não oferece API pública de afiliados"))
    return alvos
