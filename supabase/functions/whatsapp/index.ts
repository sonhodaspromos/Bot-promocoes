// Webhook do WhatsApp Business (Cloud API): consentimento (QUERO OFERTAS) e descadastro (SAIR).
// - GET: verificação do webhook (hub.verify_token = WHATSAPP_VERIFY_TOKEN).
// - POST: assinatura X-Hub-Signature-256 conferida com WHATSAPP_APP_SECRET; sem assinatura válida → 401.
// - Só registra quem pediu explicitamente; "SAIR" descadastra na hora. Nada é enviado daqui (o envio de
//   ofertas é do bot, com teto de gasto). Implantada com verify_jwt = false (a Meta chama sem JWT).
// ATENÇÃO: não validada com a Meta. Só ligue depois de configurar o app e testar com o número real.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { acoes, assinaturaValida, TEXTO_CONSENTIMENTO } from "../_compartilhado/whatsapp.ts";

const URL_BASE = Deno.env.get("SUPABASE_URL")!;
const CHAVE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const SEGREDO = Deno.env.get("WHATSAPP_APP_SECRET") ?? "";
const VERIFICACAO = Deno.env.get("WHATSAPP_VERIFY_TOKEN") ?? "";

async function rpc(nome: string, args: Record<string, unknown>) {
  const r = await fetch(`${URL_BASE}/rest/v1/rpc/${nome}`, {
    method: "POST", headers: { apikey: CHAVE, Authorization: `Bearer ${CHAVE}`, "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!r.ok) throw new Error(`${nome}: HTTP ${r.status}`);
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  if (req.method === "GET") {
    const ok = url.searchParams.get("hub.mode") === "subscribe" && VERIFICACAO.length >= 16 &&
      url.searchParams.get("hub.verify_token") === VERIFICACAO;
    return new Response(ok ? url.searchParams.get("hub.challenge") ?? "" : "proibido", { status: ok ? 200 : 403 });
  }
  if (req.method !== "POST") return new Response("método não permitido", { status: 405 });
  const corpo = await req.text();
  if (corpo.length > 200_000 || !(await assinaturaValida(SEGREDO, corpo, req.headers.get("x-hub-signature-256")))) {
    return new Response("assinatura inválida", { status: 401 });
  }
  try {
    for (const a of acoes(JSON.parse(corpo))) {
      if (a.acao === "entrar") await rpc("whatsapp_inscrever", { p_telefone: a.telefone, p_origem: "mensagem_recebida", p_texto: TEXTO_CONSENTIMENTO });
      else await rpc("whatsapp_descadastrar", { p_telefone: a.telefone });
    }
  } catch (e) {
    console.error("webhook whatsapp:", (e as Error).message);
    return new Response("erro", { status: 500 });   // a Meta reenvia; as RPCs são idempotentes
  }
  return new Response("ok");
});
