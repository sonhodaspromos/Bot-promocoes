# Regras comerciais do motor de inteligência (V2)

Este documento descreve **como o bot decide** o que publicar, em que ordem e por quê.

**Princípio geral: nada é inventado.** Preço histórico, avaliação, comissão, estoque e métricas vêm só de:
- dados registrados pelo próprio bot;
- APIs oficiais das lojas;
- relatórios oficiais de comissão.

Quando falta dado, a decisão diz explicitamente "sem dados" e o fator fica fora da conta.

**Arquivos:** `backend/app/inteligencia/` (`precos.py`, `equivalencia.py`, `aprendizado.py`, `saturacao.py`, `ranking.py`, `experimentos.py`, `motor.py`), `backend/app/servicos/selecao.py`, migration `20261125180000_inteligencia_comercial.sql` (019).

## O que já existia e foi preservado

O motor (`motor.py`) continua sendo o **filtro de qualidade**: nota de 0 a 100, confiança, bloqueios, motivos e alertas. As notas e as classificações (excelente / boa / não recomendada) **não mudaram**.

A única mudança no motor é um bloqueio novo: desconto artificial do tipo "sobe e desce" (regra 3). Ele pode ser desligado em `inteligencia.bloquear_desconto_artificial`.

A camada nova (seleção) só **ordena e explica** as ofertas que o motor aprovou.

## 1. Histórico de 7, 30 e 90 dias

- **Como o banco grava:** o banco registra uma linha a cada **mudança** de preço ou de disponibilidade. Por isso, o preço vigente num momento é o do último registro anterior a ele.
- **Âncora:** a RPC `historico_precos_janela` devolve também o último preço *antes* da janela. Sem ela, um produto com o mesmo preço há 4 meses pareceria "sem histórico".
- **Disponibilidade:** períodos com o produto **indisponível** não contam como preço.
- **Valores por janela:**
  - mínimo;
  - máximo;
  - média ponderada pelo tempo em que cada preço vigorou;
  - cobertura (% da janela que o bot observou).
- **Cobertura mínima:** 80% (`inteligencia.historico_cobertura_minima_pct`). Abaixo disso a janela fica "insuficiente": os números aparecem, mas **nenhuma conclusão** é tirada dela.

## 2. Menor preço histórico

- **Definição:** menor preço observado com o produto disponível, sempre informado junto com o período monitorado (ex.: "menor preço desde 10/09/2026, 76 dias").
- **Mínimo de monitoramento:** com menos de 7 dias, o bot **não afirma** menor preço.
- **Tolerância:** 1% (arredondamentos da loja).

## 3. Desconto artificial

Só é avaliado com histórico suficiente. Sem histórico, a análise diz "histórico insuficiente para avaliar".

| Padrão | Efeito |
|---|---|
| **"Sobe e desce":** nos últimos 30 dias o preço subiu ao menos 15% acima do típico de 90 a 30 dias atrás; o "preço de" é o preço inflado; e o preço atual **não** está abaixo do típico | **Bloqueio**: "Desconto artificial" |
| "Preço de" acima de tudo que foi observado na janela | Alerta. O desconto divulgado é recalculado pelo histórico (regra que já existia) |
| Preço atual mais de 5% acima do menor dos últimos 30 dias | Alerta: "já esteve mais barato" |

## 4. Ranking

Só entram ofertas aprovadas pelo motor. Cada fator vale de 0 a 1. Fator sem dados fica fora da média ponderada e aparece em "sem dados".

| Fator | Peso | Regra | Sem dados quando |
|---|---|---|---|
| Qualidade | 30 | nota do motor ÷ 100 | nunca |
| Desconto comprovado | 20 | desconto real ÷ meta (60%), só se comprovado. **Não comprovado vale 0** | — |
| Comissão | 15 | % oficial ÷ meta (10%) | não veio da API oficial |
| Vendedor | 10 | loja oficial = 1; vendedor comum = 0,5 | a loja não informa (a Shopee informa `shopType`; se a API recusar o campo, fica sem dados) |
| Estoque | 5 | 10 ou mais = 1; de 1 a 9 = 0,5 ("risco de esgotar"); **0 = recusa** | a loja não informa (a API da Shopee **não** informa estoque) |
| Potencial de conversão | 20 | fator de cliques do segmento ÷ 2 (regra 6) | amostra insuficiente |

