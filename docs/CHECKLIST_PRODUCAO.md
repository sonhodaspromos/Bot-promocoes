# Checklist de produção: Bot de Ofertas v2

Marque cada item. **Só ligue as publicações reais quando todos os itens 🔴 estiverem feitos.** Passo a passo de cada item em [IMPLANTACAO.md](IMPLANTACAO.md).

## 🔴 Auditoria complementar (25/11/2026): ver [AUDITORIA_COMPLEMENTAR.md](AUDITORIA_COMPLEMENTAR.md) e [PENDENCIAS.md](PENDENCIAS.md)

- [ ] Projeto Supabase de produção **reativado** (estava pausado). Plano Pro avaliado (sem pausa automática).
- [ ] Backup feito **antes** de aplicar a migration `20261125120000_auditoria_complementar.sql`
- [ ] Migration 017 aplicada e `testes.resumo()` → **132 / 132 / 0**
- [ ] `select public.definir_email_dono('seu@email');` executado; conta do dono criada **com 2º fator (TOTP)**
- [ ] Supabase Auth: MFA TOTP **ligado**; cadastro público **desligado**; convite de teste funcionando
- [ ] Variáveis do GitHub: `AMBIENTE=producao` e `INTEGRACOES_ATIVAS` definidos; o bot **bloqueia** (não cai para demo) se faltar credencial
- [ ] `python -m app.main validar-integracoes --imgbb` → **APROVADO** (evidência JSON guardada)
- [ ] `marketplaces.parametros_afiliado` e `id_afiliado` preenchidos e conferidos com um link real (atribuição chega ao destino)
- [ ] Segredo `CLIQUE_SAL` (32+ caracteres) na função `r`; funções `r` e `disparar` atualizadas
- [ ] `HEALTHCHECK_URL` cadastrado e alerta externo testado (desligue o bot 30 min e confira se o alerta chega)
- [ ] Proteção da `main` com checks obrigatórios: backend, banco, painel, edge-functions
- [ ] Botão **⛔ Parar tudo** testado no painel (para, e só o dono retoma)
- [ ] Aviso de privacidade publicado (LGPD) e termos brasileiros da Shopee lidos
- [ ] #publi visível na 1ª linha das legendas (CONAR, maio/2026)

## 🔴 Bloqueantes (sem isso, não ligar)

- [x] Migration `20261122140850_manutencao_diaria.sql` aplicada (`select public.manutencao_diaria();` responde)
- [x] Migrations `20261122141500_auditoria_correcoes.sql` e `20261123120000_cupons_campanhas.sql` aplicadas
- [x] Testes do banco carregados (00 a 10) e `select * from testes.resumo();` → **118 / 118 / 0**
- [ ] `python -m app.main testar-integracoes` → `REAL ✔` em **cada** integração que será usada
- [ ] Formato do link de afiliado (`padrao_afiliado`) conferido com um link real de cada loja
- [ ] Bot do Telegram é **admin** do canal, com permissão de publicar **e editar**
- [ ] Conta do Instagram é **profissional** e o token tem permissão de publicar
- [ ] Secrets obrigatórios cadastrados: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_DB_URL`, `BACKUP_SENHA`
- [ ] `BACKUP_SENHA` guardada **fora** do GitHub (gerenciador de senhas)
- [ ] 1ª execução do **Backup do banco** verde ("✔ backup legível")
- [ ] 1ª execução do **Bot de ofertas** (origem teste) verde, com "✔ tudo certo" no painel
- [ ] Workflow **Testes** verde

## 🟠 Segurança

- [ ] Cadastro de novos usuários **desligado** no Supabase (Authentication → Allow new users to sign up). Desde a migration 017 o banco também só aceita convidados.
- [ ] Site URL do Supabase = endereço do painel
- [ ] Tokens do GitHub (`GH_PAT`, `GH_TOKEN`) são **fine-grained**, só deste repositório, com a permissão mínima
- [ ] Nenhum arquivo `.env` foi enviado ao GitHub (o `.gitignore` já bloqueia; confira a lista de arquivos)
- [x] `limpar_teste_e2e.sql` rodado (remove `pg_net` do schema público)
- [x] Advisor de segurança do Supabase revisado: só os 3 avisos aceitos (`salvar_credencial`, `remover_credencial`, `transferir_posse`)

## 🟡 Operação 24 h

- [ ] **Manter agendamentos ativos** rodado 1 vez à mão (✔ nos 5 workflows)
- [ ] **Manutenção diária** rodada 1 vez à mão (verde)
- [ ] **Renovar token do Instagram** rodado 1 vez à mão (se usar Instagram)
- [ ] E-mail de notificação do GitHub ativo (Settings → Notifications → Actions → "Send notifications for failed workflows only")
- [ ] Limites diários conferidos (Supabase → Table Editor → `canais`): Telegram ≤ 200, Stories ≤ 45, Feed = 1 (cota do Instagram: 50 em 24 h)
- [ ] Horário de publicação e intervalo mínimo conferidos (Ajustes → Publicação)
- [ ] Cupons conferidos (Ajustes → Cupons e campanhas): horário, máximo de stories por dia, Telegram

## 🟢 Primeiros dias

- [ ] Aprovação manual **ligada** nos 2 primeiros dias, conferindo cada post
- [ ] Clique de teste em um link publicado → abre a loja **com** a atribuição de afiliado (confira no seu painel de afiliado no dia seguinte)
- [ ] Painel → Resumo sem alertas vermelhos
- [ ] Painel → Logs sem ERRO/CRÍTICO repetidos
- [ ] Depois de 7 dias: comissões aparecendo (Shopee) ou registradas à mão (Mercado Livre)
- [ ] Depois de 30 dias: restauração do backup treinada num projeto de teste
- [ ] Depois de 30 dias: revisar o advisor de desempenho (índices sem uso)

## Critério de "pronto"

O sistema está **pronto para produção** quando todos os itens 🔴 e 🟠 estiverem marcados e a primeira semana (🟢) passar sem alerta vermelho no painel.
