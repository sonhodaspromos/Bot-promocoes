"""Motor de IA (P0): textos de divulgação escritos por IA, sempre conferidos contra os dados reais.

Módulos
-------
* provedor.py  — chamada à API do Claude (HTTP), tokens e erros. `ProvedorFalso` para testes.
* seguranca.py — dados da oferta saneados (anti prompt injection) e validação da resposta (nada inventado).
* prompts.py   — preenchimento estrito dos modelos versionados (só variáveis conhecidas).
* servico.py   — etapa da rodada: orçamento, cache/idempotência, disjuntor, registro e aprovação.

Regras que a IA NUNCA muda: preços, descontos, links, #publi, limites e validações. Ela escreve só a
chamada (título) e uma frase; os valores continuam vindo do bot. Se a resposta inventar qualquer coisa,
é descartada e o post sai com o texto padrão.
"""