A avaliação do produto e as vendas na loja já entram na nota de qualidade do motor (quesito popularidade), por isso não são contadas de novo.

## 5. Produtos repetidos entre marketplaces

| Confiança | Critério | Efeito |
|---|---|---|
| **Alta** | Mesmo GTIN/EAN válido (dígito verificador conferido; o banco recusa GTIN inválido) | Só a oferta de **menor preço** segue; a outra é recusada com o motivo |
| **Média** | Mesma marca **e** mesmo código de modelo, por exemplo "WH-1000XM4". Medidas como 128GB ou 110V não contam como modelo | Igual à confiança alta |
| **Baixa** | Títulos com 85% ou mais de palavras em comum, sem identificador | **Só aviso.** As duas seguem, porque o título não prova que é o mesmo produto |

Regras adicionais:
- GTINs válidos e diferentes, ou modelos diferentes, indicam produtos diferentes, mesmo com título igual.
- A regra antiga de "mesmo título dentro da mesma oferta ativa" continua valendo.
- Equivalências ficam em `produtos_equivalentes`, onde o dono ou um editor pode confirmar ou rejeitar.

## 6. Aprendizado por categoria, horário, canal e faixa de preço

**Fonte:** `vw_desempenho_publicacoes`, com uma linha por publicação real.

**Cliques:**
- só cliques únicos;
- os suspeitos ficam em `cliques_suspeitos` e não contam.

**Atribuição de vendas:**
- o relatório oficial traz o código do item;
- o banco liga a venda ao produto e à última oferta publicada antes da venda;
- itens que o bot nunca publicou ficam como `produto_sem_publicacao` ou `item_desconhecido` e não contam;
- se a oferta saiu em vários canais, a comissão é dividida igualmente entre eles. O relatório da loja não diz em qual canal a pessoa clicou.

**Segmentos:**
- horário: madrugada, manhã, tarde e noite;
- faixas de preço: até R$ 50, R$ 50–100, R$ 100–250, R$ 250–500 e acima de R$ 500.

**Amostra mínima:** 20 publicações por segmento (`inteligencia.amostra_minima_segmento`). Abaixo disso o fator fica **neutro (1,0)**.

**Estimativa encolhida (média bayesiana):**
- (cliques do segmento + k × média geral) ÷ (publicações do segmento + k), com k igual à amostra mínima;
- assim, poucos dados não geram conclusões exageradas.

## 7. Prioridade por receita esperada, sem sacrificar a qualidade

- **Fórmula:** receita esperada por publicação = preço × comissão × conversão por clique × cliques por publicação × fator do segmento.
- **Quando é calculada:** só com **10 ou mais vendas atribuídas** (`inteligencia.min_conversoes`). Antes disso: "receita esperada indisponível".
- **Prioridade final:** 70% da prioridade dos fatores mais 30% da receita (`inteligencia.peso_receita`, máximo 70%), normalizada pela maior receita da rodada.
- **Garantia de qualidade:** a ordenação é **primeiro por classificação**. Toda oferta "excelente" vem antes de toda "boa". A receita só reordena dentro da mesma classificação.
- **Vagas:** quem passa do limite da rodada ou do dia é registrado como "sem vaga nesta rodada (Nº lugar)".

## 8. Saturação da audiência

- **Canal:**
  - compara a média de cliques nas primeiras 2 horas dos posts das últimas 24 h com a base de 2 a 14 dias atrás;
  - exige pelo menos 10 posts recentes e 30 de base;
  - queda de 50% ou mais (`inteligencia.saturacao_queda_pct`) deixa o canal **saturado**: nesta rodada, só ofertas "excelentes" entram nele. Ofertas manuais sempre passam;
  - sem amostra, o canal fica como "dados insuficientes" e nada é limitado.
