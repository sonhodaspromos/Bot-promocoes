// Ofertas: filtrar, aprovar, rejeitar, pausar e reativar.
import { q } from "../api.js";
import { $, $$, ROTULO_STATUS, aviso, brl, dataHora, esc, urlSegura } from "../ui.js";
import { compartilharStory, desenharStory, linkDoStory } from "../story.js";

const POR_PAGINA = 30;
let itensCarregados = new Map();
let filtro = { status: "encontrada", categoria: "", busca: "", classe: "", ordem: "recentes" }, pagina = 0;
const CLASSES = { excelente: ["Excelente oportunidade", "cl-excelente"], boa: ["Boa oportunidade", "cl-boa"],
  nao_recomendada: ["Oferta não recomendada", "cl-nao"] };

export async function montar(el, ctx) {
  const categorias = await q(ctx.sb.from("categorias").select("id,nome").order("ordem"));
  el.innerHTML = `
    <div class="chips" id="f-status">
      ${["encontrada", "aprovada", "publicada", "pausada", "rejeitada", "expirada", ""].map((s) =>
        `<span class="chip ${s === filtro.status ? "on" : ""}" data-s="${s}">${s ? ROTULO_STATUS[s] : "Todas"}</span>`).join("")}
    </div>
    <div class="linha" style="margin-bottom:10px">
      <input id="f-busca" placeholder="Buscar produto…" value="${esc(filtro.busca)}" aria-label="Buscar produto">
      <select id="f-cat" aria-label="Categoria"><option value="">Todas as categorias</option>
        ${categorias.map((c) => `<option value="${c.id}" ${String(c.id) === filtro.categoria ? "selected" : ""}>${esc(c.nome)}</option>`).join("")}</select>
    </div>
    <div class="linha" style="margin-bottom:10px">
      <select id="f-classe" aria-label="Classificação">
        ${[["", "Todas as classificações"], ["excelente", "⭐ Excelentes"], ["boa", "👍 Boas"], ["nao_recomendada", "⛔ Não recomendadas"], ["sem", "⏳ Aguardando análise"]]
          .map(([v, t]) => `<option value="${v}" ${v === filtro.classe ? "selected" : ""}>${t}</option>`).join("")}</select>
      <select id="f-ordem" aria-label="Ordenar">
        <option value="recentes" ${filtro.ordem === "recentes" ? "selected" : ""}>Mais recentes</option>
        <option value="pontuacao" ${filtro.ordem === "pontuacao" ? "selected" : ""}>Maior pontuação</option></select>
    </div>
    ${ctx.podeEditar ? '<button class="b ok hide" id="aprovar-todas" style="margin-bottom:10px">Aprovar todas as que aguardam</button>' : ""}
    <div class="card" id="lista"></div>
    <p class="centro"><button class="b hide" id="mais">Carregar mais</button></p>`;

  $$("#f-status .chip", el).forEach((c) => c.addEventListener("click", () => { filtro.status = c.dataset.s; montar(el, ctx); }));
  let t; $("#f-busca", el).addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { filtro.busca = e.target.value.trim(); carregar(el, ctx, true); }, 350); });
  $("#f-cat", el).addEventListener("change", (e) => { filtro.categoria = e.target.value; carregar(el, ctx, true); });
  $("#f-classe", el).addEventListener("change", (e) => { filtro.classe = e.target.value; carregar(el, ctx, true); });
  $("#f-ordem", el).addEventListener("change", (e) => { filtro.ordem = e.target.value; carregar(el, ctx, true); });
  $("#mais", el).addEventListener("click", () => carregar(el, ctx, false));
  $("#aprovar-todas", el)?.addEventListener("click", async () => {
    if (!confirm("Aprovar todas as ofertas que aguardam aprovação?")) return;
    try { await q(ctx.sb.from("promocoes").update({ status: "aprovada" }).eq("status", "encontrada")); aviso("Todas aprovadas ✔"); carregar(el, ctx, true); }
    catch (e) { aviso(e.message, "erro"); }
  });
  $("#lista", el).addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-acao]");
    if (b?.dataset.acao === "story") abrirStory(ctx, itensCarregados.get(String(b.dataset.id)));
    else if (b) acao(el, ctx, b.dataset.id, b.dataset.acao);
  });
  await carregar(el, ctx, true);
}

