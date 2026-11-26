"""Preenchimento estrito dos prompts: só as variáveis conhecidas; nada de format() solto."""
from __future__ import annotations

import re

VARIAVEIS = ("titulo", "loja", "categoria", "preco", "preco_antigo", "desconto", "cupom", "preco_com_cupom",
             "pix", "tom_de_voz", "dados")
_RE = re.compile(r"\{([a-z_]+)\}")


class PromptInvalido(ValueError):
    pass


def renderizar(modelo: str, valores: dict[str, str]) -> str:
    """Troca {variavel} pelo valor. Variável desconhecida = erro (o banco também recusa ao salvar)."""
    desconhecidas = sorted({m for m in _RE.findall(modelo) if m not in VARIAVEIS})
    if desconhecidas:
        raise PromptInvalido(f"variáveis desconhecidas no prompt: {', '.join(desconhecidas)}")
    return _RE.sub(lambda m: str(valores.get(m.group(1), "")), modelo)
