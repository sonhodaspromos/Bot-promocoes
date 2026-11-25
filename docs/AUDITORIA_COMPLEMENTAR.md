# Auditoria complementar obrigatória: Bot de Ofertas v2

**Data:** 25/11/2026 · **Branch:** `auditoria-complementar` (sem merge, sem push, sem deploy)
**Escopo:** técnica, operacional, financeira e de segurança, itens P0 (1–10) e P1 (11–20).

**Restrições respeitadas:**
- banco de produção NÃO alterado (o projeto `tafmitwmtjbdopchycac` está pausado e a migration 017 não foi aplicada);
- nenhuma oferta real publicada;
- nenhuma credencial exposta;
- nenhuma integração real considerada aprovada com base em teste simulado.

---

## 1. Resultado dos testes

| Bateria | Antes (main) | Agora | Como rodar |
|---|---|---|---|
| Backend (pytest) | 187 | **263 aprovados / 0 falhas** | `cd backend && python -m pytest -q` |
| Os 187 originais, sem alteração, contra o código novo | — | **183 aprovados / 4 reprovados (intencional, ver abaixo)** | — |
| Banco (RLS, SECURITY DEFINER, regras) | 118 | **132 / 132 / 0** | `supabase/homologacao/rodar.sh` |
| Concorrência, interrupção, parada de emergência, backup e restauração | — | **10 / 10** | `supabase/homologacao/concorrencia_e_backup.sh` |
| Painel (Playwright, dados simulados) | 45 | **60 / 60** | `python frontend/testes/testar_painel.py` |
| Regras das Edge Functions | — | **5 / 5** | `node --experimental-strip-types --test supabase/functions/testes/regras.test.ts` |

**Os 4 testes originais que mudaram** (cada um comprovava comportamento errado ou usava um dublê irreal):

| Teste original | Motivo da mudança |
|---|---|
| `test_comissoes_ignoram_repetidas` | Exigia `ignore-duplicates`, que descartava cancelamentos e mudanças de status (achado C3/A5). |
| `test_comissoes_so_na_janela_das_6h_ou_forcado` | Exigia a janela de 20 minutos, que o cron atrasado perde (achado A6). |
| `test_link_que_redireciona_para_a_pagina_inicial...` | O dublê simulava redirecionamento automático. Agora cada salto é conferido (A1). |
| `test_mede_a_imagem_de_verdade...` | O dublê devolvia imagem sem Content-Type, que agora é recusada corretamente (A2). |

**Ambiente de homologação:**
- local: PostgreSQL 16 com simulação do Supabase (roles, `auth`, Vault cifrado);
- CI: PostgreSQL **17**, a mesma versão de produção (job `banco`).

---

## 2. Achados

**Legenda de gravidade:**
- **Crítica:** perda de dinheiro, de controle ou publicação enganosa.
- **Alta:** risco relevante com caminho de exploração ou falha silenciosa.
- **Média:** confiabilidade ou observabilidade.
- **Baixa:** melhoria.

> Os arquivos e linhas da coluna "Onde estava" referem-se à `main`. A correção está na branch `auditoria-complementar`.

### Críticos

#### C1. Projeto de produção do Supabase pausado

- **Onde estava:** infraestrutura (projeto `tafmitwmtjbdopchycac`, status INACTIVE).
- **Problema:** o plano gratuito pausa o projeto após 7 dias de pouca atividade. Com o projeto pausado, o painel, o bot, os cliques e as comissões ficam fora do ar.
- **Impacto:** perda total de operação e de cliques, e portanto de comissões.
- **Correção:**
  - NÃO reativado (exige sua autorização);
  - batimento externo (C-P15) para detectar a parada;
  - recomendação do plano Pro (ver §4).
- **Teste:** `test_batimento_*`.
- **Residual:** enquanto estiver no plano gratuito, pode pausar de novo.

#### C2. O primeiro cadastro virava dono, com o cadastro aberto

