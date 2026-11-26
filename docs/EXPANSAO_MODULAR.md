# Expansão modular do Bot de Promoções V2

Branch `feat/expansao-modular`, criada a partir de `feat/inteligencia-comercial`. Data: 25/11/2026.

> **Atualização (auditoria de 25/11/2026):** ver `docs/RELATORIO_AUDITORIA_EXPANSAO_MODULAR.md`. A auditoria corrigiu o adaptador Amazon (endereços oficiais da região NA agora são o padrão, token em JSON com escopo e `x-marketplace`), tornou o orçamento do WhatsApp atômico, protegeu o webhook contra replay, adicionou o rollup diário de cliques e corrigiu a atribuição de cliques às publicações. Os números de carga desta página foram substituídos pelos do relatório da auditoria (1 mil a 500 mil cliques/dia).

> **Situação: nenhuma integração nova foi validada de verdade.** AliExpress, Amazon e WhatsApp foram
> implementados conforme a documentação pública e testados só com dublês (respostas simuladas). Eles
> continuam **desligados**: as flags estão em `false` e os marketplaces estão em modo `desativado`. Uma
> integração só pode ser considerada aprovada depois de `python -m app.main validar-integracoes` passar
> com credenciais reais e gravar a evidência. Nada foi aplicado no banco de produção, não houve deploy,
> push nem merge e nenhuma oferta foi publicada.

## 1. Resumo

| # | Item | Entrega | Situação |
|---|---|---|---|
| 1 | Avaliação das APIs e dos programas de afiliados | Seção 2 | Pesquisa feita em 25/11/2026 |
| 2 | Adaptadores por marketplace | `fontes/aliexpress.py` e `fontes/amazon.py`; Magalu e Mercado Livre ficam manuais | Testados com dublês, sem validação real |
| 3 | WhatsApp Business | Seção 4, `servicos/whatsapp.py` e a edge function `whatsapp` (consentimento e descadastro) | Não validado; exige WABA, template aprovado e opt-in |
| 4 | Vitrine pública | `servicos/vitrine.py`: HTML estático com SEO, categorias, busca, filtros e cupons | Testado; publicação opcional |
| 5 | Alertas de preço | `servicos/alertas.py`: bot do Telegram com /alerta, /meusalertas, /apagar e /parar | Testado com dublê |
| 6 | Vários canais por categoria | Tabela `canais_categorias` e a rota em `publicacao._canais_alvo` | Testado |
| 7 | Agendamento inteligente | `servicos/agendamento.py`: intervalo por canal, horário permitido e desempenho medido | Testado |
| 8 | Workers e filas persistentes | Tabela `tarefas` (SKIP LOCKED, idempotência e espera crescente), `worker.py` e `worker.yml` | Testado no banco e no backend |
| 9 | Teste de carga | `supabase/homologacao/carga_cliques.sh` | Executado (seção 6). Achou e corrigiu um gargalo real |
| 10 | Custos por cenário | Seção 7 | Estimativa a partir dos preços públicos |

Feature flags (configuração `recursos.*`, todas `false` por padrão): `fonte_amazon`, `fonte_aliexpress`,
`whatsapp`, `vitrine`, `alertas_preco`, `canais_por_categoria`, `agendamento_inteligente` e `workers`.
Para desligar tudo com urgência, sem passar pelo painel, há a variável de ambiente
`RECURSOS_DESLIGADOS=whatsapp,vitrine,...`, que vence o banco.

Com todas as flags desligadas, o comportamento é o mesmo de antes. As baterias anteriores continuam
passando sem nenhum teste removido ou enfraquecido.

## 2. Disponibilidade oficial por marketplace

