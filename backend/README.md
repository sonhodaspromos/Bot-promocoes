# Backend: Bot de Ofertas v2

Worker em Python que roda de 10 em 10 minutos no GitHub Actions. Ele lê as configurações do banco (Supabase), busca e valida ofertas, publica nos canais e acompanha preço e estoque.

## Estrutura

```
backend/
├── app/
│   ├── main.py              linha de comando
│   ├── orquestrador.py      uma rodada completa (etapas isoladas)
│   ├── config.py            variáveis de ambiente, .env e Vault; modo REAL/DEMO por integração
│   ├── logs.py              log estruturado, com máscara de segredos
│   ├── erros.py             exceções próprias e códigos de regra do banco (P00xx)
│   ├── cliente_http.py      timeout, retentativas com espera crescente
│   ├── modelos.py           estruturas de dados
│   ├── repositorio.py       interface + Supabase (PostgREST com a chave secreta)
│   ├── repositorio_memoria.py  versão em memória (testes e simulação)
│   ├── servicos/            validação, busca, publicação, monitor, comissões, arte, textos
│   └── integracoes/
│       ├── base.py          interfaces FonteOfertas, CanalPublicacao, HospedagemImagem
│       ├── registro.py      escolhe REAL ou DEMO para cada integração
│       ├── fontes/          shopee.py · manual.py (Mercado Livre) · demo.py
│       ├── canais/          telegram.py · instagram.py · demo.py
│       └── imagens/         imgbb.py
└── tests/                   80 testes, sem internet
```

## Comandos

```bash
cd backend
pip install -r requirements-dev.txt

python -m pytest -q                       # 80 testes
python -m app.main simular                # rodada completa em memória, modo DEMO (sem banco e sem internet)
python -m app.main arte-exemplo           # artes de exemplo em backend/saida/
python -m app.main testar-integracoes     # (precisa do .env) mostra o modo de cada integração e testa as reais
python -m app.main rodar                  # rodada de verdade (usada pelo GitHub Actions)
python -m app.main comissoes --dias 30    # importa comissões agora
```

## O fluxo de cada rodada

1. **Configuração**: lê os ajustes do painel (tabela `configuracoes`).
2. **Modos**: grava no banco se cada canal e marketplace está em modo real ou demo.
3. **Monitor**:
   - Shopee: consulta preço e estoque. O banco pausa a oferta se o preço subir além da tolerância ou o produto esgotar.
   - Mercado Livre: confere se o link ainda abre.
4. **Encerrar pausadas**: a mensagem do Telegram vira "❌ OFERTA ENCERRADA".
5. **Comissões**: às 6h (horário local), importa da Shopee.
6. **Busca**:
   - procura nas categorias ativas, com desconto mínimo, nota mínima e sem duplicidade;
   - aprovação automática ou manual;
   - o mesmo produto só volta depois de N dias.
7. **Validação de links**: HTTPS, domínio de afiliado da loja e link que abre. Sem isso, nada é publicado.
8. **Fila**:
   - Ofertas manuais entram a qualquer hora.
   - Automáticas respeitam horário, intervalo, quantidade por rodada e limite por dia.
   - O feed recebe 1 oferta imperdível por dia.
9. **Publicação**: confere o link de novo, gera a arte e a legenda e publica. Tudo fica registrado no histórico.

Cada etapa é isolada: se uma falha, as outras continuam e a rodada termina como "parcial". Os logs (com segredos mascarados) e o resumo vão para as tabelas `logs` e `execucoes`.

## Modo demonstração

Sem credenciais, a integração entra em DEMO:

- **Shopee sem credenciais**: a busca usa a **Loja de Demonstração**. Os produtos têm o título começando com `[DEMO]` e links em `https://demo.invalid/`.
- **Canais sem credenciais**: não publicam nada, só registram o que seria publicado.
- **Identificação**: artes e legendas de oferta demo têm a faixa **DEMONSTRAÇÃO**.
- **Trava no banco**: oferta demo **nunca** vai para canal real.
- **Limites**: horário, intervalo e limite diário só valem para publicações reais.

## Segurança das credenciais

- **Onde ficam:** em variáveis de ambiente (GitHub Secrets) ou no Supabase Vault, cadastradas pelo painel. O `.env` serve só para uso local e está no `.gitignore`.
- **Configuração:** o backend recusa a chave pública do Supabase no lugar da secreta, e o resumo de configuração no log nunca contém valores.
- **Logs:** todo log passa por uma máscara antes de sair (token do Telegram, `sb_secret_`, JWT, `access_token=`, Bearer, assinatura da Shopee), e o banco aplica uma segunda máscara.

## O que é real e o que precisa ser confirmado

| Integração | Situação |
|---|---|
| Telegram | Bot API oficial: `sendPhoto`, `editMessageCaption`, `getMe` |
| Instagram | Graph API oficial: container, verificação de status, `media_publish` (feed e stories) |
| ImgBB | API oficial de upload |
| Shopee | API oficial (GraphQL assinado). ⚠ Os nomes dos campos seguem referências públicas, porque a documentação só abre com login de afiliado. Confirme rodando `testar-integracoes`: se algum campo tiver outro nome, a resposta mostra qual. |
| Mercado Livre | Sem API pública de afiliados: cadastro manual pelo painel, **sem scraping** |

> Os testes usam dublês de HTTP, ou seja, nenhuma chamada real. A primeira execução com credenciais deve ser o `testar-integracoes`.

## Como adicionar uma loja ou canal

1. Crie `app/integracoes/fontes/<loja>.py` herdando de `FonteOfertas`, ou `app/integracoes/canais/<canal>.py` herdando de `CanalPublicacao`.
2. Registre a classe em `app/integracoes/registro.py`, com as credenciais em `config.CREDENCIAIS`.
3. Cadastre o marketplace (com os domínios de afiliado) ou o canal no banco.
4. Escreva os testes com `tests/dubles.py`.