- **Onde estava:** `supabase/migrations/20261122140100_base_e_administradores.sql:73-82`.
- **Problema:** a chave pública fica no painel, que é público. Qualquer pessoa que chegasse antes do dono podia criar a conta e tomar o controle (credenciais, links e publicações).
- **Impacto:** sequestro total do bot e desvio de comissões.
- **Correção:** migration 017.
  - Gatilho `privado.autorizar_cadastro` (P0028): cadastro só por convite ou pelo e-mail do dono.
  - `definir_email_dono()` só pelo SQL Editor ou service_role, e uma única vez.
  - Tabela `convites` (só o dono gerencia).
  - Tela de convites no painel.
- **Teste:** banco `11_auditoria_complementar` (cadastro fechado, bootstrap do dono, convites, só o dono convida); painel "dono cria convite".
- **Evidência:** 132/132.
- **Residual:** desligar também "Allow new users to sign up" no Supabase (dupla barreira).

#### C3. A importação de comissões nunca funcionou

- **Onde estava:** `cliques_comissoes_config_logs.sql:118` (índice parcial) e `backend/app/repositorio.py:509`.
- **Problema:** o `ON CONFLICT (marketplace_id, referencia_externa)` do PostgREST não reconhece índice único parcial e falha com o erro 42P10. Reproduzido localmente.
- **Impacto:** nenhuma comissão era registrada, os relatórios ficavam zerados e não havia como conferir os pagamentos.
- **Correção:** constraint única completa `comissoes_referencia_unica`.
- **Teste:** banco "on_conflict exige índice completo" e "comissão repetida atualiza".
- **Evidência:** reproduzido antes, aprovado depois.
- **Residual:** confirmar com a API real (P-3).

#### C4. Os posts mostravam o "De R$" da loja mesmo quando a análise contestava o desconto

- **Onde estava:** `backend/app/servicos/textos.py:19-20` e a arte.
- **Problema:** o post usava o preço de referência informado pela loja, inclusive quando o motor indicava desconto inflado ou sem histórico.
- **Impacto:** publicidade enganosa (CDC art. 37), risco de denúncia, de bloqueio no programa de afiliados e de perda de confiança.
- **Correção:**
  - `aplicar_desconto_da_analise()` em `publicacao.py:376`;
  - `mostra_desconto()` nas legendas e na arte;
  - bloqueio "Desconto não comprovado", sempre ligado em produção (`motor.py:168`).
- **Teste:** `test_auditoria2_precos.py` (17 testes).
- **Residual:** depende da qualidade do histórico de preços nas primeiras semanas.

### Altos

#### A1. A validação de link seguia qualquer redirecionamento

- **Onde estava:** `backend/app/servicos/validacao.py:64` (`allow_redirects=True`).
- **Problema:**
  - permitia SSRF (rede interna e metadados da nuvem);
  - o domínio final não era conferido;
  - aceitava `http:`;
  - não verificava se o ID de afiliado chegava ao destino.
- **Impacto:** link sem atribuição publicado (venda sem comissão) e acesso a endereços internos.
- **Correção:**
  - `backend/app/rede.py:58,88`: cada salto é conferido; só https e porta 443; sem usuário na URL; todos os IPs resolvidos precisam ser públicos; no máximo 5 saltos.
  - `validacao.py:51`: domínio final, página inicial, parâmetros de afiliado e ID de outra conta.
- **Teste:** `test_auditoria2_rede.py` (28 testes, todos reprovados antes da correção).
- **Residual:** troca de DNS entre a checagem e a conexão (DNS rebinding) é mitigada, não eliminada. Os nomes dos parâmetros reais da Shopee precisam ser confirmados (P-4).

#### A2. Download de imagem sem limites

- **Onde estava:** `publicacao.py:272` (`_foto`).
- **Problema:** aceitava http, qualquer tamanho e qualquer formato, sem prazo e com risco de "bomba de pixels".
- **Impacto:** travamento ou estouro de memória do job, rodada perdida e ataque via imagem.
- **Correção:** `rede.py:114` (`baixar_imagem`):
  - só JPEG, PNG ou WebP;
  - até 5 MB, 40 MP e 8000 px por lado;
  - prazo de 15 s;
  - verificação pelo Pillow.
- **Teste:** `test_auditoria2_rede.py`.
- **Residual:** nenhum relevante.

#### A3. Volta silenciosa para o modo demonstração sem credencial

