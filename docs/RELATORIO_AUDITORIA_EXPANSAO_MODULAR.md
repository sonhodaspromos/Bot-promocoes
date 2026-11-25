# Relatório de auditoria — Bot de Promoções V2 · Expansão modular

- **Branch auditada:** `feat/expansao-modular` (commit 29d54fd).
- **Correções:** branch `fix/auditoria-expansao-modular`, criada a partir dela.
- **Data:** 25/11/2026.
- **O que não foi feito:** merge, push, deploy, alteração no banco de produção (pausado), mensagem real ou integração ligada.

## 1. Resumo executivo

- **Parecer: REPROVADO para produção com as funcionalidades novas ligadas.**
  - Nenhuma integração nova (AliExpress, Amazon, WhatsApp) foi validada com credenciais reais, porque nenhuma credencial foi fornecida.
  - Os testes de carga rodaram só em homologação **local**. Não há infraestrutura real de homologação disponível.
- **Liberação possível, com a sua autorização:** apenas a **Fase 0**.
  1. Aplicar as migrations 020 e 021 numa branch/projeto de homologação do Supabase.
  2. Rodar lá as baterias.
  3. Só então aplicar em produção, **com todas as flags desligadas**.
- **Achados:** 22 problemas — 0 P0, 6 P1, 12 P2 e 4 P3.
  - **Corrigidos:** 21. O restante depende de credenciais, infraestrutura ou autorização (seção 9).
- **Achado mais grave (P1-1):** o clique nunca era ligado à publicação. A função `registrar_clique` não gravava `publicacao_id`. Com isso:
  - o painel de desempenho, o aprendizado por horário/canal, os testes A/B, a saturação e o ranking por desempenho real viam **zero cliques** fora dos testes automatizados;
  - os testes não pegavam o erro porque inseriam `publicacao_id` direto na tabela.
- **Achado do teste de carga:** o rollup diário pedido não existia. Sem ele:
  - o painel lê todos os cliques brutos;
  - a retenção de 365 dias apagaria a contagem histórica.

  Corrigido com agregação idempotente: com 15 milhões de linhas, o painel diário caiu de **4,8 s** (brutos) para **2,7 ms** (rollup).

## 2. Problemas encontrados e gravidade

Escala de gravidade:

| Nível | Significado |
|---|---|
| P0 | Dano real imediato em produção |
| P1 | Funcionalidade quebrada ou risco legal/financeiro quando ligada |
| P2 | Defeito com impacto limitado ou contornável |
| P3 | Melhoria ou risco baixo |