async function carregar(el, ctx, reiniciar) {
  if (reiniciar) { pagina = 0; itensCarregados = new Map(); $("#lista", el).innerHTML = '<p class="muted">Carregando…</p>'; }
  let consulta = ctx.sb.from("vw_promocoes_painel").select("*");
  if (filtro.status) consulta = consulta.eq("status", filtro.status);
  if (filtro.categoria) consulta = consulta.eq("categoria_id", filtro.categoria);
  if (filtro.busca) consulta = consulta.ilike("titulo", `%${filtro.busca.replace(/[%_]/g, "")}%`);
  if (filtro.classe === "sem") consulta = consulta.is("classificacao", null);
  else if (filtro.classe) consulta = consulta.eq("classificacao", filtro.classe);
  if (filtro.ordem === "pontuacao") consulta = consulta.order("pontuacao", { ascending: false, nullsFirst: false });
  consulta = consulta.order(filtro.status === "publicada" ? "publicada_em" : "criado_em", { ascending: false })
    .range(pagina * POR_PAGINA, pagina * POR_PAGINA + POR_PAGINA - 1);
  try {
    const itens = await q(consulta);
    itens.forEach((o) => itensCarregados.set(String(o.id), o));
    const html = itens.map((o) => cartao(o, ctx)).join("");
    if (reiniciar) $("#lista", el).innerHTML = html || '<p class="muted">Nenhuma oferta aqui.</p>';
    else $("#lista", el).insertAdjacentHTML("beforeend", html);
    $("#mais", el).classList.toggle("hide", itens.length < POR_PAGINA);
    $("#aprovar-todas", el)?.classList.toggle("hide", !(filtro.status === "encontrada" && itens.length));
    pagina++;
  } catch (e) { $("#lista", el).innerHTML = `<p class="muted">Erro: ${esc(e.message)}</p>`; }
}

const ACOES = {
  encontrada: [["aprovar", "Aprovar", "ok"], ["rejeitar", "Rejeitar", "erro"]],
  aprovada: [["rejeitar", "Tirar da fila", "erro"]],
  publicada: [["pausar", "Pausar", "erro"]],
  pausada: [["reativar", "Reativar", "ok"]],
  rejeitada: [["aprovar", "Aprovar", "ok"]],
};

function cartao(o, ctx) {
  const acoes = ctx.podeEditar ? ACOES[o.status] || [] : [];
  const foto = /^https:\/\//.test(o.imagem_url || "") ? `<img src="${esc(o.imagem_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '<div class="sem-foto"></div>';
  return `<div class="oferta" id="of-${o.id}">${foto}
    <div style="flex:1;min-width:0">
      <div><span class="tag st-${o.status}">${ROTULO_STATUS[o.status] || o.status}</span>${o.demo ? '<span class="tag st-demo">DEMO</span>' : ""}
        <span class="tag ${o.link_validado ? "st-link-ok" : "st-link-pendente"}">${o.link_validado ? "link validado" : "link aguardando validação"}</span></div>
      <div class="t">${esc(o.titulo)}</div>
      <div><span class="preco">${brl(o.preco_promocional)}</span>${o.preco_referencia ? `<span class="antigo">${brl(o.preco_referencia)}</span>` : ""}
        ${Number(o.desconto_pct) ? ` <b>-${Math.round(o.desconto_pct)}%</b>` : ""}${o.comissao_pct ? ` <span class="m">· comissão ${o.comissao_pct}%</span>` : ""}</div>
      <div class="m">${esc(o.marketplace_nome)} · ${esc(o.categoria || "Sem categoria")} · 👆 ${o.cliques} · 💰 ${brl(o.comissao_total)} · ${dataHora(o.publicada_em || o.criado_em)}</div>
      ${selo(o)}
      ${o.motivo_status ? `<div class="m" style="color:var(--erro)">Motivo: ${esc(o.motivo_status)}</div>` : ""}
      ${explicacao(o)}
      <div class="acoes">${acoes.map(([a, t, c]) => `<button class="b ${c}" data-acao="${a}" data-id="${o.id}">${t}</button>`).join("")}
        ${["aprovada", "publicada"].includes(o.status) && o.link_url ? `<button class="b p" data-acao="story" data-id="${o.id}">📲 Story</button>` : ""}
        ${o.link_url ? `<a class="b" href="${esc(urlSegura(o.link_url))}" target="_blank" rel="noopener noreferrer">Abrir link</a>` : ""}</div>
    </div></div>`;
}