- **Onde estava:** `backend/app/integracoes/registro.py:50,59-60`.
- **Problema:** em produção, uma credencial ausente trocava o canal por demonstração sem avisar. O bot "rodava", mas não publicava.
- **Impacto:** dias sem publicar sem ninguém perceber, e perda de comissões.
- **Correção:**
  - `ambiente.py:52,74`: `AMBIENTE` obrigatório; produção exige `INTEGRACOES_ATIVAS`; o banco precisa ter `sistema.ambiente` igual ao do bot.
  - `registro.py:70`: credencial ausente gera "BLOQUEADO" com alerta.
  - Homologação nunca publica.
- **Teste:** `test_auditoria2_ambiente.py` (11).
- **Residual:** definir as variáveis no GitHub (P-6).

#### A4. Editor podia trocar domínios ou o ID de afiliado e desligar o #publi

- **Onde estava:** gatilhos e políticas (antes da 017).
- **Impacto:** desvio de comissão por alguém da equipe e infração ao CONAR.
- **Correção:**
  - `privado.proteger_campos_sensiveis` (P0027): só o dono altera;
  - `sistema.*` só muda por RPC;
  - campos travados no painel.
- **Teste:** banco "campos sensíveis"; painel "editor não altera domínios".
- **Residual:** nenhum.

#### A5. Mudanças de status das comissões eram ignoradas, e o relatório não era paginado

- **Onde estava:**
  - `repositorio.py:509` (ignore-duplicates);
  - `shopee.py` (`comissoes()`, só a 1ª página).
- **Problema:** cancelamentos não apareciam, e com mais de 500 vendas no período o resto era perdido.
- **Impacto:** comissão estimada acima da real e erro de caixa.
- **Correção:**
  - upsert com `merge-duplicates`, preservando o que já está marcado como pago;
  - paginação por `scrollId`, até 40 páginas, sem duplicar.
- **Teste:** `test_auditoria2_comissoes.py` (9).
- **Residual:** nomes `pageInfo`/`scrollId` a confirmar com a API real.

#### A6. A janela de 20 min das 6h fazia a importação diária falhar quando o cron atrasava

- **Onde estava:** `servicos/comissoes.py:24`.
- **Problema:** o GitHub atrasa crons com frequência.
- **Correção:** importação uma vez por dia, a partir das 6h, registrada em `comissoes_importacoes`.
- **Teste:** `test_importa_uma_vez_por_dia_mesmo_com_cron_atrasado`.

#### A7. Não havia reconciliação das comissões

- **Correção:**
  - `ServicoComissoes.reconciliar()` semanal: relê 90 dias, compara itens e totais, registra divergências e gera AVISO;
  - comando `comissoes --reconciliar`.
- **Teste:** `test_reconciliacao_*`.
- **Residual:** comparar com o extrato de pagamento da Shopee, que é manual.

#### A8. Inflação de cliques

- **Onde estava:**
  - `functions/r/index.ts:50` (o 1º X-Forwarded-For, que o visitante forja);
  - `:12` (sal padrão igual à URL pública);
  - deduplicação de só 30 s (`...logs.sql:84`).
- **Impacto:** métricas manipuláveis, decisões erradas sobre o que publicar e IP reversível pelo hash (LGPD).
- **Correção:**
  - IP vem do proxy (`cf-connecting-ip`/`x-real-ip` ou o último item do XFF);
  - sal secreto obrigatório (32 caracteres ou mais);
  - hash de IP + navegador;
  - visitante único por 24 h;
  - limites de 20 por visitante a cada 10 min e 120 por link por minuto;
  - suspeitos vão para `cliques_suspeitos`, fora das métricas.
- **Teste:** `regras.test.ts` (5); banco "dedupe e suspeitos", "excesso".
- **Residual:** quem usa muitos IPs distintos ainda consegue gerar cliques (só a comissão real é métrica confiável).

#### A9. Preços entre variações de produto não eram conferidos

- **Problema:** um produto "a partir de R$ 9,90" podia ter a variação anunciada a R$ 99.
- **Correção:** `priceMax` da Shopee; bloqueio quando a diferença passa de 5% (`motor.py:160`).
- **Teste:** `test_auditoria2_precos.py`.

