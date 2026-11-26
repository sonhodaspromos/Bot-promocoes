# Motor de IA — relatório técnico (P0)

Branch `feat/motor-ia-p0` · migration **028** (`20261126170000_motor_ia.sql`) · **não aplicado em produção** · IA nasce **desligada**.

## 1. Auditoria (o que já existia e foi reaproveitado)

| Pedido | Situação antes | O que foi feito |
|---|---|---|
| Offer Score | **Existe**: motor 0–100, pesos configuráveis, bloqueios e alertas (`inteligencia/motor.py`) | Reaproveitado (a IA não mexe na nota) |
| Price Intelligence | **Existe**: histórico, desconto artificial, monitor da página a cada 6 h | Reaproveitado; a IA só recebe desconto **comprovado** |
| Self-Learning / Revenue | **Existe**: aprendizado por categoria, horário, canal e faixa (amostra mínima + encolhimento), comissões e financeiro | Reaproveitado |
| A/B Testing | **Existe** para legenda e arte (teste binomial exato) | A/B **entre prompts** fica para o P1 |
| Anti-Spam | **Existe**: equivalência/saturação, limites diários, cupom 1×/dia | Reaproveitado |
| Validação antes de publicar | **Existe**: link, identificador de afiliado, domínio, preço reconferido, duplicidade, #publi, limites do canal | Texto da IA é **revalidado** na publicação |
| Filas, idempotência, orçamento atômico, disjuntor HTTP | **Existe** (`tarefas`, `orcamento_reservar`, `ClienteHTTP`) | Reaproveitado (serviço `ia` no orçamento) |
| Prompts, geração de texto por IA, tom de voz, custos de IA, anti prompt injection | **Não existia** | **Implementado no P0** |

## 2. Plano por prioridade

- **P0 (esta entrega):** provedor (API do Claude), prompts versionados com rollback e editor no painel, geração da chamada + frase de cada oferta, validação contra invenção, anti prompt injection, orçamento diário/mensal, cache/idempotência, disjuntor, aprovação manual, uso e custos no painel, testes.
- **P1:** prompts por categoria/canal/público; A/B entre versões de prompt (reaproveitando `experimentos.py`); ranking de copies por clique; histórico completo de conteúdos com filtros; Brand Voice com exemplos.
- **P2:** roteiros de Reels, campanhas temáticas, comparativos, perguntas frequentes, Trend Detector, Smart Scheduler com o aprendizado. **E-mails** dependem de um canal de e-mail, que o bot não tem.

## 3. Como funciona

```
rodada: … inteligência → [textos da IA] → fila → publicação …
```

1. Para cada oferta **na fila** sem texto da versão atual do prompt (até `ia.max_por_rodada`):
   - fatos = os mesmos dados do post (título limpo, loja, categoria, preço, Pix, preço antigo/desconto **só comprovados**, cupom aprovado);
   - `chave_idem = sha256(id do prompt + fatos)` → já existe? não chama de novo (**cache + idempotência**);
   - **disjuntor**: `ia.circuito_falhas` falhas seguidas → pausa `ia.circuito_pausa_min` minutos;
   - **orçamento**: reserva atômica do pior caso no teto de hoje = mín(teto diário, mensal − gasto do mês); estorna a sobra; falha certa estorna tudo, falha incerta mantém (conservador);
   - chama o modelo, **valida** e grava o texto (`pendente` com aprovação manual, `aprovado` sem ela). Inválido não vira texto; 2 inválidos seguidos → para de tentar aquela oferta (sem loop de gasto).
2. Na publicação, o texto aprovado é **conferido de novo** com os dados do momento. Se o preço mudou e o texto não bate, sai o texto padrão.
3. No post, a IA troca só a **chamada** (1ª linha) e acrescenta **uma frase** (✨). Preço, De/Por, cupom, link, aviso e #publi continuam sendo do bot.

### Validação da resposta (`ia/seguranca.py`)
Recusa se: não for JSON com `chamada` e `texto`; tamanho fora do limite; link, domínio, @ ou hashtag; marcação `<…>`; **qualquer número que não esteja nos dados**; promessa sem base nos dados (frete grátis, últimas unidades, só hoje, menor preço, garantia, brinde, original…); eco de instruções ("ignore as instruções…").

### Anti prompt injection
- Entrada: título/categoria normalizados (NFKC), sem caracteres de controle ou invisíveis de direção, sem `< > { }`, até 200 caracteres, enviados como **JSON dentro de `<dados>`**, separados das instruções; o sistema manda tratar `<dados>` como dado.
- Saída: a validação acima é a defesa principal. Mesmo que o modelo "obedeça" a um título malicioso, a resposta é descartada (teste `test_dados_vao_separados_das_instrucoes_e_injecao_no_titulo_e_barrada`).
- Variáveis de prompt: só uma lista fechada; o banco recusa variável desconhecida e modelo sem `{dados}`.

### O que a IA não pode fazer
Mudar regras, limites financeiros, pesos, validações ou preços. Prompts e configurações só o **dono** altera, pelo painel; toda versão fica guardada e é reversível.

## 4. Arquivos