- **Categoria:** 3 ou mais posts da mesma categoria nas últimas 6 horas, somando os canais, fazem as novas ofertas dela esperarem. A recusa traz o motivo.

## 9. Testes A/B de artes e legendas

- **Configuração:** um teste ativo por vez, criado na tela **Desempenho**.
- **Variantes:**

| Tipo | Variante A | Variante B |
|---|---|---|
| Legenda | atual | preço antes do título |
| Arte | atual | fundo escuro |

- **Sorteio:** determinístico pelo SHA-256 de (teste, oferta, canal). Reenviar não troca a variante.
- **Registro:** cada publicação guarda `experimento_id` e `variante`.
- **Métrica:** cliques únicos por publicação. As vendas aparecem só como informação.
- **Teste estatístico:** binomial condicional exato (aproximação normal acima de 1.000 cliques).
- **Vencedor:** só com **30 ou mais publicações por variante e p < 0,05**. Caso contrário: "sem resultado conclusivo".

## 10. Dashboard (painel → Desempenho)

- **Indicadores:** publicações, cliques, cliques por post, vendas atribuídas e comissão atribuída.
- **Tabelas por segmento:** canal, categoria, horário e faixa de preço, marcando "poucos dados".
- **Aprendizado:** inclui o que falta para concluir.
- **Saturação:** por canal e por categoria.
- **Teste A/B:** resultado e criação ou encerramento do teste.
- **Conciliação de comissões:** tabela mensal e formulário de repasse.
- **Seleção:** as últimas decisões, com fatores, motivos e "sem dados".
- **Tela Ofertas:** a explicação de cada oferta passou a mostrar o histórico de 7, 30 e 90 dias e o menor preço.

## 11. Conciliação de comissões

- **Status:** pendente → confirmada (aprovada) → paga. Ou cancelada.
- **Trilha:** cada criação ou mudança de status e de valor fica em `comissoes_eventos`, que é imutável.
- **"Paga" é estado final:** só o dono desfaz (erro P0029). A importação automática nunca altera uma comissão paga.
- **Repasse:** o dono informa o valor recebido de um mês (RPC `registrar_repasse`).
  - Se bater com a soma das **confirmadas** daquele mês (diferença de até R$ 1,00), o repasse é "conciliado" e elas viram **pagas**.
  - Se não bater, fica "divergente" e **nada** é marcado.
  - Cada repasse fica registrado em `comissoes_pagamentos` (imutável).
- **Alertas mensais:** pendentes há mais de 90 dias e confirmadas sem repasse após 75 dias.
- **Reconciliação semanal com o relatório oficial:** a de 90 dias, que já existia, continua.

## 12. Explicação das decisões

**Motor:** cada oferta tem motivos, bloqueios, alertas, quesitos e, agora, `historico_precos` (janelas, menor preço, desconto artificial e "sem dados").

**Seleção:** cada decisão grava em `selecoes_ofertas` (guardada por 30 dias):
- selecionada ou recusada;
- posição;
- prioridade;
- receita esperada;
- fatores com nota, peso e explicação;
- motivos;
- motivo da recusa;
- avisos;
- lista do que ficou sem dados.

**Se a seleção falhar** (por exemplo, banco sem a migration 019), a publicação **continua** pela ordem de nota do motor, com aviso no log.

## Limitações conhecidas (sem dados, não estimados)

- **Estoque:** a API de afiliados da Shopee não informa estoque. A coluna existe para lojas que informarem ou para cadastro manual.
- **Vendedor:** os campos `shopId`, `shopName` e `shopType` da Shopee precisam ser confirmados com credenciais reais. Se a API recusar algum, a busca refaz sem eles e o vendedor fica "sem dados".
- **GTIN:** a API da Shopee não informa GTIN. A confiança alta depende de cadastro manual ou de outra loja que informe.
- **Atribuição por canal:** é dividida igualmente. Atribuição exata por clique exigiria um `subId` por canal no link de afiliado, o que ainda não está implementado.
- **Mercado Livre:** sem API de comissões. Os lançamentos manuais entram como atribuição `manual` e não alimentam o aprendizado.