#### A10. O preço não era conferido logo antes de publicar

- **Correção:** `publicacao.py:218` (`_preco_mudou`):
  - consulta a loja antes do envio real;
  - diferença acima de 0,5% ou produto indisponível: atualiza e devolve para nova análise;
  - sem consulta automática disponível, exige verificação nas últimas 24 h.
- **Teste:** `test_auditoria2_precos.py`.

#### A11. Não havia botão de emergência

- **Correção:**
  - RPCs `ativar_parada_emergencia` (editor ou dono) e `desativar_parada_emergencia` (só o dono);
  - a fila não entrega itens;
  - o backend relê o banco antes de cada envio real; se o banco estiver indisponível, considera parado;
  - botão "⛔ Parar tudo" e faixa vermelha no painel.
- **Teste:**
  - banco "parada de emergência";
  - script de concorrência (passo 3);
  - painel (3 verificações);
  - backend `parada_ativa`.

#### A12. Não havia autenticação multifator

- **Correção:**
  - `privado.sessao_segura()` exige `aal2`, e todo o RLS depende de `papel_atual()`;
  - tela de cadastro e confirmação TOTP no painel;
  - "Publicar agora" exige `aal2` (`podeDisparar`).
- **Teste:** banco "MFA aal1 × aal2"; painel com 5 verificações do 2º fator; `regras.test.ts`.
- **Residual:** habilitar TOTP no Supabase Auth (P-7).

### Médios e baixos

#### M1. A bateria de testes do banco contava teste com resultado nulo como aprovado

- **Onde estava:** `tests/00_utilitarios.sql`.
- **Problema:** isso escondeu o vazamento da flag `bot.via_rpc`, que já foi corrigido.
- **Correção:** resultado nulo agora é reprovação; `rodar.sh` sai com erro se algum teste falhar.

#### M2. Testes originais confirmavam defeitos

Ver §1. Foram corrigidos, e a mudança está documentada.

#### M3. Fila atrasada sem alerta

- **Correção:** alerta CRÍTICO quando há oferta atrasada há mais de `publicacao.alerta_fila_atrasada_min` (120). Ele marca a rodada como falha e aciona `/fail` do monitor.
- **Teste:** `test_fila_atrasada_*`.

#### M4. #publi ficava no fim da legenda do Instagram, escondido em "mais"

- **Problema:** o Guia do CONAR de maio/2026 exige identificação visível sem tocar em "mais".
- **Correção:** `#publi · link de afiliado` na 1ª linha.
- **Teste:** `test_instagram_publi_visivel_na_primeira_linha`.

#### M5. Limite padrão de 60 stories por dia acima da cota da Meta

- **Problema:** a Meta permite 50 publicações por conta em 24 h móveis.
- **Correção:** 45 na migration 017. O bot já consultava a cota.

#### M6. "Testar integrações" não conferia permissões

- **Correção:**
  - Telegram exige o bot como administrador que publica e edita;
  - Instagram exige conta profissional e mostra a cota;
  - Shopee lê o relatório de conversões;
  - ImgBB tem teste opcional (1 px, expira em 60 s);
  - novo comando `validar-integracoes` grava evidência em JSON sem segredos.
- **Teste:** `test_auditoria2_operacao.py`.

#### B1. Funções do schema `privado` executáveis por `authenticated`

- **Situação:** necessário para as políticas RLS. Não estão expostas pela API (o PostgREST só publica `public`).
- **Ação:** aceito.

#### B2. O backup do GitHub não inclui `auth` (usuários) nem o Vault

- **Situação:** é intencional para não expor segredos.
- **Recuperação:**
  - recadastrar o dono pelo `definir_email_dono`;
  - reconvidar a equipe;
  - recadastrar as credenciais a partir do gerenciador de senhas.
- **Teste:** a restauração completa local inclui usuários e Vault.
- **Residual:** a recuperação de contas é manual.

---

## 3. Auditoria de RLS, SECURITY DEFINER, tokens e credenciais

Evidência: consultas no banco de homologação (17 migrations) e testes do banco.

