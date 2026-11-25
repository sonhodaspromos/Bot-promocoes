# Auditoria corretiva P0: Bot de Promoções V2

**Data:** 25/11/2026
**Branch:** `fix/auditoria-p0-v2`, criada a partir de `auditoria-complementar`.
**Restrições respeitadas:** sem merge na main, sem push, sem deploy. O banco de produção não foi tocado: a migration 018 está **apenas** no repositório e no banco local de homologação.

## Resultado dos testes

| Bateria | Antes desta branch | Agora |
|---|---|---|
| Backend (pytest) | 263 | **318 aprovados, 0 falhas** (55 novos) |
| Banco (RLS, RPCs, regras) | 132 | **141 / 141** (9 novos em `12_publicacao_segura.sql`) |
| Concorrência, interrupção, emergência, backup e restauração | 10 | **11 / 11** (nova: reserva disputada por 2 processos) |
| Painel (Playwright) | 60 | **60 / 60** |
| Regras das Edge Functions | 5 | **5 / 5** |
| Simulação completa (`app.main simular`) | ok | ok |

**Evidência de que os testes novos pegam os defeitos:**
- Contra o código anterior, `test_auditoria_p0_v2.py` nem chega a carregar, porque os mecanismos de proteção não existiam.
- Reprodução direta no código anterior: um HTTP 502 durante o envio voltava com `incerto = False`. Ou seja, o item seria reenviado e o post sairia duplicado.

**Testes existentes alterados (nenhum foi removido ou afrouxado):**

| Teste | Motivo |
|---|---|
| `test_auditoria.py::test_backend_funciona_antes_da_migration_015` | Exigia o comportamento permissivo: sem a RPC de proteção, o bot só **avisava** e seguia. Agora o teste exige o contrário: sem reserva gravada, nada é enviado e um alerta CRÍTICO é registrado. A parte de leitura com banco antigo continua testada. |
| `test_auditoria2_operacao.py` (2 testes) | Portados para a nova interface de `validar()`. A intenção foi mantida: integração simulada não aprova e a evidência não contém segredos. |

## Problemas, causas e correções

### P1. `validar-integracoes` era incompatível com homologação

- **Causa:** o comando usava `registro.montar()`. Em homologação, essa função troca **todos os canais por simulados** (correto para a rodada, que nunca deve publicar). A validação via `modo != real` e reprovava tudo, então não havia como validar Telegram ou Instagram fora da produção.
- **Correção:**
  - Nova função `registro.montar_validacao()`, separada da montagem da rodada: cria clientes **reais** com HTTP somente leitura.
  - `cmd_validar` (em `main.py`) passou a usá-la. Não depende mais de `registro.montar`.
  - Demonstração é recusada com mensagem clara.
- **Testes:**
  - `test_p1_validacao_em_homologacao_monta_cliente_real_e_execucao_continua_simulada`
  - `test_p1_validacao_recusa_ambiente_de_demonstracao`
  - `test_p1_homologacao_valida_telegram_real_e_aprova_com_evidencia`

### P2. Não havia validação real somente leitura

- **Causa:** a validação usava o mesmo cliente HTTP da publicação. Nada impedia uma chamada de escrita, por exemplo um `testar()` mal escrito ou um link gerado por `mutation`.
- **Correção:** `ClienteHTTPSomenteLeitura` e `chamada_de_leitura()` em `cliente_http.py`.
  - Permitido: GET/HEAD; no Telegram, só `getMe`, `getChat`, `getChatMember` e `getChatAdministrators`; na Shopee, GraphQL **sem** `mutation`.
  - O ImgBB não tem consulta de leitura. Só aceita o envio de teste com a flag `--imgbb` explícita.
  - Qualquer outra chamada gera `EscritaBloqueada` **antes** de sair do bot.
- **O que é verificado:**
  - credenciais e permissões (Telegram exige administrador que publica e edita; Instagram exige conta profissional);
  - cota (Instagram, `content_publishing_limit`);
  - respostas das APIs (busca e relatório de conversões da Shopee);
  - atribuição do link de amostra;
  - esquema do banco.
- **Testes:** `test_p2_*` (14 casos), incluindo "canal de validação não consegue publicar e nenhuma requisição sai".

### P3. Integrações reais, manuais, simuladas e desativadas misturadas

- **Causa:** a fonte manual do Mercado Livre (`modo manual`) era tratada como integração real não aprovada e reprovava a validação.
- **Correção:** cada item tem uma **categoria**.
  - `real`: validada de verdade.
  - `manual`: aparece no relatório, nunca aprova nem reprova.
  - `simulada`: não conta.
  - `desativada`: sem credencial e fora de `INTEGRACOES_ATIVAS`; não reprova.
- **Testes:** `test_p3_categorias_real_manual_desativada`, `test_p3_fonte_manual_nao_reprova_integracao_automatica`.

