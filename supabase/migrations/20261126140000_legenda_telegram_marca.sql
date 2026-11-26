-- 025 · Chamadas (headlines) da legenda do Telegram no formato Sonho das Promos.
-- Editável no painel (Ajustes), separadas por vírgula. Vazia = usa a lista padrão do bot.
insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo)
values ('textos.headlines_telegram',
        '["O SITE TÁ FICANDO LOUCO COM ESSE PRECINHO 😵💖", "CORRE QUE ESSE PREÇO NÃO DURA 🏃‍♀️💨", "ACHADINHO DO DIA PRA VOCÊ 💖", "PREÇO DE BANANA DETECTADO 🍌", "OLHA ESSE DESCONTO 😱", "PROMOÇÃO QUE DÁ GOSTO DE VER 😍"]',
        'lista', 'Chamadas do topo das mensagens do Telegram (separe por vírgula)', null, null)
on conflict (chave) do nothing;
