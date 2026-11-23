-- =====================================================================
-- 000 · Remove a estrutura da versão 1 (as tabelas estavam vazias)
-- =====================================================================
drop trigger if exists ao_criar_usuario on auth.users;
drop view if exists public.v_ofertas;
drop table if exists public.cliques, public.publicacoes, public.comissoes,
                     public.ofertas, public.config, public.estado, public.admins cascade;
drop function if exists public.primeiro_admin();
drop function if exists public.eh_admin();
