// Story para postar à mão no Instagram: a arte sai no fundo da marca (img/story_fundo.png), com o produto
// num cartão acima da faixa "Clique aqui". No celular abre o menu de compartilhar (dá para escolher o
// Instagram direto); no computador baixa o arquivo. O link vai à parte, para o adesivo de link.
import { SUPABASE_URL } from "./config.js";
import { brl } from "./ui.js";

const L = 1080, A = 1920, M = 60;
let fontes;

function carregarFontes() {
  fontes ??= Promise.all([
    new FontFace("PoppinsArte", `url(${new URL("../fontes/Poppins-Bold.ttf", import.meta.url)})`, { weight: "700" }),
    new FontFace("PoppinsArte", `url(${new URL("../fontes/Poppins-Regular.ttf", import.meta.url)})`, { weight: "400" }),
  ].map((f) => f.load().then((ff) => document.fonts.add(ff)))).catch(() => null);
  return fontes;
}

function imagem(src, cors) {
  return new Promise((ok) => {
    const i = new Image();
    if (cors) { i.crossOrigin = "anonymous"; i.referrerPolicy = "no-referrer"; }
    i.onload = () => ok(i);
    i.onerror = () => ok(null);
    i.src = src;
  });
}

function quebrar(ctx, texto, largura, maxLinhas) {
  const palavras = String(texto || "").split(/\s+/).filter(Boolean), linhas = [];
  let atual = "";
  for (const p of palavras) {
    const teste = (atual + " " + p).trim();
    if (ctx.measureText(teste).width <= largura) atual = teste;
    else { if (atual) linhas.push(atual); atual = p; }
    if (linhas.length === maxLinhas) break;
  }
  if (atual && linhas.length < maxLinhas) linhas.push(atual);
  if (linhas.length === maxLinhas && linhas.join(" ") !== palavras.join(" ")) {
    let u = linhas[maxLinhas - 1];
    while (u && ctx.measureText(u + "...").width > largura) u = u.slice(0, -1);
    linhas[maxLinhas - 1] = u.trimEnd() + "...";
  }
  return linhas;
}

function retangulo(ctx, x0, y0, x1, y1, r) {
  ctx.beginPath(); ctx.roundRect(x0, y0, x1 - x0, y1 - y0, r);
}

