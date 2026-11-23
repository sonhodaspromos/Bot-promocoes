# Bot de Ofertas v2

Busca ofertas, valida o link de afiliado, publica no Telegram e no Instagram, acompanha preço e estoque e mostra tudo num painel com login.

```
.github/workflows/   bot (10 em 10 min), testes, painel (GitHub Pages), manutenção, token do Instagram
backend/             worker em Python: serviços, motor de inteligência e integrações independentes (164 testes)
frontend/painel/     painel em módulos JavaScript (login, resumo, ofertas, nova, comissões, logs, ajustes)
frontend/testes/     teste automatizado do painel com dados simulados (33 verificações)
supabase/            migrations SQL, testes de integridade (97 + 10 da auditoria), Edge Functions r e disparar
docs/                ARQUITETURA · BANCO_DE_DADOS · MOTOR_INTELIGENCIA · SEGURANCA · AUDITORIA · IMPLANTACAO · CHECKLIST_PRODUCAO
.env.example         modelo de variáveis (sem valores reais)
```

**Comece por [`docs/INSTALACAO.md`](docs/INSTALACAO.md).**

| Camada | Testes |
|---|---|
| Banco (PostgreSQL/Supabase) | `select * from testes.resumo();` → 107 de 107 (após a migration 015) |
| Backend (Python) | `cd backend && python -m pytest -q` → 164 de 164 |
| Painel | `python frontend/testes/testar_painel.py` → 33 de 33 |
| Simulação completa (sem banco e sem internet) | `cd backend && python -m app.main simular` |
