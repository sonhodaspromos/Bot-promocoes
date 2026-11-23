# Relatório de auditoria técnica: Bot de Ofertas v2

**Data:** 22/11/2026 · **Escopo:** banco (Supabase), backend (Python), painel, Edge Functions, workflows do GitHub, integrações e operação 24 h.

## Veredito

> **Atualização 23/11/2026:** migrations 008b e 015 aplicadas, 107/107 testes do banco aprovados, dados de teste e `pg_net` removidos (A6, C2 e B1 resolvidos no banco). Continua pendente o item 3 (integrações reais), além da implantação no GitHub.
>
> ⛔ **O sistema ainda NÃO está pronto para produção.** O código está corrigido e testado. O que falta são **3 passos de implantação** que só o dono pode fazer, e enquanto eles não forem feitos há riscos críticos ativos:
>
> 1. **Aplicar a migration 015** (`20261122141500_auditoria_correcoes.sql`) e rodar os testes `09_auditoria.sql`. Sem ela, as proteções contra post duplicado e contra perda de ofertas com token vencido funcionam só em parte. O assistente tentou aplicar, mas o Supabase pediu uma confirmação que não foi respondida a tempo.
> 2. **Aplicar a migration 008b** (`manutencao_diaria`). Sem ela, os logs nunca são apagados, o banco chega ao limite do plano grátis em cerca de 1 ano e o workflow de manutenção falha todo dia.
> 3. **Testar as integrações com credenciais reais** (`python -m app.main testar-integracoes`). Shopee, Telegram, Instagram e ImgBB **nunca foram chamados de verdade**: todos os testes usam servidores falsos. Os nomes dos campos da API da Shopee não podem ser confirmados sem login de afiliado.
>
> Com esses 3 itens feitos e o [checklist de produção](CHECKLIST_PRODUCAO.md) marcado, não sobra nenhuma falha crítica ou alta conhecida.

## Resultado dos testes

| Bateria | Antes da auditoria | Depois das correções |
|---|---|---|
| Cenários de falha da auditoria (`backend/tests/test_auditoria.py`) | **7 de 28** passavam | **28 de 28** |
| Backend completo (`pytest`) | 136 | **164 de 164** |
| Painel (Playwright, dados simulados) | 32 | **33 de 33** |
| Banco (`testes.resumo()`) | 97 de 97 | 97 de 97 no Supabase · **+10 testes novos (`09_auditoria.sql`) aguardando a migration 015** |
| Simulação completa (`python -m app.main simular`) | ok | ok (2 rodadas, 6 publicações demo) |

O "antes" foi medido rodando os testes novos sobre o código anterior à auditoria.

## Os 13 cenários pedidos

