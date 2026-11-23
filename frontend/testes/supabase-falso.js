// Dublê do supabase-js para testar o painel sem internet e sem banco.
// O cenário (papel, logado ou não) vem de ?papel=dono|editor|leitor|nenhum&logado=0|1 na URL.
const params = new URLSearchParams(location.search);
const PAPEL = params.get("papel") || "dono";
const LOGADO = params.get("logado") !== "0";
const agora = new Date().toISOString();
const hoje = new Date().toLocaleDateString("sv-SE");

window.__chamadas = [];

const DADOS = {
  configuracoes: [
    { chave: "publicacao.aprovacao_manual", valor: false, tipo: "booleano", descricao: "Se ligado, nada é publicado sem aprovação no painel" },
    { chave: "publicacao.max_por_dia", valor: 60, tipo: "numero", descricao: "Máximo de ofertas automáticas por dia", minimo: 1, maximo: 500 },
    { chave: "filtros.desconto_minimo", valor: 20, tipo: "numero", descricao: "Desconto mínimo (%)", minimo: 0, maximo: 95 },
    { chave: "monitor.ativo", valor: true, tipo: "booleano", descricao: "Pausar ofertas quando o preço sobe ou o produto esgota" },
    { chave: "transparencia.aviso_afiliado", valor: "Link de afiliado: posso ganhar comissão.", tipo: "texto", descricao: "Aviso de comissão" },
    { chave: "visual.nome_perfil", valor: "Sonho das Promos", tipo: "texto", descricao: "Nome no topo das artes" },
    { chave: "visual.cor_principal", valor: "#EE4D2D", tipo: "texto", descricao: "Cor principal" },
  ],
  categorias: [
    { id: 1, nome: "Eletrônicos", slug: "eletronicos", palavras_chave: ["fone bluetooth", "smartwatch"], ativa: true, ordem: 1 },
    { id: 2, nome: "Casa", slug: "casa", palavras_chave: ["air fryer"], ativa: false, ordem: 2 },
  ],
  marketplaces: [
    { id: 1, codigo: "shopee", nome: "Shopee", modo: "demo", ativo: true, dominios_afiliado: ["s.shopee.com.br", "shope.ee"] },
    { id: 2, codigo: "mercado_livre", nome: "Mercado Livre", modo: "manual", ativo: true, dominios_afiliado: ["mercadolivre.com"] },
    { id: 3, codigo: "demo", nome: "Loja de Demonstração", modo: "demo", ativo: true, dominios_afiliado: ["demo.invalid"] },
  ],
  canais: [
    { id: 1, codigo: "telegram", nome: "Canal do Telegram", modo: "demo", ativo: true, limite_diario: 200 },
    { id: 2, codigo: "instagram_story", nome: "Stories do Instagram", modo: "demo", ativo: true, limite_diario: 60 },
    { id: 3, codigo: "instagram_feed", nome: "Feed do Instagram", modo: "demo", ativo: true, limite_diario: 1 },
  ],
  vw_promocoes_painel: [
    { id: 11, produto_id: 21, status: "encontrada", titulo: "[DEMO] Fone Bluetooth <script>alert(1)</script> com estojo", preco_promocional: 59.9, preco_referencia: 149.9, desconto_pct: 60, comissao_pct: 8, demo: true, link_validado: false, link_url: "https://demo.invalid/p/1", marketplace_nome: "Loja de Demonstração", categoria: "Eletrônicos", categoria_id: 1, cliques: 12, comissao_total: 3.5, criado_em: agora, imagem_url: null,
      pontuacao: 0, classificacao: "nao_recomendada", confianca: 40, analise: { bloqueios: ["Link de afiliado inválido: link quebrado (HTTP 404)"], motivos: [], alertas: ["Oferta de DEMONSTRAÇÃO"], desconto_real: 60, base_desconto: "preço informado pela loja", componentes: [{ nome: "desconto real", peso: 35, nota: 1, pontos: 35, explicacao: "60% de desconto real" }, { nome: "comissão", peso: 20, nota: null, pontos: 0, explicacao: "Comissão não informada pela loja: não entra na conta" }] } },
    { id: 12, produto_id: 22, status: "encontrada", titulo: "Air Fryer 4L", preco_promocional: 299.9, preco_referencia: 499.9, desconto_pct: 40, demo: false, link_validado: true, link_url: "https://mercadolivre.com/sec/abc", marketplace_nome: "Mercado Livre", categoria: "Casa", categoria_id: 2, cliques: 3, comissao_total: 0, criado_em: agora, imagem_url: "javascript:alert(1)", motivo_status: null,
      pontuacao: 88, classificacao: "excelente", confianca: 92, analise: { bloqueios: [], motivos: ["Desconto real de 40% (preço de R$ 499,90 confirmado pelo histórico)", "Menor preço registrado nos últimos 90 dias"], alertas: [], desconto_real: 40, base_desconto: "histórico", componentes: [{ nome: "desconto real", peso: 35, nota: 0.77, pontos: 26.95, explicacao: "40% de desconto real" }] } },
  ],
  vw_metricas_diarias: [
    { dia: hoje, produtos_encontrados: 40, promocoes_criadas: 12, publicacoes: 8, publicacoes_telegram: 4, publicacoes_stories: 3, publicacoes_feed: 1, cliques: 25, comissoes: 7.5 },
  ],
  execucoes: [
    { id: "e1", origem: "agendada", modo: "demo", status: "parcial", iniciada_em: agora, finalizada_em: agora, resumo: { publicadas: 4, etapas_com_erro: ["monitor"], canais_bloqueados: { telegram: "credencial recusada" } } },
    { id: "e2", origem: "manual", modo: "demo", status: "sucesso", iniciada_em: agora, finalizada_em: agora, resumo: { publicadas: 2, etapas_com_erro: [] } },
  ],
  logs: [
    { id: 2, nivel: "ERRO", modulo: "monitor", mensagem: "Falha ao conferir oferta", contexto: { erro: "timeout", token: "***" }, criado_em: agora },
    { id: 1, nivel: "INFO", modulo: "publicacao", mensagem: "telegram: publicado (DEMO)", contexto: {}, criado_em: agora },
  ],
  comissoes: [
    { id: 1, data_venda: hoje, valor_comissao: 7.5, status: "confirmada", origem: "manual", descricao: "Air Fryer", marketplaces: { nome: "Mercado Livre" } },
    { id: 2, data_venda: hoje, valor_comissao: 2, status: "pendente", origem: "api", descricao: "Fone", marketplaces: { nome: "Shopee" } },
  ],
  credenciais_integracoes: [{ integracao: "telegram", nome: "TOKEN", dica: "••••a1b2", atualizado_em: agora }],
  promocoes: [],
};
const CONTAGENS = { promocoes: 3 };