async function acao(el, ctx, id, tipo) {
  const dados = {
    aprovar: { status: "aprovada" },
    rejeitar: { status: "rejeitada", motivo_status: "Rejeitada no painel" },
    pausar: { status: "pausada", motivo_status: "Pausada no painel" },
    reativar: { status: "aprovada" },
  }[tipo];
  if (tipo === "pausar" && !confirm("Pausar esta oferta? Ela deixa de ser divulgada e a mensagem no Telegram é marcada como encerrada.")) return;
  try {
    await q(ctx.sb.from("promocoes").update(dados).eq("id", id));
    document.getElementById("of-" + id)?.remove();
    aviso({ aprovar: "Aprovada ✔ vai para a fila", rejeitar: "Rejeitada", pausar: "Pausada", reativar: "Reativada ✔ vai para a fila" }[tipo]);
  } catch (e) { aviso(e.message, "erro"); }
}

function selo(o) {
  if (!o.classificacao) return '<div class="selo cl-sem">⏳ Aguardando análise do motor</div>';
  const [rotulo, classe] = CLASSES[o.classificacao] || [o.classificacao, ""];
  const aviso = o.classificacao === "nao_recomendada" && ["encontrada", "aprovada"].includes(o.status)
    ? '<div class="m">Mesmo aprovada, ela não é publicada enquanto o motor não recomendar.</div>' : "";
  return `<div class="selo ${classe}"><b>${Number(o.pontuacao)}</b>/100 · ${esc(rotulo)}
    <span class="m">· confiança ${Number(o.confianca ?? 0)}%</span></div>${aviso}`;
}

function explicacao(o) {
  const a = o.analise;
  if (!a || !a.componentes) return "";
  const lista = (itens, cls) => (itens || []).map((t) => `<li class="${cls}">${esc(t)}</li>`).join("");
  const comps = a.componentes.map((c) => `<tr><td>${esc(c.nome)}</td>
      <td class="n">${c.nota === null ? "—" : `${Number(c.pontos).toFixed(1)}/${Number(c.peso)}`}</td>
      <td class="m">${esc(c.explicacao)}</td></tr>`).join("");
  return `<details class="analise"><summary>Por que esta classificação?</summary>
    <ul class="motivos">${lista(a.bloqueios, "bloqueio")}${lista(a.motivos, "motivo")}${lista(a.alertas, "alerta")}</ul>
    <table class="comps"><thead><tr><th>Quesito</th><th class="n">Pontos</th><th>Detalhe</th></tr></thead><tbody>${comps}</tbody></table>
    <p class="m">Desconto real: ${a.desconto_real == null ? "não calculado" : esc(String(a.desconto_real).replace(".", ",")) + "%"}
      · base: ${esc(a.base_desconto || "—")}. Quesitos com “—” não tinham dados confiáveis e ficaram fora da conta.</p>
    ${historicoPrecos(a.historico_precos)}
  </details>`;
}

