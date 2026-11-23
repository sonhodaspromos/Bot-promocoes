# Testes de integridade do banco

Os arquivos desta pasta criam o schema `testes` (não exposto pela API) com 84 testes
distribuídos em 6 grupos. Todos rodam em subtransações desfeitas no final:
nenhum dado de teste fica gravado.

Ordem para instalar: `00_utilitarios.sql`, `01_...` até `06_carga.sql`
(o `00` deve ser rodado de novo por último para fixar o search_path).

```sql
select * from testes.resumo();                 -- total / aprovados / reprovados
select * from testes.rodar();                  -- todos, com detalhe
select * from testes.rodar('seguranca');       -- um grupo
select * from testes.carga(10000);             -- teste de volume (10 mil produtos)
```
