// Desempenho: cliques, vendas atribuídas, comissões, aprendizado, saturação, testes A/B, conciliação de
// comissões e o porquê de cada oferta escolhida ou recusada. Os números vêm do banco (dados reais);
// quando não há dados suficientes, a tela diz isso em vez de mostrar estimativa.
import { q } from "../api.js";
import { $, $$, aviso, brl, data, dataHora, esc, numero } from "../ui.js";

const DIMENSOES = { canal: "Canal", categoria: "Categoria", periodo: "Horário", faixa_preco: "Faixa de preço" };

function agrupar(linhas, campo) {
  const g = new Map();
  for (const l of linhas) {
    const k = l[campo] || "sem informação";
    const a = g.get(k) || { publicacoes: 0, cliques: 0, vendas: 0, comissao: 0 };
    a.publicacoes += 1;
    a.cliques += Number(l.cliques || 0);
    a.vendas += Number(l.conversoes_atribuidas || 0);
    a.comissao += Number(l.comissao_atribuida || 0);
    g.set(k, a);
  }
  return [...g.entries()].sort((x, y) => y[1].cliques - x[1].cliques);
}

function tabelaSegmentos(linhas, campo, amostra) {
  const grupos = agrupar(linhas, campo);
  if (!grupos.length) return '<p class="muted">Sem publicações reais no período.</p>';
  return `<div style="overflow-x:auto"><table><thead><tr><th>${DIMENSOES[campo]}</th><th class="n">Posts</th><th class="n">Cliques</th>
    <th class="n">Cliques/post</th><th class="n">Vendas</th><th class="n">Comissão</th></tr></thead><tbody>
    ${grupos.map(([k, a]) => `<tr><td>${esc(k)}${a.publicacoes < amostra ? ' <span class="muted">(poucos dados)</span>' : ""}</td>
      <td class="n">${a.publicacoes}</td><td class="n">${a.cliques}</td>
      <td class="n">${(a.cliques / a.publicacoes).toFixed(2).replace(".", ",")}</td>
      <td class="n">${a.vendas.toFixed(1).replace(".", ",")}</td><td class="n">${brl(a.comissao)}</td></tr>`).join("")}
  </tbody></table></div>`;
}