| # | Grav. | Problema | Onde | Situação |
|---|---|---|---|---|
| 1 | P1 | `registrar_clique` não gravava `publicacao_id`. Painel, aprendizado, A/B, saturação e ranking por desempenho real ficavam sem cliques | migration 019 | **Corrigido** + correção retroativa dos cliques antigos |
| 2 | P1 | Adaptador Amazon não funcionaria com a Creators API (detalhe abaixo) | `fontes/amazon.py` | **Corrigido** conforme a documentação oficial; **não validado** com credenciais |
| 3 | P1 | Teto de gasto do WhatsApp **não atômico**: a checagem era feita no Python antes de enviar, então dois workers simultâneos podiam passar do teto | `servicos/whatsapp.py` | **Corrigido** com reserva atômica no banco (`whatsapp_reservar_envio` + `orcamento_reservar`) |
| 4 | P1 | Webhook do WhatsApp sem deduplicação nem proteção contra *replay* (detalhe abaixo) | edge `whatsapp` | **Corrigido**: id da mensagem guardado 14 dias, janela de 7 dias e ordem por data da mensagem |
| 5 | P1 | Vitrine podia mostrar como atual um preço desatualizado ou que já subiu, além de oferta vencida | `ofertas_vitrine` | **Corrigido**: só oferta vigente, preço conferido há até 24 h e sem alta |
| 6 | P1 | Vitrine sem as exigências da Amazon: aviso "Associado Amazon" e data/hora do preço junto ao valor | `servicos/vitrine.py` | **Corrigido** (o texto exato em PT-BR deve ser conferido no contrato BR, ver §9) |
| 7 | P2 | Sem agregação diária de cliques (painel O(n); retenção apagaria contagens) | banco | **Implementado** o rollup idempotente e reprocessável |
| 8 | P2 | Limite por pessoa do WhatsApp com corrida (consulta e reserva separadas) | banco | **Corrigido** (linha do inscrito travada) |
| 9 | P2 | Worker sem renovação de trava: tarefa longa podia ser assumida por outro worker | `worker.py` | **Corrigido**: renovação em segundo plano (`tarefa_renovar`); trava perdida descarta o resultado |
| 10 | P2 | Sem alerta para fila acumulada, tarefa abandonada ou falha definitiva | — | **Corrigido**: `fila_saude()` gera ERRO no log e código de saída 1 (o GitHub avisa por e-mail) |
| 11 | P2 | Falha ao ler as regras de canais por categoria publicava em **todos** os canais extras | `publicacao._canais_alvo` | **Corrigido**: agora pula os extras |
| 12 | P2 | Cliques da vitrine, do WhatsApp e dos alertas ficavam sem canal (os códigos não existiam) | banco | **Corrigido**: canais de atribuição inativos |
| 13 | P2 | Token da Amazon revogado antes da hora (401) não era renovado | `fontes/amazon.py` | **Corrigido**: renova uma vez |
| 14 | P2 | Ranking usava a comissão **bruta** e ignorava cancelamentos | `ranking.py` | **Corrigido**: comissão líquida com amostra mínima de 20; sem amostra, avisa |
| 15 | P2 | Faltava A/B de horário de publicação | — | **Implementado** (tipo `horario`) |
| 16 | P2 | Faltava a janela de 60 dias no histórico de preços | `precos.py` | **Implementado** (7/30/60/90) |
| 17 | P2 | Contraste do botão da vitrine em 3,66:1, abaixo do AA (4,5:1) | vitrine | **Corrigido** (5,5:1) |
| 18 | P2 | A arte reaproveitaria a imagem de produto da Amazon (re-hospedagem) | `publicacao._montar` | **Corrigido**: produto Amazon sai com a arte padrão |
| 19 | P3 | `Retry-After` dos serviços ignorado no 429/503 | `cliente_http.py` | **Corrigido** (limite de 60 s) |
| 20 | P3 | Views novas com permissão de escrita herdada para `authenticated` (inofensiva, porque são views de agregação) | migration 021 | **Corrigido** (revogada) |
| 21 | P3 | Webhook lia o corpo inteiro antes de checar o tamanho | edge `whatsapp` | **Corrigido**: confere `content-length` antes |
| 22 | P3 | Endpoints públicos (`r`, `whatsapp`) sem limite por IP na borda (o banco já limita cliques por visitante e por link) | Supabase | **Pendente**: risco residual, ver §8 |

**Detalhe do achado 2 (Amazon).** Os erros eram:

- token enviado como formulário em vez de JSON;
- sem o `scope: creatorsapi::default`;
- sem o cabeçalho `x-marketplace`;
- sem `itemIdType`;
- sem os endereços oficiais.

**Detalhe do achado 4 (webhook).** Um "QUERO OFERTAS" antigo reentregue depois de um "SAIR" reinscrevia a pessoa: violação de consentimento (LGPD).

**Também observado (regressão do meu próprio trabalho, pega pelos testes):** a barra inferior do painel transbordava em 390 px ao ganhar a aba Financeiro. Ela agora rola na horizontal.

## 3. Validação das integrações (documentação oficial vigente)

Nenhuma credencial real foi fornecida, então **nenhum teste somente leitura com credenciais foi executado**. As quatro integrações continuam **desativadas**: flags `false` e modo `desativado`/`manual`.

| Marketplace | O que a documentação oficial diz (conferido em 25/11/2026) | Ajuste feito | Situação |
|---|---|---|---|
| **Amazon** | Brasil fica na região NA com credencial versão 3.1. Token do Login with Amazon em `https://api.amazon.com/auth/o2/token`, por POST JSON com `grant_type`, `client_id`, `client_secret` e `scope=creatorsapi::default`; o token vale 3600 s. Catálogo em `https://creatorsapi.amazon/catalog/v1/` com o cabeçalho `x-marketplace` obrigatório. Limite inicial de 1 req/s e 8.640 req/dia nos primeiros 30 dias. Acima do limite, ou sem vendas qualificadas por 30 dias, HTTP 429 | Endereços oficiais como padrão (variáveis de ambiente só para substituir), token em JSON com escopo, `x-marketplace`, `itemIdType`, renovação no 401 e `Retry-After` no 429 | **Não validado** (sem credenciais) |
| **AliExpress** | Open Platform de afiliados (gateway `api-sg`; `product.query`, `productdetail.get`, `link.generate`, `order.list`). A documentação de limites exige login no console; nenhum limite oficial foi confirmado nesta auditoria | Timeout e disjuntor testados; assinatura e erros já cobertos | **Não validado**; limites a confirmar no console |
| **Mercado Livre** | Links de afiliado criados pela Barra de Afiliados ou pela Central de Afiliados e Criadores. Não há API de link de afiliado | Continua **manual** | Sem integração automática (correto) |
| **Magalu** | O link é gerado pelo app ou painel do Parceiro Magalu (Compartilhar). Nenhuma API pública | Continua **manual** | Sem integração automática (correto) |
| **WhatsApp** | Cobrança por mensagem desde 01/07/2025; marketing é sempre cobrado. A Meta reentrega webhooks (entrega "pelo menos uma vez"): deduplicar pelo id e guardar as chaves por pelo menos 7 dias | Deduplicação, janela de 7 dias, ordem e orçamento atômico | **Não validado** (sem WABA, template ou número) |

