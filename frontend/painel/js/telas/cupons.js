// Cupons e campanhas: cadastrar cupom, pausar/reativar, produtos do cupom e sugestões do bot.
// O bot publica cada cupom válido 1 vez por dia nos stories (e no Telegram, se ligado em Ajustes).
import { q } from "../api.js";
import { $, $$, aviso, brl, dataHora, esc, numero } from "../ui.js";
import { desenharStoryCupom, janelaStory } from "../story.js";

let carregados = new Map();

let filtro = "ativo";
const ROTULO = { ativo: "Ativos", pausado: "Pausados", expirado: "Vencidos" };

export async function montar(el, ctx) {
  el.innerHTML = `
    <h2>Cupons e campanhas</h2>
    <p class="muted">Cada cupom válido vai para o Telegram <b>1 vez por dia</b> até vencer. Para os stories, toque em
      <b>📲 Story</b> e poste você mesma, com o adesivo de link. Campanhas da Shopee entram sozinhas; cupons com código você cadastra aqui.</p>
    ${ctx.podeEditar ? '<button class="b p grande" id="c-novo" style="margin-bottom:12px">➕ Novo cupom</button>' : ""}
    <div id="c-form"></div>
    <div class="chips" id="c-filtro">${Object.entries(ROTULO).map(([v, t]) =>
      `<span class="chip ${v === filtro ? "on" : ""}" data-v="${v}">${t}</span>`).join("")}</div>
    <div id="c-lista"><p class="muted">Carregando…</p></div>`;
  $$("#c-filtro .chip", el).forEach((c) => c.addEventListener("click", () => { filtro = c.dataset.v; montar(el, ctx); }));
  $("#c-novo", el)?.addEventListener("click", () => formulario(el, ctx));
  $("#c-lista", el).addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-acao]");
    if (b?.dataset.acao === "story") {
      const c = carregados.get(String(b.dataset.id));
      if (c) janelaStory(ctx, { desenhar: (cores) => desenharStoryCupom(c, cores), nome: `cupom-${c.id}.jpg`, link: c.link_afiliado || "" });
    } else if (b) acao(el, ctx, b.dataset);
  });
  await carregar(el, ctx);
}

async function carregar(el, ctx) {
  const cupons = await q(ctx.sb.from("vw_cupons_painel").select("*").eq("status", filtro).order("fim_em", { ascending: filtro !== "expirado" }).limit(100));
  if (!cupons.length) {
    $("#c-lista", el).innerHTML = `<div class="card muted">Nenhum cupom ${ROTULO[filtro].toLowerCase()}.</div>`;
    return;
  }
  carregados = new Map(cupons.map((c) => [String(c.id), c]));
  $("#c-lista", el).innerHTML = cupons.map((c) => cartao(c, ctx)).join("");
}

function descricao(c) {
  const partes = [];
  if (c.desconto_tipo === "percentual") partes.push(`${Number(c.desconto_valor)}% OFF`);
  if (c.desconto_tipo === "valor") partes.push(`${brl(c.desconto_valor)} OFF`);
  if (c.desconto_maximo) partes.push(`até ${brl(c.desconto_maximo)}`);
  if (c.valor_minimo) partes.push(`acima de ${brl(c.valor_minimo)}`);
  return partes.join(" · ");
}

