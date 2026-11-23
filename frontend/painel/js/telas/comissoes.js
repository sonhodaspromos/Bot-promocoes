// Comissões: automáticas (API) e lançamentos manuais.
import { q } from "../api.js";
import { $, aviso, brl, data, esc, numero } from "../ui.js";

const ROTULO = { pendente: "Pendente", confirmada: "Confirmada", cancelada: "Cancelada", paga: "Paga" };

export async function montar(el, ctx) {
  const [lojas, ofertas, lista] = await Promise.all([
    q(ctx.sb.from("marketplaces").select("id,nome").neq("codigo", "demo").order("nome")),
    q(ctx.sb.from("vw_promocoes_painel").select("id,produto_id,titulo,marketplace_nome").in("status", ["publicada", "pausada", "expirada"]).order("publicada_em", { ascending: false }).limit(150)),
    q(ctx.sb.from("comissoes").select("id,data_venda,valor_comissao,status,origem,descricao,marketplaces(nome)").order("data_venda", { ascending: false }).order("id", { ascending: false }).limit(200)),
  ]);
  const total = lista.filter((c) => c.status !== "cancelada").reduce((s, c) => s + Number(c.valor_comissao), 0);
  el.innerHTML = `
    <h2>Comissões</h2>
    <p class="muted">As da Shopee são importadas sozinhas todo dia às 6h. As das outras lojas você lança aqui.</p>
    ${ctx.podeEditar ? `
    <form class="card" id="form-com">
      <div class="linha">
        <div><label for="c-data">Data da venda</label><input id="c-data" type="date" required value="${new Date().toLocaleDateString("sv-SE")}"></div>
        <div><label for="c-valor">Comissão (R$)</label><input id="c-valor" inputmode="decimal" placeholder="12,50" required></div>
      </div>
      <label for="c-loja">Loja</label><select id="c-loja">${lojas.map((l) => `<option value="${l.id}">${esc(l.nome)}</option>`).join("")}</select>
      <label for="c-oferta">Oferta (opcional)</label>
      <select id="c-oferta"><option value="">— não vincular —</option>${ofertas.map((o) => `<option value="${o.id}" data-produto="${o.produto_id}">${esc(o.marketplace_nome)} · ${esc(o.titulo.slice(0, 60))}</option>`).join("")}</select>
      <label for="c-desc">Descrição (opcional)</label><input id="c-desc" maxlength="300">
      <p></p><button class="b p grande" type="submit">Lançar comissão</button>
    </form>` : ""}
    <h3>Lançamentos · total ${brl(total)}</h3>
    <div class="card"><table>
      <thead><tr><th>Data</th><th>Loja / descrição</th><th class="n">Valor</th><th></th></tr></thead>
      <tbody>${lista.length ? lista.map((c) => `<tr>
        <td>${data(c.data_venda)}</td>
        <td>${esc(c.marketplaces?.nome || "")}<br><span class="muted">${esc(c.descricao || "")} · ${ROTULO[c.status]}${c.origem === "api" ? " · automática" : ""}</span></td>
        <td class="n">${brl(c.valor_comissao)}</td>
        <td>${ctx.podeEditar && c.origem === "manual" ? `<button class="b erro" data-apagar="${c.id}" aria-label="Apagar">✕</button>` : ""}</td></tr>`).join("")
      : '<tr><td colspan="4" class="muted">Nenhuma comissão ainda.</td></tr>'}</tbody></table></div>`;

  $("#form-com", el)?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const valor = numero($("#c-valor", el).value);
    if (valor == null || valor < 0) return aviso("Informe o valor da comissão.", "erro");
    const sel = $("#c-oferta", el);
    const opcao = sel.options[sel.selectedIndex];
    try {
      await q(ctx.sb.from("comissoes").insert({
        marketplace_id: Number($("#c-loja", el).value), data_venda: $("#c-data", el).value, valor_comissao: valor,
        promocao_id: sel.value ? Number(sel.value) : null, produto_id: sel.value ? Number(opcao.dataset.produto) : null,
        descricao: $("#c-desc", el).value.trim() || (sel.value ? opcao.text : null), origem: "manual", status: "confirmada",
      }));
      aviso("Comissão lançada ✔");
      montar(el, ctx);
    } catch (e) { aviso(e.message, "erro"); }
  });
  el.querySelectorAll("[data-apagar]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Apagar este lançamento?")) return;
    try { await q(ctx.sb.from("comissoes").delete().eq("id", b.dataset.apagar)); montar(el, ctx); }
    catch (e) { aviso(e.message, "erro"); }
  }));
}