Cenários de falha testados com dublês, sem rede:

- token expirado e renovado;
- token revogado (401) renovado uma vez;
- 401 persistente, com erro de autenticação claro;
- 429 com `Retry-After` respeitado (esperas de 7 s);
- timeout de leitura vira erro temporário e abre o disjuntor (não insiste no serviço fora do ar);
- recusa certa do WhatsApp devolve o orçamento; resultado incerto mantém o custo e nunca reenvia.

## 4. Escalabilidade do banco

### 4.1 Rollup diário de cliques (`cliques_diarios`)

- **Idempotente:** `agregar_cliques(dia)` apaga e regrava o dia numa transação, com trava consultiva por dia. Rodar duas vezes dá o mesmo resultado.
- **Reprocessável:** `agregar_cliques_periodo(de, ate)` (até 400 dias) pula, sem erro, os dias fora da retenção.
- **Eventos atrasados:** a manutenção diária reagrega os últimos 3 dias (configurável).
- **Duplicados:** o clique repetido do mesmo visitante vira 1 clique + 1 suspeito, e o rollup conta 1.
- **Retenção:** a manutenção agrega **antes** de apagar, inclusive dias antigos nunca agregados. O dia já agregado e fora da retenção é **protegido** contra regravação (regravar daria uma contagem menor). O rollup fica 1.095 dias.
- **Conferência:** `verificar_rollup(dia)` compara com os brutos. A diferença foi **0** em todos os cenários de carga (30 dias × 4 volumes) e nos testes.
- **Calendário:** a manutenção percorre o calendário, sem varrer a tabela de cliques. Uma primeira versão varria a tabela e levou 1,8 s com só 30 mil linhas; foi corrigida antes da carga.

### 4.2 Índices, autovacuum, crescimento e consultas lentas

- **Índices novos:**
  - `historico_pub_link_canal_idx`, para a atribuição do clique;
  - os do rollup;
  - os da 020 (`cliques_publicacao_data_idx` e a correção de tipo `char(64)` em `registrar_clique`) continuam valendo.
- **Autovacuum:** a contagem por publicação depende do *visibility map*. Sem vacuum, logo após uma carga em massa, o painel de 30 dias leva cerca de 9 s com 3 milhões de linhas (medido na rodada anterior). Com o rollup, as telas diárias não dependem mais disso.
- **Crescimento medido em 30 dias (tabela + índices):**

| Volume | Tamanho |
|---|---|
| 1 mil/dia | 13 MB |
| 10 mil/dia | 125 MB |
| 100 mil/dia | 1,25 GB |
| 500 mil/dia | 6,2 GB |

- **Consultas lentas que restam:**
  - o painel de desempenho por publicação (brutos) leva 644 ms com 15 milhões de linhas;
  - reprocessar 30 dias leva 21 s a 500 mil/dia. É aceitável numa tarefa diária, mas não num pedido do painel.

### 4.3 Carga progressiva — **resultados LOCAIS**

- **Máquina:** PostgreSQL 16, 2 CPUs, 7 GB de RAM, `shared_buffers` 128 MB. **Não é o Supabase.**
- **Premissas:** hora de pico = 15% do dia; rajada = 3× a média dessa hora; teste a **5× a rajada** por 30 s com 8 conexões; 30 dias de histórico em ordem de tempo; 70% de visitantes únicos.
- **Latências:** p50/p95/p99 vêm do log por transação do pgbench e incluem o atraso de agendamento.

