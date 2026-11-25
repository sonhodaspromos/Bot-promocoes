// Ponto de entrada: autenticação, navegação entre telas, faixa de demonstração e "Publicar agora".
import { sb, publicarAgora, mensagemErro } from "./api.js";
import { minhaSessao, sair, sessaoAtual, telaLogin, telaMfa, telaSemAcesso } from "./auth.js";
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
  let s = await minhaSessao();
  if (s.mfa_exigido !== false && s.aal !== "aal2") {
    $("#btn-sair").classList.remove("hide");
    // o 2º fator vale para todos: sem ele o banco não libera nenhum dado (papel_atual exige aal2)
    return telaMfa(conteudo, async () => { await sb.auth.refreshSession(); iniciar(); });
  }
  ctx.papel = s.papel;
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
  $("#btn-parar").classList.toggle("hide", !ctx.podeEditar);
  $$('#nav button[data-precisa="editar"]').forEach((b) => b.classList.toggle("hide", !ctx.podeEditar));
  $("#nav").classList.remove("hide");
  atualizarFaixaDemo().catch(() => {});
  atualizarParada().catch(() => {});
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
// ---- botão de emergência: interrompe TODAS as publicações reais (editor ativa; só o dono reativa)
async function atualizarParada() {
  const { data } = await sb.from("configuracoes").select("chave,valor").in("chave", ["sistema.parada_emergencia", "sistema.parada_motivo"]);
  const v = Object.fromEntries((data || []).map((c) => [c.chave, c.valor]));
  ctx.parada = v["sistema.parada_emergencia"] === true;
  const faixa = $("#faixa-parada"), botao = $("#btn-parar");
  faixa.classList.toggle("hide", !ctx.parada);
  faixa.textContent = ctx.parada ? `⛔ PUBLICAÇÕES INTERROMPIDAS — ${v["sistema.parada_motivo"] || "parada de emergência"}` : "";
  botao.textContent = ctx.parada ? "▶ Retomar" : "⛔ Parar tudo";
  botao.classList.toggle("hide", !ctx.podeEditar || (ctx.parada && !ctx.ehDono));
}
$("#btn-parar").addEventListener("click", async () => {
  try {
    if (ctx.parada) {
      if (!confirm("Retomar as publicações reais?")) return;
      const { error } = await sb.rpc("desativar_parada_emergencia");
      if (error) throw new Error(mensagemErro(error));
      aviso("Publicações retomadas ✔ (valem a partir da próxima rodada)");
    } else {
      const motivo = prompt("PARAR TODAS as publicações reais agora? Descreva o motivo:");
      if (!motivo) return;
      const { error } = await sb.rpc("ativar_parada_emergencia", { p_motivo: motivo });
      if (error) throw new Error(mensagemErro(error));
      aviso("⛔ Publicações interrompidas. Nada mais sai até o dono retomar.", "erro");
    }
    await atualizarParada();
  } catch (e) { aviso(e.message, "erro"); }
});

window.addEventListener("hashchange", () => ctx.papel && abrir(location.hash.slice(1)));
sb.auth.onAuthStateChange((evento) => { if (evento === "SIGNED_OUT") location.reload(); });

iniciar();
