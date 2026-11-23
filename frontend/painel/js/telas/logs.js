// Logs: rodadas do bot (execuções) e os registros de cada uma. Os segredos já chegam mascarados.
import { q } from "../api.js";
import { $, $$, dataHora, esc, haQuanto } from "../ui.js";

let nivel = "", execucao = null;

export async function montar(el, ctx) {
  const execucoes = await q(ctx.sb.from("execucoes").select("id,origem,modo,status,iniciada_em,finalizada_em,resumo")
    .order("iniciada_em", { ascending: false }).limit(30));
  el.innerHTML = `
    <h2>Logs</h2>
    <div class="chips" id="f-nivel">
      ${[["", "Todos"], ["AVISO", "Avisos"], ["ERRO", "Erros"]].map(([v, t]) => `<span class="chip ${v === nivel ? "on" : ""}" data-n="${v}">${t}</span>`).join("")}
    </div>
    <div class="card"><table>
      <thead><tr><th>Rodada</th><th>Situação</th><th class="n">Publicou</th></tr></thead>
      <tbody>${execucoes.map((e) => `<tr class="exec" data-id="${e.id}">
        <td>${dataHora(e.iniciada_em)}<br><span class="muted">${esc(e.origem)}${e.modo === "demo" ? " · DEMO" : ""} · ${haQuanto(e.iniciada_em)}</span></td>
        <td><span class="nivel nivel-${e.status === "sucesso" ? "INFO" : e.status === "parcial" ? "AVISO" : e.status === "erro" ? "ERRO" : "DEBUG"}">${esc(e.status)}</span>
          ${(e.resumo?.etapas_com_erro || []).length ? `<br><span class="muted">falhou: ${esc(e.resumo.etapas_com_erro.join(", "))}</span>` : ""}</td>
        <td class="n">${Number(e.resumo?.publicadas || 0)}</td></tr>`).join("") || '<tr><td colspan="3" class="muted">O bot ainda não rodou.</td></tr>'}</tbody>
    </table></div>
    <h3 id="titulo-logs">${execucao ? "Registros da rodada" : "Últimos registros"}</h3>
    <div class="card" id="registros"><p class="muted">Carregando…</p></div>`;

  $$("#f-nivel .chip", el).forEach((c) => c.addEventListener("click", () => { nivel = c.dataset.n; montar(el, ctx); }));
  $$(".exec", el).forEach((tr) => tr.addEventListener("click", () => { execucao = tr.dataset.id; carregarRegistros(el, ctx); }));
  await carregarRegistros(el, ctx);
}

async function carregarRegistros(el, ctx) {
  let consulta = ctx.sb.from("logs").select("id,nivel,modulo,mensagem,contexto,criado_em").order("id", { ascending: false }).limit(150);
  if (execucao) consulta = consulta.eq("execucao_id", execucao);
  if (nivel === "AVISO") consulta = consulta.in("nivel", ["AVISO", "ERRO", "CRITICO"]);
  if (nivel === "ERRO") consulta = consulta.in("nivel", ["ERRO", "CRITICO"]);
  const registros = await q(consulta);
  $("#titulo-logs", el).textContent = execucao ? "Registros da rodada selecionada" : "Últimos registros";
  $("#registros", el).innerHTML = registros.length ? `<table><tbody>${registros.map((r) => `<tr>
      <td style="white-space:nowrap">${dataHora(r.criado_em)}<br><span class="nivel nivel-${esc(r.nivel)}">${esc(r.nivel)}</span></td>
      <td><b>${esc(r.modulo)}</b> · ${esc(r.mensagem)}
        ${r.contexto && Object.keys(r.contexto).length ? `<div class="log-ctx">${esc(JSON.stringify(r.contexto))}</div>` : ""}</td></tr>`).join("")}</tbody></table>`
    : '<p class="muted">Nada por aqui.</p>';
}
