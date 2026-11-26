// Central de IA (P0): ligar/desligar, limites de gasto, tom de voz, textos para aprovar, uso e custos,
// e o editor de prompts com versões (editar = nova versão; "voltar para esta" = rollback).
// A IA só escreve a chamada e uma frase; preços, links e #publi continuam vindo do bot e cada texto é
// conferido contra os dados da oferta antes de publicar.
import { q } from "../api.js";
import { $, $$, aviso, dataHora, esc, numero } from "../ui.js";

const CHAVE_PROMPT = "copy_oferta";
const usd = (v) => `US$ ${Number(v || 0).toFixed(4).replace(".", ",")}`;
const RESULTADO = { sucesso: "✔ gerado", erro: "✖ falha do provedor", invalido: "⚠ descartado (validação)",
  sem_orcamento: "⛔ teto atingido", pausado: "⏸ pausado" };

export async function montar(el, ctx) {
  el.innerHTML = `
    <h2>Inteligência artificial</h2>
    <p class="muted">A IA escreve a <b>chamada</b> e uma <b>frase</b> de cada oferta. Preços, desconto, cupom, link e
      #publi continuam vindo do bot, e todo texto é conferido contra os dados reais: se inventar algo, é descartado.</p>
    <div id="ia-config"></div>
    <h3>Textos para aprovar</h3><div id="ia-pendentes"><p class="muted">Carregando…</p></div>
    <h3>Uso e custos</h3><div id="ia-uso"></div>
    <h3>Prompt (versões)</h3><div id="ia-prompts"></div>`;
  await Promise.all([config(el, ctx), pendentes(el, ctx), uso(el, ctx), prompts(el, ctx)]);
}

// ------------------------------------------------------------------ configuração
async function config(el, ctx) {
  const chaves = ["recursos.ia", "ia.aprovacao_manual", "ia.orcamento_diario_usd", "ia.orcamento_mensal_usd",
    "ia.max_por_rodada", "ia.modelo", "ia.tom_de_voz"];
  const linhas = await q(ctx.sb.from("configuracoes").select("chave,valor").in("chave", chaves));
  const v = Object.fromEntries(linhas.map((l) => [l.chave, l.valor]));
  if (!linhas.length) {
    $("#ia-config", el).innerHTML = '<div class="card muted">As tabelas da IA ainda não foram criadas no banco (migration 028).</div>';
    return;
  }
  const dis = ctx.ehDono ? "" : "disabled";
  $("#ia-config", el).innerHTML = `
    <form class="card" id="ia-form">
      <label class="sw" style="color:var(--tx)"><span><b>IA ligada</b> (escreve textos nas próximas rodadas)</span>
        <span class="tg"><input type="checkbox" id="ia-ligada" ${v["recursos.ia"] === true ? "checked" : ""} ${dis}><span></span></span></label>
      <label class="sw" style="color:var(--tx)"><span>Aprovar cada texto antes de usar</span>
        <span class="tg"><input type="checkbox" id="ia-aprovar" ${v["ia.aprovacao_manual"] !== false ? "checked" : ""} ${dis}><span></span></span></label>
      <div class="linha">
        <div><label for="ia-dia">Teto por dia (US$)</label><input id="ia-dia" inputmode="decimal" value="${esc(v["ia.orcamento_diario_usd"] ?? 0.5)}" ${dis}></div>
        <div><label for="ia-mes">Teto por mês (US$)</label><input id="ia-mes" inputmode="decimal" value="${esc(v["ia.orcamento_mensal_usd"] ?? 5)}" ${dis}></div>
      </div>
      <div class="linha">
        <div><label for="ia-rodada">Textos por rodada</label><input id="ia-rodada" inputmode="numeric" value="${esc(v["ia.max_por_rodada"] ?? 5)}" ${dis}></div>
        <div><label for="ia-modelo">Modelo</label><input id="ia-modelo" maxlength="60" value="${esc(v["ia.modelo"] ?? "claude-haiku-5-5")}" ${dis}></div>
      </div>
      <label for="ia-tom">Tom de voz da marca</label>
      <textarea id="ia-tom" rows="3" maxlength="500" ${dis}>${esc(v["ia.tom_de_voz"] ?? "")}</textarea>
      <p class="ajuda">A chave da API fica em Ajustes → Credenciais (“IA (Claude) · chave da API”) ou no segredo ANTHROPIC_API_KEY do GitHub.</p>
      ${ctx.ehDono ? '<button class="b p grande" type="submit">Salvar</button>' : '<p class="m">Só o dono altera estas opções.</p>'}
    </form>`;
  $("#ia-form", el).addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const dia = numero($("#ia-dia", el).value), mes = numero($("#ia-mes", el).value), rodada = numero($("#ia-rodada", el).value);
    if (!(dia >= 0 && dia <= 100) || !(mes >= 0 && mes <= 1000)) return aviso("Tetos: até US$ 100 por dia e US$ 1.000 por mês.", "erro");
    if (!(rodada >= 0 && rodada <= 50)) return aviso("Textos por rodada: de 0 a 50.", "erro");
    const novos = { "recursos.ia": $("#ia-ligada", el).checked, "ia.aprovacao_manual": $("#ia-aprovar", el).checked,
      "ia.orcamento_diario_usd": dia, "ia.orcamento_mensal_usd": mes, "ia.max_por_rodada": Math.round(rodada),
      "ia.modelo": $("#ia-modelo", el).value.trim() || "claude-haiku-5-5", "ia.tom_de_voz": $("#ia-tom", el).value.trim() };
    try {
      for (const [chave, valor] of Object.entries(novos)) await q(ctx.sb.from("configuracoes").update({ valor }).eq("chave", chave));
      aviso("Configuração da IA salva ✔");
    } catch (e) { aviso(e.message, "erro"); }
  });
}