/** Desenha o story (canvas 1080x1920). Retorna { canvas, semFoto }. */
export async function desenharStory(o, cores = {}) {
  const principal = cores.principal || "#DC1B63", realce = cores.destaque || "#FDC302";
  await carregarFontes();
  const [fundo, foto] = await Promise.all([
    imagem(new URL("../img/story_fundo.png", import.meta.url).href, false),
    /^https:\/\//.test(o.imagem_url || "") ? imagem(o.imagem_url, true) : Promise.resolve(null),
  ]);
  const cv = document.createElement("canvas");
  cv.width = L; cv.height = A;
  const c = cv.getContext("2d");
  if (fundo) c.drawImage(fundo, 0, 0, L, A); else { c.fillStyle = principal; c.fillRect(0, 0, L, A); }

  const y0 = 90, y1 = Math.round(A * 0.70);
  retangulo(c, M, y0, L - M, y1, 40); c.fillStyle = "#fff"; c.fill();
  c.lineWidth = 6; c.strokeStyle = principal; c.stroke();

  const area = L - 4 * M, altFoto = Math.round(area * 0.9);
  let semFoto = !foto && /^https:\/\//.test(o.imagem_url || "");  // tinha foto, mas não carregou
  if (foto) {
    const esc = Math.min(area / foto.naturalWidth, altFoto / foto.naturalHeight);
    const w = foto.naturalWidth * esc, h = foto.naturalHeight * esc;
    c.drawImage(foto, (L - w) / 2, y0 + 40 + (altFoto - h) / 2, w, h);
    try { c.getImageData(0, 0, 1, 1); } catch {  // a loja não liberou a foto para o painel: refaz sem ela
      return desenharStory({ ...o, imagem_url: null }, cores).then((r) => ({ ...r, semFoto: true }));
    }
  } else {
    c.fillStyle = "#F4F4F4"; retangulo(c, 2 * M, y0 + 40, L - 2 * M, y0 + 40 + altFoto, 24); c.fill();
    c.fillStyle = "#999"; c.font = "400 44px PoppinsArte, system-ui, sans-serif"; c.textAlign = "center"; c.textBaseline = "middle";
    c.fillText("sem foto", L / 2, y0 + 40 + altFoto / 2);
  }

  const desconto = Number(o.desconto_pct) || 0;
  if (desconto >= 1) {
    const r = 95, cx = L - M - r + 25, cy = y0 + r - 25;
    c.beginPath(); c.arc(cx, cy, r, 0, Math.PI * 2); c.fillStyle = realce; c.fill();
    c.fillStyle = "#000"; c.textAlign = "center"; c.textBaseline = "middle";
    c.font = "700 54px PoppinsArte, system-ui, sans-serif"; c.fillText(`-${Math.round(desconto)}%`, cx, cy - 12);
    c.font = "700 30px PoppinsArte, system-ui, sans-serif"; c.fillText("OFF", cx, cy + 38);
  }

  let x = M + 20;
  for (const [t, cor] of [...(o.demo ? [["DEMONSTRAÇÃO", "#6B21A8"]] : []), ["PUBLI", "#222222"]]) {
    c.font = "700 26px PoppinsArte, system-ui, sans-serif";
    const w = c.measureText(t).width + 30;
    retangulo(c, x, y0 + 20, x + w, y0 + 62, 20); c.fillStyle = cor; c.fill();
    c.fillStyle = "#fff"; c.textAlign = "left"; c.textBaseline = "middle"; c.fillText(t, x + 15, y0 + 41);
    x += w + 10;
  }

  c.textAlign = "left"; c.textBaseline = "top";
  let y = y0 + 40 + altFoto + 30;
  c.font = "700 44px PoppinsArte, system-ui, sans-serif"; c.fillStyle = "#222";
  for (const linha of quebrar(c, o.titulo, area, 3)) { c.fillText(linha, 2 * M, y); y += 56; }
  y += 15;
  if (o.preco_referencia && Number(o.preco_referencia) > Number(o.preco_promocional)) {
    c.font = "400 40px PoppinsArte, system-ui, sans-serif"; c.fillStyle = "#888";
    const t = `de ${brl(o.preco_referencia)}`;
    c.fillText(t, 2 * M, y);
    const ini = 2 * M + c.measureText("de ").width, fim = 2 * M + c.measureText(t).width;
    c.fillRect(ini, y + 26, fim - ini, 4);
    y += 50;
  }
  c.font = "700 96px PoppinsArte, system-ui, sans-serif"; c.fillStyle = principal;
  c.fillText(brl(o.preco_promocional), 2 * M, y);
  c.font = "700 34px PoppinsArte, system-ui, sans-serif"; c.fillStyle = "#555";
  c.fillText(String(o.marketplace_nome || "").slice(0, 30), 2 * M, y + 114);
  return { canvas: cv, semFoto };
}

/** Link para o adesivo: o rastreado (conta os cliques do story) quando o link já foi validado pelo bot. */
export function linkDoStory(o) {
  if (o.link_id && o.link_validado) return `${SUPABASE_URL}/functions/v1/r?l=${Number(o.link_id)}&c=instagram_story`;
  return o.link_url || "";
}

/** Entrega a arte já desenhada: menu de compartilhar no celular, download no computador.
 * Chamar direto do toque no botão (o navegador só abre o menu de compartilhar com um toque recente). */
export async function compartilharStory(blob, nome) {
  const arquivo = new File([blob], nome, { type: "image/jpeg" });
  if (navigator.canShare?.({ files: [arquivo] })) {
    try { await navigator.share({ files: [arquivo] }); return "compartilhado"; }
    catch (e) { if (e?.name === "AbortError") return "cancelado"; }
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = nome;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  return "baixado";
}