| Cenário | Alvo/s | Atingido/s | p50 | p95 | p99 | Erros | CPU | RSS Postgres (pico) | Conexões (pico) | Linhas | Tabela | Painel 30 d (brutos) | Cliques/dia 30 d brutos → rollup | Reprocessar 30 d | Dif. | Manutenção |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 mil/dia | 1 | 1,2 | 3,3 ms | 9,5 ms | 11,0 ms | 0% | 3% | 379 MB | 10 | 30 mil | 13 MB | 4 ms | 10,7 → 2,1 ms | 0,08 s | 0 | 0,10 s |
| 10 mil/dia | 6 | 5,5 | 2,5 ms | 7,4 ms | 12,2 ms | 0% | 4% | 381 MB | 9 | 300 mil | 125 MB | 37 ms | 200 → 3,7 ms | 0,4 s | 0 | 0,12 s |
| 100 mil/dia | 62 | 62,0 | 1,8 ms | 3,0 ms | **203 ms** | 0% | 7% | 675 MB | 9 | 3,0 mi | 1,25 GB | 149 ms | 947 → 2,4 ms | 3,6 s | 0 | 0,48 s |
| 500 mil/dia | 312 | 315,9 | 1,6 ms | 7,1 ms | **343 ms** | 0% | 20% | 1.017 MB | 9 | 15,0 mi | 6,2 GB | 644 ms | 4.796 → 2,7 ms | 21,1 s | 0 | 2,76 s |

Notas sobre a carga:

- A taxa de 1/s com 30 s gera poucas amostras: os números do cenário de 1 mil/dia variam bastante.
- O p99 de 100 mil e de 500 mil mostra picos de 0,2 a 0,35 s, provavelmente checkpoint/autovacuum (não investigado a fundo). O p95 fica abaixo de 8 ms.
- A rodada anterior mediu, sem limite de taxa, **2.448 cliques/s** a 100 mil/dia.
- Antes da correção de tipo da 020, o cenário de 100 mil/dia atingia só 4,7 de 62 cliques/s, com 8,5 s de latência.
- **Infraestrutura real de homologação: NÃO testada.** Exige criar uma branch ou projeto no Supabase, o que gera custo e depende da sua autorização. Até lá, os números acima **não** representam o Supabase: compute Micro/Small, disco de rede, pooler e limites de conexões são outros.

## 5. Workers e concorrência

Testes com **conexões simultâneas reais** (`homologacao/concorrencia_expansao.sh`):

| Teste | Resultado |
|---|---|
| 8 workers consumindo 600 tarefas | 600 processadas, **0 duplicadas**, 8 workers participaram |
| Worker interrompido (conexão encerrada sem concluir) | Tarefa retomada na 2ª tentativa; a conclusão tardia do worker morto foi **recusada** |
| Renovação de trava × 20 tentativas de outro worker | A trava renovada **nunca** foi tomada |
| 40 reservas simultâneas de US$ 0,0625 com teto de US$ 1,00 | **Exatamente 16** aceitas, gasto de US$ 1,0000 |
| 12 reservas simultâneas para a mesma pessoa (limite de 3/dia) | 3 aceitas, 9 recusadas |
| A mesma mensagem do webhook 10 vezes ao mesmo tempo | Processada **1** vez, 1 evento de opt-in |

- **Publicação duplicada com resultado incerto:** o mecanismo das auditorias anteriores foi conferido e continua valendo. Envio com `repetir=False`, e timeout ou 5xx marcam o item como **incerto**, que nunca é reenviado automaticamente. A mesma regra vale para o WhatsApp: a reserva fica e o custo é contado.
- **Alertas:** o `worker` sai com código 1 e grava ERRO no log (visível no painel) quando há:
  - falha definitiva nas últimas 24 h;
  - tarefa atrasada há mais de 30 min;
  - mais de 50 tarefas pendentes vencidas;
  - trava vencida (tarefa abandonada).

  Com o código 1, o GitHub Actions marca a execução como falha e avisa por e-mail.

## 6. Segurança

