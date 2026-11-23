// Resumo: produtos encontrados, aprovadas, publicações, pausadas, cliques, comissões e saúde do bot.
import { contar, q } from "../api.js";
import { $, $$, brl, esc, haQuanto } from "../ui.js";

let dias = 0;

export async function montar(el, { sb }) {
  el.innerHTML = `
    <div id="alertas"></div>
    <div class="chips" id="periodo">
      ${[[0, "Hoje"], [7, "7 dias"], [30, "30 dias"], [89, "90 dias"]].map(([d, t]) => `<span class="chip ${d === dias ? "on" : ""}" data-d="${d}">${t}</span>`).join("")}
    </div>
    <div class="grade">
      ${kpi("encontrados", "Produtos encontrados")}${kpi("aprovadas", "Ofertas aprovadas")}
      ${kpi("publicacoes", "Publicações")}${kpi("pausadas", "Ofertas pausadas")}
      ${kpi("cliques", "Cliques")}${kpi("comissoes", "Comissões")}
      ${kpi("ctr", "Cliques por publicação")}${kpi("bot", "Última rodada do bot")}
    </div>
    <h3>Ofertas mais clicadas</h3><div class="card"><ol id="top-cliques" style="margin:0;padding-left:20px"></ol></div>
    <h3>Ofertas que mais renderam</h3><div class="card"><ol id="top-comissao" style="margin:0;padding-left:20px"></ol></div>`;
  $$("#periodo .chip", el).forEach((c) => c.addEventListener("click", () => { dias = +c.dataset.d; montar(el, { sb }); }));

  const hoje = new Date().toLocaleDateString("sv-SE");
  const inicio = new Date(Date.now() - dias * 86400000).toLocaleDateString("sv-SE");
  const desde24h = new Date(Date.now() - 86400000).toISOString();
  const [metricas, aprovadas, aguardando, pausadas, execucao, incertas] = await Promise.all([
    q(sb.from("vw_metricas_diarias").select("*").gte("dia", inicio).lte("dia", hoje)),
    contar("promocoes", (x) => x.eq("status", "aprovada")),
    contar("promocoes", (x) => x.eq("status", "encontrada")),
    contar("promocoes", (x) => x.eq("status", "pausada")),
    q(sb.from("execucoes").select("iniciada_em,status,modo,resumo").order("iniciada_em", { ascending: false }).limit(1)),
    contar("fila_publicacoes", (x) => x.eq("status", "falhou").like("ultimo_erro", "Resultado incerto%").gte("atualizado_em", desde24h)),
  ]);
  const soma = (campo) => metricas.reduce((s, m) => s + Number(m[campo] || 0), 0);
  const pubs = soma("publicacoes");
  preencher("encontrados", soma("produtos_encontrados"));
  preencher("aprovadas", aprovadas, aguardando ? `${aguardando} aguardando sua aprovação` : "na fila para publicar");
  preencher("publicacoes", pubs, `Telegram ${soma("publicacoes_telegram")} · Stories ${soma("publicacoes_stories")} · Feed ${soma("publicacoes_feed")}`);
  preencher("pausadas", pausadas, "preço subiu, esgotou ou link quebrou");
  preencher("cliques", soma("cliques"));
  preencher("comissoes", brl(soma("comissoes")));
  preencher("ctr", pubs ? (soma("cliques") / pubs).toFixed(1) : "–", "só publicações reais contam");
  const ex = execucao[0];
  if (ex) {
    const atrasado = Date.now() - new Date(ex.iniciada_em) > 30 * 60000;
    const icone = { sucesso: "✔ tudo certo", parcial: "⚠ algumas etapas falharam", erro: "✖ falhou", rodando: "⏳ rodando" }[ex.status] || ex.status;
    preencher("bot", haQuanto(ex.iniciada_em), `${atrasado ? "⚠ o bot parece parado · " : ""}${icone}${ex.modo === "demo" ? " · DEMO" : ""}`);
  } else preencher("bot", "nunca", "o bot ainda não rodou");
  $("#alertas", el).innerHTML = alertas(ex, incertas).map((a) => `<div class="alerta ${a[0]}">${a[1]}</div>`).join("");

  const [topC, topM] = await Promise.all([
    q(sb.from("vw_promocoes_painel").select("id,titulo,cliques,demo").gt("cliques", 0).order("cliques", { ascending: false }).limit(5)),
    q(sb.from("vw_promocoes_painel").select("id,titulo,comissao_total").gt("comissao_total", 0).order("comissao_total", { ascending: false }).limit(5)),
  ]);
  $("#top-cliques", el).innerHTML = topC.length ? topC.map((o) => `<li>${esc(o.titulo)} — <b>${o.cliques}</b> cliques</li>`).join("") : '<span class="muted">Nenhum clique ainda.</span>';
  $("#top-comissao", el).innerHTML = topM.length ? topM.map((o) => `<li>${esc(o.titulo)} — <b>${brl(o.comissao_total)}</b></li>`).join("") : '<span class="muted">Nenhuma comissão vinculada ainda.</span>';
}

// Problemas que pedem ação do dono, em linguagem simples
export function alertas(ex, incertas) {
  const lista = [];
  if (!ex) return lista;
  const minutos = (Date.now() - new Date(ex.iniciada_em)) / 60000;
  if (minutos > 30) lista.push(["erro", `⛔ O bot não roda há ${Math.round(minutos)} minutos. Confira a aba Actions do GitHub (o agendamento pode ter sido desligado).`]);
  const bloqueados = Object.entries((ex.resumo || {}).canais_bloqueados || {});
  for (const [canal, motivo] of bloqueados) {
    lista.push(["erro", `🔑 ${esc(canal)}: ${esc(motivo)}. As ofertas continuam na fila e saem assim que a credencial for corrigida.`]);
  }
  if ((ex.resumo || {}).interrompida) lista.push(["aviso", "⚠ A última rodada foi interrompida antes de terminar (tempo limite ou queda). A próxima continua de onde parou."]);
  if (incertas) lista.push(["aviso", `⚠ ${incertas} publicação(ões) com resultado incerto nas últimas 24 h: confira o canal. Elas não são repetidas sozinhas para não duplicar posts.`]);
  return lista;
}

const kpi = (id, rotulo) => `<div class="card kpi"><div class="l">${rotulo}</div><div class="v" id="k-${id}">–</div><div class="s" id="s-${id}"></div></div>`;
function preencher(id, valor, sub = "") {
  document.getElementById("k-" + id).textContent = valor;
  document.getElementById("s-" + id).textContent = sub;
}
