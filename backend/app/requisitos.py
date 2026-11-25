"""Requisitos do banco para PUBLICAR DE VERDADE (conferidos antes de qualquer envio real).

O bot pergunta ao banco (RPC public.esquema_publicacao, migration 018) a versão do esquema, as RPCs,
as configurações e as colunas existentes. Faltou qualquer item, nenhum envio real acontece: sem a
reserva exclusiva (fila_reservar_envio / cupom_reservar_envio) o bot não tem como garantir que um
post não sai duas vezes, e sem a coluna de atribuição não tem como provar a comissão.
"""
from __future__ import annotations

from .erros import ErroBanco

VERSAO_MINIMA = 18
RPCS_PUBLICACAO = {"pegar_proximas_publicacoes", "fila_reservar_envio", "fila_devolver", "fila_marcar_incerta",
                   "cupom_reservar_envio", "ativar_parada_emergencia", "registrar_clique"}
CONFIGS_PUBLICACAO = {"sistema.ambiente", "sistema.parada_emergencia", "links.validade_atribuicao_horas"}
COLUNAS_PUBLICACAO = {"links_afiliados.atribuicao_verificada_em", "fila_publicacoes.publicando_desde",
                      "marketplaces.parametros_afiliado", "marketplaces.id_afiliado", "marketplaces.dominios_destino"}


def faltando_para_publicar(repo) -> list[str]:
    """Lista do que falta no banco. Vazia = pode publicar de verdade."""
    try:
        e = repo.esquema_publicacao() or {}
    except ErroBanco as erro:
        return [f"RPC esquema_publicacao indisponível ({erro}): aplique a migration 018"]
    faltas = []
    try:
        versao = int(e.get("versao") or 0)
    except (TypeError, ValueError):
        versao = 0
    if versao < VERSAO_MINIMA:
        faltas.append(f"esquema na versão {versao}, exigida {VERSAO_MINIMA}")
    faltas += [f"RPC {r}" for r in sorted(RPCS_PUBLICACAO - set(e.get("rpcs") or []))]
    faltas += [f"configuração {c}" for c in sorted(CONFIGS_PUBLICACAO - set(e.get("configuracoes") or []))]
    faltas += [f"coluna {c}" for c in sorted(COLUNAS_PUBLICACAO - set(e.get("colunas") or []))]
    return faltas
