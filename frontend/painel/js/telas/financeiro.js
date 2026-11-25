// Financeiro: custos por serviço e receita ESTIMADA x CONFIRMADA x lucro líquido.
// Regras: pendente é estimativa (pode ser cancelada), nunca receita. Lucro líquido só sobre a receita
// confirmada. Custos estão em US$ e comissões em R$: a conversão usa a cotação que VOCÊ informa
// (sem cotação, o lucro não é calculado — nada inventado).
import { q } from "../api.js";
import { $, aviso, brl, esc, guardar, numero } from "../ui.js";

const usd = (v) => Number(v || 0).toLocaleString("pt-BR", { style: "currency", currency: "USD" });
const mesTxt = (m) => new Date(m + "T12:00").toLocaleDateString("pt-BR", { month: "short", year: "numeric" });

export async function montar(el, ctx) {
  const [meses, custos, orc] = await Promise.all([
    q(ctx.sb.from("vw_financeiro_mensal").select("*").order("mes", { ascending: false }).limit(12)),
    q(ctx.sb.from("custos_servicos").select("id,mes,servico,valor_usd,origem,observacao").order("mes", { ascending: false }).limit(60)),
    q(ctx.sb.from("orcamento_consumo").select("servico,dia,gasto_usd,teto_usd").order("dia", { ascending: false }).limit(10)),
  ]);
  const cot = numero(guardar("cotacao_usd_brl"));
  const lucro = (m) => (cot ? Number(m.receita_confirmada_brl) - (Number(m.custos_faturados_usd) + Number(m.whatsapp_consumido_usd)) * cot : null);
  el.innerHTML = `
    <h2>Financeiro</h2>
    <p class="muted">Receita <b>estimada</b> = comissões pendentes (podem ser canceladas). Receita <b>confirmada</b> = confirmadas e pagas.
    O lucro líquido usa só a confirmada, menos os custos faturados e o WhatsApp consumido.</p>
    <div class="card"><label for="f-cot">Cotação do dólar (R$ por US$) para calcular o lucro</label>
      <input id="f-cot" inputmode="decimal" placeholder="ex.: 5,40" value="${cot ?? ""}">
      <p class="muted">${cot ? "Usando a cotação informada por você." : "Sem cotação informada: o lucro em R$ não é calculado."}</p></div>
    <div class="card"><table>
      <thead><tr><th>Mês</th><th class="n">Estimada</th><th class="n">Confirmada</th><th class="n">Recebida</th><th class="n">Custos (fatura)</th><th class="n">WhatsApp</th><th class="n">Lucro líquido</th></tr></thead>
      <tbody>${meses.length ? meses.map((m) => `<tr>
        <td>${esc(mesTxt(m.mes))}</td><td class="n muted">${brl(m.receita_estimada_brl)}</td><td class="n">${brl(m.receita_confirmada_brl)}</td>
        <td class="n">${brl(m.receita_recebida_brl)}</td><td class="n">${usd(m.custos_faturados_usd)}${Number(m.custos_estimados_usd) ? `<br><span class="muted">previsto ${usd(m.custos_estimados_usd)}</span>` : ""}</td>
        <td class="n">${usd(m.whatsapp_consumido_usd)}</td><td class="n">${lucro(m) == null ? '<span class="muted">—</span>' : brl(lucro(m))}</td></tr>`).join("")
      : '<tr><td colspan="7" class="muted">Sem comissões nem custos lançados ainda.</td></tr>'}</tbody></table></div>
    <h3>Orçamento de hoje</h3>
    <div class="card">${orc.length ? orc.map((o) => {
      const pct = Number(o.teto_usd) ? Math.round((100 * o.gasto_usd) / o.teto_usd) : 0;
      return `<p>${esc(o.servico)} · ${esc(o.dia)}: ${usd(o.gasto_usd)} de ${usd(o.teto_usd)} <b class="${pct >= 80 ? "erro" : ""}">(${pct}%)</b></p>`;
    }).join("") : '<p class="muted">Nenhum consumo com teto registrado.</p>'}</div>
    <h3>Custos por serviço</h3>
    ${ctx.ehDono ? `<form class="card" id="form-custo">
      <div class="linha">
        <div><label for="k-mes">Mês</label><input id="k-mes" type="month" required value="${new Date().toLocaleDateString("sv-SE").slice(0, 7)}"></div>
        <div><label for="k-valor">Valor (US$)</label><input id="k-valor" inputmode="decimal" required placeholder="25,00"></div>
      </div>
      <div class="linha">
        <div><label for="k-serv">Serviço</label><select id="k-serv">${["supabase", "github", "whatsapp", "dominio", "imgbb", "outro"].map((s) => `<option>${s}</option>`).join("")}</select></div>
        <div><label for="k-orig">Tipo</label><select id="k-orig"><option value="fatura">Fatura (cobrado)</option><option value="estimativa">Estimativa</option></select></div>
      </div>
      <label for="k-obs">Observação</label><input id="k-obs" maxlength="300">
      <p></p><button class="b p grande" type="submit">Lançar custo</button></form>` : ""}
    <div class="card"><table><thead><tr><th>Mês</th><th>Serviço</th><th>Tipo</th><th class="n">Valor</th><th></th></tr></thead>
      <tbody>${custos.length ? custos.map((c) => `<tr><td>${esc(mesTxt(c.mes))}</td><td>${esc(c.servico)}<br><span class="muted">${esc(c.observacao || "")}</span></td>
        <td>${c.origem === "fatura" ? "Fatura" : "Estimativa"}</td><td class="n">${usd(c.valor_usd)}</td>
        <td>${ctx.ehDono ? `<button class="b erro" data-apagar-custo="${c.id}" aria-label="Apagar custo">✕</button>` : ""}</td></tr>`).join("")
      : '<tr><td colspan="5" class="muted">Nenhum custo lançado.</td></tr>'}</tbody></table></div>`;

  $("#f-cot", el).addEventListener("change", (ev) => {
    const v = numero(ev.target.value);
    if (ev.target.value && (v == null || v <= 0)) return aviso("Cotação inválida.", "erro");
    guardar("cotacao_usd_brl", v ? String(v) : "");
    montar(el, ctx);
  });
  $("#form-custo", el)?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const valor = numero($("#k-valor", el).value);
    if (valor == null || valor < 0) return aviso("Informe o valor em dólares.", "erro");
    try {
      await q(ctx.sb.from("custos_servicos").insert({ mes: `${$("#k-mes", el).value}-01`, servico: $("#k-serv", el).value,
        valor_usd: valor, origem: $("#k-orig", el).value, observacao: $("#k-obs", el).value.trim() || null }));
      aviso("Custo lançado ✔");
      montar(el, ctx);
    } catch (e) { aviso(e.message, "erro"); }
  });
  el.querySelectorAll("[data-apagar-custo]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Apagar este custo?")) return;
    try { await q(ctx.sb.from("custos_servicos").delete().eq("id", b.dataset.apagarCusto)); montar(el, ctx); }
    catch (e) { aviso(e.message, "erro"); }
  }));
}
