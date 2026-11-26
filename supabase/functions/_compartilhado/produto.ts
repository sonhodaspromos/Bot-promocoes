// Regras puras do "Puxar dados" (sem rede): quais endereços podem ser abertos e como ler foto, título e
// preços da página pública do produto. Testadas com node --experimental-strip-types.

export type DadosProduto = {
  titulo: string | null;
  imagem_url: string | null;
  preco: number | null;
  preco_antigo: number | null;
  preco_pix: boolean;
  id_externo: string | null;
};

/** O preço principal da página é o do Pix? (o Mercado Livre escreve "X% OFF no Pix" ao lado do preço).
 *  Só olha o trecho do preço principal, para não confundir com "pague no Pix" do rodapé. */
export function precoNoPix(html: string): boolean {
  const ancora = html.search(/aria-label=["'](?:Agora[:\s]|\d[\d.]*\s*reais)/i);
  const trecho = ancora >= 0 ? html.slice(ancora, ancora + 4000) : "";
  const texto = trecho.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  return /\b(?:OFF|desconto)\s+no\s+Pix\b/i.test(texto) || /aria-label=["'][^"']*\bno Pix\b/i.test(trecho);
}

/** O endereço pode ser aberto? Só https, sem usuário/senha, porta padrão, sem IP literal e com o domínio
 *  dentro da lista da loja (evita usar a função para acessar endereços internos ou de terceiros). */
export function enderecoPermitido(url: string, dominios: string[]): boolean {
  let u: URL;
  try { u = new URL(url); } catch { return false; }
  if (u.protocol !== "https:" || u.username || u.password || (u.port && u.port !== "443")) return false;
  const host = u.hostname.toLowerCase().replace(/\.$/, "");
  if (/^[\d.]+$/.test(host) || host.includes(":") || host.startsWith("[")) return false;
  return dominios.some((d) => {
    const dom = d.toLowerCase().trim();
    return !!dom && (host === dom || host.endsWith("." + dom));
  });
}

const ENTIDADES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", apos: "'", nbsp: " " };
export function decodificar(t: string): string {
  return t.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+|#39);/gi, (m, e: string) => {
    if (e[0] === "#") {
      const n = e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(n) && n > 0 && n < 0x110000 ? String.fromCodePoint(n) : m;
    }
    return ENTIDADES[e.toLowerCase()] ?? m;
  });
}

function meta(html: string, nome: string): string | null {
  const re = new RegExp(`<meta[^>]+(?:property|name|itemprop)=["']${nome}["'][^>]*>`, "i");
  const tag = html.match(re)?.[0];
  const v = tag?.match(/content=["']([^"']*)["']/i)?.[1];
  return v ? decodificar(v).trim() : null;
}

/** "1.234,56", "1234.56", 1234.56 → número; recusa zero, negativo e absurdos. */
export function numeroPreco(v: unknown): number | null {
  if (typeof v === "number") return v > 0 && v < 10_000_000 ? Math.round(v * 100) / 100 : null;
  if (typeof v !== "string") return null;
  let t = v.replace(/[^\d.,]/g, "");
  if (!t) return null;
  if (t.includes(",")) t = t.replace(/\./g, "").replace(",", ".");
  else if ((t.match(/\./g) ?? []).length > 1 || /\.\d{3}$/.test(t)) t = t.replace(/\./g, "");
  const n = parseFloat(t);
  return Number.isFinite(n) && n > 0 && n < 10_000_000 ? Math.round(n * 100) / 100 : null;
}

function produtosJsonLd(html: string): Record<string, unknown>[] {
  const saida: Record<string, unknown>[] = [];
  for (const m of html.matchAll(/<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)) {
    try {
      const j = JSON.parse(m[1].trim());
      const fila = Array.isArray(j) ? [...j] : [j];
      while (fila.length) {
        const x = fila.shift();
        if (!x || typeof x !== "object") continue;
        if (Array.isArray((x as Record<string, unknown>)["@graph"])) fila.push(...((x as Record<string, unknown>)["@graph"] as unknown[]));
        const tipo = (x as Record<string, unknown>)["@type"];
        if (tipo === "Product" || (Array.isArray(tipo) && tipo.includes("Product"))) saida.push(x as Record<string, unknown>);
      }
    } catch { /* bloco inválido: ignora */ }
  }
  return saida;
}

function primeiro<T>(v: T | T[] | undefined | null): T | null {
  return Array.isArray(v) ? (v[0] ?? null) : (v ?? null);
}

/** "Antes: 419 reais com 90 centavos" (texto de acessibilidade do preço riscado do Mercado Livre). */
function precoPorExtenso(t: string): number | null {
  const m = t.match(/(\d[\d.]*)\s*reais(?:\s*com\s*(\d{1,2})\s*centavos?)?/i);
  if (!m) return null;
  return numeroPreco(`${m[1].replace(/\./g, "")}.${(m[2] ?? "0").padStart(2, "0")}`);
}

export function extrairProduto(html: string, urlFinal = ""): DadosProduto {
  const ld = produtosJsonLd(html)[0] ?? null;
  const oferta = ld ? primeiro(ld.offers as Record<string, unknown> | Record<string, unknown>[]) : null;
  const imagemLd = ld ? primeiro(ld.image as unknown) : null;
  const imagemTxt = typeof imagemLd === "string" ? imagemLd : (imagemLd as Record<string, unknown> | null)?.url as string | undefined;

  const titulo = (typeof ld?.name === "string" && ld.name.trim()) ? decodificar(ld.name).trim()
    : meta(html, "og:title") ?? (html.match(/<title[^>]*>([^<]{3,300})<\/title>/i)?.[1] ? decodificar(html.match(/<title[^>]*>([^<]{3,300})<\/title>/i)![1]).trim() : null);
  let imagem = imagemTxt || meta(html, "og:image");
  if (imagem && !/^https:\/\//i.test(imagem)) imagem = null;

  // preço atual, do mais confiável para o menos: dados estruturados → metatags → texto de acessibilidade
  // "Agora: 276 reais com 44 centavos" → "R$ 276,44" no fim do título (o Mercado Livre põe o preço no og:title)
  const precoTitulo = (titulo ?? "").match(/[-–|]\s*R\$\s*([\d.]+(?:,\d{1,2})?)\s*$/)?.[1];
  const agora = html.match(/aria-label=["']Agora[:\s]+([^"']+)["']/i)?.[1];
  const preco = numeroPreco(oferta?.price ?? oferta?.lowPrice) ?? numeroPreco(meta(html, "product:price:amount"))
    ?? numeroPreco(meta(html, "price")) ?? (agora ? precoPorExtenso(decodificar(agora)) : null)
    ?? numeroPreco(precoTitulo);

  let antigo: number | null = null;
  const riscado = html.match(/aria-label=["'](?:Antes|Antes de|De)[:\s]+([^"']+)["']/i)?.[1];
  if (riscado) antigo = precoPorExtenso(decodificar(riscado)) ?? numeroPreco(riscado);
  if (!antigo) antigo = numeroPreco(html.match(/"original_price"\s*:\s*"?([\d.]+)"?/)?.[1]);
  if (antigo && preco && antigo <= preco) antigo = null;

  const id = (typeof ld?.sku === "string" && ld.sku) || (typeof ld?.productID === "string" && ld.productID)
    || (urlFinal + " " + html.slice(0, 200000)).match(/\bMLB-?\d{6,}\b/)?.[0]?.replace("-", "") || null;

  return {
    titulo: titulo ? titulo.replace(/\s*\|\s*Mercado Livre.*$/i, "").replace(/\s*[-–|]\s*R\$\s*[\d.]+(?:,\d{1,2})?\s*$/, "").slice(0, 300) : null,
    imagem_url: imagem,
    preco,
    preco_antigo: antigo,
    preco_pix: preco !== null && precoNoPix(html),
    id_externo: id ? String(id).slice(0, 120) : null,
  };
}

/** Página de bloqueio/verificação em vez do produto? */
export function paginaDeBloqueio(html: string): boolean {
  return /captcha|account-verification|verifique que (?:você|voce) n[aã]o (?:é|e) um rob[oô]|access denied/i.test(html.slice(0, 50000))
    && !/application\/ld\+json/i.test(html);
}
