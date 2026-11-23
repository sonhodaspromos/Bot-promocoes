-- =====================================================================
-- 014 · Ajuste encontrado pelos testes: promoções PAUSADAS também perdem a análise
-- quando o produto ou o link muda. Assim, se forem reativadas, passam pelo motor de novo.
-- =====================================================================
create or replace function privado.invalidar_analise()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update public.promocoes
     set pontuacao = null, classificacao = null, confianca = null, analise = null, analisada_em = null
   where produto_id = new.id and status in ('encontrada', 'aprovada', 'pausada') and analisada_em is not null;
  return new;
end $$;

create or replace function privado.invalidar_analise_link()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update public.promocoes
     set pontuacao = null, classificacao = null, confianca = null, analise = null, analisada_em = null
   where produto_id = new.produto_id and status in ('encontrada', 'aprovada', 'pausada') and analisada_em is not null;
  return new;
end $$;
