// Ajustes: configurações automáticas, categorias, integrações (modo e domínios) e credenciais (só dono).
import { q } from "../api.js";
import { $, $$, aviso, dataHora, esc } from "../ui.js";

const GRUPOS = {
  publicacao: "Publicação e agendamento", filtros: "Filtros de ofertas", monitor: "Monitor de preço e links",
  transparencia: "Transparência", textos: "Textos", visual: "Visual das artes", sistema: "Sistema", logs: "Logs", busca: "Busca", inteligencia: "Motor de inteligência (pontuação 0–100)", cupons: "Cupons e campanhas",
};
const CREDENCIAIS = [
  ["shopee", "APP_ID", "Shopee · App ID"], ["shopee", "SECRET", "Shopee · Secret"],
  ["telegram", "TOKEN", "Telegram · token do bot"], ["telegram", "CANAL", "Telegram · @canal"],
  ["instagram", "TOKEN", "Instagram · token"], ["instagram", "USER_ID", "Instagram · ID da conta"], ["imgbb", "KEY", "ImgBB · chave"],
];

export async function montar(el, ctx) {
  const [configs, categorias, mkts, canais, creds, convites] = await Promise.all([
    q(ctx.sb.from("configuracoes").select("*").order("chave")),
    q(ctx.sb.from("categorias").select("*").order("ordem")),
    q(ctx.sb.from("marketplaces").select("*").order("nome")),
    q(ctx.sb.from("canais").select("*").order("id")),
    ctx.ehDono ? q(ctx.sb.from("credenciais_integracoes").select("integracao,nome,dica,expira_em,ultimo_uso_em,atualizado_em")) : Promise.resolve([]),
    ctx.ehDono ? q(ctx.sb.from("convites").select("*").order("criado_em", { ascending: false })) : Promise.resolve([]),
  ]);
  const ro = ctx.podeEditar ? "" : "disabled";
  const porGrupo = {};
  configs.forEach((c) => (porGrupo[c.chave.split(".")[0]] ||= []).push(c));

  el.innerHTML = `
    <h2>Ajustes</h2>
    ${ctx.podeEditar ? "" : '<p class="muted">Você está como <b>leitor</b>: pode ver, mas não alterar.</p>'}
    <form id="form-cfg">
      ${Object.entries(porGrupo).map(([g, itens]) => `<h3>${esc(GRUPOS[g] || g)}</h3><div class="card">${itens.map((c) => campo(c, travada(c.chave, ctx) ? "disabled" : ro)).join("")}</div>`).join("")}
      ${ctx.podeEditar ? '<p></p><button class="b p grande" type="submit">Salvar ajustes</button>' : ""}
    </form>

    <h3>Categorias</h3>
    <div id="cats">${categorias.map((c) => categoria(c, ro)).join("")}</div>
    ${ctx.podeEditar ? '<button class="b" id="nova-cat" type="button">+ Nova categoria</button>' : ""}

    <h3>Integrações</h3>
    <div class="card">
      <p class="muted" style="margin-top:0">O modo é definido pelo bot: <b>real</b> quando as credenciais estão configuradas, <b>demo</b> quando não estão.</p>
      ${mkts.map((m) => `<div class="sw"><div><b>${esc(m.nome)}</b> <span class="tag ${m.modo === "real" ? "st-publicada" : m.modo === "demo" ? "st-demo" : "st-aprovada"}">${esc(m.modo)}</span>
        <label for="dom-${m.id}">Domínios de afiliado (separados por vírgula)</label>
        <input id="dom-${m.id}" data-mkt="${m.id}" value="${esc((m.dominios_afiliado || []).join(", "))}" ${ctx.ehDono && m.codigo !== "demo" ? "" : "disabled"}></div></div>`).join("")}
      ${ctx.ehDono ? "" : '<p class="muted">Domínios e identificadores de afiliado: só o dono altera.</p>'}
      ${canais.map((c) => `<div class="sw"><div><b>${esc(c.nome)}</b> <span class="tag ${c.modo === "real" ? "st-publicada" : "st-demo"}">${esc(c.modo)}</span>
        <div class="muted">limite por dia: ${c.limite_diario ?? "sem limite"}</div></div>
        <label class="tg" title="Ativo"><input type="checkbox" data-canal="${c.id}" ${c.ativo ? "checked" : ""} ${ro}><span></span></label></div>`).join("")}
      ${ctx.podeEditar ? '<p></p><button class="b" id="salvar-integ" type="button">Salvar domínios e canais</button>' : ""}
    </div>

    ${ctx.ehDono ? `
    <h3>Credenciais (cifradas no cofre)</h3>
    <div class="card">
      <p class="muted" style="margin-top:0">Ficam cifradas no Supabase Vault. O painel nunca mostra o valor, só os 4 últimos caracteres.
        Credenciais nos Secrets do GitHub têm prioridade sobre as daqui.</p>
      ${CREDENCIAIS.map(([i, n, rotulo]) => {
        const atual = creds.find((c) => c.integracao === i && c.nome === n);
        return `<div class="sw"><div style="flex:1"><b>${esc(rotulo)}</b>
          <div class="muted">${atual ? `salva ${esc(atual.dica || "")} · atualizada ${dataHora(atual.atualizado_em)}` : "não cadastrada"}</div>
          <div class="linha" style="margin-top:6px"><input type="password" autocomplete="off" data-cred="${i}.${n}" placeholder="novo valor">
            <div><button class="b" type="button" data-salvar-cred="${i}.${n}">Salvar</button>
            ${atual ? `<button class="b erro" type="button" data-remover-cred="${i}.${n}">Remover</button>` : ""}</div></div></div></div>`;
      }).join("")}
    </div>

    <h3>Convites (cadastro fechado)</h3>
    <div class="card" id="convites">
      <p class="muted" style="margin-top:0">Só e-mails convidados conseguem criar conta. O convite vale 7 dias.</p>
      <div class="linha"><input id="cv-email" type="email" placeholder="email@exemplo.com" aria-label="E-mail do convidado">
        <select id="cv-papel" aria-label="Papel"><option value="leitor">leitor</option><option value="editor">editor</option></select>
        <button class="b" id="cv-criar" type="button">Convidar</button></div>
      <div id="cv-lista">${convites.map((c) => `<div class="sw" id="cv-${esc(c.email)}"><div><b>${esc(c.email)}</b> · ${esc(c.papel)}
        <div class="muted">${c.usado_em ? "usado em " + dataHora(c.usado_em) : "vale até " + dataHora(c.expira_em)}</div></div>
        ${c.usado_em ? "" : `<button class="b erro" type="button" data-cancelar-convite="${esc(c.email)}">Cancelar</button>`}</div>`).join("") || '<p class="muted">Nenhum convite.</p>'}</div>
    </div>` : ""}`;

  // ---- convites (dono)
  $("#cv-criar", el)?.addEventListener("click", async () => {
    const email = $("#cv-email", el).value.trim().toLowerCase();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return aviso("Digite um e-mail válido.", "erro");
    try { await q(ctx.sb.from("convites").insert({ email, papel: $("#cv-papel", el).value })); aviso("Convite criado ✔"); montar(el, ctx); }
    catch (e) { aviso(e.message, "erro"); }
  });
  $$("[data-cancelar-convite]", el).forEach((b) => b.addEventListener("click", async () => {
    try { await q(ctx.sb.from("convites").delete().eq("email", b.dataset.cancelarConvite)); montar(el, ctx); }
    catch (e) { aviso(e.message, "erro"); }
  }));

  // ---- configurações
  $("#form-cfg", el)?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const mudancas = [];
    for (const c of configs) {
      const input = el.querySelector(`[data-chave="${CSS.escape(c.chave)}"]`);
      if (!input || input.disabled) continue;
      let valor;
      if (c.tipo === "booleano") valor = input.checked;
      else if (c.tipo === "numero") {
        valor = Number(String(input.value).replace(",", "."));
        if (!Number.isFinite(valor)) return aviso(`Valor inválido em "${c.descricao || c.chave}".`, "erro");
        if ((c.minimo != null && valor < c.minimo) || (c.maximo != null && valor > c.maximo))
          return aviso(`"${c.descricao || c.chave}" deve ficar entre ${c.minimo} e ${c.maximo}.`, "erro");
      } else if (c.tipo === "lista") valor = input.value.split(",").map((s) => s.trim()).filter(Boolean);
      else valor = input.value;
      if (JSON.stringify(valor) !== JSON.stringify(c.valor)) mudancas.push({ chave: c.chave, valor });
    }
    if (!mudancas.length) return aviso("Nada para salvar.");
    try {
      for (const m of mudancas) await q(ctx.sb.from("configuracoes").update({ valor: m.valor }).eq("chave", m.chave));
      aviso(`${mudancas.length} ajuste(s) salvo(s) ✔ valem a partir da próxima rodada`);
      montar(el, ctx);
    } catch (e) { aviso(e.message, "erro"); }
  });

  // ---- categorias
  $("#nova-cat", el)?.addEventListener("click", async () => {
    const nome = prompt("Nome da nova categoria:");
    if (!nome) return;
    const slug = nome.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    try { await q(ctx.sb.from("categorias").insert({ nome: nome.trim(), slug, ordem: categorias.length + 1 })); montar(el, ctx); }
    catch (e) { aviso(e.message, "erro"); }
  });
  $$("[data-salvar-cat]", el).forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.salvarCat, caixa = b.closest(".cat");
    try {
      await q(ctx.sb.from("categorias").update({
        nome: $("[data-nome]", caixa).value.trim(), ativa: $("[data-ativa]", caixa).checked,
        palavras_chave: $("[data-palavras]", caixa).value.split(",").map((s) => s.trim()).filter(Boolean),
      }).eq("id", id));
      aviso("Categoria salva ✔");
    } catch (e) { aviso(e.message, "erro"); }
  }));
  $$("[data-apagar-cat]", el).forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Apagar esta categoria? Os produtos ficam sem categoria.")) return;
    try { await q(ctx.sb.from("categorias").delete().eq("id", b.dataset.apagarCat)); montar(el, ctx); }
    catch (e) { aviso(e.message, "erro"); }
  }));

  // ---- integrações
  $("#salvar-integ", el)?.addEventListener("click", async () => {
    try {
      for (const input of $$("[data-mkt]", el)) {
        if (input.disabled) continue;
        const dominios = input.value.split(",").map((s) => s.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "")).filter(Boolean);
        await q(ctx.sb.from("marketplaces").update({ dominios_afiliado: dominios }).eq("id", input.dataset.mkt));
      }
      for (const chk of $$("[data-canal]", el)) await q(ctx.sb.from("canais").update({ ativo: chk.checked }).eq("id", chk.dataset.canal));
      aviso("Integrações salvas ✔");
      montar(el, ctx);
    } catch (e) { aviso(e.message, "erro"); }
  });

  // ---- credenciais (dono)
  $$("[data-salvar-cred]", el).forEach((b) => b.addEventListener("click", async () => {
    const [integracao, nome] = b.dataset.salvarCred.split(".");
    const input = el.querySelector(`[data-cred="${integracao}.${nome}"]`);
    if (!input.value.trim()) return aviso("Digite o valor da credencial.", "erro");
    try {
      await q(ctx.sb.rpc("salvar_credencial", { p_integracao: integracao, p_nome: nome, p_valor: input.value.trim() }));
      input.value = "";
      aviso("Credencial salva no cofre ✔ vale a partir da próxima rodada");
      montar(el, ctx);
    } catch (e) { aviso(e.message, "erro"); }
  }));
  $$("[data-remover-cred]", el).forEach((b) => b.addEventListener("click", async () => {
    const [integracao, nome] = b.dataset.removerCred.split(".");
    if (!confirm(`Remover a credencial ${integracao}.${nome}? A integração pode voltar ao modo demonstração.`)) return;
    try { await q(ctx.sb.rpc("remover_credencial", { p_integracao: integracao, p_nome: nome })); montar(el, ctx); }
    catch (e) { aviso(e.message, "erro"); }
  }));
}