| Verificação | Resultado |
|---|---|
| Tabelas do `public` sem RLS | **0** |
| Políticas RLS | 66 |
| Funções SECURITY DEFINER | 37, **todas** com `search_path` fixo |
| SECURITY DEFINER executável por `anon` | **nenhuma** |
| SECURITY DEFINER no `public` executável por `authenticated` | 6, todas com checagem de papel e 2º fator internamente: `minha_sessao`, `salvar_credencial`, `remover_credencial`, `transferir_posse`, `ativar_parada_emergencia`, `desativar_parada_emergencia` |
| Escrita de `anon` em tabelas | **0** |
| Tabelas do `privado` legíveis por `authenticated` | **0** |

**Credenciais:**
- Vault cifrado; o painel mostra só os 4 últimos caracteres;
- os logs mascaram tokens (`Log.adicionar_segredos`);
- a evidência de validação não contém segredos (teste).

**Edge Functions:**
- `r` usa a service key só no servidor e redireciona apenas para https;
- `disparar` usa token GitHub fine-grained guardado como segredo da função e exige 2º fator;
- limite de 1 disparo a cada 90 s.

**Backup:**
- restauração completa idêntica: 805 itens de estrutura, permissões e políticas comparados;
- a bateria de testes passa no banco restaurado.

---

## 4. Custos e limites

| Serviço | Plano | Limite relevante | Situação |
|---|---|---|---|
| Supabase | Gratuito | “Pausing: After 1 week of inactivity”; “Database size500 MB per project”; “Edge function invocations500,000 per month” | **Já pausou**. O Pro custa “$25/month”, com “Daily backups (7 day retention)”. Recomendado para uso comercial. |
| GitHub Actions | Repositório público | “GitHub Actions usage is free for standard GitHub-hosted runners in public repositories”; “scheduled workflows are automatically disabled when no repository activity has occurred in 60 days” | Grátis. O workflow "Manter agendamentos ativos" cobre a desativação. Cron a cada 10 min (cerca de 4.300 rodadas por mês). |
| Instagram | API de publicação | “An Instagram professional account can only publish 50 posts within a 24 hour moving period” | Limite do bot ajustado para 45 stories + 1 feed. |
| ImgBB | Gratuito | Imagem expira em 1 dia (configurado) | Sem custo. |
| Monitor externo (healthchecks.io ou similar) | Gratuito | — | A cadastrar (P-9). |

**Custo mínimo recomendado para operação comercial:** cerca de US$ 25/mês (Supabase Pro). O restante pode ficar no gratuito.

---

## 5. LGPD e regras dos programas de afiliados

**LGPD:**
- A LGPD trata IP como dado pessoal: “endereço de IP (Protocolo da Internet) e cookies”.
- Hash é técnica de **pseudonimização**, não de anonimização: “Pseudonimização: substituição, função hash e encriptação.” Portanto, o `ip_hash` continua sendo dado pessoal.
- **Implementado:**
  - nenhum IP em texto;
  - sal secreto obrigatório (sem ele não há hash);
  - retenção de 365 dias para cliques e suspeitos (`cliques.retencao_dias`), com expurgo na manutenção diária;
  - convites expirados apagados.
- **Pendente:**
  - aviso de privacidade público, com base legal (legítimo interesse para métricas e antifraude), retenção e contato;
  - registro das operações de tratamento.

**CONAR:**
- O guia de maio/2026 enquadra o afiliado como publicidade: “a remuneração por desempenho, como a dos programas de afiliados, caracteriza publicidade”.
- Exige a identificação “sempre visíveis sem necessidade de clicar em "mais"”.
- **Implementado:**
  - #publi no início das legendas do Telegram e do Instagram;
  - selo PUBLI na arte;
  - aviso de comissão;
  - só o dono pode desligar.

**Shopee (termos internacionais do programa; os termos do Brasil devem ser lidos e aceitos por você):**
- Os termos proíbem robôs para gerar tráfego: “"use robots or other automated query tools, computer generated search requests"”.
- Proíbem redirecionar sem ação do usuário: “"without the user's engagement or action"”.
- Restringem marca: “"not contain the trademarks, names or logos of Shopee"”.
- **Situação do bot:**
  - usa somente a **API oficial de afiliados**, sem scraping;
  - nunca gera cliques: o redirecionamento só acontece quando a pessoa toca no link;
  - pré-visualizações de robôs não contam clique;
  - a arte não usa o logotipo da Shopee.