| Arquivo | O quê |
|---|---|
| `supabase/migrations/20261126170000_motor_ia.sql` | tabelas `ia_prompts`, `ia_chamadas`, `ia_conteudos`; `ia_prompt_salvar`/`ia_prompt_ativar`; RLS; configurações; prompt v1 |
| `supabase/rollback/20261126170000_motor_ia.down.sql` | reversão (volta ao esquema da 027) |
| `supabase/tests/16_motor_ia.sql` | 5 testes do banco |
| `supabase/homologacao/migracoes_ida_volta.sh` | ida e volta da 027 e da 028 |
| `supabase/homologacao/concorrencia_expansao.sh` | cenário 7: salvamentos simultâneos de prompt e reservas de orçamento da IA |
| `backend/app/ia/` | `provedor.py`, `prompts.py`, `seguranca.py`, `servico.py` |
| `backend/app/{config,recursos,orquestrador,modelos,logs,cliente_http}.py`, `integracoes/registro.py`, `servicos/{publicacao,textos}.py`, `repositorio*.py` | integração (chave, flag, etapa da rodada, texto no post, máscara `sk-ant-`, 529 temporário, métodos de repositório) |
| `backend/tests/test_motor_ia.py` | 39 testes |
| `frontend/painel/js/telas/ia.js` + aba 🤖 IA | central de IA |
| `frontend/testes/*` | 8 checagens novas do painel |
| `.github/workflows/bot.yml` | segredo `ANTHROPIC_API_KEY` |

## 5. Testes (evidências)

| Bateria | Resultado |
|---|---|
| Backend (pytest) | **481 passaram** (39 novos do motor de IA) |
| Banco (homologação) | **177/177** (5 novos), ida e volta 020/021/**027/028** idênticas |
| Concorrência | 12 salvamentos simultâneos → versões 1–12 únicas, 1 em uso; 30 reservas → exatamente 25 aceitas, gasto = teto |
| Funções (node) | 20/20 |
| Painel (Playwright) | tudo OK (aba IA: aprovar, editar prompt, rollback, tetos, sem injeção de HTML, celular) |

Cobertura dos itens pedidos: preço inventado, desconto inventado, link/@/hashtag, promessa falsa, prompt injection no título, falha da API (certa e incerta), sobrecarga 529, excesso de gasto (diário e mensal), idempotência, limite por rodada, loop de respostas inválidas, disjuntor e volta, texto que ficou desatualizado, rodada completa ponta a ponta, concorrência.

**Não testado com a API real**: não há chave cadastrada. O formato da chamada é o documentado e está coberto por teste com sessão falsa, mas a integração real só pode ser considerada aprovada depois de uma geração de verdade.

## 6. Custos estimados

Modelo padrão `claude-haiku-5-5`: US$ 0,10 por milhão de tokens de entrada e US$ 0,50 por milhão de saída (prompts até 100 mil tokens). Um texto usa ~600 tokens de entrada e ~80 de saída → **~US$ 0,0001 por texto** (≈ 1.000 textos por US$ 0,10). Com 60 ofertas/dia: ~US$ 0,18/mês. Os tetos padrão (US$ 0,50/dia e US$ 5/mês) ficam muito acima disso. O custo real de cada chamada é gravado e aparece no painel. Se o modelo for trocado, atualize os preços em `ia.preco_entrada_usd_mtok` e `ia.preco_saida_usd_mtok`.

## 7. Riscos

- Validação por números/termos é conservadora: pode descartar textos bons (ex.: "2 em 1" se o título não tiver o 2). Efeito: volta o texto padrão, nunca publica errado.
- Texto editado no painel passa pela mesma conferência só na publicação (o painel avisa).
- Preços do modelo são configuração manual: se o modelo mudar e os preços não, o teto em US$ fica impreciso (o de tokens continua limitado por `ia.max_tokens_saida` e `ia.max_por_rodada`).
- LGPD: só dados de produto vão para o provedor; nenhum dado pessoal (inscritos do WhatsApp, e-mails, telefones) entra no prompt.

## 8. Pendências

P1 e P2 da seção 2; aplicar a 028 em produção (com autorização); cadastrar a chave; primeira geração real para validar a integração.

## 9. Critérios de aceitação

1. Com `recursos.ia` desligado, nada muda nos posts (testado).
2. Ligada, cada oferta na fila recebe no máximo um texto por versão de prompt; mesmos dados nunca geram duas cobranças.
3. Nenhum texto com número, link, @, hashtag ou promessa fora dos dados chega a um post.
4. Gasto nunca passa do teto diário nem do mensal, mesmo com chamadas simultâneas.
5. Com aprovação manual, só texto aprovado é usado.
6. Editar o prompt cria versão nova; voltar uma versão funciona; só o dono altera.
7. Uma geração real com a chave cadastrada aparece em Uso e custos com tokens e custo.

## 10. Manual de configuração

1. Crie uma chave em **platform.claude.com → API Keys** (com crédito ou limite de gasto na conta).
2. Cadastre em **Painel → Ajustes → Credenciais → "IA (Claude) · chave da API"** ou no segredo `ANTHROPIC_API_KEY` do GitHub.
3. **Painel → 🤖 IA**: confira os tetos (padrão US$ 0,50/dia e US$ 5/mês) e o tom de voz; ligue **IA ligada**.
4. Nas próximas rodadas, os textos aparecem em **Textos para aprovar**. Aprove (dá para editar) ou rejeite.
5. Quer mudar o estilo? Edite o prompt (vira uma versão nova). Não gostou? **Voltar para esta**.
6. Emergência: desligue a chave em IA, ou `RECURSOS_DESLIGADOS=ia` no GitHub (vale sobre o banco).