export async function montar(el, ctx) {
  const desde = new Date(Date.now() - 30 * 86400e3).toISOString();
  const [linhas, resumos, experimentos, conciliacao, selecoes, lojas] = await Promise.all([
    q(ctx.sb.from("vw_desempenho_publicacoes").select("*").gte("publicado_em", desde).order("publicado_em", { ascending: false }).limit(5000)),
    q(ctx.sb.from("inteligencia_resumos").select("tipo,dados,calculado_em")),
    q(ctx.sb.from("experimentos").select("*").order("criado_em", { ascending: false }).limit(10)),
    q(ctx.sb.from("vw_conciliacao_comissoes").select("*").order("competencia", { ascending: false }).limit(24)),
    q(ctx.sb.from("selecoes_ofertas").select("promocao_id,decidido_em,selecionada,posicao,prioridade,receita_esperada,detalhes").order("decidido_em", { ascending: false }).limit(30)),
    q(ctx.sb.from("marketplaces").select("id,codigo,nome").neq("codigo", "demo").order("nome")),
  ]);
  const resumo = Object.fromEntries(resumos.map((r) => [r.tipo, r]));
  const ap = resumo.aprendizado?.dados;
  const sat = resumo.saturacao?.dados;
  const amostra = ap?.amostra_minima || 20;
  const tot = linhas.reduce((a, l) => ({ p: a.p + 1, c: a.c + Number(l.cliques || 0), v: a.v + Number(l.conversoes_atribuidas || 0),
                                          m: a.m + Number(l.comissao_atribuida || 0) }), { p: 0, c: 0, v: 0, m: 0 });
  const ativo = experimentos.find((e) => e.ativo);

  el.innerHTML = `
    <h2>Desempenho</h2>
    <p class="muted">Últimos 30 dias, só publicações reais. Cliques suspeitos (robôs, repetidos) ficam fora.
      Vendas e comissões vêm do relatório oficial; a venda é ligada ao post do produto feito antes dela e
      dividida igualmente entre os canais onde a oferta saiu.</p>
    <div class="grade" id="kpis">
      <div class="card kpi"><div class="l">Publicações</div><b class="v">${tot.p}</b></div>
      <div class="card kpi"><div class="l">Cliques</div><b class="v">${tot.c}</b></div>
      <div class="card kpi"><div class="l">Cliques por post</div><b class="v">${tot.p ? (tot.c / tot.p).toFixed(2).replace(".", ",") : "—"}</b></div>
      <div class="card kpi"><div class="l">Vendas atribuídas</div><b class="v">${tot.v.toFixed(1).replace(".", ",")}</b></div>
      <div class="card kpi"><div class="l">Comissão atribuída</div><b class="v">${brl(tot.m)}</b></div>
    </div>
    ${tot.p ? "" : '<p class="card muted" id="sem-dados">Ainda não há publicações reais: os indicadores aparecem depois dos primeiros posts.</p>'}

    <h3>Por segmento</h3>
    <div class="card" id="segmentos">
      <div class="linha">${Object.entries(DIMENSOES).map(([k, n], i) => `<button class="b ${i ? "" : "p"}" data-dim="${k}" type="button">${n}</button>`).join("")}</div>
      <div id="tabela-seg">${tabelaSegmentos(linhas, "canal", amostra)}</div>
    </div>

    <h3>Aprendizado e saturação</h3>
    <div class="card" id="aprendizado">
      ${ap ? `<p>Base: ${ap.publicacoes} publicações, ${ap.cliques} cliques, ${Number(ap.conversoes).toLocaleString("pt-BR")} vendas
        <span class="muted">· calculado ${dataHora(resumo.aprendizado.calculado_em)}</span></p>
        ${(ap.sem_dados || []).map((t) => `<p class="alerta aviso">⚠️ ${esc(t)}</p>`).join("")}`
        : '<p class="muted">O bot ainda não calculou o aprendizado (aparece depois da primeira rodada com a migration 019).</p>'}
      ${sat ? Object.values(sat.canais || {}).map((c) => `<p class="${c.saturado ? "alerta erro" : "muted"}">${c.saturado ? "🔴" : "🟢"} ${esc(c.explicacao)}</p>`).join("")
        + (Object.keys(sat.categorias_cheias || {}).length ? `<p class="alerta aviso">Categorias em pausa: ${Object.entries(sat.categorias_cheias).map(([c, n]) => `${esc(c)} (${n} posts)`).join(", ")}</p>` : "") : ""}
    </div>

    <h3>Teste A/B</h3>
    <div class="card" id="ab">
      ${ativo ? `<p><b>${esc(ativo.nome)}</b> · ${ativo.tipo === "legenda" ? "legenda (B = preço antes do título)" : "arte (B = fundo escuro)"}
        · desde ${data(ativo.inicio_em)}</p>
        <p id="ab-resultado">${ativo.resultado ? esc(ativo.resultado.explicacao) : "Sem resultado ainda: aguardando publicações."}</p>
        ${ctx.podeEditar ? `<button class="b" id="ab-parar" type="button">Encerrar teste</button>` : ""}`
        : `<p class="muted">Nenhum teste ativo.</p>
        ${ctx.podeEditar ? `<div class="linha"><input id="ab-nome" placeholder="Nome do teste" maxlength="80" aria-label="Nome do teste">
          <select id="ab-tipo" aria-label="Tipo"><option value="legenda">Legenda</option><option value="arte">Arte</option></select>
          <button class="b p" id="ab-criar" type="button">Iniciar teste</button></div>
          <p class="muted">Metade das ofertas recebe a variante B, por sorteio. Vencedor só com amostra mínima e diferença estatisticamente significativa (p &lt; 0,05).</p>` : ""}`}
      ${experimentos.filter((e) => !e.ativo && e.resultado).map((e) => `<p class="muted">${esc(e.nome)}: ${esc(e.resultado.explicacao)}</p>`).join("")}
    </div>

    <h3>Conciliação de comissões</h3>
    <div class="card" id="conciliacao">
      ${conciliacao.length ? `<div style="overflow-x:auto"><table><thead><tr><th>Mês da venda</th><th class="n">Pendentes</th><th class="n">Aprovadas</th>
        <th class="n">Canceladas</th><th class="n">Pagas</th><th>Situação</th></tr></thead><tbody>
        ${conciliacao.map((c) => `<tr><td>${esc(c.marketplace)} · ${data(c.competencia).slice(3)}</td>
          <td class="n">${brl(c.valor_pendente)}</td><td class="n">${brl(c.valor_confirmado)}</td>
          <td class="n">${brl(c.valor_cancelado)}</td><td class="n">${brl(c.valor_pago)}</td>
          <td>${c.repasse === "conciliado" ? "✅ repasse conferido" : c.repasse === "divergente" ? "⚠️ repasse divergente" : ""}
            ${c.pendentes_antigas ? `<br><span class="muted">⚠️ ${c.pendentes_antigas} pendente(s) há mais de 90 dias</span>` : ""}
            ${c.confirmadas_sem_repasse ? `<br><span class="muted">⚠️ ${c.confirmadas_sem_repasse} aprovada(s) sem repasse após 75 dias</span>` : ""}</td></tr>`).join("")}
        </tbody></table></div>` : '<p class="muted">Nenhuma comissão importada ainda.</p>'}
      ${ctx.ehDono ? `<form id="form-repasse" class="linha">
        <select id="rp-loja" aria-label="Loja">${lojas.map((l) => `<option value="${l.id}">${esc(l.nome)}</option>`).join("")}</select>
        <input id="rp-mes" type="month" required aria-label="Mês das vendas">
        <input id="rp-valor" inputmode="decimal" placeholder="Valor recebido" required aria-label="Valor recebido">
        <input id="rp-data" type="date" required aria-label="Data do recebimento" value="${new Date().toLocaleDateString("sv-SE")}">
        <button class="b p" type="submit">Conferir repasse</button></form>
        <p class="muted">O valor recebido é comparado com a soma das comissões aprovadas daquele mês. Só quando bate (diferença até R$ 1,00) elas viram “pagas”.</p>` : ""}
    </div>

    <h3>Por que cada oferta foi escolhida ou recusada</h3>
    <div class="card" id="selecoes">
      ${selecoes.length ? selecoes.map((s) => {
        const d = s.detalhes || {};
        return `<details><summary>${s.selecionada ? "✅" : "⛔"} Oferta #${s.promocao_id} · ${esc(d.resumo || "")}
          <span class="muted">· ${dataHora(s.decidido_em)}</span></summary>
          <ul>${(d.fatores || []).map((f) => `<li>${esc(f.nome)}: ${f.nota == null ? "—" : Math.round(f.nota * 100) + "%"} · ${esc(f.explicacao)}</li>`).join("")}</ul>
          ${(d.avisos || []).map((t) => `<p class="alerta aviso">${esc(t)}</p>`).join("")}
          ${(d.sem_dados || []).length ? `<p class="muted">Sem dados: ${(d.sem_dados || []).map(esc).join("; ")}</p>` : ""}</details>`;
      }).join("") : '<p class="muted">Nenhuma decisão registrada ainda.</p>'}
    </div>`;

  $$("[data-dim]", el).forEach((b) => b.addEventListener("click", () => {
    $$("[data-dim]", el).forEach((x) => x.classList.toggle("p", x === b));
    $("#tabela-seg", el).innerHTML = tabelaSegmentos(linhas, b.dataset.dim, amostra);
  }));
  $("#ab-criar", el)?.addEventListener("click", async () => {
    const nome = $("#ab-nome", el).value.trim();
    if (nome.length < 3) return aviso("Dê um nome ao teste (3 letras ou mais).", "erro");
    try { await q(ctx.sb.from("experimentos").insert({ nome, tipo: $("#ab-tipo", el).value })); aviso("Teste iniciado ✔"); montar(el, ctx); }
    catch (e) { aviso(e.message, "erro"); }
  });
  $("#ab-parar", el)?.addEventListener("click", async () => {
    try { await q(ctx.sb.from("experimentos").update({ ativo: false, fim_em: new Date().toISOString() }).eq("id", ativo.id)); montar(el, ctx); }
    catch (e) { aviso(e.message, "erro"); }
  });
  $("#form-repasse", el)?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const valor = numero($("#rp-valor", el).value);
    if (!(valor >= 0)) return aviso("Valor inválido.", "erro");
    try {
      const { data: r, error } = await ctx.sb.rpc("registrar_repasse", {
        p_marketplace: Number($("#rp-loja", el).value), p_competencia: $("#rp-mes", el).value + "-01",
        p_valor: valor, p_data: $("#rp-data", el).value });
      if (error) throw error;
      const linha = Array.isArray(r) ? r[0] : r;
      aviso(linha.status === "conciliado" ? `Repasse conferido ✔ (${linha.itens} comissões pagas)`
        : `Divergência de ${brl(linha.diferenca)}: esperado ${brl(linha.valor_esperado)}. Nada foi marcado como pago.`,
        linha.status === "conciliado" ? "" : "erro");
      montar(el, ctx);
    } catch (e) { aviso(e.message, "erro"); }
  });
}