| Verificação | Resultado |
|---|---|
| RLS | Todas as tabelas de `public` com RLS (consulta ao catálogo) |
| Funções expostas a `anon` | **Nenhuma**. As funções novas são só `service_role` (teste automatizado) |
| Tabelas acessíveis por `anon` | **Nenhuma** |
| SECURITY DEFINER sem `search_path` | **Nenhuma** |
| Views sem `security_invoker` | **Nenhuma** |
| Segredos no Git (histórico inteiro) | Só valores de exemplo dos testes de mascaramento (incluindo o token de exemplo da documentação do Telegram); nenhum `.env`. O backup do workflow é criptografado (AES-256) |
| XSS | Vitrine: título com `<script>` sai como texto (teste no Chromium). Painel: segmentos e regras escapados (testes existentes) |
| SSRF | Download de imagem só https, IP público, até 5 MB e só tipos de imagem (`rede.py`); imagem da Amazon nem é baixada |
| SQL injection | RPCs com parâmetros tipados; PostgREST com filtros parametrizados; o `r` aceita só id numérico |
| Open redirect | O `r` redireciona só para o link **validado** do banco (`destinoSeguro`), nunca para um parâmetro da URL |
| Manipulação de links | Atribuição pelo par link + canal do banco; um canal inexistente fica sem atribuição, sem erro |
| Webhook: assinatura | HMAC-SHA256 em tempo constante antes de qualquer acesso ao banco; `verify_token` com 16+ caracteres |
| Webhook: replay e duplicata | Id guardado 14 dias; mensagem com mais de 7 dias ignorada; mensagem mais antiga que a última mudança de consentimento ignorada (teste de replay de inscrição depois do SAIR) |
| Abuso de endpoints públicos | O banco limita cliques por visitante (20 em 10 min) e por link (120/min). Não há limite por IP na borda: **risco residual** |
| Consentimento e descadastro | Opt-in com texto e origem; SAIR imediato; telefone apagado 30 dias após o descadastro; trilha de eventos imutável |
| Retenção | Cliques: 365 dias brutos e 1.095 dias agregados (sem IP, só hash). Backups criptografados guardam dados até 30 dias depois da exclusão |

## 7. Inteligência comercial — inventário

| Item | Situação antes | O que foi feito |
|---|---|---|
| Histórico de 30/60/90 dias | Havia 7/30/90 | **Incluída** a janela de 60 dias |
| Desconto falso | Existia (desconto artificial, "sobe e desce") | — |
| Estoque e validade de cupons | Existia (monitor de preço/estoque; cupons expiram na manutenção; a vitrine só mostra cupom vigente) | A vitrine agora exige preço conferido e oferta vigente |
| Comparação entre marketplaces | Existia (equivalência por GTIN/marca/modelo/título) | — |
| Ranking por comissão líquida e desempenho real | **Incompleto**: comissão bruta, e "desempenho real" sem cliques (achado 1) | Comissão líquida (cancelamentos por loja, mínimo de 20 decididas) e cliques atribuídos |
| Conciliação aprovada/pendente/cancelada | Existia | O Financeiro separa estimada × confirmada × recebida × cancelada |
| Tráfego suspeito | Existia (`cliques_suspeitos`: repetido, excesso por visitante, pico por link, sem identificação) | — |
| A/B de textos e horários | Havia legenda e arte | **Incluído** o A/B de horário |

Nenhuma estimativa é apresentada como receita. O painel Financeiro rotula pendente como **estimativa** e calcula o lucro só sobre a receita confirmada. Sem a cotação do dólar informada por você, o lucro não é calculado.

## 8. Controle financeiro e custos

### 8.1 O que foi implementado

- **Painel Financeiro** (nova aba):
  - por mês: receita estimada, confirmada, recebida e cancelada (R$);
  - custos por serviço, faturados ou estimados (US$);
  - WhatsApp consumido;
  - lucro líquido = confirmada − (custos faturados + WhatsApp) × a cotação que você informa;
  - orçamento do dia por serviço, com a porcentagem.
- **Limite atômico:** `orcamento_reservar(servico, valor, teto)`, com uma linha por serviço/dia travada no UPDATE. Comprovado com 40 conexões simultâneas.
- **Estorno:** `orcamento_estornar` devolve o valor só quando a recusa é certa.
- **Alerta de consumo:** `orcamento_situacao` avisa **uma vez** ao cruzar 80% do teto (configurável); vira ERRO no log.

### 8.2 GitHub Actions × alternativas para execução contínua

| Opção | Custo | Prós | Contras |
|---|---|---|---|
| GitHub Actions, repositório **público** (atual) | US$ 0 | Já funciona; logs e avisos por e-mail | Agendamentos podem atrasar e são **desativados após 60 dias sem atividade** no repositório público; o código fica público (segredos não) |
| GitHub Actions, repositório **privado** | Bot a cada 10 min + worker a cada 15 min ≈ 7.500–12.000 min/mês × US$ 0,006 ≈ **US$ 45–72**, menos US$ 12 incluídos no plano Free | Código privado | Paga por minuto, arredondado para cima por job |
| **Supabase Cron** (pg_cron + pg_net), incluído no plano | US$ 0 adicional | Ideal para o que é SQL (manutenção diária, rollup, saúde da fila); intervalo de até 1 s | Não roda o bot em Python (artes com Pillow); para isso continua precisando de um runner |
| VM/contêiner pequeno (Fly, Railway, Render etc.) | **Preço não pesquisado nesta auditoria** | Execução contínua, sem atraso de agendamento | Mais operação (atualizações, monitoramento) |

