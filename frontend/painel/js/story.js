// Story para postar à mão no Instagram: a arte sai no fundo da marca (img/story_fundo.png), com o produto
// num cartão acima da faixa "Clique aqui". No celular abre o menu de compartilhar (dá para escolher o
// Instagram direto); no computador baixa o arquivo. O link vai à parte, para o adesivo de link.
import { SUPABASE_URL } from "./config.js";
import { q } from "./api.js";
import { aviso, brl, esc } from "./ui.js";

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

// ---------------------------------------------------------------- story de cupom
function descontoGrande(c) {
  if (c.desconto_tipo === "percentual" && Number(c.desconto_valor)) return `${Number(c.desconto_valor)}% OFF`;
  if (c.desconto_tipo === "valor" && Number(c.desconto_valor)) return `${brl(c.desconto_valor)} OFF`;
  return "CUPOM";
}

/** Story do cupom no fundo de cupons da marca (img/story_cupom_fundo.png): loja, desconto grande, condições (nunca cortadas), código, validade e regras. */
export async function desenharStoryCupom(c, cores = {}) {
  const principal = cores.principal || "#DC1B63", realce = cores.destaque || "#FDC302";
  await carregarFontes();
  const fundo = await imagem(new URL("../img/story_cupom_fundo.png", import.meta.url).href, false);  // fundo só com o padrão do @ (sem faixa)
  const cv = document.createElement("canvas");
  cv.width = L; cv.height = A;
  const x = cv.getContext("2d");
  if (fundo) x.drawImage(fundo, 0, 0, L, A); else { x.fillStyle = principal; x.fillRect(0, 0, L, A); }
  const area = L - 4 * M;
  let y0 = 170, y1 = A - 170;  // ajustados à altura do conteúdo mais abaixo (cartão centralizado no fundo)

  const selos = (y0) => {
    let cx = M + 20;
    for (const [t, cor] of [...(c.demo ? [["DEMONSTRAÇÃO", "#6B21A8"]] : []), ["PUBLI", "#222222"]]) {
      x.font = "700 26px PoppinsArte, system-ui, sans-serif";
      const w = x.measureText(t).width + 30;
      retangulo(x, cx, y0 + 20, cx + w, y0 + 62, 20); x.fillStyle = cor; x.fill();
      x.fillStyle = "#fff"; x.textAlign = "left"; x.textBaseline = "middle"; x.fillText(t, cx + 15, y0 + 41);
      cx += w + 10;
    }
  };

  // conteúdo centralizado na altura do cartão: 1ª passada mede (num canvas de rascunho), 2ª desenha
  const conteudo = (x, y) => {
    x.textAlign = "center"; x.textBaseline = "top";
    x.font = "700 46px PoppinsArte, system-ui, sans-serif"; x.fillStyle = "#555";
    x.fillText(`CUPOM ${String(c.marketplace_nome || "").toUpperCase()}`.slice(0, 40), L / 2, y); y += 80;
    let tam = 170;
    x.font = `700 ${tam}px PoppinsArte, system-ui, sans-serif`; x.fillStyle = principal;
    const grande = descontoGrande(c);
    while (x.measureText(grande).width > area && tam > 60) { tam -= 6; x.font = `700 ${tam}px PoppinsArte, system-ui, sans-serif`; }
    x.fillText(grande, L / 2, y); y += tam + 40;
    const cond = [c.desconto_maximo ? `até ${brl(c.desconto_maximo)} de desconto` : "", c.valor_minimo ? `em compras acima de ${brl(c.valor_minimo)}` : ""].filter(Boolean);
    x.font = "400 46px PoppinsArte, system-ui, sans-serif"; x.fillStyle = "#333";
    for (const linha of cond.flatMap((t) => quebrar(x, t, area, 2))) { x.fillText(linha, L / 2, y); y += 60; }
    y += 40;
    if (c.codigo) {
      x.font = "400 40px PoppinsArte, system-ui, sans-serif"; x.fillStyle = "#555";
      x.fillText("use este código no pagamento", L / 2, y); y += 64;
      x.font = "700 100px PoppinsArte, system-ui, sans-serif";
      const w = Math.min(area, x.measureText(c.codigo).width + 90);
      x.setLineDash([18, 12]); x.lineWidth = 5; x.strokeStyle = principal;
      retangulo(x, (L - w) / 2, y, (L + w) / 2, y + 150, 26); x.fillStyle = realce; x.fill(); x.stroke(); x.setLineDash([]);
      x.fillStyle = "#000"; x.textBaseline = "middle"; x.fillText(c.codigo, L / 2, y + 77); x.textBaseline = "top";
      y += 200;
    } else {
      x.font = "700 50px PoppinsArte, system-ui, sans-serif"; x.fillStyle = "#333";
      x.fillText("desconto direto no link", L / 2, y); y += 100;
    }
    const fim = c.fim_em ? new Date(c.fim_em) : null;
    if (fim && !isNaN(fim)) {
      x.font = "700 44px PoppinsArte, system-ui, sans-serif"; x.fillStyle = principal;
      x.fillText(`Válido até ${fim.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", timeZone: "America/Recife" })} às ${fim.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: "America/Recife" })}`, L / 2, y);
      y += 74;
    }
    if (c.regras) {
      x.font = "400 36px PoppinsArte, system-ui, sans-serif"; x.fillStyle = "#666";
      for (const linha of quebrar(x, c.regras, area, 4)) { x.fillText(linha, L / 2, y); y += 48; }
    }
    return y;
  };
  const rascunho = document.createElement("canvas").getContext("2d");
  const altura = conteudo(rascunho, 0);
  const alturaCartao = Math.min(A - 340, Math.max(1000, altura + 220));
  y0 = Math.round((A - alturaCartao) / 2); y1 = y0 + alturaCartao;
  retangulo(x, M, y0, L - M, y1, 40); x.fillStyle = "#fff"; x.fill();
  x.lineWidth = 6; x.strokeStyle = principal; x.stroke();
  selos(y0);
  conteudo(x, y0 + 80 + Math.max(0, (alturaCartao - 110 - altura) / 2));
  return { canvas: cv, semFoto: false };
}

