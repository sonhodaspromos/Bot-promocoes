// "Publicar agora": dispara uma rodada do bot no GitHub Actions.
// Segurança:
//   * exige login (token do Supabase Auth) e papel dono ou editor;
//   * a chave do GitHub fica como SEGREDO desta função (GH_TOKEN), nunca no navegador;
//   * no máximo 1 disparo a cada 90 segundos.
// Segredos necessários (Supabase > Edge Functions > Secrets):
//   GH_TOKEN  = token fine-grained com "Actions: Read and write" só no repositório do bot
//   GH_REPO   = usuario/repositorio   (ex.: minhaconta/bot-promocoes)
//   GH_REF    = branch (opcional, padrão "main")
// Implantada com verify_jwt = false porque faz a própria autenticação (e precisa responder ao CORS).
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { podeDisparar, type Sessao } from "../_compartilhado/regras.ts";

const URL_BASE = Deno.env.get("SUPABASE_URL")!;
const ANON = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICO = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const resposta = (status: number, corpo: Record<string, unknown>) =>
  new Response(JSON.stringify(corpo), { status, headers: { ...CORS, "Content-Type": "application/json" } });

async function registrarLog(nivel: string, mensagem: string, contexto: Record<string, unknown>) {
  await fetch(`${URL_BASE}/rest/v1/logs`, {
    method: "POST",
    headers: { apikey: SERVICO, Authorization: `Bearer ${SERVICO}`, "Content-Type": "application/json", Prefer: "return=minimal" },
    body: JSON.stringify({ nivel, modulo: "painel", mensagem, contexto }),
  }).catch(() => {});
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (req.method !== "POST") return resposta(405, { erro: "Use POST." });

  try {
    // 1. quem está pedindo?
    const token = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
    if (!token) return resposta(401, { erro: "Faça login no painel." });
    const u = await fetch(`${URL_BASE}/auth/v1/user`, { headers: { apikey: ANON, Authorization: `Bearer ${token}` } });
    if (!u.ok) return resposta(401, { erro: "Sessão inválida ou expirada. Entre de novo." });
    const usuario = await u.json();

    // 2. tem permissão E o 2º fator? (consulta feita COM o token da pessoa: o aal vem do próprio token)
    const p = await fetch(`${URL_BASE}/rest/v1/rpc/minha_sessao`, {
      method: "POST",
      headers: { apikey: ANON, Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: "{}",
    });
    const [sessao] = p.ok ? (await p.json()) as Sessao[] : [null];
    const permissao = podeDisparar(sessao ?? null);
    if (!permissao.ok) return resposta(403, { erro: permissao.motivo });
    const papel = sessao!.papel;

    // 3. GitHub configurado?
    const ghToken = Deno.env.get("GH_TOKEN");
    const ghRepo = Deno.env.get("GH_REPO");
    if (!ghToken || !ghRepo || !/^[\w.-]+\/[\w.-]+$/.test(ghRepo)) {
      return resposta(503, { erro: "Botão ainda não configurado: cadastre GH_TOKEN e GH_REPO nos segredos da função 'disparar'." });
    }

    // 4. limite: 1 disparo a cada 90 s
    const desde = new Date(Date.now() - 90_000).toISOString();
    const recentes = await fetch(
      `${URL_BASE}/rest/v1/logs?modulo=eq.painel&mensagem=like.Rodada%20manual*&criado_em=gte.${desde}&select=id&limit=1`,
      { headers: { apikey: SERVICO, Authorization: `Bearer ${SERVICO}` } },
    );
    if (recentes.ok && (await recentes.json()).length) {
      return resposta(429, { erro: "Uma rodada acabou de ser disparada. Aguarde 1 minuto e meio." });
    }

    // 5. dispara o workflow
    const gh = await fetch(`https://api.github.com/repos/${ghRepo}/actions/workflows/bot.yml/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${ghToken}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "bot-de-ofertas",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: Deno.env.get("GH_REF") ?? "main", inputs: { origem: "manual" } }),
    });
    if (gh.status !== 204) {
      const detalhe = (await gh.text()).slice(0, 200);
      await registrarLog("ERRO", "Falha ao disparar rodada manual no GitHub", { status: gh.status, detalhe });
      return resposta(502, { erro: `O GitHub recusou o disparo (HTTP ${gh.status}). Confira GH_TOKEN e GH_REPO.` });
    }
    await registrarLog("INFO", "Rodada manual disparada pelo painel", { usuario: usuario.id, papel });
    return resposta(200, { ok: true, mensagem: "Rodada disparada! As ofertas saem em 1 a 2 minutos." });
  } catch (_e) {
    return resposta(500, { erro: "Erro inesperado. Tente de novo." });
  }
});