function cartao(c, ctx) {
  const automatica = c.origem === "automatica";
  const acoes = !ctx.podeEditar ? "" : `
    <div class="acoes">
      ${c.status === "ativo" ? `<button class="b p" data-acao="story" data-id="${c.id}">📲 Story</button>` : ""}
      ${c.status === "ativo" ? `<button class="b" data-acao="pausar" data-id="${c.id}">⏸ Pausar</button>` : ""}
      ${c.status === "pausado" ? `<button class="b ok" data-acao="reativar" data-id="${c.id}">▶ Reativar</button>` : ""}
      <button class="b" data-acao="produtos" data-id="${c.id}" data-mkt="${esc(c.marketplace_codigo)}" data-demo="${c.demo}">
        🛍 Produtos (${Number(c.produtos_aprovados)})${Number(c.sugestoes) ? ` · ${Number(c.sugestoes)} sugestão(ões)` : ""}</button>
      ${automatica ? "" : `<button class="b" data-acao="remover" data-id="${c.id}">🗑 Remover</button>`}
    </div>`;
  return `<div class="card cupom" id="cupom-${c.id}">
    <div class="m">${esc(c.marketplace_nome)} · ${automatica ? "campanha oficial (automática)" : "cadastrado por você"}
      ${c.demo ? '<span class="tag st-demo">DEMO</span>' : ""}</div>
    ${c.codigo ? `<div class="codigo">${esc(c.codigo)}</div>` : (c.tipo === "cupom" ? '<div class="m">sem código · ativado na página do produto</div>' : "")}
    <div class="t">${esc(c.titulo)}</div>
    ${descricao(c) ? `<div class="preco">${esc(descricao(c))}</div>` : ""}
    <div class="m">Válido até ${dataHora(c.fim_em)}${c.regras ? ` · ${esc(c.regras)}` : ""}</div>
    <div class="m">Publicado ${Number(c.publicacoes)}x${c.ultima_publicacao ? `, último em ${dataHora(c.ultima_publicacao)}` : ""}
      ${c.link_afiliado ? "· com link de afiliado" : "· sem link (só o código)"}</div>
    ${acoes}
    <div class="produtos-cupom" id="prod-${c.id}"></div>
  </div>`;
}

async function acao(el, ctx, d) {
  try {
    if (d.acao === "pausar" || d.acao === "reativar") {
      await q(ctx.sb.from("cupons").update({ status: d.acao === "pausar" ? "pausado" : "ativo" }).eq("id", d.id));
      aviso(d.acao === "pausar" ? "Cupom pausado ✔" : "Cupom reativado ✔");
      return carregar(el, ctx);
    }
    if (d.acao === "remover") {
      if (!confirm("Remover este cupom? Ele para de ser publicado.")) return;
      await q(ctx.sb.from("cupons").delete().eq("id", d.id));
      aviso("Cupom removido ✔");
      return carregar(el, ctx);
    }
    if (d.acao === "produtos") return produtos(el, ctx, d.id, d.mkt, d.demo === "true");
    if (d.acao === "aprovar") {
      await q(ctx.sb.from("cupons_produtos").update({ aprovado: true }).eq("cupom_id", d.id).eq("promocao_id", d.promo));
      aviso("Produto confirmado no cupom ✔ os próximos posts dele mostram o código");
    }
    if (d.acao === "tirar") {
      await q(ctx.sb.from("cupons_produtos").delete().eq("cupom_id", d.id).eq("promocao_id", d.promo));
      aviso("Produto tirado do cupom ✔");
    }
    if (d.acao === "adicionar") {
      await q(ctx.sb.from("cupons_produtos").insert({ cupom_id: Number(d.id), promocao_id: Number(d.promo), origem: "manual" }));
      aviso("Produto adicionado ao cupom ✔");
    }
    return produtos(el, ctx, d.id, d.mkt, d.demo === "true");
  } catch (e) { aviso(e.message, "erro"); }
}

