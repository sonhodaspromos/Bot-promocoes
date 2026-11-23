// Login, cadastro do primeiro acesso e papel do usuário.
import { sb, mensagemErro } from "./api.js";
import { $, esc } from "./ui.js";

export async function sessaoAtual() {
  const { data: { session } } = await sb.auth.getSession();
  return session;
}

export async function papelAtual() {
  const { data, error } = await sb.rpc("meu_papel");
  if (error) return null;
  return data || null;
}

export function telaLogin(el, aoEntrar) {
  el.innerHTML = `
    <section class="login">
      <div class="card">
        <h2>Entrar no painel</h2>
        <form id="form-login">
          <label for="email">E-mail</label><input id="email" type="email" autocomplete="email" required>
          <label for="senha">Senha</label><input id="senha" type="password" autocomplete="current-password" minlength="8" required>
          <p></p><button class="b p grande" type="submit">Entrar</button>
        </form>
        <p class="muted centro" style="margin:16px 0 6px">Primeiro acesso?</p>
        <button id="btn-criar" class="b grande" type="button">Criar meu acesso</button>
        <p class="ajuda">O primeiro cadastro vira o <b>dono</b> do painel. Outros cadastros só têm acesso se o dono liberar.</p>
        <p id="msg-login" class="muted" role="status"></p>
      </div>
    </section>`;
  const msg = (t) => ($("#msg-login").textContent = t);

  $("#form-login").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    msg("Entrando…");
    const { error } = await sb.auth.signInWithPassword({ email: $("#email").value.trim(), password: $("#senha").value });
    if (error) return msg("Não foi possível entrar: " + (error.message === "Invalid login credentials" ? "e-mail ou senha incorretos." : mensagemErro(error)));
    aoEntrar();
  });

  $("#btn-criar").addEventListener("click", async () => {
    const email = $("#email").value.trim(), senha = $("#senha").value;
    if (!email || senha.length < 8) return msg("Preencha o e-mail e uma senha com pelo menos 8 caracteres.");
    const { data, error } = await sb.auth.signUp({ email, password: senha, options: { emailRedirectTo: location.href.split("#")[0] } });
    if (error) return msg("Erro: " + mensagemErro(error));
    if (!data.session) msg("Pronto! Confirme pelo link enviado para " + email + " e depois volte aqui para entrar.");
    else aoEntrar();
  });
}

export function telaSemAcesso(el, email) {
  el.innerHTML = `<section class="login"><div class="card"><h2>Sem acesso</h2>
    <p>Sua conta ainda não tem permissão neste painel. Peça ao dono para liberar seu acesso
    (ele precisa do seu e-mail: <b>${esc(email)}</b>).</p></div></section>`;
}

export async function sair() {
  await sb.auth.signOut();
  location.reload();
}
