// Regras puras das Edge Functions (sem Deno, sem rede): testadas com `node --experimental-strip-types`
// e com `deno test` no CI. As funções `r` e `disparar` importam daqui.

/** IP do visitante sem confiar no que o próprio visitante pode forjar.
 *  O 1º item do X-Forwarded-For é enviado pelo cliente (forjável); usamos o cabeçalho do proxy
 *  (cf-connecting-ip / x-real-ip) e, sem ele, o ÚLTIMO item do X-Forwarded-For (o que o proxy anexou). */
export function ipDoVisitante(h: { get(nome: string): string | null }): string | null {
  for (const nome of ["cf-connecting-ip", "x-real-ip"]) {
    const v = (h.get(nome) ?? "").trim();
    if (ipValido(v)) return v;
  }
  const cadeia = (h.get("x-forwarded-for") ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const ultimo = cadeia[cadeia.length - 1] ?? "";
  return ipValido(ultimo) ? ultimo : null;
}

export function ipValido(ip: string): boolean {
  return /^(\d{1,3}\.){3}\d{1,3}$/.test(ip) && ip.split(".").every((n) => Number(n) <= 255) || /^[0-9a-f:]{2,39}$/i.test(ip) && ip.includes(":");
}

const ROBOS = /bot|crawler|spider|preview|facebookexternalhit|whatsapp|telegram|slack|discord|curl|python-requests|wget|headless|phantom|httpclient|okhttp|axios|go-http|java\//i;
export function ehRobo(ua: string): boolean {
  return !ua || ua.length < 12 || ROBOS.test(ua);
}

/** Sal obrigatório e secreto: sem ele o hash do IP seria reversível (só ~4 bilhões de IPv4). */
export function salValido(sal: string | undefined | null, urlProjeto: string): boolean {
  return !!sal && sal.length >= 32 && sal !== urlProjeto;
}

/** Visitante = IP + navegador (reduz colisão de quem divide o mesmo IP na operadora, CGNAT). */
export function textoDoVisitante(sal: string, ip: string, ua: string): string {
  return `${sal}|${ip}|${ua.slice(0, 200)}`;
}

/** Só redireciona para https (nunca javascript:, data:, http:). */
export function destinoSeguro(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" && !u.username && !u.password ? u.toString() : null;
  } catch {
    return null;
  }
}

export type Sessao = { papel: string | null; aal: string | null; mfa_exigido: boolean | null };
/** "Publicar agora": dono/editor E com o 2º fator quando o MFA é exigido. */
export function podeDisparar(s: Sessao | null): { ok: boolean; motivo?: string } {
  if (!s || (s.papel !== "dono" && s.papel !== "editor")) return { ok: false, motivo: "Apenas dono ou editor podem publicar agora." };
  if (s.mfa_exigido !== false && s.aal !== "aal2") return { ok: false, motivo: "Confirme o código do app autenticador (2º fator) para continuar." };
  return { ok: true };
}

/** Ações de edição do painel (ex.: "Puxar dados"): dono/editor E com o 2º fator quando o MFA é exigido. */
export function podeEditar(s: Sessao | null): { ok: boolean; motivo?: string } {
  if (!s || (s.papel !== "dono" && s.papel !== "editor")) return { ok: false, motivo: "Apenas dono ou editor podem cadastrar ofertas." };
  if (s.mfa_exigido !== false && s.aal !== "aal2") return { ok: false, motivo: "Confirme o código do app autenticador (2º fator) para continuar." };
  return { ok: true };
}
