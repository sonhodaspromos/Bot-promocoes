# Motor de inteligência de ofertas

O motor decide **quais ofertas merecem ser divulgadas e em que ordem**. Cada oferta recebe:

- uma **pontuação de 0 a 100**;
- uma **classificação**: Excelente oportunidade, Boa oportunidade ou Oferta não recomendada;
- uma **confiança de 0 a 100%**, que diz o quanto dos dados usados é real e confirmado;
- uma **explicação**: o que aprovou, o que bloqueou, os alertas e os pontos de cada quesito. Ela aparece no painel em "Por que esta classificação?".

Código: `backend/app/inteligencia/` (motor puro, sem banco nem internet) e `backend/app/servicos/inteligencia.py` (coleta os dados reais e grava o resultado).

## Princípio: nunca inventar dados

| Dado | De onde vem | Se não existir |
|---|---|---|
| Preço atual | loja / cadastro | oferta bloqueada (inconsistente) |
| Preço anterior ("preço de") | loja (API) ou cadastro manual | o desconto só sai do histórico; sem histórico, a oferta é **bloqueada** |
| Histórico de preços | gravado pelo próprio sistema a cada mudança | desconto marcado como "não confirmado" e confiança menor |
| Comissão | só da API oficial da loja | quesito fica **fora da conta** e a confiança cai |
| Avaliação e vendas | só da API oficial da loja (nunca demo ou manual) | quesito fica **fora da conta** e a confiança cai |
| Tamanho da imagem | a imagem é baixada e medida de verdade | "tamanho não verificado" (nota parcial) |

**Quesito sem dados não vale zero, e também não vale nota inventada.** O peso dele é redistribuído entre os outros e a **confiança** da análise cai.

## Regras de bloqueio (oferta não recomendada)

| Regra | Padrão | Onde é garantida |
|---|---|---|
| Desconto real abaixo do mínimo | **20%** (`filtros.desconto_minimo`) | motor |
| Produto indisponível | — | motor + banco (monitor pausa) |
| Link de afiliado inválido (ou oferta sem link) | — | motor + banco (fila exige link validado) |
| Mesma oferta no mesmo canal em 24 h | **24 h** (`inteligencia.janela_repeticao_horas`) | serviço + **banco** (erro P0018) |
| Oferta duplicada (mesmo título em outro produto com oferta ativa) | — | motor |
| Sem preço de referência confiável | — | motor ("o sistema não inventa preço anterior") |
| Informações inconsistentes | preço ≤ 0; título vazio; preço da oferta diferente do preço do produto; avaliação fora de 0–5; vendas negativas; desconto acima de **90%**; comissão acima de **50%** | motor |
| Pontuação abaixo do mínimo | **50** (`inteligencia.nota_boa`) | motor |
| Sem imagem | desligada por padrão (`inteligencia.exigir_imagem`) | motor |

O banco recusa colocar na fila qualquer oferta sem classificação **excelente** ou **boa** (erro P0019). Se o preço, a disponibilidade ou o link mudarem, o banco **apaga a análise**, e a oferta volta para o motor antes de poder ser publicada.

## Desconto real

1. **"Preço de" da loja + histórico suficiente** (padrão: 3 registros em 90 dias):
   - Se o histórico já mostrou um preço perto do "preço de" (tolerância de 5%), o desconto é **confirmado** (confiança 100%).
   - Se o histórico cobre 30 dias ou mais e o "preço de" **nunca apareceu**, o "preço de" é considerado **inflado**. O desconto passa a ser calculado sobre o maior preço visto, e um alerta aparece.
   - Se o histórico é curto demais para contestar, vale o desconto da loja, marcado como "não confirmado" (confiança 70%).
2. **"Preço de" sem histórico:** vale o informado, marcado como "não confirmado" (confiança 70% da loja, 50% do cadastro manual).
3. **Sem "preço de", com histórico:** desconto sobre a mediana dos preços registrados.
4. **Nada disso:** a oferta é **bloqueada**.

Bônus de **+10%** na nota do desconto quando é o menor preço registrado no período. Desconto de **−20%** quando o preço atual está acima da média recente do próprio produto.

## Pontuação (0–100)

| Quesito | Peso padrão | Como pontua |
|---|---|---|
| Desconto real | 35 | desconto ÷ 60% (60% ou mais = nota máxima), com bônus ou desconto do histórico |
| Comissão | 20 | comissão ÷ 10% (10% ou mais = nota máxima); mostra a estimativa em R$ por venda |
| Popularidade | 15 | média de avaliação (3,5 = 0, 5,0 = máximo) e vendas (escala logarítmica, 1.000 = máximo) |
| Relevância comercial | 15 | categoria ativa (40%), preço na faixa de maior apelo de R$ 10 a R$ 1.500 (30%), título informativo (30%) |
| Qualidade | 15 | imagem (50%: ≥600 px = 1; ≥400 px = 0,7; menor = 0,3; não medida = 0,6; sem imagem = 0) e link (50%: validado = 1; pendente = 0,5) |

**Pontuação** = soma dos pontos ÷ soma dos pesos **dos quesitos com dados** × 100. Oferta bloqueada fica com 0 (a nota "por mérito" é guardada na análise).

**Confiança** = parte do peso total coberta por dados reais, ponderada pela confiabilidade de cada quesito.

## Classificação

| Classificação | Condição (padrão) |
|---|---|
| ⭐ **Excelente oportunidade** | sem bloqueios, pontuação ≥ **75** **e** confiança ≥ **60%** |
| 👍 **Boa oportunidade** | sem bloqueios e pontuação ≥ **50**; ou pontuação ≥ 75 com confiança abaixo de 60% |
| ⛔ **Oferta não recomendada** | qualquer bloqueio ou pontuação abaixo de 50 |

A **fila** prioriza por pontuação (maior primeiro) e só aceita excelente e boa. O **post do dia no feed** usa a oferta publicada de maior pontuação.

> Ofertas de demonstração e cadastros manuais sem comissão e sem avaliação tendem a ficar como "Boa": a nota pode ser alta, mas a confiança dos dados é baixa. Isso é intencional.

## Configuração

Todas as regras ficam no painel, em **Ajustes → Motor de inteligência** (tabela `configuracoes`, grupo `inteligencia.*`, e `filtros.desconto_minimo`): pesos, metas, limites de plausibilidade, notas de corte, confiança mínima, histórico, faixa de preço, janela de repetição, exigir imagem e verificar imagem. Valores fora dos limites são recusados pelo painel e pelo banco.

## Testes

| Onde | Quantidade | O que cobre |
|---|---|---|
| `backend/tests/test_inteligencia.py` | 43 | cada regra do motor: pontuação, classificações, desconto real, histórico, preço inflado, comissão, popularidade, imagem, relevância, duplicidade, inconsistências, confiança, explicação, prioridade |
| `backend/tests/test_inteligencia_servico.py` | 13 | coleta de dados reais, medição de imagem, gravação, nova análise após mudança de preço, fila, prioridade, janela de 24 h, rodada completa |
| `supabase/tests/08_inteligencia.sql` | 9 | travas do banco: classificação na fila (P0019), repetição em 24 h (P0018), janela configurável, análise apagada após mudança, painel não altera a nota (P0023) |
| `frontend/testes/testar_painel.py` | 6 novas verificações | selo de pontuação, explicação de aprovação e rejeição, ordenação e filtro |
