// "Puxar dados": a partir do link de afiliado, abre a página pública do produto e devolve título, foto,
// preço atual e preço antigo para preencher o formulário de nova oferta. Nada é salvo aqui: a pessoa confere
// e salva pelo painel (cadastrar_oferta_manual, que valida o link como sempre).
// Segurança:
//   * exige login e papel dono/editor (com 2º fator quando exigido);
//   * só abre https dos domínios cadastrados da loja (dominios_afiliado + dominios_destino), conferindo
//     CADA redirecionamento; sem IP literal, sem porta, no máximo 6 saltos, 10 s e 3 MB;
//   * a resposta traz só os campos extraídos (nunca o HTML).
// Implantada com verify_jwt = false porque faz a própria autenticação (e precisa responder ao CORS).
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { podeEditar, type Sessao } from "../_compartilhado/regras.ts";
import { enderecoPermitido, extrairProduto, paginaDeBloqueio } from "../_compartilhado/produto.ts";

const URL_BASE = Deno.env.get("SUPABASE_URL")!;
const ANON = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICO = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const NAVEGADOR = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";
const LIMITE_BYTES = 3_000_000;

const resposta = (status: number, corpo: Record<string, unknown>) =>
  new Response(JSON.stringify(corpo), { status, headers: { ...CORS, "Content-Type": "application/json" } });

async function lerTexto(r: Response): Promise<string> {
  const leitor = r.body?.getReader();
  if (!leitor) return "";
  const partes: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await leitor.read();
    if (done) break;
    total += value.length;
    if (total > LIMITE_BYTES) { await leitor.cancel(); break; }
    partes.push(value);
  }
  const tudo = new Uint8Array(Math.min(total, LIMITE_BYTES));
  let pos = 0;
  for (const p of partes) { tudo.set(p.subarray(0, tudo.length - pos), pos); pos += p.length; if (pos >= tudo.length) break; }
  return new TextDecoder().decode(tudo);
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (req.method !== "POST") return resposta(405, { erro: "Use POST." });
  try {
    const token = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
    if (!token) return resposta(401, { erro: "Faça login no painel." });
    const p = await fetch(`${URL_BASE}/rest/v1/rpc/minha_sessao`, {
      method: "POST", headers: { apikey: ANON, Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: "{}",
    });
    if (p.status === 401) return resposta(401, { erro: "Sessão inválida ou expirada. Entre de novo." });
    const [sessao] = p.ok ? (await p.json()) as Sessao[] : [null];
    const permissao = podeEditar(sessao ?? null);
    if (!permissao.ok) return resposta(403, { erro: permissao.motivo });

    const corpo = await req.json().catch(() => ({}));
    const url = String(corpo?.url ?? "").trim().slice(0, 2000);
    const codigo = String(corpo?.marketplace ?? "").trim();
    if (!/^[a-z0-9_]{2,40}$/.test(codigo)) return resposta(400, { erro: "Escolha a loja." });

    const m = await fetch(`${URL_BASE}/rest/v1/marketplaces?codigo=eq.${codigo}&select=nome,dominios_afiliado,dominios_destino`,
      { headers: { apikey: SERVICO, Authorization: `Bearer ${SERVICO}` } });
    const [loja] = m.ok ? await m.json() : [];
    if (!loja) return resposta(400, { erro: "Loja não encontrada." });
    if (codigo === "shopee") {
      return resposta(422, { erro: "Na Shopee o preenchimento automático depende da API de afiliados (App ID e Secret). Por enquanto, preencha à mão." });
    }
    const dominios = [...(loja.dominios_afiliado ?? []), ...(loja.dominios_destino ?? [])];
    if (!enderecoPermitido(url, dominios)) return resposta(400, { erro: `Esse link não é de ${loja.nome}.` });

    let atual = url, r: Response | null = null;
    for (let salto = 0; salto < 7; salto++) {
      if (!enderecoPermitido(atual, dominios)) return resposta(400, { erro: "O link leva para fora da loja; confira se é o link certo." });
      r = await fetch(atual, {
        redirect: "manual", signal: AbortSignal.timeout(10_000),
        headers: { "User-Agent": NAVEGADOR, Accept: "text/html,application/xhtml+xml", "Accept-Language": "pt-BR,pt;q=0.9" },
      });
      const destino = r.headers.get("location");
      if (r.status >= 300 && r.status < 400 && destino) {
        await r.body?.cancel();
        atual = new URL(destino, atual).toString();
        continue;
      }
      break;
    }
    if (!r || r.status >= 300) {
      return resposta(502, { erro: `A loja não abriu a página (código ${r?.status ?? "?"}). Preencha à mão ou tente de novo.` });
    }
    const html = await lerTexto(r);
    if (paginaDeBloqueio(html)) {
      return resposta(502, { erro: "A loja pediu verificação de robô e não deixou ler a página. Preencha à mão." });
    }
    const dados = extrairProduto(html, atual);
    if (!dados.titulo && !dados.preco) {
      return resposta(422, { erro: "Não encontrei os dados do produto nessa página. Confira se o link é de um produto." });
    }
    return resposta(200, { ...dados, url_final: atual });
  } catch (e) {
    const tempo = e instanceof DOMException && e.name === "TimeoutError";
    return resposta(502, { erro: tempo ? "A loja demorou demais para responder. Tente de novo." : "Não deu para ler a página agora. Tente de novo." });
  }
});
