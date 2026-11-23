# Implantação em produção: Bot de Ofertas v2

Siga **na ordem**. Cada passo diz como conferir se deu certo. Para marcar o progresso, use o [checklist de produção](CHECKLIST_PRODUCAO.md).

> 💡 Faça pelo computador, se puder. No celular, abra o GitHub e o Supabase no navegador em "modo computador".

## Visão geral

```
GitHub Actions (a cada 10 min) ──► backend Python ──► Supabase (banco + Vault + funções)
        │                              │
        │                              ├──► Shopee API (ou DEMO)
        │                              ├──► Telegram Bot API
        │                              └──► Instagram Graph API (imagem via ImgBB)
        ├── manter_ativo (semanal)   ├── backup (diário, criptografado)
        ├── manutencao (diária)      └── renovar_token (dias 1 e 15)
GitHub Pages ──► painel (login Supabase, RLS)
```

## 1. Banco de dados (Supabase, projeto bot-promocoes)

O banco já existe, com as migrations 000 a 014 aplicadas. Falta aplicar 2 arquivos.

1. Supabase → **SQL Editor** → **New query**.
2. Cole e rode (**Run**), nesta ordem:
   1. `supabase/migrations/20261122140850_manutencao_diaria.sql`
   2. `supabase/migrations/20261122141500_auditoria_correcoes.sql`
3. Rode os testes: cole o arquivo `supabase/tests/09_auditoria.sql` → Run. Depois rode:
   ```sql
   select * from testes.resumo();                               -- esperado: 107 / 107 / 0
   select * from testes.rodar('auditoria') where not ok;        -- esperado: nenhuma linha
   ```
4. *(recomendado)* Rode `supabase/manutencao/limpar_teste_e2e.sql`. Ele apaga os dados do teste de cliques e a extensão `pg_net`.
5. Confira a manutenção: `select public.manutencao_diaria();` deve devolver um JSON com as contagens.

**Deu certo se:** `testes.resumo()` mostra 0 reprovados.

## 2. Repositório no GitHub

1. **New repository** → nome `bot-promocoes` → **Public** → Create.
   - Público = GitHub Actions grátis e sem limite de minutos. Segredos ficam em "Secrets", os dados no banco (login + RLS), e os logs são mascarados.
   - ⚠ **Logs do Actions de repositório público podem ser vistos por qualquer pessoa.** O bot mascara tokens, mas não coloque segredos em mensagens de commit nem em arquivos.
2. Envie **todo o conteúdo do zip**: `.github`, `backend`, `frontend`, `supabase`, `docs` e os arquivos da raiz.
   - Se a pasta `.github` não subir, crie cada workflow à mão: **Add file → Create new file** → `.github/workflows/<nome>.yml`. São 7: `bot`, `testes`, `painel`, `manutencao`, `renovar_token`, `backup`, `manter_ativo`.
3. **Settings → Actions → General → Workflow permissions:** deixe "Read repository contents". Cada workflow pede só o que precisa.

**Deu certo se:** a aba **Actions** lista os 7 workflows.

## 3. Segredos (GitHub → Settings → Secrets and variables → Actions)

| Nome | De onde vem | Obrigatório |
|---|---|---|
| `SUPABASE_URL` | `https://tafmitwmtjbdopchycac.supabase.co` | ✅ |
| `SUPABASE_SERVICE_KEY` | Supabase → Project Settings → API Keys → **secret key** (`sb_secret_…`) | ✅ |
| `SUPABASE_DB_URL` | Supabase → **Connect** → **Session pooler** (porta 5432) → copie a URI e troque `[YOUR-PASSWORD]` pela senha do banco | ✅ backup |
| `BACKUP_SENHA` | senha **aleatória com 20+ caracteres**. Guarde também num gerenciador de senhas: sem ela, o backup não abre | ✅ backup |
| `SHOPEE_APP_ID`, `SHOPEE_SECRET` | Painel de afiliado Shopee → Open API | para a Shopee |
| `TELEGRAM_TOKEN`, `TELEGRAM_CANAL` | @BotFather e `@seu_canal`. O bot precisa ser **admin** com permissão de **publicar e editar** | para o Telegram |
| `IG_TOKEN`, `IG_USER_ID` | Meta for Developers → app → API do Instagram com login do Instagram (conta profissional) | para o Instagram |
| `IMGBB_KEY` | api.imgbb.com | para o Instagram |
| `GH_PAT` | GitHub → token **fine-grained**, só neste repositório, com **Secrets: Read and write** | para renovar o token do Instagram |

- Sem as credenciais de uma integração, ela fica em **modo demonstração**: nada é publicado de verdade e o painel mostra a faixa roxa.
- Shopee, Telegram, Instagram e ImgBB também podem ser cadastrados pelo painel (Ajustes → Credenciais), cifrados no Vault. Os Secrets do GitHub têm prioridade.
- Por que o "Session pooler": a conexão direta do Supabase usa IPv6, que os servidores do GitHub não têm.

## 4. Painel (GitHub Pages)