async function produtos(el, ctx, cupomId, mkt, demo) {
  const alvo = $(`#prod-${cupomId}`, el);
  alvo.innerHTML = '<p class="muted">Carregando…</p>';
  const ligados = await q(ctx.sb.from("cupons_produtos").select("promocao_id,origem,aprovado,motivo").eq("cupom_id", cupomId));
  const ids = ligados.map((l) => l.promocao_id);
  const ofertas = ids.length ? await q(ctx.sb.from("vw_promocoes_painel").select("id,titulo,preco_promocional,status").in("id", ids)) : [];
  const porId = Object.fromEntries(ofertas.map((o) => [o.id, o]));
  const linha = (l) => {
    const o = porId[l.promocao_id] || { titulo: `oferta #${l.promocao_id}` };
    const botoes = l.aprovado
      ? `<button class="b" data-acao="tirar" data-id="${cupomId}" data-promo="${l.promocao_id}" data-mkt="${esc(mkt)}" data-demo="${demo}">Tirar</button>`
      : `<button class="b ok" data-acao="aprovar" data-id="${cupomId}" data-promo="${l.promocao_id}" data-mkt="${esc(mkt)}" data-demo="${demo}">Aprovar</button>
         <button class="b" data-acao="tirar" data-id="${cupomId}" data-promo="${l.promocao_id}" data-mkt="${esc(mkt)}" data-demo="${demo}">Recusar</button>`;
    return `<div class="oferta"><div style="flex:1"><div class="t">${esc(o.titulo)}</div>
      <div class="m">${o.preco_promocional ? brl(o.preco_promocional) + " · " : ""}${l.aprovado ? "✔ vale o cupom" : `💡 sugestão do bot (${esc(l.motivo || "")})`}</div>
      <div class="acoes">${botoes}</div></div></div>`;
  };
  alvo.innerHTML = `
    <h3>Produtos em que o cupom vale</h3>
    ${ligados.length ? ligados.map(linha).join("") : '<p class="muted">Nenhum produto ainda.</p>'}
    <p class="ajuda">Sugestões do bot só aparecem nos posts depois que você aprovar: a loja não informa em quais produtos o cupom vale.</p>
    <label for="busca-${cupomId}">Adicionar produto já cadastrado</label>
    <input id="busca-${cupomId}" placeholder="Digite parte do nome…" aria-label="Buscar produto para o cupom">
    <div id="res-${cupomId}"></div>
    <p class="ajuda">Produto ainda não cadastrado? Cadastre em ➕ Nova e depois adicione aqui.</p>`;
  let t;
  $(`#busca-${cupomId}`, alvo).addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(async () => {
      const termo = e.target.value.trim().replace(/[%_]/g, "");
      const res = $(`#res-${cupomId}`, alvo);
      if (termo.length < 2) { res.innerHTML = ""; return; }
      try {
        const achados = await q(ctx.sb.from("vw_promocoes_painel").select("id,titulo,preco_promocional,marketplace")
          .eq("marketplace", mkt).eq("demo", demo).in("status", ["encontrada", "aprovada", "publicada"])
          .ilike("titulo", `%${termo}%`).limit(8));
        const livres = achados.filter((o) => !ids.includes(o.id));
        res.innerHTML = livres.length ? livres.map((o) => `<div class="oferta"><div style="flex:1"><div class="t">${esc(o.titulo)}</div>
            <div class="m">${brl(o.preco_promocional)}</div>
            <div class="acoes"><button class="b p" data-acao="adicionar" data-id="${cupomId}" data-promo="${o.id}" data-mkt="${esc(mkt)}" data-demo="${demo}">Adicionar ao cupom</button></div></div></div>`).join("")
          : '<p class="muted">Nenhum produto dessa loja com esse nome.</p>';
      } catch (err) { aviso(err.message, "erro"); }
    }, 350);
  });
}