// ---------------------------------------------------------------- janela de prévia (ofertas e cupons)
let coresCache;
async function coresDaMarca(ctx) {
  if (coresCache) return coresCache;
  const cfg = await q(ctx.sb.from("configuracoes").select("chave,valor").in("chave", ["visual.cor_principal", "visual.cor_destaque"]));
  const v = Object.fromEntries(cfg.map((c) => [c.chave, c.valor]));
  const cor = (x) => (/^#[0-9a-f]{6}$/i.test(x || "") ? x : null);
  return (coresCache = { principal: cor(v["visual.cor_principal"]), destaque: cor(v["visual.cor_destaque"]) });
}

/** Abre a prévia do story com "Compartilhar / salvar" e "Copiar link do adesivo". */
export async function janelaStory(ctx, { desenhar, nome, link, nota = "" }) {
  const janela = document.createElement("div");
  janela.className = "janela-story";
  janela.setAttribute("role", "dialog"); janela.setAttribute("aria-modal", "true"); janela.setAttribute("aria-label", "Story para o Instagram");
  janela.innerHTML = '<div class="caixa"><p class="muted centro">Montando o story…</p></div>';
  document.body.appendChild(janela);
  const fechar = () => janela.remove();
  janela.addEventListener("click", (e) => { if (e.target === janela) fechar(); });
  try {
    const { canvas, semFoto } = await desenhar(await coresDaMarca(ctx));
    const blob = await new Promise((ok) => canvas.toBlob(ok, "image/jpeg", 0.92));
    const url = URL.createObjectURL(blob);
    janela.innerHTML = `<div class="caixa">
      <img src="${url}" alt="Prévia do story">
      ${semFoto ? '<p class="m" style="color:var(--aviso)">A loja não liberou a foto para o painel: o story saiu sem ela. Você pode pôr a foto por cima no Instagram.</p>' : ""}
      <button class="b p grande" data-x="compartilhar">📲 Compartilhar / salvar imagem</button>
      ${link ? '<button class="b grande" data-x="copiar">🔗 Copiar link do adesivo</button>' : '<p class="m">Sem link de afiliado: o story divulga só o código.</p>'}
      <p class="m">No Instagram: crie o story com a imagem${link ? ", toque no ícone de adesivos → <b>Link</b>, cole o link e ponha o adesivo em cima da faixa “Clique aqui”" : ""}.
        ${nota ? `<br>${esc(nota)}` : ""}</p>
      <button class="b grande" data-x="fechar">Fechar</button></div>`;
    janela.addEventListener("click", async (e) => {
      const x = e.target.closest("[data-x]")?.dataset.x;
      if (x === "fechar") { URL.revokeObjectURL(url); fechar(); }
      if (x === "compartilhar") {
        const r = await compartilharStory(blob, nome);
        if (r === "baixado") aviso("Imagem baixada ✔ (no iPhone, também dá para tocar e segurar a prévia)");
      }
      if (x === "copiar") {
        try { await navigator.clipboard.writeText(link); aviso("Link copiado ✔"); }
        catch { prompt("Copie o link:", link); }
      }
    });
  } catch (e) {
    janela.innerHTML = `<div class="caixa"><p>Erro ao montar o story: ${esc(e.message)}</p><button class="b grande" data-x="fechar">Fechar</button></div>`;
    janela.querySelector("[data-x]").addEventListener("click", fechar);
  }
}
