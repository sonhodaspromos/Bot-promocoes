// Regras puras do webhook do WhatsApp (testadas com node --test e deno test).

const SAIR = ["sair", "parar", "stop", "cancelar", "descadastrar"];
const ENTRAR = ["quero ofertas", "quero receber ofertas", "entrar"];
export const TEXTO_CONSENTIMENTO =
  "Quero receber ofertas com links de afiliado (#publi) pelo WhatsApp. Posso sair a qualquer momento enviando SAIR.";

function normalizar(t: string): string {
  return t.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z ]/g, " ").replace(/\s+/g, " ").trim();
}

/** "entrar" só com pedido explícito; "sair" com qualquer palavra de saída. Outras mensagens: null. */
export function intencao(texto: string | undefined | null): "entrar" | "sair" | null {
  const t = normalizar(texto ?? "");
  if (!t) return null;
  if (SAIR.includes(t) || SAIR.some((p) => t.startsWith(p + " "))) return "sair";
  if (ENTRAR.includes(t)) return "entrar";
  return null;
}

/** wa_id da Meta ("5581999990000") → E.164 ("+5581999990000"). */
export function telefoneE164(waId: string | undefined | null): string | null {
  const d = String(waId ?? "").replace(/\D/g, "");
  return d.length >= 8 && d.length <= 15 && d[0] !== "0" ? `+${d}` : null;
}

function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Confere X-Hub-Signature-256 (HMAC-SHA256 do corpo com o App Secret), em tempo constante. */
export async function assinaturaValida(segredo: string, corpo: string, cabecalho: string | null): Promise<boolean> {
  if (!segredo || !cabecalho?.startsWith("sha256=")) return false;
  const chave = await crypto.subtle.importKey("raw", new TextEncoder().encode(segredo), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const esperado = hex(await crypto.subtle.sign("HMAC", chave, new TextEncoder().encode(corpo)));
  const recebido = cabecalho.slice(7).toLowerCase();
  if (recebido.length !== esperado.length) return false;
  let dif = 0;
  for (let i = 0; i < esperado.length; i++) dif |= esperado.charCodeAt(i) ^ recebido.charCodeAt(i);
  return dif === 0;
}

export type Acao = { telefone: string; acao: "entrar" | "sair" };
/** Extrai as ações (entrar/sair) das mensagens de texto do payload do webhook. */
export function acoes(payload: unknown): Acao[] {
  const saida: Acao[] = [];
  const p = payload as { entry?: { changes?: { value?: { messages?: { from?: string; type?: string; text?: { body?: string } }[] } }[] }[] };
  for (const e of p?.entry ?? []) for (const c of e.changes ?? []) for (const m of c.value?.messages ?? []) {
    if (m.type !== "text") continue;
    const tel = telefoneE164(m.from);
    const a = intencao(m.text?.body);
    if (tel && a) saida.push({ telefone: tel, acao: a });
  }
  return saida;
}

export type Mensagem = Acao & { id: string; enviadaEm: string };
/** Como `acoes`, mas com o id da mensagem (deduplicação) e a hora em que a pessoa enviou (ordem/replay).
 *  Mensagem sem id ou sem timestamp válido é descartada: não dá para provar que não é repetição. */
export function mensagens(payload: unknown): Mensagem[] {
  const saida: Mensagem[] = [];
  const p = payload as { entry?: { changes?: { value?: { messages?: { id?: string; timestamp?: string; from?: string; type?: string; text?: { body?: string } }[] } }[] }[] };
  for (const e of p?.entry ?? []) for (const c of e.changes ?? []) for (const m of c.value?.messages ?? []) {
    if (m.type !== "text") continue;
    const tel = telefoneE164(m.from);
    const a = intencao(m.text?.body);
    const id = String(m.id ?? "");
    const ts = Number(m.timestamp);
    if (!tel || !a || id.length < 4 || id.length > 200 || !Number.isFinite(ts) || ts <= 0) continue;
    saida.push({ telefone: tel, acao: a, id, enviadaEm: new Date(ts * 1000).toISOString() });
  }
  return saida;
}
