"""Validação das integrações REAIS (somente leitura), com evidência gravada em arquivo.

  python -m app.main validar-integracoes [--imgbb] [--link URL]

  * Nada é publicado: Telegram (getMe/getChat/getChatMember), Instagram (me + cota), Shopee
    (busca de 1 oferta + relatório de conversões das últimas 24 h).
  * --imgbb  envia 1 imagem de 1 pixel que expira em 60 s (é um envio real: use só com autorização).
  * Atribuição: o link de afiliado da oferta de amostra (ou --link) é seguido redirecionamento a
    redirecionamento até o domínio final, exigindo os parâmetros de afiliado e o seu ID de afiliado.
  * Evidência: saida/validacao_integracoes.json (sem tokens; segredos mascarados pelo Log).
Integração só é considerada APROVADA quando este comando passa com as credenciais de produção.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .erros import ErroBot
from .logs import mascarar
from .integracoes.base import REAL
from .servicos.validacao import validar_link


def validar(integ, repo, log, *, imgbb=None, link_manual: str | None = None, http=None, saida: Path) -> int:
    resultado = {"executado_em": datetime.now(timezone.utc).isoformat(), "itens": []}
    falhou = False

    def registrar(nome, ok, detalhe):
        nonlocal falhou
        falhou |= not ok
        resultado["itens"].append({"integracao": nome, "aprovado": ok, "detalhe": mascarar(str(detalhe), log.segredos)})
        print(f"  {'✔' if ok else '✖'} {nome}: {mascarar(str(detalhe), log.segredos)}")

    for item in list(integ.fontes.values()) + list(integ.canais.values()):
        if getattr(item, "modo", None) != REAL:
            registrar(item.codigo, False, "não está em modo REAL (sem credencial ou ambiente de demonstração)")
            continue
        try:
            registrar(item.codigo, True, item.testar())
        except ErroBot as e:
            registrar(item.codigo, False, e)
    if imgbb is not None:
        try:
            registrar("imgbb", True, imgbb.testar())
        except ErroBot as e:
            registrar("imgbb", False, e)

    # atribuição do link de afiliado (redirecionamentos + identificadores de comissão)
    shopee = integ.fontes.get("shopee")
    link = link_manual or getattr(shopee, "amostra_link", None)
    mkt = next((m for m in repo.marketplaces() if m["codigo"] == "shopee"), None)
    if not link or not mkt:
        registrar("atribuicao_afiliado", False, "sem link de amostra (Shopee real) e sem --link")
    else:
        r = validar_link(link, mkt.get("dominios_afiliado") or [], http, demo=False, verificar_http=True,
                         padrao=mkt.get("padrao_afiliado"), dominios_destino=mkt.get("dominios_destino"),
                         parametros_afiliado=mkt.get("parametros_afiliado"), id_afiliado=mkt.get("id_afiliado"),
                         exigir_atribuicao=True)
        registrar("atribuicao_afiliado", r.valido, r.motivo or "destino final na loja com os identificadores de afiliado preservados")

    resultado["aprovado"] = not falhou
    saida.parent.mkdir(exist_ok=True)
    saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'APROVADO' if not falhou else 'NÃO APROVADO'} · evidência em {saida}")
    return 1 if falhou else 0