### P4. Aprovação sem nenhuma integração obrigatória validada

- **Causa:** não havia o conceito de "obrigatória". Não verificado era tratado como neutro.
- **Correção:** obrigatórias são as de `INTEGRACOES_ATIVAS`, mais a atribuição (se a Shopee for obrigatória) e o esquema do banco.
  - **APROVADO** só se houver pelo menos 1 integração obrigatória **e** todas as obrigatórias estiverem em `aprovado`.
  - `nao_verificado` reprova (ex.: ImgBB sem `--imgbb`).
  - Obrigatória sem credencial reprova.
  - A evidência JSON lista `validadas_de_verdade` e `motivos_reprovacao`.
- **Testes:** `test_p4_*` (4).

### P5. `_marcar_publicando()` permissivo

- **Causa:** em `publicacao.py`:
  - erros 404/PGRST na marcação viravam só um aviso, e **o post era enviado**;
  - a RPC antiga `fila_marcar_publicando` devolvia `void` e atualizava **0 linhas em silêncio** quando a trava do worker já tinha vencido (outro processo podia ter pego o item).
- **Correção:**
  - Migration 018: `fila_reservar_envio(p_id) → boolean`. Só reserva se o item estiver `processando`, com trava vigente e sem reserva anterior.
  - `_marcar_publicando` foi **removido**. `_reservar_envio()`:
    - erro do banco: alerta CRÍTICO e interrupção, nada enviado;
    - `false` ou ausência de `true`: nada enviado.
  - Sem fallback.
- **Testes:**
  - `test_p5_*` (4);
  - banco: "reserva do envio" e "reserva recusada quando a trava venceu";
  - script de concorrência: "só um processo reserva o mesmo envio".

### P6. Comportamentos semelhantes nos cupons e campanhas

- **Causa:** `cupons.py` não gravava nenhum estado antes de enviar.
  - Se o job morresse depois do envio, o cupom era reenviado.
  - Se gravar o sucesso falhasse, o erro era só registrado no log, e o cupom saía de novo na rodada seguinte.
  - Resultado incerto era gravado como "erro", e a regra ainda dependia de um prefixo de texto.
- **Correção:**
  - Migration 018: resultado `enviando` e RPC `cupom_reservar_envio()`, com trava (advisory lock) para rodadas simultâneas e as mesmas regras de validade, canal e demonstração.
  - Sem reserva, sem envio.
  - Reserva sem resultado é tratada como incerta e **nunca** é reenviada.
  - Incerto não grava "erro" (que liberaria nova tentativa).
  - Banco indisponível interrompe os cupons.
  - Canais reais também respeitam a verificação de requisitos (P8).
- **Testes:**
  - `test_p6_*` (4): sem reserva; incerto; sucesso não gravado; erro definitivo libera uma nova tentativa;
  - banco: 3 testes de reserva de cupom.

### P7. Falha de comunicação após o envio

- **Causa:**
  - HTTP 5xx em um envio sem repetição era marcado `temporario` mas **não** `incerto`, então virava falha comum e o item voltava para a fila (post duplicado);
  - exceção inesperada do canal depois do envio subia sem tratamento;
  - banco caindo ao gravar o sucesso não tinha tratamento explícito.
- **Correção:**
  - `cliente_http.py`: 5xx ou 408 em envio sem repetição passa a ser `incerto=True`.
  - `publicacao.py`: qualquer exceção do canal vira incerto (`marcar_incerta`).
  - `ErroBanco` ao gravar o sucesso gera alerta CRÍTICO com o ID da mensagem e interrompe. A reserva continua, e o banco encerra o item como "resultado incerto".
- **Testes:** `test_p7_*` (4), incluindo "post enviado e banco cai ao gravar não republica".

### P8. Verificação de migrations e RPCs antes de publicar de verdade

- **Causa:** o backend tinha fallbacks para banco antigo. Em produção, um banco sem as migrations publicava sem as proteções.
- **Correção:**
  - Migration 018: `esquema_publicacao()` informa versão, RPCs, configurações e colunas.
  - `app/requisitos.py` (`faltando_para_publicar`).
  - Antes do primeiro envio real da rodada, falta qualquer item:
    - alerta CRÍTICO;
    - itens reais devolvidos à fila;
    - cupons reais bloqueados;
    - canais simulados seguem normalmente.
  - O mesmo teste reprova a `validar-integracoes` (item `esquema_banco`).
- **Testes:** `test_p8_*` (4); banco: "esquema_publicacao informa versão 18…".

### P9. Atribuição dos links de afiliado

