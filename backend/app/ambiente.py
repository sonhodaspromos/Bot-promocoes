"""Ambientes isolados: demonstração, homologação e produção.

  AMBIENTE=demonstracao  tudo simulado; credenciais reais são ignoradas; nada sai do bot.
  AMBIENTE=homologacao   lojas e links são consultados de verdade (somente leitura), mas os canais
                         são SEMPRE simulados: nenhum post real, mesmo com tokens configurados.
  AMBIENTE=producao      só integrações reais. Cada integração em INTEGRACOES_ATIVAS precisa ter
                         credenciais; se faltar, o bot PARA com alerta crítico. Nunca troca
                         silenciosamente por demonstração.

O banco também declara o próprio ambiente (configuração `sistema.ambiente`): um bot de homologação
apontado para o banco de produção (ou o contrário) é recusado antes de qualquer etapa.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .erros import ErroConfiguracao

AMBIENTES = ("demonstracao", "homologacao", "producao")
INTEGRACOES = ("shopee", "telegram", "instagram", "aliexpress", "amazon", "whatsapp")
CANAIS_DA_INTEGRACAO = {"telegram": ["telegram"], "instagram": ["instagram_story", "instagram_feed"]}


@dataclass
class Ambiente:
    nome: str
    ativas: list[str] = field(default_factory=list)

    @property
    def producao(self) -> bool:
        return self.nome == "producao"

    @property
    def canais_desativados(self) -> list[str]:
        """Canais que existem no banco mas não estão em INTEGRACOES_ATIVAS (ficam 'desativado' em produção)."""
        if not self.producao:
            return []
        return [c for integ, canais in CANAIS_DA_INTEGRACAO.items() if integ not in self.ativas for c in canais]

    def ajustar(self, cfg: dict) -> dict:
        """Regras que o painel não pode afrouxar em produção."""
        cfg = dict(cfg)
        if self.producao:
            cfg["monitor.verificar_links"] = True
            cfg["links.exigir_atribuicao"] = True
            cfg["inteligencia.exigir_desconto_comprovado"] = True
        else:
            cfg.setdefault("links.exigir_atribuicao", False)
        return cfg


def ler_ambiente(env: dict) -> Ambiente:
    nome = (env.get("AMBIENTE") or "").strip().lower()
    demo_forcado = (env.get("MODO_DEMO") or "").strip().lower() in ("1", "true", "sim", "yes")
    if not nome:
        raise ErroConfiguracao("Defina a variável AMBIENTE (demonstracao, homologacao ou producao). "
                               "Sem ela o bot não roda, para nunca publicar no ambiente errado.")
    if nome not in AMBIENTES:
        raise ErroConfiguracao(f"AMBIENTE inválido: {nome!r}. Use demonstracao, homologacao ou producao.")
    if nome == "producao" and demo_forcado:
        raise ErroConfiguracao("Conflito: AMBIENTE=producao com MODO_DEMO ligado. Remova MODO_DEMO.")
    bruto = (env.get("INTEGRACOES_ATIVAS") or "").replace(" ", "").lower()
    ativas = [i for i in bruto.split(",") if i]
    desconhecidas = [i for i in ativas if i not in INTEGRACOES]
    if desconhecidas:
        raise ErroConfiguracao(f"INTEGRACOES_ATIVAS com nome desconhecido: {', '.join(desconhecidas)} "
                               f"(válidos: {', '.join(INTEGRACOES)}).")
    if nome == "producao" and not ativas:
        raise ErroConfiguracao("Em produção, defina INTEGRACOES_ATIVAS (ex.: shopee,telegram,instagram) "
                               "com as integrações que devem funcionar de verdade.")
    return Ambiente(nome, ativas)


def conferir_banco(amb: Ambiente, cfg_banco: dict) -> None:
    """O banco precisa declarar o mesmo ambiente do bot."""
    declarado = cfg_banco.get("sistema.ambiente")
    if not declarado:
        if amb.nome == "demonstracao":
            return
        raise ErroConfiguracao("O banco não declara o próprio ambiente (sistema.ambiente). Aplique a migration 017.")
    if declarado != amb.nome:
        raise ErroConfiguracao(f"Este bot está em AMBIENTE={amb.nome}, mas o banco é de {declarado}. "
                               "Bloqueado para não misturar dados nem publicar no lugar errado.")