- **Pendentes:**
  - confirmar nos termos brasileiros que a automação de **busca e publicação** pela Open API é permitida;
  - confirmar que o nome "Shopee" escrito na legenda ou na arte é aceito;
  - garantir que o nome e a foto do perfil não usem a marca.

---

## 6. Integrações reais: NÃO APROVADAS

Nenhuma credencial real foi fornecida para esta auditoria, e o projeto de produção está pausado. Por isso:

| Integração | Teste simulado | Validação real |
|---|---|---|
| Shopee (busca, links, relatório de conversões, paginação) | ✔ | **Não executada** |
| Telegram (admin publica e edita) | ✔ | **Não executada** |
| Instagram (conta profissional, cota, publicação) | ✔ | **Não executada** |
| ImgBB | ✔ | **Não executada** |
| Atribuição do link (redirecionamentos e ID de afiliado no destino) | ✔ | **Não executada** |

**Para aprovar:** com sua autorização, rode `python -m app.main validar-integracoes --imgbb` com `AMBIENTE=homologacao`. O comando grava a evidência em `backend/saida/validacao_integracoes.json`. A integração só é aprovada se o resultado for "APROVADO".

---

## 7. Parecer

**Lançamento comercial: NÃO AUTORIZADO neste momento.**

**O código está pronto para homologação:**
- todos os itens P0 e P1 que dependem só de código foram corrigidos e cobertos por testes automáticos;
- 263 testes de backend, 132 do banco, 10 de operação e backup, 60 do painel e 5 das Edge Functions passam.

**O lançamento continua bloqueado por fatores que não dependem de código, todos com efeito direto em dinheiro ou risco legal:**
1. **O banco de produção está pausado** e ainda não tem a migration 017. Sem ela, o cadastro aberto continua permitindo o sequestro do painel (C2), a importação de comissões continua quebrada (C3) e não há MFA nem botão de emergência.
2. **Nenhuma integração real foi validada.** Atribuição de comissão, permissões do Telegram e Instagram e paginação do relatório só podem ser provadas com as credenciais reais.
3. **Configuração de segurança pendente no Supabase:** TOTP, cadastro desligado, e-mail do dono e `CLIQUE_SAL`.
4. **Conformidade:** aviso de privacidade (LGPD) e leitura dos termos brasileiros do programa da Shopee.
5. **Operação:** plano gratuito com pausa automática, monitor externo não cadastrado e proteção da branch main não configurada.

**Condição para reavaliar:** concluir os itens 🔴 de `CHECKLIST_PRODUCAO.md` e `PENDENCIAS.md`. Feito isso, o lançamento pode ser autorizado em **modo controlado**: primeiros 7 dias com aprovação manual ligada, só no Telegram, conferindo diariamente a reconciliação de comissões.

---

## Fontes

- Supabase, preços e pausa do plano gratuito: https://supabase.com/pricing · https://supabase.com/docs/guides/platform/free-plan-pausing
- GitHub Actions, cobrança e desativação de agendamentos: https://docs.github.com/en/actions/reference/usage-limits-billing-and-administration
- Meta, limite de publicação do Instagram: https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media_publish
- ANPD, estudo sobre anonimização e pseudonimização: https://www.mayerbrown.com/pt/insights/publications/2024/02/anonymization-and-pseudonymization-anpd-kicks-off-public-consultation-on-preliminary-study
- IP como dado pessoal (guia LGPD do TCE-PB): https://tce.pb.gov.br/wp-content/uploads/2024/12/guia-de-boas-praticas-no-tratamento-de-dados-pessoais_-lgpd_atualizado.pdf
- CONAR, guia de influenciadores (maio/2026): https://www.uai.com.br/networking-e-negocios/2026/09/23/ganha-comissao-com-link-de-afiliado-para-o-conar-isso-ja-e-publicidade/ · https://propmark.com.br/?p=112
- Shopee, termos do programa de afiliados (versão internacional): https://help.shopee.sg/portal/4/article/88952
