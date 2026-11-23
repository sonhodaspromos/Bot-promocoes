// Link rastreado do Bot de Ofertas.
// Uso: https://<projeto>.supabase.co/functions/v1/r?l=<id_do_link>&c=<codigo_do_canal>
// - Conta o clique (registrar_clique) e redireciona para o link de afiliado VALIDADO.
// - Link não validado/inativo: ninguém é redirecionado.
// - Oferta encerrada (pausada/expirada): mostra aviso com opção de abrir o produto.
// - O IP nunca é gravado: só um hash SHA-256 com sal, para não contar o mesmo clique duas vezes.
// Implantada com verify_jwt = false (é um link público).
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const URL_BASE = Deno.env.get("SUPABASE_URL")!;
const CHAVE = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const SAL = Deno.env.get("CLIQUE_SAL") ?? URL_BASE;
const ROBOS = /bot|crawler|spider|preview|facebookexternalhit|whatsapp|telegram|slack|discord|curl|python-requests|wget/i;
const cab = { apikey: CHAVE, Authorization: `Bearer ${CHAVE}`, "Content-Type": "application/json" };

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

function pagina(titulo: string, texto: string, link?: string | null) {
  const botao = link
    ? `<a href="${esc(link)}" rel="nofollow noopener" style="display:inline-block;margin-top:18px;padding:12px 22px;background:#EE4D2D;color:#fff;border-radius:24px;text-decoration:none;font-weight:bold">Ver o produto mesmo assim</a>`
    : "";
  return new Response(
    `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>${esc(titulo)}</title></head><body style="font-family:system-ui,sans-serif;text-align:center;padding:60px 20px;color:#222"><h1 style="font-size:22px">${esc(titulo)}</h1><p style="color:#555">${esc(texto)}</p>${botao}</body></html>`,
    { status: 200, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } },
  );
}

async function sha256(texto: string) {
  const dados = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(texto));
  return [...new Uint8Array(dados)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

Deno.serve(async (req: Request) => {
  try {
    const url = new URL(req.url);
    const link = url.searchParams.get("l") ?? "";
    const canal = (url.searchParams.get("c") ?? "").slice(0, 40);
    if (!/^[0-9]{1,18}$/.test(link)) return pagina("Link inválido", "Não encontramos essa oferta.");

    const ua = req.headers.get("user-agent") ?? "";
    let destino: string | null = null;
    let ativa = false;

    if (ROBOS.test(ua)) {
      // pré-visualização de apps/robôs: não conta clique
      const r = await fetch(`${URL_BASE}/rest/v1/links_afiliados?id=eq.${link}&select=url,url_curta,validado,ativo`, { headers: cab });
      const [l] = r.ok ? await r.json() : [];
      if (l?.validado && l?.ativo) { destino = l.url_curta ?? l.url; ativa = true; }
    } else {
      const ip = (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim();
      const r = await fetch(`${URL_BASE}/rest/v1/rpc/registrar_clique`, {
        method: "POST",
        headers: cab,
        body: JSON.stringify({
          p_link: Number(link),
          p_canal: canal || null,
          p_ip_hash: ip ? await sha256(SAL + ip) : null,
          p_user_agent: ua.slice(0, 400),
          p_referer: (req.headers.get("referer") ?? "").slice(0, 500) || null,
        }),
      });
      const [res] = r.ok ? await r.json() : [];
      if (!res) return pagina("Oferta não encontrada", "Esse link não existe mais.");
      destino = res.url_destino;
      ativa = res.oferta_ativa;
    }

    if (!destino) return pagina("Oferta indisponível", "Esta oferta não está mais disponível. Fique de olho no canal para as próximas promoções!");
    if (!ativa) return pagina("Essa oferta acabou 😢", "O produto esgotou ou o preço mudou. Fique de olho no canal para as próximas promoções!", destino);
    return new Response(null, { status: 302, headers: { Location: destino, "Cache-Control": "no-store" } });
  } catch (_e) {
    return pagina("Algo deu errado", "Tente novamente em instantes.");
  }
});
