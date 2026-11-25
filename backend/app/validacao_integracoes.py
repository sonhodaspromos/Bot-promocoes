"""Validação das integrações REAIS em modo SOMENTE LEITURA, com evidência gravada em arquivo.

  AMBIENTE=homologacao INTEGRACOES_ATIVAS=shopee,telegram python -m app.main validar-integracoes [--imgbb] [--link URL]

O que é chamado (nada é publicado; o cliente HTTP barra qualquer escrita antes de sair do bot):
  * Shopee:    busca de 1 oferta + relatório de conversões das últimas 24 h (consultas GraphQL, sem mutation)
  * Telegram:  getMe, getChat, getChatMember (bot precisa ser admin com permissão de publicar e editar)
  * Instagram: GET me (conta profissional) + GET content_publishing_limit (cota restante)
  * ImgBB:     não existe consulta de leitura. Só com --imgbb (envio de 1 pixel que expira em 60 s)
  * Atribuição: o link de afiliado de amostra é seguido redirecionamento a redirecionamento até a loja,
               exigindo o parâmetro e o SEU ID de afiliado no destino final
  * Banco:     versão do esquema, RPCs, configurações e colunas exigidas para publicar de verdade

Categorias: real (validada de verdade) · manual (Mercado Livre: não entra na conta) · simulada ·
desativada (sem credencial e fora de INTEGRACOES_ATIVAS: não reprova).

APROVADO só quando: (1) há pelo menos UMA integração obrigatória (INTEGRACOES_ATIVAS) e (2) TODAS as
obrigatórias, a atribuição (se a Shopee for obrigatória) e o esquema do banco foram efetivamente
verificados com sucesso. "Não verificado" reprova. Evidência: saida/validacao_integracoes.json (sem segredos).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .erros import ErroBot
from .logs import mascarar
from .requisitos import faltando_para_publicar
from .servicos.validacao import LinkTemporariamenteIndisponivel, validar_link

APROVADO, REPROVADO, NAO_VERIFICADO, IGNORADO = "aprovado", "reprovado", "nao_verificado", "ignorado"


def validar(alvos, repo, log, *, link_manual: str | None = None, http=None, saida: Path, ambiente: str = "") -> int:
    resultado = {"executado_em": datetime.now(timezone.utc).isoformat(), "ambiente": ambiente,
                 "modo": "somente_leitura", "itens": []}

    def registrar(nome, categoria, obrigatoria, status, detalhe):
        texto = mascarar(str(detalhe), log.segredos)
        resultado["itens"].append({"integracao": nome, "categoria": categoria, "obrigatoria": obrigatoria,
                                   "status": status, "detalhe": texto})
        simbolo = {APROVADO: "✔", REPROVADO: "✖", NAO_VERIFICADO: "?", IGNORADO: "·"}[status]
        marca = " (obrigatória)" if obrigatoria else ""
        print(f"  {simbolo} {nome} [{categoria}]{marca}: {texto}")

    shopee = None
    for a in alvos:
        if a.categoria in ("manual", "simulada", "desativada"):
            registrar(a.nome, a.categoria, False, IGNORADO, a.motivo or a.categoria)
            continue
        if a.cliente is None:
            status = NAO_VERIFICADO if "não é verificada" in a.motivo.lower() else REPROVADO
            registrar(a.nome, a.categoria, a.obrigatoria, status if a.obrigatoria else IGNORADO, a.motivo)
            continue
        try:
            detalhe = a.cliente.testar()
            registrar(a.nome, a.categoria, a.obrigatoria, APROVADO, (detalhe + (f" · {a.motivo}" if a.motivo else "")))
            if a.nome == "shopee":
                shopee = a
        except ErroBot as e:
            registrar(a.nome, a.categoria, a.obrigatoria, REPROVADO, e)

    # atribuição do link de afiliado (obrigatória quando a Shopee é obrigatória)
    shopee_obrig = any(a.nome == "shopee" and a.obrigatoria for a in alvos)
    link = link_manual or (getattr(shopee.cliente, "amostra_link", None) if shopee else None)
    mkt = next((m for m in repo.marketplaces() if m["codigo"] == "shopee"), None)
    if not link or not mkt:
        registrar("atribuicao_afiliado", "real", shopee_obrig, REPROVADO if shopee_obrig else IGNORADO,
                  "sem link de amostra (Shopee real) e sem --link")
    else:
        try:
            r = validar_link(link, mkt.get("dominios_afiliado") or [], http, demo=False, verificar_http=True,
                             padrao=mkt.get("padrao_afiliado"), dominios_destino=mkt.get("dominios_destino"),
                             parametros_afiliado=mkt.get("parametros_afiliado"), id_afiliado=mkt.get("id_afiliado"),
                             exigir_atribuicao=True)
            ok = r.valido and r.atribuicao
            registrar("atribuicao_afiliado", "real", shopee_obrig or bool(link_manual), APROVADO if ok else REPROVADO,
                      "parâmetro e ID de afiliado chegaram ao site da loja" if ok else (r.motivo or "sem comprovação"))
        except LinkTemporariamenteIndisponivel as e:
            registrar("atribuicao_afiliado", "real", shopee_obrig, NAO_VERIFICADO, f"loja indisponível agora: {e}")

    # requisitos do banco para publicar de verdade (migrations e RPCs)
    faltas = faltando_para_publicar(repo)
    registrar("esquema_banco", "real", True, REPROVADO if faltas else APROVADO,
              "faltando: " + "; ".join(faltas) if faltas else "migrations e RPCs de publicação presentes (versão ≥ 18)")

    integracoes_obrig = [i for i in resultado["itens"] if i["obrigatoria"]
                         and i["integracao"] not in ("esquema_banco", "atribuicao_afiliado")]
    obrigatorios = [i for i in resultado["itens"] if i["obrigatoria"]]
    motivos = []
    if not integracoes_obrig:
        motivos.append("nenhuma integração obrigatória: defina INTEGRACOES_ATIVAS (ex.: shopee,telegram)")
    motivos += [f"{i['integracao']}: {i['status']}" for i in obrigatorios if i["status"] != APROVADO]
    aprovado = not motivos
    resultado["aprovado"] = aprovado
    resultado["motivos_reprovacao"] = motivos
    resultado["validadas_de_verdade"] = [i["integracao"] for i in resultado["itens"] if i["status"] == APROVADO]
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'APROVADO' if aprovado else 'NÃO APROVADO'} · evidência em {saida}")
    for m in motivos:
        print(f"  - {m}")
    return 0 if aprovado else 1