/** sistema.* muda só por RPC (botão de emergência / SQL); auth.* e o #publi só o dono altera. */
function travada(chave, ctx) {
  if (chave.startsWith("sistema.ambiente") || chave.startsWith("sistema.parada") || chave === "auth.email_dono") return true;
  return !ctx.ehDono && (chave.startsWith("auth.") || chave === "transparencia.marcar_publi");
}

function campo(c, ro) {
  const id = `cfg-${c.chave.replace(/\W/g, "-")}`;
  const rotulo = esc(c.descricao || c.chave);
  if (c.tipo === "booleano")
    return `<div class="sw"><label for="${id}" style="margin:0;color:var(--tx)">${rotulo}</label>
      <span class="tg"><input type="checkbox" id="${id}" data-chave="${esc(c.chave)}" ${c.valor ? "checked" : ""} ${ro}><span></span></span></div>`;
  if (c.tipo === "numero")
    return `<label for="${id}">${rotulo}${c.minimo != null ? ` <span class="muted">(${c.minimo} a ${c.maximo})</span>` : ""}</label>
      <input id="${id}" type="number" step="any" data-chave="${esc(c.chave)}" value="${esc(c.valor)}" ${c.minimo != null ? `min="${c.minimo}"` : ""} ${c.maximo != null ? `max="${c.maximo}"` : ""} ${ro}>`;
  if (c.chave.startsWith("visual.cor"))
    return `<label for="${id}">${rotulo}</label><input id="${id}" type="color" data-chave="${esc(c.chave)}" value="${esc(c.valor)}" style="height:44px" ${ro}>`;
  const valor = Array.isArray(c.valor) ? c.valor.join(", ") : c.valor;
  return `<label for="${id}">${rotulo}</label><input id="${id}" data-chave="${esc(c.chave)}" value="${esc(valor)}" ${ro}>`;
}

function categoria(c, ro) {
  return `<div class="cat">
    <div class="sw" style="padding-top:0"><input data-nome value="${esc(c.nome)}" aria-label="Nome" ${ro} style="font-weight:600">
      <label class="tg" title="Ativa"><input type="checkbox" data-ativa ${c.ativa ? "checked" : ""} ${ro}><span></span></label></div>
    <label>Palavras buscadas na Shopee (separadas por vírgula)</label>
    <textarea data-palavras ${ro}>${esc((c.palavras_chave || []).join(", "))}</textarea>
    ${ro ? "" : `<div class="acoes"><button class="b" type="button" data-salvar-cat="${c.id}">Salvar</button>
      <button class="b erro" type="button" data-apagar-cat="${c.id}">Apagar</button></div>`}</div>`;
}