function historicoPrecos(h) {
  if (!h || !h.janelas) return "";
  const linha = (d) => {
    const j = h.janelas[d];
    if (!j || j.minimo == null) return `<tr><td>${d} dias</td><td colspan="3" class="m">sem preço observado</td></tr>`;
    return `<tr><td>${d} dias</td><td class="n">${brl(j.minimo)}</td><td class="n">${brl(j.maximo)}</td>
      <td class="m">${j.suficiente ? `${Math.round(j.cobertura_pct)}% observado` : `insuficiente (${Math.round(j.cobertura_pct)}% observado): sem conclusões`}</td></tr>`;
  };
  return `<table class="comps historico"><thead><tr><th>Histórico</th><th class="n">Mínimo</th><th class="n">Máximo</th><th>Cobertura</th></tr></thead>
    <tbody>${[7, 30, 90].map(linha).join("")}</tbody></table>
    <p class="m">${h.menor_preco != null ? `Menor preço observado: ${brl(h.menor_preco)} (monitorado há ${Math.round(h.dias_monitorados)} dias).` : ""}
      ${(h.desconto_artificial || []).map((t) => `<br>⚠️ ${esc(t)}`).join("")}
      ${(h.sem_dados || []).map((t) => `<br>Sem dados: ${esc(t)}`).join("")}</p>`;
}

// ---- story manual: prévia, compartilhar/salvar e copiar o link do adesivo ----
let cores;
async function abrirStory(ctx, o) {
  if (!o) return;
  const janela = document.createElement("div");
  janela.className = "janela-story";
  janela.setAttribute("role", "dialog"); janela.setAttribute("aria-modal", "true"); janela.setAttribute("aria-label", "Story para o Instagram");
  janela.innerHTML = '<div class="caixa"><p class="muted centro">Montando o story…</p></div>';
  document.body.appendChild(janela);
  const fechar = () => janela.remove();
  janela.addEventListener("click", (e) => { if (e.target === janela) fechar(); });
  try {
    if (!cores) {
      const cfg = await q(ctx.sb.from("configuracoes").select("chave,valor").in("chave", ["visual.cor_principal", "visual.cor_destaque"]));
      const v = Object.fromEntries(cfg.map((c) => [c.chave, c.valor]));
      const cor = (x) => (/^#[0-9a-f]{6}$/i.test(x || "") ? x : null);
      cores = { principal: cor(v["visual.cor_principal"]), destaque: cor(v["visual.cor_destaque"]) };
    }
    const { canvas, semFoto } = await desenharStory(o, cores);
    const blob = await new Promise((ok) => canvas.toBlob(ok, "image/jpeg", 0.92));
    const url = URL.createObjectURL(blob), link = linkDoStory(o);
    janela.innerHTML = `<div class="caixa">
      <img src="${url}" alt="Prévia do story">
      ${semFoto ? '<p class="m" style="color:var(--aviso)">A loja não liberou a foto para o painel: o story saiu sem ela. Você pode pôr a foto por cima no Instagram.</p>' : ""}
      <button class="b p grande" data-x="compartilhar">📲 Compartilhar / salvar imagem</button>
      <button class="b grande" data-x="copiar" ${link ? "" : "disabled"}>🔗 Copiar link do adesivo</button>
      <p class="m">No Instagram: crie o story com a imagem, toque no ícone de adesivos → <b>Link</b>, cole o link e ponha o adesivo em cima da faixa “Clique aqui”.
        ${o.link_validado ? "" : "<br>Este link ainda não foi conferido pelo bot; os cliques do story não entram nas estatísticas."}</p>
      <button class="b grande" data-x="fechar">Fechar</button></div>`;
    janela.addEventListener("click", async (e) => {
      const x = e.target.closest("[data-x]")?.dataset.x;
      if (x === "fechar") { URL.revokeObjectURL(url); fechar(); }
      if (x === "compartilhar") {
        const r = await compartilharStory(blob, `story-${o.id}.jpg`);
        if (r === "baixado") aviso("Imagem baixada ✔ (no iPhone, também dá para tocar e segurar a prévia)");
      }
      if (x === "copiar") {
        try { await navigator.clipboard.writeText(link); aviso("Link copiado ✔"); }
        catch { prompt("Copie o link:", link); }
      }
    });
  } catch (e) { janela.innerHTML = `<div class="caixa"><p>Erro ao montar o story: ${esc(e.message)}</p><button class="b grande" data-x="fechar">Fechar</button></div>`;
    janela.querySelector("[data-x]").addEventListener("click", fechar); }
}
