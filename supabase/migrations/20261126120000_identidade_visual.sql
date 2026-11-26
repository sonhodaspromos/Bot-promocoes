-- 023 · Identidade visual Sonho das Promos nas artes (rosa #DC1B63 + amarelo #FDC302).
-- Só troca se ainda estiver na cor padrão antiga: não sobrescreve uma cor escolhida no painel.
update public.configuracoes set valor = '"#DC1B63"' where chave = 'visual.cor_principal' and valor = '"#EE4D2D"';
update public.configuracoes set valor = '"#FDC302"' where chave = 'visual.cor_destaque' and valor = '"#FFD400"';
insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo)
values ('visual.usar_fundo_story', 'true', 'booleano', 'Usar o fundo da marca nos stories', null, null)
on conflict (chave) do nothing;
