// Ponto de entrada: autenticação, navegação entre telas, faixa de demonstração e "Publicar agora".
import { sb, publicarAgora } from "./api.js";
import { papelAtual, sair, sessaoAtual, telaLogin, telaSemAcesso } from "./auth.js";
import { $, $$, aviso, esc } from "./ui.js";

const TELAS = {
  resumo: () => import("./telas/resumo.js"),
  ofertas: () => import("./telas/ofertas.js"),
  nova: () => import("./telas/nova.js"),
  cupons: () => import("./telas/cupons.js"),
  comissoes: () => import("./telas/comissoes.js"),
  logs: () => import("./telas/logs.js"),
  ajustes: () => import("./telas/ajustes.js"),
};

const ctx = { sb, papel: null, podeEditar: false, ehDono: false, email: "" };
const conteudo = $("#conteudo");

async function abrir(nome) {
  if (!TELAS[nome]) nome = "resumo";
  if (nome === "nova" && !ctx.podeEditar) nome = "ofertas";
  $$("#nav button").forEach((b) => b.classList.toggle("on", b.dataset.tela === nome));
  conteudo.innerHTML = '<p class="muted centro">Carregando…</p>';
  try {
    const modulo = await TELAS[nome]();
    await modulo.montar(conteudo, ctx);
  } catch (e) {
    conteudo.innerHTML = `<div class="card"><b>Não foi possível abrir esta tela.</b><p class="muted">${esc(e.message)}</p></div>`;
  }
  if (location.hash !== "#" + nome) history.replaceState(null, "", "#" + nome);
  window.scrollTo(0, 0);
}

export async function atualizarFaixaDemo() {
  const [{ data: canais }, { data: mkts }] = await Promise.all([
    sb.from("canais").select("nome,modo,ativo"),
    sb.from("marketplaces").select("nome,modo,ativo"),
  ]);
  const demoCanais = (canais || []).filter((c) => c.ativo && c.modo === "demo").map((c) => c.nome);
  const demoLojas = (mkts || []).filter((m) => m.ativo && m.modo === "demo" && m.nome !== "Loja de Demonstração").map((m) => m.nome);
  const faixa = $("#faixa-demo");
  if (!demoCanais.length && !demoLojas.length) return faixa.classList.add("hide");
  const partes = [];
  if (demoCanais.length) partes.push(`canais em demonstração: ${demoCanais.join(", ")} (nada é publicado de verdade)`);
  if (demoLojas.length) partes.push(`lojas sem API configurada: ${demoLojas.join(", ")}`);
  faixa.textContent = "🧪 MODO DEMONSTRAÇÃO — " + partes.join(" · ");
  faixa.classList.remove("hide");
}

async function iniciar() {
  const sessao = await sessaoAtual();
  if (!sessao) return telaLogin(conteudo, iniciar);
  ctx.email = sessao.user?.email || "";
  ctx.papel = await papelAtual();
  if (!ctx.papel) {
    $("#btn-sair").classList.remove("hide");
    return telaSemAcesso(conteudo, ctx.email);
  }
  ctx.podeEditar = ["dono", "editor"].includes(ctx.papel);
  ctx.ehDono = ctx.papel === "dono";

  const { data: nome } = await sb.from("configuracoes").select("valor").eq("chave", "visual.nome_perfil").maybeSingle();
  $("#titulo").textContent = (nome?.valor || "Bot de Ofertas") + " · Painel";
  $("#papel").textContent = ctx.papel;
  $("#papel").classList.remove("hide");
  $("#btn-sair").classList.remove("hide");
  $("#btn-publicar").classList.toggle("hide", !ctx.podeEditar);
  $$('#nav button[data-precisa="editar"]').forEach((b) => b.classList.toggle("hide", !ctx.podeEditar));
  $("#nav").classList.remove("hide");
  atualizarFaixaDemo().catch(() => {});
  abrir(location.hash.slice(1) || "resumo");
}

$$("#nav button").forEach((b) => b.addEventListener("click", () => abrir(b.dataset.tela)));
$("#btn-sair").addEventListener("click", sair);
$("#btn-publicar").addEventListener("click", async (ev) => {
  const botao = ev.currentTarget;
  botao.disabled = true;
  try { aviso(await publicarAgora()); } catch (e) { aviso(e.message, "erro"); }
  setTimeout(() => (botao.disabled = false), 5000);
});
window.addEventListener("hashchange", () => ctx.papel && abrir(location.hash.slice(1)));
sb.auth.onAuthStateChange((evento) => { if (evento === "SIGNED_OUT") location.reload(); });

iniciar();