| Marketplace | Programa | API oficial para afiliado | Decisão no bot |
|---|---|---|---|
| **AliExpress** | Portals / Open Platform | Sim. `aliexpress.affiliate.product.query`, `productdetail.get`, `link.generate` e `order.list`, com app_key, app_secret e tracking_id, assinatura HMAC-SHA256 e gateway `api-sg.aliexpress.com` | Adaptador `AliExpressFonte` (busca, consulta e comissões). Precisa de aprovação do app no Open Platform |
| **Amazon** | Amazon Associates | A Creators API substitui a PA-API 5: OAuth 2.0 (Credential ID e Secret), operações `searchItems` e `getItems` com `offersV2`. A PA-API foi descontinuada em 30/04/2026 e desligada em 15/05/2026 | Adaptador `AmazonFonte`. As URLs do token e da API vêm de variáveis de ambiente porque o endpoint para amazon.com.br não foi confirmado na documentação oficial. Exige conta Associates aprovada (segundo fontes de terceiros, com vendas qualificadas recentes) |
| **Mercado Livre** | Programa de Afiliados e Criadores | **Não há API pública para gerar link de afiliado.** Os links são gerados pela Central de Afiliados ou pela Barra de Afiliados | Fica **manual**: o link é colado no painel. Nada de automação de navegador nem scraping |
| **Magalu** | Parceiro / Influenciador Magalu | **Não foi encontrada API pública.** O link é gerado no app ou no painel do Parceiro Magalu | Fica **manual** (`ManualFonte("magalu")`), sem domínios automáticos |

Regras do adaptador:

- **Nenhum dado é inventado.** A nota fica `None` no AliExpress, porque `evaluate_rate` é a porcentagem de
  avaliações positivas e não uma nota de 0 a 5. A comissão fica `None` na Amazon, porque a API não a informa.
- **Preço original inconsistente é descartado.** Se o "original" é menor que o preço, ele não é usado.
- **Validação só de leitura.** O `cliente_http` só deixa passar, durante a validação, os métodos de consulta
  do AliExpress, os caminhos `/searchItems` e `/getItems` e a troca de token `client_credentials`. Os
  métodos `link.generate` e de envio do WhatsApp ficam bloqueados.
- **Produção sem credencial bloqueia a rodada.** Uma integração listada em `INTEGRACOES_ATIVAS` sem
  credencial nunca cai para demonstração, mantendo a regra das auditorias anteriores.
- **A busca exige as duas condições.** As fontes novas só entram na busca com a flag ligada **e** o
  marketplace em modo `real`.

## 3. Adaptadores e arquitetura

```
orquestrador ─┬─ busca ─ fontes{shopee, aliexpress*, amazon*, manuais(ml, magalu)}   * só com a flag
              ├─ publicação ─ canais base + canais extras por categoria* + agendamento*
              └─ módulos ─┬─ inline (padrão): vitrine*, alertas*, whatsapp*
                          └─ workers*: enfileira em `tarefas` (uma chave por hora) → worker.yml consome
```

- **Fila persistente.** `tarefa_enfileirar` aceita uma chave de idempotência. `tarefas_pegar` usa
  `FOR UPDATE SKIP LOCKED`, com trava de prazo definido, e marca como falha a tarefa que esgotou as
  tentativas. `tarefa_concluir` aplica espera crescente `min(3600 s, 30·2^tentativas)` e **recusa o
  resultado** de um worker que não é mais o dono da tarefa.
- **Isolamento.** Uma falha de módulo vira `resumo.modulos[...]` com erro e não derruba a rodada. Uma falha
  de banco (`ErroBanco`) interrompe o worker, para não mascarar indisponibilidade.
- **Sem envio fora de produção.** Alertas e WhatsApp só enviam em `AMBIENTE=producao` e com credenciais. Em
  homologação o módulo devolve "pulado: fora de produção".

## 4. WhatsApp Business: consentimento, descadastro, limites e custos

**Caminho oficial: WhatsApp Cloud API (Meta).** Não foram usadas bibliotecas não oficiais nem automação do
WhatsApp Web, que violam os termos e levam a banimento.

