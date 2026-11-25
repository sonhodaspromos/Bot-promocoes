# Pendências após a auditoria complementar (25/11/2026)

**Legenda:**
- **🔴** bloqueia o lançamento.
- **🟠** fazer antes de escalar.
- **🟡** melhoria.
- **[você]** precisa da sua ação ou autorização.
- **[técnico]** pode ser feito por mim com autorização.

## 🔴 Bloqueantes

| # | Pendência | Quem | Como |
|---|---|---|---|
| P-1 | Reativar o projeto Supabase de produção (está pausado) | [você] | Supabase → projeto → "Restore project". Avaliar o plano Pro (US$ 25/mês, sem pausa automática e com backup diário). |
| P-2 | Aplicar a migration `20261125120000_auditoria_complementar.sql` em produção | [você autoriza] [técnico] | Antes: backup. Depois: rodar `tests/11_auditoria_complementar.sql` e `select * from testes.resumo()` (esperado 132/132). |
| P-3 | Validar as integrações reais com credenciais | [você] [técnico] | `AMBIENTE=homologacao python -m app.main validar-integracoes --imgbb`. Guardar `saida/validacao_integracoes.json`. Só "APROVADO" libera. |
| P-4 | Confirmar com um link real da Shopee os parâmetros de afiliado que chegam ao destino e o seu ID de afiliado | [você] [técnico] | Preencher `marketplaces.parametros_afiliado` e `id_afiliado`. Confirmar `pageInfo`/`scrollId` no relatório de conversões. |
| P-5 | Definir o e-mail do dono, criar a conta e cadastrar o 2º fator | [você] | SQL Editor: `select public.definir_email_dono('seu@email');` → criar a conta no painel → cadastrar o TOTP. |
| P-6 | Variáveis do GitHub | [você] | Settings → Variables: `AMBIENTE=producao` e `INTEGRACOES_ATIVAS` (ex.: `shopee,telegram`). Secret `HEALTHCHECK_URL`. |
| P-7 | Supabase Auth | [você] | Ligar MFA TOTP. Desligar "Allow new users to sign up" (os convites continuam funcionando pelo gatilho; teste antes). |
| P-8 | Segredo `CLIQUE_SAL` (32 caracteres aleatórios ou mais) na Edge Function `r` | [você] | Sem ele, todos os cliques contam como suspeitos. |
| P-9 | Monitor externo | [você] | Criar um check em healthchecks.io (grátis), período de 10 min e tolerância de 30 min, com alerta por e-mail ou Telegram. Colar a URL em `HEALTHCHECK_URL`. |
| P-10 | Proteção da branch main | [você] | Settings → Branches → regra para `main`: "Require a pull request" e "Require status checks": `backend`, `banco`, `painel`, `edge-functions`. |
| P-11 | Aviso de privacidade (LGPD) | [você] [técnico] | Página pública com: dados coletados (hash pseudonimizado de IP + navegador e e-mails da equipe), finalidade, base legal, retenção (365 dias) e contato. Link na bio e no canal. |
| P-12 | Termos brasileiros do Programa de Afiliados da Shopee | [você] | Confirmar que a automação via Open API é permitida e que o nome e a foto do perfil não usam a marca. |
| P-13 | Deploy das Edge Functions `r` e `disparar` atualizadas | [você autoriza] [técnico] | Dependem de `_compartilhado/regras.ts` e da RPC `minha_sessao` (migration 017). |
| P-14 | Publicar o painel atualizado | [você autoriza] | Merge na main, depois que P-2 e P-13 estiverem prontos. O painel novo exige 2º fator e a RPC `minha_sessao`. |

## 🟠 Antes de escalar

| # | Pendência |
|---|---|
| P-15 | Primeiros 7 dias com `publicacao.aprovacao_manual = true`, só no Telegram. |
| P-16 | Conferir semanalmente `comissoes_importacoes` (status `divergente`) e o extrato de pagamento da Shopee. |
| P-17 | Testar a restauração do backup do GitHub num projeto Supabase de teste (a local já foi testada). |
| P-18 | Guardar todas as credenciais num gerenciador de senhas. O backup do GitHub não inclui o Vault nem os usuários (é intencional). |
| P-19 | Rodar o job `banco` do CI uma vez em PostgreSQL 17 e confirmar que fica verde. Localmente foi validado em PG 16. |
| P-20 | Rodar `deno check` das Edge Functions no CI. Não havia Deno no ambiente da auditoria. |

## 🟡 Melhorias

- Revalidar o DNS no momento da conexão (mitigação completa de DNS rebinding).
- Antifraude de cliques por reputação de IP: hoje há deduplicação e limites por visitante e por link.
- Reconciliação automática com o extrato de **pagamento** (hoje é com o relatório de conversões).
- Mercado Livre: a importação de comissões continua manual (não há API de afiliados pública equivalente).