// ------------------------------------------------------------------ aprovação
async function pendentes(el, ctx) {
  const alvo = $("#ia-pendentes", el);
  let itens;
  try {
    itens = await q(ctx.sb.from("ia_conteudos").select("id,promocao_id,chamada,texto,criado_em").eq("status", "pendente")
      .order("criado_em", { ascending: true }).limit(30));
  } catch { alvo.innerHTML = '<div class="card muted">Sem as tabelas da IA ainda.</div>'; return; }
  if (!itens.length) { alvo.innerHTML = '<div class="card muted">Nenhum texto esperando aprovação.</div>'; return; }
  const ofertas = await q(ctx.sb.from("vw_promocoes_painel").select("id,titulo,preco_promocional,marketplace_nome")
    .in("id", itens.map((i) => i.promocao_id)));
  const porId = Object.fromEntries(ofertas.map((o) => [o.id, o]));
  alvo.innerHTML = itens.map((i) => {
    const o = porId[i.promocao_id] || {};
    return `<div class="card ia-texto" id="iat-${i.id}">
      <div class="m">${esc(o.marketplace_nome || "")} · ${esc(o.titulo || "oferta " + i.promocao_id)}</div>
      ${ctx.podeEditar
        ? `<label for="iac-${i.id}">Chamada</label><input id="iac-${i.id}" maxlength="90" value="${esc(i.chamada)}">
           <label for="iax-${i.id}">Frase</label><textarea id="iax-${i.id}" rows="2" maxlength="300">${esc(i.texto)}</textarea>`
        : `<b>${esc(i.chamada)}</b><p>${esc(i.texto)}</p>`}
      ${ctx.podeEditar ? `<div class="acoes">
        <button class="b ok" data-ia="aprovar" data-id="${i.id}">✔ Aprovar</button>
        <button class="b" data-ia="rejeitar" data-id="${i.id}">✖ Rejeitar</button></div>
        <p class="m">Se editar, o texto ainda passa pela conferência (sem números, links ou promessas fora dos dados) na hora de publicar.</p>` : ""}
    </div>`;
  }).join("");
  alvo.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-ia]");
    if (!b) return;
    const id = b.dataset.id;
    try {
      const mudanca = { status: b.dataset.ia === "aprovar" ? "aprovado" : "rejeitado" };
      if (b.dataset.ia === "aprovar") {
        mudanca.chamada = $(`#iac-${id}`, el).value.trim();
        mudanca.texto = $(`#iax-${id}`, el).value.trim();
        if (mudanca.chamada.length < 3 || mudanca.texto.length < 10) return aviso("Chamada (3+) e frase (10+) não podem ficar vazias.", "erro");
      }
      await q(ctx.sb.from("ia_conteudos").update(mudanca).eq("id", id));
      $(`#iat-${id}`, el).remove();
      aviso(b.dataset.ia === "aprovar" ? "Texto aprovado ✔ entra no próximo post da oferta" : "Texto rejeitado");
    } catch (e) { aviso(e.message, "erro"); }
  });
}