| Tema | Implementação |
|---|---|
| Consentimento (LGPD e Meta) | Só recebe quem se inscreveu de forma ativa: mandando mensagem com intenção clara ou pelo formulário. São gravados a origem, o texto do consentimento e a data (`whatsapp_inscritos`), e os eventos ficam num registro que não pode ser alterado (`whatsapp_eventos`) |
| Descadastro | As palavras "sair", "parar", "stop", "cancelar" e "descadastrar" são tratadas pelo webhook (`whatsapp_descadastrar`), que tem efeito imediato. O telefone é apagado 30 dias depois (`privado.limpar_expansao`) |
| Segurança do webhook | Assinatura `X-Hub-Signature-256` comparada em tempo constante e `verify_token` na verificação |
| Limites | No máximo `whatsapp.max_por_inscrito_dia` (3) mensagens por pessoa por dia; a mesma oferta nunca é repetida (chave inscrito + promoção); o filtro de categoria respeita a escolha do inscrito |
| Custo | Teto diário em `whatsapp.orcamento_diario_usd` (padrão US$ 5). Ao atingir o teto, o envio para e registra `bloqueio_orcamento`. Um envio com resultado incerto conta como gasto e não é reenviado |
| Template | Só envia template de marketing aprovado (`WHATSAPP_TEMPLATE`), com o link rastreado `&c=whatsapp` |

Desde 01/07/2025 a Meta cobra por mensagem, e todo template de marketing é cobrado. Para o Brasil, o
marketing custa cerca de US$ 0,0625 por mensagem segundo um parceiro oficial; outra fonte cita US$ 0,07188
a partir de abril de 2026. A partir de 01/10/2026 as mensagens de serviço também passam a ser cobradas.
**Confirme a tabela vigente no Gerenciador do WhatsApp antes de ligar a flag.**

Pré-requisitos ainda não cumpridos:

- conta WABA verificada;
- número dedicado;
- template de marketing aprovado;
- página pública de inscrição com o texto de consentimento;
- deploy da edge function `whatsapp` com `WHATSAPP_APP_SECRET` e `WHATSAPP_VERIFY_TOKEN`;
- rodar o `validar-integracoes`, que faz apenas um GET no phone_id e não envia nada.

## 5. Vitrine, alertas, canais por categoria e agendamento

- **Vitrine estática.** Não cria nenhuma superfície pública nova no banco: é gerada por RPC de service_role
  e publicada como arquivos, por exemplo no GitHub Pages pelo `worker.yml` com `VITRINE_PUBLICAR=sim`.
  Inclui:
  - página inicial com busca, filtro por categoria, marketplace e preço, e ordenação, tudo em JavaScript
    puro;
  - páginas por categoria e por oferta, com JSON-LD `Product/Offer`;
  - `cupons.html`, `privacidade.html`, `sitemap.xml`, `robots.txt` e `ofertas.json`.

  Toda página mostra o aviso #publi, e os links têm `rel="sponsored nofollow"` e passam pelo redirecionador
  com `c=vitrine`, para a atribuição. O "De R$" só aparece com `desconto_comprovado`. Todo o conteúdo é
  escapado, as imagens precisam ser https e ofertas de demonstração nunca entram.
- **Alertas de preço.** Funcionam no Telegram, em conversa privada; grupos são ignorados. Para cada pessoa
  ficam guardados só o chat_id e os termos. São no máximo 10 alertas e 5 avisos por dia por pessoa, e um
  aviso por oferta. `/parar` apaga tudo. Se o bot for bloqueado (resposta 403), os dados são apagados.
- **Canais por categoria.** Canais extras do Telegram, cada um com `identificador_externo`, recebem só as
  categorias vinculadas em `canais_categorias`. Os canais base continuam iguais.
- **Agendamento inteligente.** Mantém o intervalo mínimo por canal (`agendamento.min_intervalo_canal_min`)
  e o horário de publicação. Com amostra suficiente, prefere o período de melhor desempenho medido. Sem
  dados, apenas espaça as publicações, e diz isso na explicação. As publicações manuais continuam saindo
  na hora.