function construtor(tabela) {
  const estado = { tabela, filtros: [], operacao: "select", corpo: null, unico: false, contar: false };
  const api = {
    select(_c, opcoes) { if (opcoes?.count) estado.contar = true; return api; },
    insert(corpo) { estado.operacao = "insert"; estado.corpo = corpo; return api; },
    update(corpo) { estado.operacao = "update"; estado.corpo = corpo; return api; },
    delete() { estado.operacao = "delete"; return api; },
    maybeSingle() { estado.unico = true; return api; },
    single() { estado.unico = true; return api; },
    then(ok, falha) {
      window.__chamadas.push({ tabela, operacao: estado.operacao, corpo: estado.corpo, filtros: estado.filtros });
      if (PAPEL === "leitor" && estado.operacao !== "select") return Promise.resolve({ data: null, error: { code: "42501", message: "denied" } }).then(ok, falha);
      let dados = DADOS[tabela] || [];
      for (const [op, campo, valor] of estado.filtros) {
        if (op === "eq") dados = dados.filter((d) => d[campo] === undefined || String(d[campo]) === String(valor));
      }
      const resposta = estado.contar ? { data: null, count: CONTAGENS[tabela] ?? dados.length, error: null }
        : { data: estado.unico ? (dados[0] || null) : dados, error: null };
      return Promise.resolve(resposta).then(ok, falha);
    },
  };
  for (const m of ["eq", "neq", "gt", "gte", "lte", "in", "ilike", "order", "range", "limit", "not", "is", "like"]) {
    api[m] = (campo, valor) => { estado.filtros.push([m, campo, valor]); return api; };
  }
  return api;
}

export function createClient() {
  const sessao = LOGADO ? { access_token: "token-teste", user: { id: "u1", email: "dona@teste.com" } } : null;
  return {
    auth: {
      getSession: async () => ({ data: { session: sessao } }),
      signInWithPassword: async () => ({ error: { message: "Invalid login credentials" } }),
      signUp: async () => ({ data: { session: null }, error: null }),
      signOut: async () => ({}),
      onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }),
    },
    rpc: async (nome, args) => {
      window.__chamadas.push({ rpc: nome, args });
      if (nome === "meu_papel") return { data: PAPEL === "nenhum" ? null : PAPEL, error: null };
      if (nome === "cadastrar_oferta_manual" && !/mercadolivre\.com\//.test(args.p_link))
        return { data: null, error: { code: "P0011", message: "Esse link não é um link de afiliado de Mercado Livre (domínios aceitos: mercadolivre.com)." } };
      return { data: 99, error: null };
    },
    from: construtor,
  };
}
