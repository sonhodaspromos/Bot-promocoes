# Arquitetura: Bot de Promoções v2

## 1. Visão geral

```
                 ┌──────────────────────────── GitHub Actions (grátis) ───────────────────────────┐
                 │  a cada 10 min                                                                  │
                 │  ┌──────────────────────── BACKEND (Python) ──────────────────────────┐        │
                 │  │  main.py ─► Serviços ─────────────► Integrações (interfaces)       │        │
                 │  │            busca · validação ·      Fontes:  Shopee | Manual | Demo │        │
                 │  │            publicação · monitor ·   Canais:  Telegram | Instagram | Demo     │
                 │  │            comissões · arte         Imagens: ImgBB                  │        │
                 │  │              │  logs · erros · config (.env / Secrets)              │        │
                 │  └──────────────┼───────────────────────────────────────────────────────┘        │
                 └─────────────────┼──────────────────────────────────────────────────────────────┘
                                   │ chave secreta (só no backend)
                                   ▼
        ┌────────────────────────── SUPABASE ─────────────────────────────┐
        │ Postgres + RLS: ofertas · publicacoes · cliques · comissoes ·   │
        │                 config · logs · execucoes · auditoria · admins  │
        │ Auth (login da administradora)                                  │
        │ Edge Functions: r (clique → link afiliado) · disparar (rodar já)│
        └───────────────▲─────────────────────────────▲───────────────────┘
                        │ chave pública + login        │ clique
        ┌───────────────┴──────────┐          ┌────────┴─────────┐
        │ FRONTEND (painel)        │          │ Seguidores do    │
        │ GitHub Pages, HTML/JS    │          │ Telegram         │
        └──────────────────────────┘          └──────────────────┘
```

**Princípios**
- **Camadas separadas:** frontend (painel) ⇄ banco/API (Supabase) ⇄ backend (worker Python) ⇄ integrações externas.
- **Integrações independentes:** cada loja ou canal é um módulo que implementa uma interface. Adicionar Amazon, WhatsApp etc. significa criar um arquivo novo e registrá-lo, sem mexer no núcleo.
- **Modo demonstração explícito:** sem credencial, a integração vira `DEMO`. Dados demo são marcados no banco, aparecem com faixa "MODO DEMONSTRAÇÃO" no painel e **nunca** são publicados em canais reais.
- **Link afiliado validado é obrigatório:** o banco **recusa** marcar uma oferta como publicada sem `link_validado = true`. É uma trava no próprio banco, não só no código.

## 2. Estrutura de pastas

```
bot-promocoes/
├── backend/
│   ├── app/
│   │   ├── main.py                 # entrada: rodar | testar-integracoes | demo | arte-exemplo
│   │   ├── config.py               # lê .env/Secrets, valida, decide modo REAL/DEMO por integração
│   │   ├── logs.py                 # log estruturado (console + tabela logs), mascara segredos
│   │   ├── erros.py                # hierarquia de exceções
│   │   ├── modelos.py              # Oferta, Publicacao, Comissao, ResultadoValidacao
│   │   ├── repositorio.py          # interface Repositorio + SupabaseRepositorio + MemoriaRepositorio (testes)
│   │   ├── http.py                 # cliente HTTP com timeout, retentativas e backoff
│   │   ├── servicos/
│   │   │   ├── busca.py            # coleta nas fontes ativas, filtros (desconto, nota, categoria), sem repetidos
│   │   │   ├── validacao.py        # link afiliado: domínio permitido + padrão + resposta HTTP; preço
│   │   │   ├── publicacao.py       # agenda, limites diários, fila, distribuição para os canais
│   │   │   ├── monitor.py          # preço subiu / indisponível / link quebrado → pausa
│   │   │   ├── comissoes.py        # importa das fontes que oferecem relatório
│   │   │   └── arte.py             # artes de feed, story e Telegram
│   │   └── integracoes/
│   │       ├── base.py             # interfaces FonteOfertas, CanalPublicacao, HospedagemImagem
│   │       ├── registro.py         # registro de integrações (plugins)
│   │       ├── fontes/   shopee.py · manual.py · demo.py
│   │       ├── canais/   telegram.py · instagram.py · demo.py
│   │       └── imagens/  imgbb.py
│   ├── tests/                      # pytest, sem rede (dublês de HTTP e banco em memória)
│   ├── requirements.txt            # requests, Pillow, python-dotenv
│   └── requirements-dev.txt        # pytest
├── frontend/painel/
│   ├── index.html
│   ├── css/painel.css
│   └── js/  config.js · api.js · auth.js · app.js · telas/{resumo,ofertas,nova,comissoes,logs,ajustes}.js
├── supabase/
│   ├── migrations/                 # SQL versionado (001_estrutura … 00N)
│   └── functions/  r/index.ts · disparar/index.ts
├── .github/workflows/  bot.yml · testes.yml · painel.yml · renovar_token.yml
├── docs/  ARQUITETURA.md · INSTALACAO.md · SEGURANCA.md · INTEGRACOES.md
├── .env.example
├── .gitignore
└── README.md
```