**Recomendação:** mover `manutencao_diaria` (que já agrega o rollup) para o Supabase Cron e manter o bot no Actions enquanto o repositório for público. Ao tornar o repositório privado, reavaliar a VM.

### 8.3 Estimativa de custos por volume de cliques (US$/mês)

Premissas:

- 1 clique = 1 invocação de edge function, mais cerca de 10 mil invocações por mês de outras funções;
- banco = tamanho medido em 30 dias × 12,2 (retenção de 365 dias) **× 1,5 de margem** (user agents reais são mais longos que os do teste);
- preços públicos do Supabase:
  - Pro: US$ 25, com US$ 10 de crédito de compute;
  - invocações: 2 milhões incluídas, depois US$ 2 por milhão (pacote inteiro);
  - disco: 8 GB incluídos, depois US$ 0,125/GB;
  - compute: Small US$ 15, Medium US$ 60.

| Item | 1 mil/dia | 10 mil/dia | 100 mil/dia | 500 mil/dia |
|---|---|---|---|---|
| Invocações por mês | 30 mil | 300 mil | 3 milhões | 15 milhões |
| Cliques brutos em 365 dias | ~0,2 GB | ~2,3 GB | ~23 GB | ~114 GB |
| Compute sugerido (**a validar em infra real**) | Micro | Micro | Small (+US$ 5) | Medium (+US$ 50) |
| Invocações excedentes | 0 | 0 | US$ 2 | US$ 26 |
| Disco excedente | 0 | 0 | ~US$ 1,90 | ~US$ 13,30 |
| **Total com retenção de 365 dias** | US$ 0 (Free, sem backup) a 25 | **~US$ 25** | **~US$ 34** | **~US$ 114** |
| **Total com retenção de 90 dias** (o rollup guarda os totais por 3 anos) | igual | igual | ~US$ 32 | ~US$ 104 |

O plano Free cabe a 1 mil/dia, mas não tem backup diário e pausa após 7 dias sem uso. Para produção, o recomendado é o Pro.

### 8.4 Estimativa de custos por volume de usuários

- **WhatsApp (marketing):** cálculo = inscritos × mensagens/dia × 30 × US$ 0,0625 a 0,0719.

| Inscritos | 1 mensagem/dia |
|---|---|
| 100 | US$ 188–216/mês |
| 1.000 | US$ 1.875–2.156/mês |
| 10.000 | US$ 18.750–21.564/mês |

  Com o teto padrão de US$ 5/dia, o gasto fica limitado a **US$ 150/mês** (cerca de 80 mensagens/dia), agora com garantia atômica.
- **Alertas no Telegram:** sem custo de mensagem. O custo é só banco/compute, desprezível perto dos cliques.
- **Painel e vitrine:** a vitrine é estática (GitHub Pages, US$ 0); o painel usa as cotas do Supabase.
- **Sem dados de conversão:** nenhum retorno foi estimado.

## 9. Pendências que dependem de autorização ou credenciais

1. **Credenciais reais** para o `validar-integracoes` somente leitura:
   - AliExpress: `APP_KEY`, `APP_SECRET`, `TRACKING_ID`;
   - Amazon: `CREDENTIAL_ID`, `CREDENTIAL_SECRET` (versão 3.1) e `PARTNER_TAG` .com.br. Além disso, a conta precisa ter vendas qualificadas recentes, ou a API responde 429;
   - WhatsApp: WABA verificada, número, template de marketing aprovado, `WHATSAPP_APP_SECRET` e `WHATSAPP_VERIFY_TOKEN`.
2. **Infraestrutura real de homologação:** autorização para criar uma branch ou projeto no Supabase (com custo) e repetir lá a carga, a ida e volta das migrations e o backup.
3. **Aplicar 020 + 021 em produção** e publicar as edge functions `r` (sem mudança) e `whatsapp`. Só com autorização e depois do item 2.
4. **Conferir no contrato do Programa de Associados BR** o texto exato do aviso. O texto usado é "Como Associado Amazon, ganho com compras qualificadas.", conferido em fonte secundária.
5. **Limites oficiais do AliExpress** no console do Open Platform.
6. **Limite por IP na borda** para `r` e `whatsapp`: avaliar o recurso do Supabase ou um proxy, se houver abuso.
7. **Merge** da `fix/auditoria-expansao-modular` na `feat/expansao-modular` e depois na `main`: exige a sua autorização.

## 10. Plano de implantação gradual

Mesmo plano do relatório da expansão, com os gates desta auditoria. Avance só depois de 7 dias sem erro na fase anterior.