async function formulario(el, ctx) {
  const [lojas, categorias] = await Promise.all([
    q(ctx.sb.from("marketplaces").select("id,codigo,nome,dominios_afiliado").eq("ativo", true).neq("modo", "desativado").order("nome")),
    q(ctx.sb.from("categorias").select("id,nome").eq("ativa", true).order("ordem")),
  ]);
  const amanha = new Date(Date.now() + 86400000);
  amanha.setHours(23, 59, 0, 0);
  const padraoFim = new Date(amanha.getTime() - amanha.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const alvo = $("#c-form", el);
  alvo.innerHTML = `
    <form class="card" id="form-cupom">
      <h3>Novo cupom</h3>
      <label for="cp-loja">Loja</label>
      <select id="cp-loja" required>${lojas.map((l) => `<option value="${l.id}">${esc(l.nome)}</option>`).join("")}</select>
      <label for="cp-codigo">Código do cupom <span class="m">(deixe vazio se o cupom é ativado na página, ex.: “cupom por seguir a loja”)</span></label>
      <input id="cp-codigo" maxlength="30" autocapitalize="characters" placeholder="EX.: ACHADO15" pattern="[A-Za-z0-9_\\- ]{3,30}">
      <label for="cp-titulo">Nome que aparece no post</label>
      <input id="cp-titulo" required minlength="3" maxlength="120" placeholder="Ex.: Cupom de fim de semana">
      <div class="linha">
        <div><label for="cp-tipo">Desconto</label>
          <select id="cp-tipo"><option value="percentual">% (porcentagem)</option><option value="valor">R$ (valor fixo)</option><option value="">Não informar</option></select></div>
        <div><label for="cp-valor">Valor</label><input id="cp-valor" inputmode="decimal" placeholder="15"></div>
      </div>
      <div class="linha">
        <div><label for="cp-minimo">Compra mínima (R$)</label><input id="cp-minimo" inputmode="decimal" placeholder="opcional"></div>
        <div><label for="cp-maximo">Desconto máximo (R$)</label><input id="cp-maximo" inputmode="decimal" placeholder="opcional"></div>
      </div>
      <label for="cp-fim">Válido até</label><input id="cp-fim" type="datetime-local" required value="${padraoFim}">
      <label for="cp-link">Seu link de afiliado da página do cupom (opcional)</label>
      <input id="cp-link" type="url" inputmode="url" placeholder="https://…">
      <p class="ajuda" id="cp-dominios"></p>
      <label for="cp-palavras">Palavras para o bot sugerir produtos (opcional)</label>
      <input id="cp-palavras" placeholder="ex.: fone, smartwatch, carregador">
      <label for="cp-cat">Ou categoria</label>
      <select id="cp-cat"><option value="">Nenhuma</option>${categorias.map((c) => `<option value="${c.id}">${esc(c.nome)}</option>`).join("")}</select>
      <label for="cp-regras">Regras curtas (aparecem no post)</label>
      <input id="cp-regras" maxlength="300" placeholder="ex.: Válido em produtos selecionados">
      <p></p>
      <div class="linha"><button class="b" type="button" id="cp-cancelar">Cancelar</button>
        <button class="b p" type="submit" id="cp-salvar">Salvar cupom</button></div>
    </form>`;
  const mostrar = () => {
    const l = lojas.find((x) => String(x.id) === $("#cp-loja", alvo).value);
    $("#cp-dominios", alvo).textContent = l ? `Links aceitos: ${l.dominios_afiliado.join(", ")}` : "";
  };
  $("#cp-loja", alvo).addEventListener("change", mostrar);
  mostrar();
  $("#cp-cancelar", alvo).addEventListener("click", () => (alvo.innerHTML = ""));
  $("#form-cupom", alvo).addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const tipo = $("#cp-tipo", alvo).value || null;
    const valor = numero($("#cp-valor", alvo).value);
    if (tipo && (!valor || valor <= 0)) return aviso("Informe o valor do desconto.", "erro");
    if (tipo === "percentual" && valor > 90) return aviso("Desconto acima de 90% não parece real. Confira o cupom.", "erro");
    const codigo = $("#cp-codigo", alvo).value.trim().toUpperCase().replace(/\s+/g, "") || null;
    if (!codigo && !tipo) return aviso("Cupom sem código precisa do valor do desconto (para o bot calcular o preço com cupom).", "erro");
    if (codigo && !/^[A-Z0-9_-]{3,30}$/.test(codigo)) return aviso("O código deve ter de 3 a 30 letras, números, - ou _.", "erro");
    const fim = new Date($("#cp-fim", alvo).value);
    if (!(fim > new Date())) return aviso("A validade precisa ser no futuro.", "erro");
    const botao = $("#cp-salvar", alvo);
    botao.disabled = true;
    try {
      await q(ctx.sb.from("cupons").insert({
        marketplace_id: Number($("#cp-loja", alvo).value), tipo: "cupom",
        codigo,
        titulo: $("#cp-titulo", alvo).value.trim(), desconto_tipo: tipo, desconto_valor: tipo ? valor : null,
        valor_minimo: numero($("#cp-minimo", alvo).value), desconto_maximo: numero($("#cp-maximo", alvo).value),
        fim_em: fim.toISOString(), link_afiliado: $("#cp-link", alvo).value.trim() || null,
        palavras_chave: $("#cp-palavras", alvo).value.split(",").map((p) => p.trim()).filter(Boolean).slice(0, 20),
        categoria_id: $("#cp-cat", alvo).value ? Number($("#cp-cat", alvo).value) : null,
        regras: $("#cp-regras", alvo).value.trim() || null,
      }));
      aviso("Cupom salvo ✔ ele vai para o Telegram a partir do horário dos cupons");
      alvo.innerHTML = "";
      filtro = "ativo";
      await montar(el, ctx);
    } catch (e) { aviso(e.message, "erro"); }
    botao.disabled = false;
  });
}