1. **Settings → Pages → Source: GitHub Actions**.
2. **Actions → Publicar painel → Run workflow**.
3. Abra `https://sonhodaspromos.github.io/bot-promocoes/` → **Criar meu acesso**. **O primeiro cadastro vira o dono.**
4. Supabase → **Authentication → URL Configuration → Site URL** = endereço do painel.
5. Supabase → **Authentication → Sign In / Providers** → desligue **Allow new users to sign up** (ninguém mais consegue criar conta).

## 5. Botão "⚡ Publicar agora"

Supabase → **Edge Functions → disparar → Secrets**:

| Nome | Valor |
|---|---|
| `GH_TOKEN` | token fine-grained com **Actions: Read and write**, só neste repositório |
| `GH_REPO` | `sonhodaspromos/bot-promocoes` |

## 6. Testar antes de ligar de verdade

1. **Actions → Testes → Run workflow**: testes do backend e simulação demo. Esperado: tudo verde.
2. No computador, com `.env` preenchido (copie de `.env.example`):
   ```bash
   cd backend && pip install -r requirements.txt
   python -m app.main testar-integracoes
   ```
   Cada integração real precisa mostrar `REAL ✔`. Se a Shopee acusar campo desconhecido, o erro mostra o nome exato a ajustar em `backend/app/integracoes/fontes/shopee.py`.
3. **Confirme os formatos de link de afiliado** com um link real gerado no seu painel de afiliado:
   ```sql
   select codigo, dominios_afiliado, padrao_afiliado from public.marketplaces;
   ```
   Padrões instalados:
   - Shopee: `^https://(s\.shopee\.com\.br|shope\.ee)/…`
   - Mercado Livre: `^https://mercadolivre\.com/sec/…`

   Se o seu link for diferente, ajuste:
   ```sql
   update public.marketplaces set padrao_afiliado = '^https://…' where codigo = 'mercado_livre';
   ```
4. **Actions → Bot de ofertas → Run workflow (origem: teste)**. Confira no painel: **Resumo → Última rodada** (✔) e **Logs**.
5. **Actions → Backup do banco → Run workflow**. Esperado: "✔ backup legível: N tabelas" e um artefato `backup-banco`.
6. **Actions → Manter agendamentos ativos → Run workflow**. Esperado: "✔ … ativo" para os 5 workflows agendados.

## 7. Ligar em produção

1. Cadastre os tokens reais (passo 3). Na rodada seguinte, o bot passa cada integração para "real" sozinho.
2. Comece com aprovação manual (**Ajustes → Publicação → aprovação manual**) por 1 ou 2 dias. Aprove pelo painel e confira os posts.
3. Desligue a aprovação manual quando estiver confiante.

## 8. Operação do dia a dia

| O que acontece | Onde você vê | O que fazer |
|---|---|---|
| ⛔ "O bot não roda há X minutos" | Painel → Resumo | GitHub → Actions → veja se o "Bot de ofertas" está desativado (clique em **Enable workflow**) ou falhando |
| 🔑 "credencial recusada" | Painel → Resumo + e-mail do GitHub | Gere um token novo e atualize o Secret (ou Ajustes → Credenciais). As ofertas esperam na fila. |
| ⚠ "resultado incerto" | Painel → Resumo | Abra o canal: se o post saiu, nada a fazer; se não saiu, reaprove a oferta |
| E-mail "workflow failed" do GitHub | sua caixa de entrada | Abra o link e veja o passo que falhou. Os detalhes também ficam no painel → Logs. |
| Token do Instagram vence em 60 dias | automático | O workflow renova nos dias 1 e 15. Se falhar, chega o e-mail e o painel mostra 🔑. |

## 9. Restaurar um backup

1. GitHub → **Actions → Backup do banco** → abra a execução do dia → baixe o artefato `backup-banco` (vem um `.zip`).
2. No computador (Linux/Mac, ou WSL no Windows):
   ```bash
   unzip backup-banco.zip
   gpg --decrypt bot-ofertas-AAAAMMDD-HHMM.sql.gz.gpg | gunzip > backup.sql   # pede a BACKUP_SENHA
   ```
3. **Num projeto Supabase novo (ou vazio)**, rode o `backup.sql` com o psql 17:
   ```bash
   psql "SUA_SUPABASE_DB_URL" -f backup.sql
   ```
4. Depois da restauração:
   - **Credenciais:** cadastre de novo pelo painel. O Vault não vai no backup, de propósito.
   - **Contas do painel:** recrie. O primeiro cadastro vira dono.
   - **Segredos:** atualize `SUPABASE_URL` e as chaves nos Secrets, se o projeto mudou.
5. Rode `select * from testes.resumo();` (se o schema `testes` foi restaurado) e uma rodada de teste.

> Treine a restauração uma vez, num projeto de teste, antes de precisar dela de verdade.

## 10. Atualizar o sistema depois

1. Mudanças no banco: novo arquivo em `supabase/migrations/` → SQL Editor → rodar os testes.
2. Mudanças no backend: o workflow **Testes** roda sozinho a cada envio. Só publique se ficar verde.
3. Mudanças no painel: o workflow **Publicar painel** atualiza o site sozinho.
