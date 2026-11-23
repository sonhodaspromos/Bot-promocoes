-- =====================================================================
-- 009 · Dados iniciais
-- Tudo começa em modo DEMONSTRAÇÃO. O backend muda para "real" só quando
-- as credenciais forem cadastradas e testadas.
-- ⚠ Confirme os domínios de afiliado com os links que o seu painel de afiliada gera.
-- =====================================================================

insert into public.marketplaces (codigo, nome, site_url, dominios_afiliado, possui_api, modo) values
  ('shopee',        'Shopee',        'https://shopee.com.br',         array['s.shopee.com.br', 'shope.ee'], true,  'demo'),
  ('mercado_livre', 'Mercado Livre', 'https://www.mercadolivre.com.br', array['mercadolivre.com'],          false, 'manual'),
  ('demo',          'Loja de Demonstração', null,                     array['demo.invalid'],                false, 'demo');

insert into public.categorias (nome, slug, palavras_chave, ordem) values
  ('Eletrônicos', 'eletronicos', array['fone bluetooth', 'smartwatch', 'carregador turbo', 'caixa de som bluetooth'], 1),
  ('Beleza',      'beleza',      array['maquiagem', 'skincare', 'perfume feminino', 'secador de cabelo'], 2),
  ('Casa',        'casa',        array['organizador', 'air fryer', 'luminária led', 'jogo de cama'], 3),
  ('Moda',        'moda',        array['tênis feminino', 'bolsa feminina', 'relógio masculino'], 4),
  ('Brinquedos',  'brinquedos',  array['brinquedo educativo', 'blocos de montar', 'boneca'], 5);

insert into public.canais (codigo, tipo, nome, modo, limite_diario, intervalo_minimo_min) values
  ('telegram',        'telegram',        'Canal do Telegram',      'demo', 200, 0),
  ('instagram_story', 'instagram_story', 'Stories do Instagram',   'demo', 60,  0),
  ('instagram_feed',  'instagram_feed',  'Feed do Instagram',      'demo', 1,   0);

insert into public.configuracoes (chave, valor, tipo, descricao, minimo, maximo) values
  ('publicacao.aprovacao_manual',   'false', 'booleano', 'Se ligado, nada é publicado sem aprovação no painel', null, null),
  ('publicacao.horario_inicio',     '0',     'numero',   'Hora em que as publicações automáticas começam (0-23)', 0, 23),
  ('publicacao.horario_fim',        '24',    'numero',   'Hora em que as publicações automáticas param (1-24)', 1, 24),
  ('publicacao.intervalo_minutos',  '10',    'numero',   'Intervalo mínimo entre rodadas de publicação', 5, 1440),
  ('publicacao.por_rodada',         '2',     'numero',   'Ofertas automáticas publicadas por rodada', 1, 20),
  ('publicacao.max_por_dia',        '60',    'numero',   'Máximo de ofertas automáticas por dia', 1, 500),
  ('publicacao.feed_horario',       '19',    'numero',   'Hora do post diário no feed', 0, 23),
  ('filtros.desconto_minimo',       '20',    'numero',   'Desconto mínimo (%) para uma oferta entrar', 0, 95),
  ('filtros.nota_minima',           '4.5',   'numero',   'Nota mínima do produto (0 a 5)', 0, 5),
  ('filtros.repetir_apos_dias',     '30',    'numero',   'Dias até o mesmo produto poder voltar', 1, 365),
  ('monitor.ativo',                 'true',  'booleano', 'Pausar ofertas quando o preço sobe ou o produto esgota', null, null),
  ('monitor.tolerancia_preco_pct',  '5',     'numero',   'Aumento de preço tolerado antes de pausar (%)', 0, 100),
  ('monitor.acompanhar_dias',       '7',     'numero',   'Por quantos dias acompanhar uma oferta publicada', 1, 60),
  ('monitor.verificar_links',       'true',  'booleano', 'Verificar se o link abre antes de publicar', null, null),
  ('transparencia.marcar_publi',    'true',  'booleano', 'Selo PUBLI na arte e #publi nas legendas', null, null),
  ('transparencia.aviso_afiliado',  '"Link de afiliado: posso ganhar comissão, sem custo extra para você."', 'texto', 'Aviso de comissão nas legendas', null, null),
  ('textos.chamada_instagram',      '"🔗 Link no nosso canal do Telegram (link na bio)"', 'texto', 'Chamada na legenda do Instagram', null, null),
  ('textos.rodape_story',           '"Link no canal do Telegram"', 'texto', 'Texto no rodapé do story', null, null),
  ('textos.hashtags',               '"#achadinhos #promoção #ofertas"', 'texto', 'Hashtags do Instagram', null, null),
  ('visual.nome_perfil',            '"Sonho das Promos"', 'texto', 'Nome no topo das artes', null, null),
  ('visual.cor_principal',          '"#EE4D2D"', 'texto', 'Cor principal das artes', null, null),
  ('visual.cor_destaque',           '"#FFD400"', 'texto', 'Cor de destaque das artes', null, null),
  ('sistema.fuso_horario',          '"America/Recife"', 'texto', 'Fuso horário usado em horários e relatórios', null, null),
  ('logs.retencao_dias',            '30',    'numero',   'Dias que os logs de execução ficam guardados', 7, 365);