## 3. Banco de dados

| Tabela | Para quê | Novidade na v2 |
|---|---|---|
| `admins` | quem acessa o painel (o 1º cadastro) | — |
| `config` | ajustes do painel (JSON validado) | integrações liga/desliga, domínios de afiliado por loja |
| `ofertas` | todas as ofertas e seus status | `link_validado`, `link_validado_em`, `demo`; **trava**: não fica "publicada" sem link validado |
| `publicacoes` | cada envio por canal (ok/erro) | `demo` |
| `cliques` | cliques via link rastreado | — |
| `comissoes` | comissões (API ou manuais) | — |
| `execucoes` | cada rodada do bot: início, fim, status, resumo | **nova** (substitui `estado`) |
| `logs` | logs estruturados: nível, módulo, mensagem, contexto | **nova**, limpeza automática após 30 dias |
| `auditoria` | quem mudou o quê no painel (aprovar, pausar, ajustes) | **nova**, preenchida por gatilho |

Tudo com **RLS**: só a administradora logada lê e escreve. O backend usa a chave secreta.

## 4. Integrações: o que é real e o que não é

| Integração | Situação | Como será feita |
|---|---|---|
| **Shopee Afiliados** | API oficial existe (GraphQL, `open-api.affiliate.shopee.com.br`) | Implementada conforme referências públicas da API. ⚠ A documentação oficial só abre com login de afiliado, então **os nomes dos campos precisam ser confirmados com suas credenciais**. Há o comando `testar-integracoes` para isso. Sem credenciais: **DEMO**. |
| **Mercado Livre** | Não encontrei API pública para afiliados | **Sem scraping.** Fonte "Manual": você cadastra no painel. O link só é aceito se for do domínio de afiliado configurado e responder. |
| **Telegram** | Bot API oficial | Real. Sem token: canal DEMO (só registra no log). |
| **Instagram** | Graph API oficial (conta profissional) | Real: feed e stories. Sem token: DEMO. Sem sticker de link (a API não permite). |
| **ImgBB** | API oficial | Real. Necessária para o Instagram. |

## 5. Validação de link afiliado (regra obrigatória)

Uma oferta só é publicada se passar em **todas** estas etapas:
1. **Origem:** veio de uma fonte REAL (nunca DEMO).
2. **Domínio:** o link pertence à lista de domínios de afiliado da loja (ex.: Shopee `s.shopee.com.br`, `shope.ee`). A lista é editável em Ajustes. ⚠ Os domínios iniciais são sugestões e devem ser confirmados com os links que o seu painel de afiliada gera.
3. **Resposta:** o link abre (sem erro 404/410 nem falha de conexão) e redireciona para a loja esperada.
4. **Banco:** a gravação `status = publicada` exige `link_validado = true` (restrição no banco).

