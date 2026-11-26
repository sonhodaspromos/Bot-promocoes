// Nova oferta manual (Mercado Livre e lojas sem API). O link é validado pelo sistema antes de publicar.
import { publicarAgora, puxarDados, q } from "../api.js";
import { $, aviso, brl, esc, numero } from "../ui.js";

export async function montar(el, ctx) {
  if (!ctx.podeEditar) { el.innerHTML = '<div class="card">Apenas dono ou editor cadastram ofertas.</div>'; return; }
  const [lojas, categorias] = await Promise.all([
    q(ctx.sb.from("marketplaces").select("codigo,nome,modo,dominios_afiliado").eq("ativo", true).neq("modo", "desativado").neq("codigo", "demo").order("nome")),
    q(ctx.sb.from("categorias").select("id,nome").eq("ativa", true).order("ordem")),
  ]);
  el.innerHTML = `
    <h2>Nova oferta manual</h2>
    <p class="muted">Para lojas sem API (como o Mercado Livre). Cole o link de afiliado gerado no painel da loja.
      O sistema confere se o link é de afiliado e se abre <b>antes</b> de publicar.</p>
    <form class="card" id="form-nova">
      <label for="n-loja">Loja</label>
      <select id="n-loja" required>${lojas.map((l) => `<option value="${esc(l.codigo)}">${esc(l.nome)}</option>`).join("")}</select>
      <p class="ajuda" id="n-dominios"></p>
      <label for="n-link">Link de afiliado</label><input id="n-link" type="url" inputmode="url" placeholder="https://…" required>
      <button class="b p grande" type="button" id="n-puxar" style="margin-top:8px">✨ Puxar dados do produto</button>
      <div id="n-previa" class="previa-produto hide"></div>
      <label for="n-titulo">Título do produto</label><input id="n-titulo" maxlength="300" minlength="3" required>
      <div class="linha">
        <div><label for="n-preco">Preço (R$)</label><input id="n-preco" inputmode="decimal" placeholder="99,90" required></div>
        <div><label for="n-antigo">Preço antigo (opcional)</label><input id="n-antigo" inputmode="decimal" placeholder="149,90"></div>
      </div>
      <label class="sw" style="color:var(--tx)"><span>Esse preço é no Pix</span>
        <span class="tg"><input type="checkbox" id="n-pix"><span></span></span></label>
      <p class="ajuda" id="n-ajuda-antigo">Preço antigo lido da página da loja pelo “Puxar dados” aparece no post como “De … Por …”.
        Digitado à mão, só aparece depois que o histórico de preços do bot confirmar.</p>
      <label for="n-cat">Categoria</label>
      <select id="n-cat"><option value="">Sem categoria</option>${categorias.map((c) => `<option value="${c.id}">${esc(c.nome)}</option>`).join("")}</select>
      <label for="n-img">Link da foto (opcional)</label><input id="n-img" type="url" inputmode="url" placeholder="https://…">
      <label for="n-id">Código do produto na loja (opcional)</label><input id="n-id" maxlength="120" placeholder="ex.: MLB123456789">
      <p class="ajuda">Com o código, o sistema evita cadastrar o mesmo produto duas vezes.</p>
      <label class="sw" style="color:var(--tx)"><span>Publicar agora (sem esperar a próxima rodada)</span>
        <span class="tg"><input type="checkbox" id="n-agora"><span></span></span></label>
      <p></p><button class="b p grande" type="submit" id="n-salvar">Salvar oferta</button>
    </form>`;

  const mostrarDominios = () => {
    const loja = lojas.find((l) => l.codigo === $("#n-loja", el).value);
    $("#n-dominios", el).textContent = loja ? `Links aceitos: ${loja.dominios_afiliado.join(", ")}` : "";
  };
  $("#n-loja", el).addEventListener("change", mostrarDominios);
  mostrarDominios();

  const moeda = (v) => (v ? Number(v).toFixed(2).replace(".", ",") : "");
  let antigoDaPagina = null;  // preço riscado lido da página: só conta como "da loja" se ninguém mexer no campo
  $("#n-puxar", el).addEventListener("click", async () => {
    const link = $("#n-link", el).value.trim();
    if (!/^https:\/\//i.test(link)) return aviso("Cole primeiro o link de afiliado.", "erro");
    const botao = $("#n-puxar", el);
    botao.disabled = true; botao.textContent = "Buscando na loja…";
    try {
      const d = await puxarDados(link, $("#n-loja", el).value);
      if (d.titulo) $("#n-titulo", el).value = d.titulo;
      if (d.preco) $("#n-preco", el).value = moeda(d.preco);
      $("#n-antigo", el).value = moeda(d.preco_antigo);
      antigoDaPagina = d.preco_antigo ? Number(d.preco_antigo) : null;
      $("#n-pix", el).checked = !!d.preco_pix;
      if (/^https:\/\//.test(d.imagem_url || "")) $("#n-img", el).value = d.imagem_url;
      if (d.id_externo) $("#n-id", el).value = d.id_externo;
      const foto = /^https:\/\//.test(d.imagem_url || "") ? `<img src="${esc(d.imagem_url)}" alt="" referrerpolicy="no-referrer">` : '<div class="sem-foto"></div>';
      const p = $("#n-previa", el);
      p.innerHTML = `${foto}<div><b>${esc(d.titulo || "Sem título")}</b><div>${d.preco ? brl(d.preco) : "preço não encontrado"}
        ${d.preco_antigo ? ` <s class="m">${brl(d.preco_antigo)}</s>` : ""}</div>
        <div class="m">Confira os campos abaixo antes de salvar.</div></div>`;
      p.classList.remove("hide");
      aviso(d.preco ? "Dados preenchidos ✔ confira antes de salvar" : "Achei o produto, mas não o preço: preencha à mão.");
    } catch (e) { aviso(e.message, "erro"); }
    botao.disabled = false; botao.textContent = "✨ Puxar dados do produto";
  });

  $("#form-nova", el).addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const preco = numero($("#n-preco", el).value);
    if (!preco || preco <= 0) return aviso("Informe um preço válido.", "erro");
    const botao = $("#n-salvar", el);
    botao.disabled = true;
    try {
      await q(ctx.sb.rpc("cadastrar_oferta_manual", {
        p_marketplace: $("#n-loja", el).value, p_titulo: $("#n-titulo", el).value.trim(), p_preco: preco,
        p_link: $("#n-link", el).value.trim(), p_preco_antigo: numero($("#n-antigo", el).value),
        p_categoria_id: $("#n-cat", el).value ? Number($("#n-cat", el).value) : null,
        p_imagem_url: $("#n-img", el).value.trim() || null, p_id_externo: $("#n-id", el).value.trim() || null,
        p_referencia_da_pagina: antigoDaPagina !== null && numero($("#n-antigo", el).value) === antigoDaPagina,
        p_preco_pix: $("#n-pix", el).checked,
      }));
      let msg = "Oferta salva ✔ o link será validado na próxima rodada (até 10 min).";
      if ($("#n-agora", el).checked) {
        try { msg = "Oferta salva ✔ " + (await publicarAgora()); }
        catch (e) { msg = "Oferta salva ✔ (não deu para rodar agora: " + e.message + ")"; }
      }
      aviso(msg);
      ["#n-link", "#n-titulo", "#n-preco", "#n-antigo", "#n-img", "#n-id"].forEach((s) => ($(s, el).value = ""));
      $("#n-pix", el).checked = false; antigoDaPagina = null;
      $("#n-previa", el).classList.add("hide");
    } catch (e) { aviso(e.message, "erro"); }
    botao.disabled = false;
  });
}