## 6. Teste de carga progressivo dos cliques

O script é `supabase/homologacao/carga_cliques.sh`. Ele roda só em homologação local e aborta se detectar
um Supabase real.

**Premissas:**

- hora de pico com 15% dos cliques do dia, e rajada de 3× a média dessa hora;
- teste a **5× a rajada**, com 8 conexões por 20 s;
- 70% de visitantes únicos;
- 30 dias de cliques já gravados antes de medir;
- `vacuum analyze`, ou seja, autovacuum em dia.

**Máquina:** PostgreSQL 16 local com 2 CPUs. Não é o hardware do Supabase.

### Resultado final (com a correção)

| Cenário | Alvo | Atingido | Latência média | Falhas | Linhas (30 d) | Tabela + índices | Painel 30 d | Manutenção |
|---|---|---|---|---|---|---|---|---|
| 1.000/dia | 1/s | 0,85/s* | 6,4 ms | 0 | 30.017 | 15 MB | 5,7 ms | 6,8 ms |
| 10.000/dia | 6/s | 6,3/s | 3,4 ms | 0 | 300.125 | 142 MB | 14,2 ms | 14,0 ms |
| 100.000/dia | 62/s | 62,6/s | 11,6 ms | 0 | 3.001.250 | 1.459 MB | 89,3 ms | 108,7 ms |

\* Com a taxa de 1/s, a janela de 20 s gera poucas amostras; a variação é estatística.

**Capacidade máxima medida** (100 mil/dia, sem limite de taxa): **2.448 cliques/s** com 3,3 ms de latência
média e zero falhas. É cerca de 40× a rajada estimada do cenário de 100 mil/dia.

### Gargalo real encontrado e corrigido

A primeira rodada, antes da correção, mostrou que o cenário de 100 mil/dia **não aguentava**:

| Cenário | Atingido | Latência | Painel 30 d |
|---|---|---|---|
| 100.000/dia (antes) | 4,7/s de 62/s | 8.468 ms | 9.445 ms |

**Causa 1, no caminho do clique.** `registrar_clique` comparava `p_ip_hash` (text) com a coluna `ip_hash`
(char(64)). O Postgres convertia a coluna e deixava de usar o índice `cliques_ip_idx`, então o limite por
visitante varria a tabela, a cerca de 330 ms por clique com 3 milhões de linhas.

*Correção:* uma variável `v_ip char(64)`, do mesmo tipo da coluna. A migration 20261125190000 redefine a
função, com o mesmo comportamento e as mesmas regras de deduplicação, e as 162 asserções do banco continuam
passando.

**Causa 2, no painel.** A contagem de cliques por publicação lia a tabela inteira, e não só o índice.

*Correção:* o índice `cliques_publicacao_data_idx (publicacao_id, ocorrido_em)`, que permite contar só pelo
índice.

Depois das correções, o cenário de 100 mil/dia foi de 4,7/s para 62,6/s atingidos, com a latência caindo de
8,5 s para 11,6 ms e o painel de 9,4 s para 89 ms.

**Limitação.** Sem o vacuum em dia, isto é, logo depois de uma carga em massa, o painel ainda leva cerca de
9 s com 3 milhões de linhas, porque a leitura só pelo índice depende do *visibility map*. Em operação
normal o autovacuum cuida disso. Para escalar além de 100 mil/dia, a recomendação é uma tabela de
agregados diários (rollup), que é o próximo passo e não foi implementado.

## 7. Estimativa de custos por cenário (USD/mês)

**Premissas:**

- um clique gera uma invocação de edge function (o redirecionador);
- outras invocações (webhook e funções do painel) somam menos de 10 mil por mês;
- o banco é estimado pelo tamanho medido em 30 dias × 12,2, para a retenção de 365 dias, com margem de
  1,5× porque os user agents reais são mais longos que os do teste.

Preços públicos do Supabase:

- edge functions: o Free inclui 500 mil invocações e o Pro inclui 2 milhões, depois US$ 2 por milhão
  (cobrado em pacotes de 1 milhão);
- o Pro custa US$ 25 e inclui 8 GB de disco, depois US$ 0,125/GB;
- o compute Small custa US$ 15, abatido o crédito de US$ 10;
- o Free tem 500 MB de banco e pausa após 7 dias sem uso.

| Item | 1.000 cliques/dia | 10.000 cliques/dia | 100.000 cliques/dia |
|---|---|---|---|
| Invocações por mês | ~30 mil | ~300 mil | ~3 milhões |
| Cliques em 365 dias (banco) | ~0,2–0,3 GB | ~1,7–2,6 GB | ~17,5–26 GB |
| Plano | Free (US$ 0) cabe, mas sem backup diário; **recomendado Pro** | Pro (o banco passa de 500 MB) | Pro |
| Plano base | US$ 0 ou 25 | US$ 25 | US$ 25 |
| Compute | Micro (coberto pelo crédito) | Micro (coberto) | Small recomendado: +US$ 5 |
| Invocações excedentes | US$ 0 | US$ 0 | 1 pacote: US$ 2 |
| Disco excedente (> 8 GB) | US$ 0 | US$ 0 | ~US$ 1,20–2,30 |
| Egress (redirecionamento < 1 KB) | desprezível | desprezível | ~3 GB, dentro dos 250 GB |
| Vitrine (GitHub Pages) | US$ 0 | US$ 0 | US$ 0 |
| GitHub Actions | US$ 0 em repositório público | idem | idem |
| **Total de infraestrutura** | **US$ 0–25** | **~US$ 25** | **~US$ 33–35** |
| Alternativa: retenção de 90 dias | igual | igual | o banco cai para ~4–7 GB: ~US$ 27 |

Observações sobre o GitHub Actions: num repositório **privado**, o bot (a cada 10 min) mais o worker (a
cada 15 min) passam de 7 mil minutos por mês e excedem a cota gratuita. Confira o preço vigente antes de
tornar o repositório privado.

### WhatsApp: custo variável, que domina tudo o resto

Cálculo: inscritos × mensagens por dia × 30 × preço por mensagem (US$ 0,0625; o pior caso é US$ 0,0719).

| Inscritos | 1 mensagem/dia | 3 mensagens/dia (máximo configurado) |
|---|---|---|
| 100 | US$ 187,50 a 215,60 | US$ 562,50 a 646,90 |
| 1.000 | US$ 1.875 a 2.156 | US$ 5.625 a 6.471 |

Com o teto padrão de US$ 5/dia, o gasto fica limitado a **US$ 150/mês**, o que equivale a cerca de 80
mensagens por dia. Por isso o WhatsApp é a **última** fase da implantação e só deve ser ligado com
comissão medida que justifique o custo. Não há dados de conversão do canal WhatsApp neste projeto, e nenhum
retorno foi estimado.

## 8. Testes

| Bateria | Resultado |
|---|---|
| Backend (pytest) | **396 aprovados**, dos quais 32 são novos, em `tests/test_expansao_modular.py` |
| Banco (homologação PG16) | **162/162**, incluindo `14_expansao_modular.sql` com 9 testes |
| Concorrência e backup | TUDO OK |
| Painel (Playwright) | TUDO OK |
| Edge functions (node --test) | 9 aprovados: regras 5 e whatsapp 4 (o CI agora roda `*.test.ts`) |
| Carga de cliques | Seção 6 |
| Simulação demo | 8 promoções e 8 publicações simuladas, sem nada publicado |

Os testes novos cobrem:

- flags e kill switch;
- assinatura, parsing, erros e comissões do AliExpress, sem inventar nota;
- token em cache da Amazon, https obrigatório e comissão `None`;
- bloqueio em produção sem credencial;
- alvos de validação, com Magalu e Mercado Livre manuais;
- busca condicionada à flag;
- vitrine: SEO, JSON-LD, escape, exclusão de demonstração, "De" só com desconto comprovado e sitemap;
- comandos do bot, grupo ignorado, aviso uma única vez e dados apagados após 403;
- WhatsApp: consentimento, categoria, descadastro, teto de gasto, envio incerto e nada fora de produção;
- rota de canais por categoria;
- agendamento com e sem dados, e intervalo gravado na fila;
- worker: retentativa com espera, idempotência, descarte de tarefa assumida por outro worker e parada com
  o banco fora;
- enfileiramento a cada hora;
- vitrine gerada pelo worker.

## 9. Plano de implantação gradual

Cada fase só começa depois de a anterior rodar **7 dias sem erro** no painel. A reversão é sempre imediata:
desligar a flag no painel ou usar `RECURSOS_DESLIGADOS` nas variáveis do repositório.

| Fase | O que ligar | Pré-condição (gate) | Como verificar |
|---|---|---|---|
| 0 | Nada | Aplicar a migration 20261125190000 numa **branch do Supabase**, rodar `homologacao/rodar.sh` e depois aplicar em produção **com autorização** | As flags aparecem como `false` e a rodada continua igual |
| 1 | `agendamento_inteligente` e `canais_por_categoria` | Canal extra do Telegram com `identificador_externo` e categorias vinculadas | Fila com `agendado_para` espaçado; o canal recebe só a sua categoria |
| 2 | `vitrine` (+ `VITRINE_PUBLICAR=sim`) | `vitrine.url_base` definida; GitHub Pages ativo | Revisão manual da página, Search Console e cliques com `c=vitrine` |
| 3 | `alertas_preco` | O bot do Telegram aceita conversa privada; texto de privacidade revisado | Teste com a própria conta: /alerta, aviso e /parar |
| 4 | `workers` (+ `WORKERS_ATIVOS=sim`) | Fases 2 e 3 estáveis | Tabela `tarefas` sem falhas acumuladas |
| 5 | `fonte_aliexpress` | App aprovado no Open Platform, `INTEGRACOES_ATIVAS` com aliexpress e **`validar-integracoes` aprovado** | Ofertas com link de afiliado; `order.list` conciliando comissões |
| 6 | `fonte_amazon` | Conta Associates elegível para a Creators API, endpoint oficial para .com.br confirmado e `validar-integracoes` aprovado | Ofertas com a tag de afiliado; preço vindo de `offersV2` |
| 7 | `whatsapp` | WABA verificada, template aprovado, página de inscrição, edge `whatsapp` publicada, `validar-integracoes` aprovado e orçamento definido | Inscrição e descadastro de ponta a ponta com o próprio número; gasto diário abaixo do teto |

Mercado Livre e Magalu continuam manuais, com o link colado no painel. Se algum dia surgir uma API oficial
de afiliado, basta criar um adaptador novo no mesmo padrão.

## 10. Arquivos

- **Banco:** `supabase/migrations/20261125190000_expansao_modular.sql` e
  `supabase/tests/14_expansao_modular.sql`.
- **Carga:** `supabase/homologacao/carga_cliques.sh`.
- **Backend:**
  - módulos novos: `app/recursos.py`, `app/modulos.py`, `app/worker.py`;
  - fontes: `app/integracoes/fontes/{aliexpress,amazon}.py`;
  - serviços: `app/servicos/{vitrine,alertas,whatsapp,agendamento}.py`;
  - testes: `tests/test_expansao_modular.py`;
  - alterados: `config`, `ambiente`, `registro`, `cliente_http`, `busca`, `publicacao`, `orquestrador`,
    `repositorio(_memoria)`, `main`, `modelos`.
- **Edge:** `supabase/functions/_compartilhado/whatsapp.ts`, `supabase/functions/whatsapp/index.ts` e
  `supabase/functions/testes/whatsapp.test.ts`.
- **CI:** `.github/workflows/worker.yml` (novo) e `testes.yml` (edge com `*.test.ts`).
