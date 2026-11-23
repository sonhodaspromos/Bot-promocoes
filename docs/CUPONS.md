# Cupons de desconto e campanhas

O bot divulga **cupons e campanhas** nos stories (e no Telegram) **uma vez por dia** enquanto estiverem válidos. Também mostra o código e o **preço com cupom** nos posts dos produtos em que o cupom vale.

## O que é automático e o que não é

| Item | Como entra | Por quê |
|---|---|---|
| **Campanhas da Shopee** (ex.: "Semana do Frete Grátis") | **Automático**, 1 vez por dia, pela API oficial (`shopeeOfferV2`) | A API de afiliados da Shopee informa nome, link de afiliado, imagem e período das campanhas |
| **Cupons com código** (Shopee, Mercado Livre e outras lojas) | **Você cadastra** no painel → aba **🎟 Cupons** | Nenhuma loja oferece cupons por API. Pegá-los de páginas da loja seria scraping, que o projeto não faz. |
| **Produtos em que o cupom vale** | Você adiciona, **ou aprova** as sugestões do bot | A loja não informa em quais produtos o cupom vale ("produtos selecionados"). O bot sugere, mas **só publica depois da sua aprovação**. |

## Como cadastrar um cupom (≈ 30 segundos)

Abra o painel → **🎟 Cupons** → **➕ Novo cupom** e preencha:

| Campo | Exemplo | Observação |
|---|---|---|
| Loja | Shopee | o cupom só vale para produtos dessa loja |
| Código | `ACHADO15` | vira maiúsculo automaticamente |
| Nome no post | Cupom de fim de semana | |
| Desconto | 15 % (ou R$ 20) | acima de 90% é recusado como erro de digitação |
| Compra mínima | R$ 79,00 | opcional; abaixo disso o bot **não** mostra preço com cupom |
| Desconto máximo | R$ 30,00 | opcional (teto do desconto em %) |
| Válido até | 30/11 23:59 | depois disso o cupom para sozinho |
| Seu link de afiliado da página do cupom | `https://s.shopee.com.br/…` | opcional; precisa ser link de afiliado válido (mesma regra dos produtos) |
| Palavras para sugerir produtos | fone, smartwatch | opcional; sem palavras nem categoria, o bot não sugere nada |
| Regras curtas | Válido em produtos selecionados | aparecem no post, sem cortes |

## O que é publicado

- **Story do cupom (1080×1920):** nome da loja, desconto grande, condições (teto e mínimo, **nunca cortadas**), o **código em destaque** ("use este código no pagamento"), validade, regras, selo **PUBLI** (e **DEMONSTRAÇÃO**, quando for o caso).
- **Telegram:** a mesma arte, com o código em formato "toque para copiar" e o botão **🎟 Pegar cupom** quando houver link de afiliado.
- **Posts dos produtos do cupom:** faixa "COM O CUPOM ACHADO15: R$ 84,92", calculada pelas regras do cupom (percentual, teto e mínimo).

> O link do story do Instagram não é clicável pela API (limitação da Meta). O story leva as pessoas ao canal do Telegram ("link na bio").

## Regras (garantidas pelo backend **e** pelo banco)

| Regra | Padrão | Ajuste |
|---|---|---|
| Cada cupom sai **1 vez por dia** em cada canal | — | trava no banco (índice único) |
| Horário a partir do qual os cupons do dia saem | 9h | `cupons.horario` |
| Máximo de stories de cupom por dia | 3 | `cupons.max_stories_dia` (máx. 4: com 45 stories de ofertas + 1 feed, o total fica dentro da cota de 50 publicações/dia do Instagram) |
| Publicar cupons também no Telegram | sim | `cupons.publicar_telegram` |
| Ordem | os **seus** cupons primeiro, depois as campanhas; entre eles, o que **vence antes** | — |
| Cupom vencido ou pausado | não publica (P0025) e vira "vencido" sozinho | — |
| Cupom de demonstração | nunca vai para canal real (P0015) | — |
| Produto de outra loja | recusado (P0026) | — |
| Link do cupom | domínio e formato de afiliado (P0011, P0024) | — |
| Timeout no envio | não repete no mesmo dia (o post pode ter saído) | — |
| Token vencido | canal pausado na rodada e alerta crítico | — |
| Desconto do cupom na análise do motor | o "desconto real" passa a considerar o preço **com** cupom aprovado, e a análise diz isso | — |

Todos os ajustes ficam em **Ajustes → Cupons e campanhas**.

## Testes

| Onde | Quantidade |
|---|---|
| `backend/tests/test_cupons.py` | 23 (regras do cupom, campanhas da API, sincronização diária, sugestões, publicação diária, limites, demo, token, timeout, arte, motor, rodada completa) |
| `supabase/tests/10_cupons.sql` | 11 (código obrigatório, link de afiliado, dados inconsistentes, 1 por dia, validade, demo, loja diferente, permissões do painel, visitante, imutabilidade, cota do Instagram) |
| `frontend/testes/testar_painel.py` | 9 verificações da aba Cupons |