## 6. Logs e erros
- **Níveis:** DEBUG, INFO, AVISO, ERRO. Saem no console (relatório do GitHub) e vão para a tabela `logs`, ligados à `execucao`.
- **Segredos mascarados:** tokens e chaves são trocados por `***` antes de qualquer log (inclusive URLs do Telegram, que contêm o token).
- **Exceções próprias:** `ErroConfiguracao`, `IntegracaoIndisponivel`, `ErroIntegracao` (com `temporario` sim/não), `LinkInvalido`, `ErroBanco`.
- **Isolamento:** cada etapa e cada canal falha sozinho, sem derrubar o resto. Erros temporários têm retentativa com espera crescente.
- **Painel:** nova aba **Logs** com filtro por nível e por execução. O Resumo avisa se o bot parou ou teve erros.

## 7. Segurança das credenciais
- Segredos ficam **só** em GitHub Secrets (produção) ou `.env` local (que está no `.gitignore`). O `.env.example` não tem valores reais.
- O frontend só tem a URL e a **chave pública** do Supabase (feitas para ficar públicas, protegidas por RLS).
- O "Publicar agora" passa a usar a função `disparar` (exige login de admin). A chave do GitHub fica como segredo no Supabase, e não mais no navegador.
- O backend valida a configuração ao iniciar e nunca imprime segredos.
- Recomendações no `SEGURANCA.md`: desligar novos cadastros, renovar tokens, limitar permissões dos tokens.

## 8. Expansões futuras
- **Nova loja:** criar `integracoes/fontes/amazon.py` implementando `FonteOfertas` e registrar.
- **Novo canal (WhatsApp, X…):** criar `integracoes/canais/<nome>.py` implementando `CanalPublicacao`.
- Migrações SQL numeradas, configuração com valores padrão e versão, testes por módulo.

## 9. Dependências

| Camada | Dependência | Motivo |
|---|---|---|
| Backend | Python 3.12, `requests`, `Pillow`, `python-dotenv` | HTTP, artes, `.env` local |
| Testes | `pytest` | testes de cada módulo, sem rede |
| Frontend | `@supabase/supabase-js@2` (CDN, versão fixa) | login e acesso ao banco |
| Edge | Deno (runtime do Supabase) | funções `r` e `disparar` |
| Infra | GitHub Actions, GitHub Pages, Supabase (grátis) | execução, painel e banco |

## 10. Plano de execução (cada etapa com testes)

| # | Etapa | Testes |
|---|---|---|
| 1 | Núcleo: config + modo DEMO, logs com máscara, erros, modelos, HTTP com retentativa | unitários |
| 2 | Banco: migrações (logs, execuções, auditoria, trava de link validado) + repositório | SQL aplicado no seu Supabase + repositório em memória |
| 3 | Integrações: interfaces + Shopee, Manual, Demo, Telegram, Instagram, ImgBB | dublês de HTTP (formato das chamadas e tratamento de erro) |
| 4 | Serviços: validação de link, busca e filtros, publicação e agenda, monitor, comissões, arte | unitários + fluxo completo em memória |
| 5 | Edge Functions: `r` e `disparar` | chamada real no Supabase |
| 6 | Frontend modular + aba Logs + faixa de DEMO | sintaxe + renderização com dados de teste |
| 7 | CI/CD: bot, testes automáticos, publicação do painel, renovação de token | validação dos YAML |
| 8 | Documentação + `.env.example` + rodada completa em modo DEMO | ponta a ponta |

**O que não dá para testar daqui:** as chamadas reais à Shopee, ao Telegram e ao Instagram, porque dependem das suas credenciais. Para isso, o comando `python -m app.main testar-integracoes` mostra, para cada integração, se ela está REAL, em DEMO ou com erro.
