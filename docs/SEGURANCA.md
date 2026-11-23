# Segurança

## Onde fica cada segredo

| Segredo | Onde fica | Quem acessa |
|---|---|---|
| Chave secreta do Supabase | GitHub Secrets | só o backend |
| Tokens das integrações | GitHub Secrets **ou** Supabase Vault (cifrado) | só o backend (`obter_credencial` exige a chave secreta) |
| Chave do GitHub do "Publicar agora" | Segredo da Edge Function `disparar` | só a função, no servidor |
| Chave **publicável** do Supabase | `frontend/painel/js/config.js` | pública por natureza; o login e o RLS protegem os dados |
| `.env` local | só no seu computador | está no `.gitignore`. Use `.env.example` como modelo |

## Camadas de proteção

1. **Login:** o primeiro cadastro vira o dono. Os outros só acessam se o dono der um papel (dono, editor ou leitor).
2. **RLS:**
   - visitante e usuário sem papel não leem nada;
   - o leitor só lê;
   - o editor opera;
   - só o dono vê auditoria e credenciais.
3. **Regras no próprio banco:**
   - Sem link de afiliado validado não há publicação.
   - Validar link e marcar como publicada são tarefas exclusivas do sistema.
   - Oferta demo nunca vai para canal real.
   - Os históricos são imutáveis.
4. **Logs mascarados duas vezes** (backend e banco): token do Telegram, `sb_secret_`, JWT, `github_pat_`, `access_token=`, Bearer e a assinatura da Shopee.
5. **Painel:**
   - todo texto vindo do banco passa por escape de HTML;
   - links e imagens só são aceitos com `https://`;
   - o painel não usa `eval` nem HTML vindo de fora.
6. **Cliques:** o IP é guardado só como hash SHA-256 com sal, e robôs de pré-visualização não contam.
7. **Auditoria:** toda alteração feita por pessoas no painel fica registrada, e só o dono vê.

## Boas práticas

- Use tokens do GitHub **fine-grained**, só para este repositório e com a permissão mínima necessária.
- Desligue novos cadastros no Supabase depois de criar o seu acesso.
- Se um token vazar: gere outro no serviço (BotFather, Meta, Shopee), atualize o segredo e rode o bot de novo.
- Nunca cole a chave secreta (`sb_secret_…`) no painel nem em mensagens.


## Auditoria final (22/11/2026)

- **Logs públicos:** em repositório público, qualquer pessoa vê os logs do GitHub Actions. Por isso, toda saída do bot passa pela máscara de segredos, erros de rede nunca levam a URL (a do Telegram contém o token) e exceções inesperadas não geram traceback cru.
- **Backup criptografado:** o `backup.yml` gera `pg_dump` diário cifrado com AES-256 (senha `BACKUP_SENHA`). Os artefatos de repositório público podem ser baixados por qualquer pessoa logada no GitHub, mas não abrem sem a senha. O Vault (chaves de API) **não** entra no backup.
- **Credencial recusada:** o canal é pausado na rodada, nada é perdido e chega um alerta (e-mail do GitHub e painel).
- **Link de afiliado:** além do domínio, o formato de afiliado da loja é obrigatório no backend e no banco (P0024).
- Relatório completo em `docs/AUDITORIA.md`.