| # | Cenário | Teste | Antes | Depois | O que o sistema faz agora |
|---|---|---|---|---|---|
| 1 | Produto sem preço | `test_produto_sem_preco_e_ignorado_sem_derrubar_a_busca` | ✅ | ✅ | O item é descartado na leitura da API. Se chegar com preço 0, o filtro descarta e o motor bloqueia como "inconsistente". O banco também exige preço > 0. A busca continua com os outros itens. |
| 2 | Produto sem imagem | `test_produto_sem_imagem_publica_com_arte_gerada_e_avisa_na_analise` | ✅ | ✅ | Publica com a arte gerada pelo bot (foto padrão). A análise perde pontos em "qualidade" e avisa. Se o dono ligar `inteligencia.exigir_imagem`, a oferta é bloqueada. |
| 3 | Link inválido | `test_link_invalido_nunca_entra_na_fila` | ✅ | ✅ | Link com 404/410 é recusado, a oferta fica "não recomendada" e nunca entra na fila. O banco recusa publicação sem link validado (P0010). |
| 4 | Link sem atribuição de afiliado | `test_link_no_dominio_da_loja_mas_sem_atribuicao…`, `…redireciona_para_a_pagina_inicial…`, `…padrao_de_afiliado…` | ❌ | ✅ | Além do domínio, o link precisa seguir o **formato de link de afiliado** da loja (ex.: `mercadolivre.com/sec/…`), conferido no backend e no banco (P0024). Link que cai na página inicial da loja (afiliação expirada) é recusado. |
| 5 | Oferta duplicada | `test_oferta_duplicada_nao_gera_segunda_promocao…` | ✅ | ✅ | O mesmo produto não gera 2 promoções ativas. A mesma oferta não volta ao mesmo canal em 24 h (P0018). Títulos iguais em produtos diferentes são bloqueados pelo motor. |
| 6 | Preço alterado | `test_preco_alterado_pausa_cancela_fila_apaga_analise…` | ❌ | ✅ | Pausa a oferta, cancela a fila, **apaga a análise** (antes ficava a nota antiga em ofertas publicadas) e edita o post do Telegram para "OFERTA ENCERRADA". |
| 7 | Produto esgotado | `test_produto_esgotado_pausa_a_oferta` | ✅ | ✅ | Pausa a oferta e cancela a fila. O motor bloqueia produto indisponível. |
| 8 | API indisponível | `test_api_fora_do_ar_nao_trava_a_rodada`, `test_rodada_respeita_o_prazo…` | ❌ | ✅ | **Disjuntor:** depois de 2 falhas seguidas no mesmo servidor, as chamadas seguintes falham na hora. **Prazo de 6 min:** as etapas opcionais são puladas, mas a fila e a publicação sempre rodam. |
| 9 | Token expirado | `test_token_do_telegram_expirado…`, `test_token_do_instagram_expirado_codigo_190` | ❌ | ✅ | O canal é pausado na rodada e as ofertas **voltam para a fila sem gastar tentativa**. Fica registrado um alerta CRÍTICO, o GitHub manda e-mail (saída 1) e o painel mostra "🔑 credencial recusada". |
| 10 | Erro no Telegram | `test_erro_permanente_do_telegram…`, `test_timeout_no_envio_do_telegram…`, `test_limite_do_telegram_429…` | ⚠ parcial | ✅ | **Erro comum:** registrado e tentado de novo em 2, 4 e 8 min. **Timeout no envio:** NÃO repete (o post pode ter saído) e fica como "resultado incerto". **429:** volta para a fila depois do tempo pedido. |
| 11 | Erro no Instagram | `test_instagram_nao_processou…`, `test_timeout_no_media_publish…`, `test_limite_de_publicacoes…`, `test_cota_do_instagram_esgotada…` | ⚠ parcial | ✅ | Mesmas regras do Telegram. Além disso, consulta a **cota diária da API** antes de publicar e não tenta quando ela acabou. |
| 12 | Queda do servidor | `test_queda_do_banco_logo_apos_publicar…`, `test_servidor_cai_antes_de_publicar…`, `test_logs_sao_gravados_a_cada_etapa` | ❌ | ✅ | **Caiu depois de enviar:** o item é marcado "publicando" e nunca é republicado sozinho (vira "resultado incerto"). **Caiu antes de enviar:** o item volta sozinho quando a trava de 10 min vence. Os logs são gravados a cada etapa. |
| 13 | Reinicialização do sistema | `test_reinicio_fecha_rodada_interrompida…` | ❌ | ✅ | Uma rodada que ficou "rodando" é encerrada como erro, com o motivo. O processo novo continua a fila de onde parou, sem duplicar. |

## Problemas encontrados

**Gravidade:** Crítica = perda de dinheiro, reputação ou dados, ou post errado ou duplicado em produção. Alta = o sistema para ou se degrada sem aviso. Média = comportamento incorreto com impacto limitado. Baixa = higiene ou observação.

