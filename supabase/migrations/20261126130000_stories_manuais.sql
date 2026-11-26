-- 024 · Stories passam a ser postados à mão pelo painel (botão "📲 Story"), com o adesivo de link do Instagram,
-- que a API não permite. O bot para de publicar stories sozinho; Telegram e feed seguem como estão.
-- Para voltar ao automático: update public.canais set ativo = true where codigo = 'instagram_story';
update public.canais set ativo = false where codigo = 'instagram_story';
