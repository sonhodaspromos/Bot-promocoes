// Acesso ao Supabase (chave pública + login). Mensagens de erro do banco viram texto amigável.
import { createClient } from "supabase";
import { SUPABASE_URL, SUPABASE_CHAVE_PUBLICA } from "./config.js";

export const sb = createClient(SUPABASE_URL, SUPABASE_CHAVE_PUBLICA, {
  auth: { persistSession: true, autoRefreshToken: true },
});

const MENSAGENS = {
  "42501": "Você não tem permissão para fazer isso.",
  "23505": "Esse registro já existe.",
  "23514": "Algum valor está fora do permitido.",
  P0002: "Este registro é histórico e não pode ser alterado.",
  P0003: "Só o valor desta configuração pode ser alterado.",
  P0010: "Oferta sem link de afiliado validado não pode ser publicada.",
  P0016: "A validação do link é feita automaticamente pelo sistema.",
  P0017: "Só o sistema marca uma oferta como publicada.",
  P0018: "Esta oferta já foi publicada neste canal nas últimas horas (regra de não repetir).",
  P0019: "O motor de inteligência não recomendou esta oferta, então ela não pode ir para a fila.",
  P0023: "Esse dado é calculado pelo sistema e não pode ser alterado pelo painel.",
  P0025: "Esse cupom está vencido ou pausado.",
  P0026: "O cupom é de outra loja: só vale para produtos da mesma loja.",
  PGRST301: "Sua sessão expirou. Entre de novo.",
};

export function mensagemErro(erro) {
  if (!erro) return "";
  const codigo = erro.code || "";
  // códigos próprios com mensagem já explicativa vinda do banco
  if (["P0011", "P0020", "P0021", "P0022", "P0024", "P0001"].includes(codigo)) return erro.message;
  return MENSAGENS[codigo] || erro.message || "Algo deu errado.";
}

/** Executa uma consulta do supabase-js e lança Error com mensagem amigável. */
export async function q(promessa) {
  const { data, error } = await promessa;
  if (error) throw new Error(mensagemErro(error));
  return data;
}

export async function contar(tabela, filtro = (x) => x) {
  const { count, error } = await filtro(sb.from(tabela).select("*", { count: "exact", head: true }));
  if (error) throw new Error(mensagemErro(error));
  return count || 0;
}

/** Chama a Edge Function "disparar" (rodar o bot agora). */
export async function publicarAgora() {
  const { data: { session } } = await sb.auth.getSession();
  if (!session) throw new Error("Faça login de novo.");
  const r = await fetch(`${SUPABASE_URL}/functions/v1/disparar`, {
    method: "POST",
    headers: { Authorization: `Bearer ${session.access_token}`, apikey: SUPABASE_CHAVE_PUBLICA, "Content-Type": "application/json" },
    body: "{}",
  });
  const corpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(corpo.erro || `Falha ao disparar (HTTP ${r.status}).`);
  return corpo.mensagem;
}