| ID | Problema | Gravidade | Arquivo responsável | Correção | Teste | Resultado |
|---|---|---|---|---|---|---|
| C1 | **Post duplicado após timeout.** O cliente HTTP repetia o envio (`sendPhoto`, `media_publish`) quando a resposta demorava. Se o canal já tinha publicado, saía 2 vezes. | Crítica | `backend/app/cliente_http.py`, `integracoes/canais/telegram.py`, `integracoes/canais/instagram.py` | Envio de post com `repetir=False`. Timeout ou queda depois de enviar vira `incerto` e o item é encerrado sem nova tentativa automática. | `test_timeout_no_envio_do_telegram…`, `test_timeout_no_media_publish…` | Antes: 2 envios por oferta ❌ · Depois: 1 envio ✅ |
| C2 | **Post duplicado quando o banco cai logo após publicar.** O erro de banco escapava da etapa, o item ficava "processando" e era republicado 10 min depois. | Crítica | `backend/app/servicos/publicacao.py`, `supabase/migrations/…141500_auditoria_correcoes.sql` | Marca "publicando" antes de enviar (`fila_marcar_publicando`). O banco converte item vencido com essa marca em "falhou: resultado incerto". A publicação para assim que o banco cai. | `test_queda_do_banco_logo_apos_publicar…` · banco: "job morreu DURANTE o envio" | Backend ✅ · banco ⏳ (aguarda migration 015) |
| C3 | **Token vencido apagava a fila.** Cada oferta gastava as 3 tentativas contra o token recusado e virava "falhou" em ~15 min, sem alerta claro. | Crítica | `servicos/publicacao.py`, `integracoes/canais/*.py`, `orquestrador.py`, `main.py` | Classificação de erro de credencial (Telegram 401/403, Instagram 190). Canal pausado na rodada, `fila_devolver` sem gastar tentativa, CRÍTICO no log, saída 1 (e-mail do GitHub) e alerta no painel. | `test_token_do_telegram_expirado…`, `test_token_do_instagram…` | ✅ |
| A1 | **API fora do ar estourava o tempo do job.** Sem disjuntor: até ~16 min de tentativas contra um job limitado a 9. O job era morto antes de publicar e os logs, guardados só no fim, se perdiam. | Alta | `cliente_http.py`, `orquestrador.py` | Disjuntor por servidor, prazo da rodada com etapas essenciais garantidas, logs gravados por etapa, rodadas órfãs encerradas. | `test_api_fora_do_ar…`, `test_rodada_respeita_o_prazo…`, `test_logs_sao_gravados…`, `test_reinicio…` | Antes: 15 chamadas ❌ · Depois: ≤ 6 ✅ |
| A2 | **Link sem atribuição de afiliado era aceito.** Bastava o domínio: `www.mercadolivre.com/qualquer-coisa` passava. A venda não renderia comissão. | Alta | `servicos/validacao.py`, migration 015 (`marketplaces.padrao_afiliado`, P0024) | Padrão de link de afiliado por loja, conferido no backend e no banco. Recusa link que redireciona para a página inicial. | 3 testes de link + banco "sem atribuição (P0024)" | Backend ✅ · banco ⏳ |
| A3 | **Stories acima da cota do Instagram.** O limite configurado (60 stories + 1 feed por dia) passava do que a API aceita: 50 publicações em 24 h. As excedentes falhariam e gastariam tentativas. | Alta | dados de `canais`, `servicos/publicacao.py`, `integracoes/canais/instagram.py` | Limite de stories 45. Consulta de `content_publishing_limit` antes de publicar. Erro de cota volta para a fila. | `test_limite_diario_dos_stories…`, `test_cota_do_instagram…`, `test_limite_de_publicacoes…` | ✅ (no banco ⏳ com a migration 015) |
| A4 | **O bot pararia sozinho em ~60 dias.** Em repositório público, o GitHub desliga os agendamentos depois de 60 dias sem atividade, e o bot não faz commits. | Alta | `.github/workflows/` | Novo `manter_ativo.yml` (semanal) reativa os agendamentos pela API. O painel alerta "o bot não roda há X minutos". | Painel: alertas · revisão do workflow | ✅ (confirmar após a 1ª execução) |
| A5 | **Sem backup.** O plano grátis do Supabase não tem backup automático. | Alta | `.github/workflows/` | Novo `backup.yml`: `pg_dump` diário, comprimido, **criptografado AES-256** e verificado (abre e tem as 16+ tabelas). Fica 30 dias como artefato. | Pipeline testado localmente (abre com a senha certa, não abre com a errada) | ✅ local · ⏳ 1ª execução real precisa de `SUPABASE_DB_URL` e `BACKUP_SENHA` |
| A6 | **Manutenção diária não aplicada.** Logs nunca apagados: ~40 MB/mês estimados com o bot real, chegando ao limite de 500 MB em ~1 ano. O workflow de manutenção falha todo dia. | Alta | `supabase/migrations/…140850_manutencao_diaria.sql` | Aplicar pelo SQL Editor (o assistente não tem permissão de rodar comandos DELETE sem confirmação). | — | ⏳ **pendente (dono)** |
| M1 | Uma exceção de rede inesperada podia chegar ao log com a URL do Telegram, que contém o token. Os logs do Actions de repositório público são públicos. | Média | `cliente_http.py`, `main.py` | Toda falha de rede vira erro sem URL. Exceções inesperadas passam pela máscara de segredos em vez de traceback cru. | `test_erro_de_rede_inesperado_nao_vaza_token…` | ✅ |
| M2 | Oferta **publicada** mantinha a análise antiga depois de mudança de preço, estoque ou link. A escolha do post do feed usava nota desatualizada. | Média | migration 015, `repositorio*.py` | A análise é apagada também para "publicada", e o motor reanalisa. | `test_preco_alterado…` · banco: "oferta publicada perde a análise" | ✅ · banco ⏳ |
| M3 | Rodadas interrompidas ficavam "rodando" para sempre, e o painel mostrava status falso. | Média | migration 015 (`encerrar_execucoes_orfas`), `orquestrador.py` | Encerradas como "erro (interrompida)" na rodada seguinte. | `test_reinicio…` · banco | ✅ |
| M4 | Limite do Telegram (429) e cota do Instagram eram tratados como erro comum e gastavam tentativas. | Média | `publicacao.py`, canais | Voltam para a fila depois da espera pedida. | `test_limite_do_telegram_429…` | ✅ |
| M5 | O painel e o backend não traduziam os códigos P0018, P0019, P0023 e P0024. | Média | `frontend/painel/js/api.js`, `backend/app/erros.py` | Mensagens claras adicionadas. | `test_codigos_de_erro…` | ✅ |
| B1 | Extensão `pg_net` no schema `public` (advisor). Sobra do teste de cliques. | Baixa | `supabase/manutencao/limpar_teste_e2e.sql` | Rodar o script de limpeza. | advisor | ⏳ pendente (dono) |
| B2 | 3 funções SECURITY DEFINER chamáveis por usuário logado (advisor). | Baixa | migrations 006/007 | **Aceito:** cada uma confere por dentro se quem chama é o dono (coberto pelos testes de segurança). | testes 05 | ✅ aceito |
| B3 | 27 índices "nunca usados" (advisor). | Baixa | migrations | **Aceito:** o banco só teve dados de teste. Reavaliar depois de 30 dias de produção. | — | ✅ aceito |
| B4 | Em modo demonstração, a mesma oferta real pode reaparecer no canal **demo**, porque a janela de 24 h só conta publicações reais. | Baixa | regra P0018 | **Aceito:** nada é publicado de verdade. | `test_oferta_duplicada…` | ✅ aceito |
| B5 | Shopee: o "preço de" calculado pela taxa de desconto sobre o menor preço das variações pode inflar o desconto. | Baixa | `integracoes/fontes/shopee.py` | **Aceito com mitigação:** o motor contesta "preço de" inflado assim que há 30 dias de histórico. Conferir em produção. | testes do motor | ✅ aceito |