**Causas:**
1. O ID de afiliado era comparado por **substring**: `12345` "casava" com `an_3123456`, que é outra conta.
2. Página inicial com parâmetro na URL era aceita como comprovação.
3. A loja bloquear o robô ainda no **encurtador** (403) era aceito.
4. Sem domínio de destino ou sem ID configurado, a atribuição era dada como comprovada.
5. O clique redireciona para `url_curta`, mas o bot validava `url`.
6. Link validado uma vez valia para sempre, inclusive links validados em homologação sem exigência.

**Correção:**
- `contem_id()` exige o ID como token inteiro.
- Com `exigir_atribuicao` (sempre ligado em produção), só passa link que **chegou ao site de destino da loja**, fora da página inicial, com o parâmetro e o seu ID. Domínios de destino e ID são obrigatórios.
- `ResultadoValidacao.atribuicao` registra a comprovação.
- Migration 018:
  - `links_afiliados.atribuicao_verificada_em`, apagada quando a URL muda;
  - o painel não pode marcá-la (P0016);
  - configuração `links.validade_atribuicao_horas` (72).
- Antes do envio real em produção, a comprovação precisa ter menos de 72 h. Fora disso, o item volta à fila e o link entra em nova conferência (`links_pendentes(revalidar_atribuicao_horas=…)`).
- O link validado agora é o **efetivo** (`url_curta`, quando existir).

**Testes:**
- `test_p9_*` (10);
- contratos Supabase (5);
- banco: "URL alterada apaga a comprovação; painel não marca atribuição".

## Arquivos alterados

| Arquivo | Mudança |
|---|---|
| `backend/app/cliente_http.py` | 5xx em envio vira incerto; `ClienteHTTPSomenteLeitura` e `chamada_de_leitura` |
| `backend/app/erros.py` | `EscritaBloqueada` |
| `backend/app/integracoes/registro.py` | `AlvoValidacao` e `montar_validacao()` |
| `backend/app/validacao_integracoes.py` | Reescrito: categorias, obrigatórias, regra de aprovação, evidência |
| `backend/app/main.py` | `cmd_validar` sem `registro.montar` |
| `backend/app/requisitos.py` (novo) | Requisitos do banco para publicar de verdade |
| `backend/app/servicos/publicacao.py` | Reserva obrigatória, requisitos, atribuição, falhas pós-envio |
| `backend/app/servicos/cupons.py` | Reserva antes do envio, incerto, requisitos |
| `backend/app/servicos/validacao.py` | Comprovação de atribuição e `contem_id` |
| `backend/app/modelos.py` | `ResultadoValidacao.atribuicao` |
| `backend/app/repositorio.py` / `repositorio_memoria.py` | `reservar_envio`, `reservar_envio_cupom`, `esquema_publicacao`, `link_atribuicao`; validação de `url_curta`; gravação da comprovação |
| `supabase/migrations/20261125130000_publicacao_segura.sql` (novo, **não aplicado em produção**) | RPCs de reserva, `enviando` nos cupons, coluna de atribuição, `esquema_publicacao` |
| `supabase/tests/12_publicacao_segura.sql` (novo) | 9 testes de banco |
| `supabase/homologacao/concorrencia_e_backup.sh` | Reserva disputada por 2 processos |
| `backend/tests/test_auditoria_p0_v2.py` (novo) | 55 testes de regressão |
| `backend/tests/test_auditoria.py`, `test_auditoria2_operacao.py` | Ver tabela acima |

## Pendências reais

1. **Integrações reais: NÃO APROVADAS.** Nenhuma validação real foi executada nesta branch, porque não há credenciais e o projeto de produção está pausado. Toda a evidência é de teste simulado.
   - Para aprovar: com sua autorização, rodar `AMBIENTE=homologacao INTEGRACOES_ATIVAS=shopee,telegram python -m app.main validar-integracoes` contra um banco de homologação com as migrations 017 e 018 (acrescente `--imgbb` se usar Instagram).
2. **Migrations 017 e 018 não aplicadas em produção.** Depois de aplicadas, o bot **bloqueia publicações reais** até ambas existirem. Isso é intencional.
3. **Configurar a atribuição:** `marketplaces.dominios_destino`, `parametros_afiliado` e `id_afiliado` da Shopee, conferidos com um link real. Sem isso, em produção **nenhum** link passa (bloqueio intencional).
4. **ImgBB** só pode ser verificado com um envio de teste (1 pixel, expira em 60 s): exige autorização explícita (`--imgbb`).
5. **Links já validados antes da migration 018** não têm comprovação de atribuição. Em produção eles voltam automaticamente para conferência antes de publicar.
6. **Telegram:** os nomes dos campos de permissão (`can_post_messages` e `can_edit_messages`) valem para canais. Em grupos, a API responde de outro jeito; confirmar na validação real.
7. As pendências da auditoria complementar (`PENDENCIAS.md`) continuam válidas: reativar o Supabase, MFA, `CLIQUE_SAL`, monitor externo, proteção da main, LGPD e termos da Shopee.