| Fase | O que ligar | Gate (condição para avançar) |
|---|---|---|
| 0 | Nada (migrations 020+021) | `rodar.sh`, `concorrencia_expansao.sh` e `migracoes_ida_volta.sh` verdes na **homologação real**; backup antes |
| 1 | Supabase Cron para `manutencao_diaria`; `agendamento_inteligente`; `canais_por_categoria` | `verificar_rollup` = 0 por 7 dias; fila sem alerta |
| 2 | `vitrine` (+ `VITRINE_PUBLICAR`) | Revisão manual; `testar_vitrine.py` verde; aviso Amazon conferido (item 9.4) |
| 3 | `alertas_preco` | Teste com a própria conta (/alerta, aviso, /parar) |
| 4 | `workers` | `fila_saude` sem alerta por 7 dias |
| 5 | `fonte_aliexpress` | `validar-integracoes` aprovado com credencial real |
| 6 | `fonte_amazon` | `validar-integracoes` aprovado; conta com vendas qualificadas; aviso e data/hora visíveis |
| 7 | `whatsapp` | WABA e template aprovados; inscrição, SAIR e replay testados com o próprio número; teto definido |

## 11. Procedimento de rollback

1. **Imediato, sem tocar no banco:** desligar a flag no painel, ou definir `RECURSOS_DESLIGADOS=whatsapp,vitrine,...` nas variáveis do repositório. Vale na próxima rodada.
2. **Workers:** `WORKERS_ATIVOS` diferente de `sim` desliga o `worker.yml`.
3. **Reverter a 021:** `psql -v ON_ERROR_STOP=1 -f supabase/rollback/20261125200000_auditoria_expansao.down.sql`.
   - **Atenção:** apaga o rollup. Dias além da retenção dos brutos perdem os totais.
   - A correção retroativa de `publicacao_id` é mantida, porque é um dado correto.
4. **Reverter a 020:** `.../20261125190000_expansao_modular.down.sql`, depois da 021. Apaga inscritos, alertas e tarefas.
5. **Comprovação:** o `migracoes_ida_volta.sh` comprova que cada reversão volta **exatamente** ao esquema e aos privilégios anteriores (`pg_dump` comparado). As duas migrations também reaplicam com a bateria inteira passando.
6. **Antes de qualquer reversão em produção:** backup (workflow `backup.yml`) e teste da reversão na homologação real.

## 12. Evidências dos testes executados

Todos os testes rodaram nesta sessão, em homologação local, sem credenciais nem rede externa.

| Bateria | Resultado |
|---|---|
| Backend (pytest) | **414 aprovados**, dos quais 18 são novos, em `tests/test_auditoria_expansao.py` |
| Banco (PG16) | **172/172**, dos quais 10 são novos, em `15_auditoria_expansao.sql` |
| Concorrência e backup (existente) | TUDO OK: `pg_restore` sem erros; 1.246 itens de esquema/RLS/permissões idênticos; 172/172 no banco restaurado |
| Concorrência da expansão (novo) | TUDO OK (seção 5) |
| Migrations: aplicar → reverter → reaplicar (novo) | Esquema e privilégios idênticos aos da 019 e da 020; 172/172 após reaplicar |
| Painel (Playwright) | TUDO OK, incluindo 3 verificações novas do Financeiro |
| Vitrine no Chromium (novo) | 16/16 em 360 e 1280 px: rolagem, XSS, imagem quebrada, h1 único, nomes acessíveis, contraste 5,5:1, foco visível, `rel=sponsored`, avisos, busca, filtro, ordenação, JSON-LD |
| Edge functions (node --test) | 10 aprovados (1 novo) |
| Carga progressiva | Seção 4.3 |
| Teste prolongado | Seção 13 |

**Antes da correção do achado 1**, a migration 020, num banco de teste, produziu: `clique.publicacao_id = NULL`, cliques no painel = 0. Depois da correção: atribuído.

Mudanças em testes existentes:

- **Nenhum teste foi removido ou enfraquecido.**
- O teste da vitrine na `14_expansao_modular.sql` ganhou uma linha de preparação (preço conferido agora), porque a regra ficou mais rígida. A asserção original continua.

## 13. Teste prolongado, backup e recuperação

### Teste prolongado

`homologacao/teste_prolongado.sh` rodou em homologação **LOCAL** com 2 CPUs:

- **Duração efetiva:** **20 min 3 s** (22:43:11Z → 23:03:14Z; planejada: 20 min).
- **Carga mista contínua:**
  - cliques a 6/s, o equivalente a cerca de 500 mil/dia de média;
  - 2 workers com 10% de falhas simuladas;
  - reservas de orçamento e mensagens de webhook (500 ids, com repetições e SAIR);
  - rollup a cada minuto e manutenção diária a cada 5 minutos.
- **Resultado:**

| Medida | Valor |
|---|---|
| Cliques | 7.063 transações, **0 falhas** |
| Latência média geral | 2,5 ms |
| Latência por minuto | média de 2,1 a 2,9 ms; máxima de 48,7 ms |
| Tarefas concluídas | 4.426 |
| Falhas definitivas | 0 (as simuladas foram retentadas) |
| Erros da carga mista | **0** |
| Rollup × brutos | diferença **0** |
| Memória do Postgres (RSS) | estável, de 465 a 498 MB, sem tendência de crescimento |

### Backup, restauração e indisponibilidade

- **Backup e restauração:** `concorrencia_e_backup.sh`, ver §12.
- **Queda real do banco no meio do processamento:**
  - 50 tarefas enfileiradas; 5 pegas por um worker;
  - Postgres derrubado com `pg_ctl -m immediate` (sem checkpoint, conexão ativa abortada);
  - o banco voltou em 0,7 s (*crash recovery*) com 45 pendentes e 5 em processamento, nada perdido;
  - vencida a trava, outro worker concluiu as 50, cada uma uma vez só (no máximo 2 tentativas).
- **Indisponibilidade de serviços externos (dublês):**
  - timeout vira erro temporário e abre o disjuntor (não insiste no mesmo serviço na rodada);
  - 429 respeita o `Retry-After`;
  - 401 renova o token uma vez;
  - banco fora (`ErroBanco`) para o worker sem marcar a tarefa como feita;
  - falha de um módulo não derruba a rodada (isolamento no orquestrador).

  Cerca de 140 testes de backend tratam erros de rede e banco, somando as baterias anteriores e as novas.
- **Recuperação após interrupção:** tarefa abandonada é retomada e a conclusão tardia é recusada (§5). Envio com resultado incerto nunca é repetido.
- **Não executado:** backup e restauração no Supabase real, e indisponibilidade da infraestrutura real. Os dois dependem de autorização (§9).

## 14. Arquivos alterados

**Novos:**

- **Banco:**
  - `supabase/migrations/20261125200000_auditoria_expansao.sql`;
  - `supabase/rollback/20261125190000_expansao_modular.down.sql` e `supabase/rollback/20261125200000_auditoria_expansao.down.sql`;
  - `supabase/tests/15_auditoria_expansao.sql`.
- **Scripts de homologação:** `supabase/homologacao/concorrencia_expansao.sh`, `migracoes_ida_volta.sh` e `teste_prolongado.sh`.
- **Backend:** `backend/tests/test_auditoria_expansao.py`.
- **Frontend:** `frontend/painel/js/telas/financeiro.js` e `frontend/testes/testar_vitrine.py`.

**Alterados:**

- **Backend:**
  - `cliente_http.py`, `config.py`, `integracoes/fontes/amazon.py`, `integracoes/registro.py`;
  - `inteligencia/precos.py`, `inteligencia/ranking.py`;
  - `main.py`, `repositorio.py`, `repositorio_memoria.py`, `worker.py`;
  - `servicos/publicacao.py`, `selecao.py`, `vitrine.py`, `whatsapp.py`.
- **Frontend:** `painel/css/painel.css`, `painel/index.html`, `painel/js/app.js`, `testes/painel.html`, `testes/supabase-falso.js`, `testes/testar_painel.py`.
- **Edge:** `_compartilhado/whatsapp.ts`, `whatsapp/index.ts`, `testes/whatsapp.test.ts`.
- **Banco e CI:**
  - `supabase/homologacao/carga_cliques.sh` (500 mil/dia, percentis, CPU, memória, conexões, rollup);
  - `supabase/tests/14_expansao_modular.sql` (preparação);
  - `.github/workflows/testes.yml` (novas baterias no CI).

## 15. Parecer final

**REPROVADO para produção** com qualquer funcionalidade nova ligada. Motivos:

- integrações sem validação real;
- carga sem infraestrutura real;
- WhatsApp sem WABA e template.

**APROVÁVEL apenas a Fase 0**, depois da sua autorização e de as baterias passarem numa homologação real do Supabase. A Fase 0 aplica as migrations 020 e 021 com todas as flags desligadas, comportamento idêntico ao atual, mas com:

- o achado 1 corrigido (cliques atribuídos às publicações);
- o rollup;
- a reversão comprovada.