## Verificação por área

| Área | Situação | Evidências |
|---|---|---|
| 1. Segurança | ✅ | RLS nas 18 tabelas. Visitante sem acesso. Papéis dono, editor e leitor testados. Painel protegido contra injeção de HTML (teste). Nenhuma chave secreta no código (varredura feita). A chave publicável é pública por natureza. |
| 2. Banco de dados | ✅ | 97/97 testes de integridade. Chaves estrangeiras, unicidade e históricos imutáveis. Migration 015 escrita e com 10 testes, ⏳ aplicação. |
| 3. Integridade das informações | ✅ | O motor não inventa preço anterior, comissão nem avaliações. Mudança de preço, estoque ou link invalida a análise (inclusive de publicadas, M2). |
| 4. Integrações dos marketplaces | ⚠ | Interfaces independentes e modo demo identificado. **Sem teste real** (pendência 3). Mercado Livre sem API pública: cadastro manual, sem scraping. |
| 5. Links de afiliados | ✅ | HTTPS, domínio de afiliado, formato de afiliado (A2), link que abre, recusa de página inicial. Conferido em 2 camadas (backend e banco). |
| 6. Telegram | ✅ (simulado) | Envio, edição "encerrada", 400/401/403/429, timeout sem duplicar. |
| 7. Instagram | ✅ (simulado) | Feed e story em 2 etapas, cota diária, código 190, limite, timeout sem duplicar. O link do story não é clicável (limitação da API, documentada). |
| 8. Filas | ✅ | `FOR UPDATE SKIP LOCKED`, limite diário e intervalo por canal, espera crescente, devolver sem gastar tentativa, resultado incerto. |
| 9. Automação 24 h | ✅ com ⏳ | Cron a cada 10 min sem rodadas simultâneas (concurrency). Keep-alive de 60 dias. Alerta de bot parado no painel. Renovação do token do Instagram nos dias 1 e 15. |
| 10. Desempenho | ✅ | Teste com 10 mil produtos: painel 59 ms, worker pega 10 itens em 63 ms, métricas 7 ms. Rodada demo completa em menos de 1 s (sem rede). |
| 11. Logs | ✅ | Máscara de segredos dupla (backend e banco), gravação por etapa, retenção de 30 dias (⏳ A6). |
| 12. Recuperação de falhas | ✅ | Cenários 8, 9, 10, 11, 12 e 13. |
| 13. Credenciais | ✅ | GitHub Secrets ou Supabase Vault (cifrado). O painel nunca recebe o valor. Tokens mascarados nos logs públicos. O backup não inclui o Vault. |
| 14. Backup | ✅ com ⏳ | Backup diário criptografado (A5). Restauração em [IMPLANTACAO.md](IMPLANTACAO.md#9-restaurar-um-backup). |
| 15. Custos | ✅ R$ 0/mês | Ver abaixo. |

## Custos operacionais (uso esperado: rodada a cada 10 min)

| Serviço | Plano | Uso estimado | Custo |
|---|---|---|---|
| GitHub Actions | grátis em repositório público | ~4.320 rodadas/mês + backup + manutenção | R$ 0 |
| GitHub Pages (painel) | grátis | site estático | R$ 0 |
| Supabase | Free (500 MB) | ~48 MB hoje; logs ~40 MB/mês com retenção de 30 dias | R$ 0. O projeto não pausa por inatividade, porque o bot o usa a cada 10 min. |
| Telegram Bot API | grátis | — | R$ 0 |
| Instagram Graph API | grátis | até 46 publicações/dia (cota de 50) | R$ 0 |
| ImgBB | grátis | 1 upload por publicação no Instagram | R$ 0 |
| Shopee Afiliados API | grátis para afiliados | — | R$ 0 |

**Quando passa a custar:** se o repositório virar **privado**, as ~4.320 rodadas/mês passam da franquia gratuita do GitHub. Nesse caso, aumente o intervalo (ex.: a cada 30 min) ou mantenha público. O plano Pro do Supabase (US$ 25/mês) só seria necessário com muito mais volume ou para ter backup gerenciado pelo Supabase.

## Riscos residuais (aceitos e documentados)

- **"Resultado incerto" exige conferência manual.** Para nunca duplicar um post, o bot prefere não repetir quando não sabe se publicou. O painel mostra essas ocorrências.
- **Atrasos do agendador do GitHub.** O cron do GitHub pode atrasar em horários de pico. Para ofertas, alguns minutos não fazem diferença.
- **APIs de terceiros mudam.** Qualquer mudança na Shopee ou na Meta aparece como erro nos logs e como alerta no painel. O motor bloqueia dados inconsistentes em vez de publicar.