// ------------------------------------------------------------------ uso e custos
async function uso(el, ctx) {
  const alvo = $("#ia-uso", el);
  const ctxSb = ctx.sb;
  try {
    const desde = new Date(Date.now() - 30 * 86400000).toISOString();
    const [chamadas, orc] = await Promise.all([
      q(ctxSb.from("ia_chamadas").select("quando,resultado,tokens_entrada,tokens_saida,custo_usd,erro").gte("quando", desde)
        .order("quando", { ascending: false }).limit(500)),
      q(ctxSb.from("orcamento_consumo").select("dia,gasto_usd,teto_usd").eq("servico", "ia").order("dia", { ascending: false }).limit(31)),
    ]);
    const mes = new Date().toISOString().slice(0, 7);
    const hoje = orc.find((o) => o.dia === new Date().toLocaleDateString("sv-SE", { timeZone: "America/Recife" }));
    const gastoMes = orc.filter((o) => String(o.dia).startsWith(mes)).reduce((s, o) => s + Number(o.gasto_usd), 0);
    const conta = (r) => chamadas.filter((c) => c.resultado === r).length;
    const tokens = chamadas.reduce((s, c) => s + c.tokens_entrada + c.tokens_saida, 0);
    const falhas = chamadas.filter((c) => c.resultado !== "sucesso").slice(0, 8);
    alvo.innerHTML = `<div class="card">
      <div class="grade">
        <div class="kpi"><div class="v">${usd(hoje?.gasto_usd)}</div><div class="l">hoje (teto ${usd(hoje?.teto_usd)})</div></div>
        <div class="kpi"><div class="v">${usd(gastoMes)}</div><div class="l">neste mês</div></div>
        <div class="kpi"><div class="v">${conta("sucesso")}</div><div class="l">textos em 30 dias</div></div>
        <div class="kpi"><div class="v">${tokens.toLocaleString("pt-BR")}</div><div class="l">tokens em 30 dias</div></div>
      </div>
      <p class="m">Descartados pela validação: ${conta("invalido")} · falhas do provedor: ${conta("erro")} · barrados pelo teto: ${conta("sem_orcamento")}</p>
      ${falhas.length ? `<table><tr><th>Quando</th><th>O que houve</th></tr>${falhas.map((f) =>
        `<tr><td class="n">${dataHora(f.quando)}</td><td>${esc(RESULTADO[f.resultado] || f.resultado)}${f.erro ? ` · ${esc(f.erro)}` : ""}</td></tr>`).join("")}</table>` : ""}
    </div>`;
  } catch { alvo.innerHTML = '<div class="card muted">Sem dados de uso ainda.</div>'; }
}

// ------------------------------------------------------------------ prompts
async function prompts(el, ctx) {
  const alvo = $("#ia-prompts", el);
  let versoes;
  try {
    versoes = await q(ctx.sb.from("ia_prompts").select("id,versao,sistema,modelo,notas,ativo,criado_em").eq("chave", CHAVE_PROMPT)
      .order("versao", { ascending: false }));
  } catch { alvo.innerHTML = '<div class="card muted">Sem as tabelas da IA ainda.</div>'; return; }
  const ativo = versoes.find((v) => v.ativo) || versoes[0];
  alvo.innerHTML = `
    ${ctx.ehDono && ativo ? `<form class="card" id="ia-pform">
      <p class="m">Editando cria a versão ${Number(versoes[0]?.versao || 0) + 1}. Variáveis: {titulo} {loja} {categoria} {preco}
        {preco_antigo} {desconto} {cupom} {preco_com_cupom} {pix} {tom_de_voz} {dados}. O modelo precisa ter {dados}.</p>
      <label for="ia-psis">Instruções (sistema)</label><textarea id="ia-psis" rows="8" maxlength="6000">${esc(ativo.sistema)}</textarea>
      <label for="ia-pmod">Pedido (modelo)</label><textarea id="ia-pmod" rows="4" maxlength="6000">${esc(ativo.modelo)}</textarea>
      <label for="ia-pnot">O que mudou</label><input id="ia-pnot" maxlength="300" placeholder="ex.: chamadas mais curtas">
      <button class="b p grande" type="submit">Salvar nova versão</button></form>` : ""}
    ${versoes.map((v) => `<div class="card"><div><b>Versão ${v.versao}</b> ${v.ativo ? '<span class="tag st-publicada">em uso</span>' : ""}
      <span class="m">${dataHora(v.criado_em)}${v.notas ? " · " + esc(v.notas) : ""}</span></div>
      ${!v.ativo && ctx.ehDono ? `<div class="acoes"><button class="b" data-pv="${v.id}">↩ Voltar para esta</button></div>` : ""}</div>`).join("")}`;
  $("#ia-pform", el)?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await q(ctx.sb.rpc("ia_prompt_salvar", { p_chave: CHAVE_PROMPT, p_sistema: $("#ia-psis", el).value,
        p_modelo: $("#ia-pmod", el).value, p_notas: $("#ia-pnot", el).value || null }));
      aviso("Nova versão salva e em uso ✔");
      prompts(el, ctx);
    } catch (e) { aviso(e.message, "erro"); }
  });
  $$("[data-pv]", alvo).forEach((b) => b.addEventListener("click", async () => {
    try { await q(ctx.sb.rpc("ia_prompt_ativar", { p_id: Number(b.dataset.pv) })); aviso("Versão restaurada ✔"); prompts(el, ctx); }
    catch (e) { aviso(e.message, "erro"); }
  }));
}
