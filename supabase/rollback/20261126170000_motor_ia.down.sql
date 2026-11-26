-- Reverte a 028 (motor de IA) e volta ao esquema da 027. Apaga prompts, conteúdos e o histórico de chamadas da IA.
drop table if exists public.ia_conteudos;
drop table if exists public.ia_chamadas;
drop table if exists public.ia_prompts;
drop function if exists public.ia_prompt_salvar(text, text, text, text);
drop function if exists public.ia_prompt_ativar(bigint);
drop function if exists privado.ia_conteudo_decidir();
drop function if exists privado.ia_variaveis_validas(text);
delete from public.configuracoes where chave in ('recursos.ia', 'ia.modelo', 'ia.preco_entrada_usd_mtok', 'ia.preco_saida_usd_mtok',
  'ia.orcamento_diario_usd', 'ia.orcamento_mensal_usd', 'ia.max_por_rodada', 'ia.max_tokens_saida', 'ia.aprovacao_manual',
  'ia.tom_de_voz', 'ia.circuito_falhas', 'ia.circuito_pausa_min');
delete from public.orcamento_consumo where servico = 'ia';
