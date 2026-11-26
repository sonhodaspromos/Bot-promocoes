// Login, cadastro por convite, 2º fator (app autenticador) e papel do usuário.
import { sb, mensagemErro } from "./api.js";
import { $, esc } from "./ui.js";

export async function sessaoAtual() {
  const { data: { session } } = await sb.auth.getSession();
  return session;
}

/** papel + nível da sessão (aal1 = só senha; aal2 = senha + código do autenticador). */
export async function minhaSessao() {
  const { data, error } = await sb.rpc("minha_sessao");
  if (error || !data) return { papel: null, aal: "aal1", mfa_exigido: true };
  return Array.isArray(data) ? data[0] || { papel: null, aal: "aal1", mfa_exigido: true } : data;
}

export async function papelAtual() {
  const { data, error } = await sb.rpc("meu_papel");
  if (error) return null;
  return data || null;
}

export function telaLogin(el, aoEntrar) {
  el.innerHTML = `
    <section class="login">
      <img class="logo-login" src="${new URL("../img/logo.png", import.meta.url)}" alt="Sonho das Promos" width="150" height="150">
      <div class="card">
        <h2>Entrar no painel</h2>
        <form id="form-login">
          <label for="email">E-mail</label><input id="email" type="email" autocomplete="email" required>
          <label for="senha">Senha</label><input id="senha" type="password" autocomplete="current-password" minlength="8" required>
          <p></p><button class="b p grande" type="submit">Entrar</button>
        </form>
        <p class="muted centro" style="margin:16px 0 6px">Recebeu um convite?</p>
        <button id="btn-criar" class="b grande" type="button">Criar meu acesso</button>
        <p class="ajuda">O cadastro é <b>fechado</b>: só funciona para e-mails convidados pelo dono. O e-mail do dono é
          definido uma única vez pelo SQL Editor do Supabase.</p>
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

/** 2º fator obrigatório: cadastra o app autenticador (1ª vez) ou pede o código de 6 dígitos. */
export async function telaMfa(el, aoConfirmar) {
  const { data: fatores } = await sb.auth.mfa.listFactors();
  const verificado = (fatores?.totp || []).find((f) => f.status === "verified");
  let fatorId = verificado?.id, qr = "", segredo = "";
  if (!fatorId) {
    // limpa tentativas antigas não concluídas antes de criar uma nova
    // (fatores.totp só traz os VERIFICADOS; os pendentes ficam em fatores.all)
    for (const f of (fatores?.all || []).filter((f) => f.factor_type === "totp" && f.status !== "verified")) {
      await sb.auth.mfa.unenroll({ factorId: f.id });
    }
    // nome único: se ainda sobrar um pendente com o mesmo nome, o cadastro não trava
    const nome = `Painel do bot ${new Date().toLocaleDateString("pt-BR")} ${Date.now().toString(36).slice(-4)}`;
    const { data, error } = await sb.auth.mfa.enroll({ factorType: "totp", friendlyName: nome });
    if (error) {
      el.innerHTML = `<section class="login"><div class="card"><h2>2º fator</h2><p>Não foi possível iniciar o cadastro do app autenticador:
        ${esc(mensagemErro(error))}</p><p class="muted">Verifique se o MFA (TOTP) está habilitado em Supabase → Authentication.</p></div></section>`;
      return;
    }
    fatorId = data.id; qr = data.totp?.qr_code || ""; segredo = data.totp?.secret || "";
  }
  el.innerHTML = `
    <section class="login"><div class="card" id="mfa">
      <h2>Verificação em duas etapas</h2>
      ${verificado ? '<p>Abra o app autenticador e digite o código de 6 dígitos do <b>Painel do bot</b>.</p>' : `
        <p>Para proteger o painel (credenciais, links de afiliado e publicações), cadastre um app autenticador
          (Google Authenticator, Microsoft Authenticator, 1Password…). Escaneie o QR code ou digite a chave:</p>
        ${qr ? `<p class="centro"><img id="mfa-qr" alt="QR code do autenticador" src="${esc(qr)}" width="180" height="180"></p>` : ""}
        <p class="centro"><code id="mfa-segredo" style="word-break:break-all">${esc(segredo)}</code></p>`}
      <form id="form-mfa">
        <label for="mfa-codigo">Código de 6 dígitos</label>
        <input id="mfa-codigo" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required>
        <p></p><button class="b p grande" type="submit">Confirmar</button>
      </form>
      <p id="msg-mfa" class="muted" role="status"></p>
    </div></section>`;
  $("#form-mfa").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("#msg-mfa").textContent = "Conferindo…";
    const { error } = await sb.auth.mfa.challengeAndVerify({ factorId: fatorId, code: $("#mfa-codigo").value.trim() });
    if (error) return ($("#msg-mfa").textContent = "Código incorreto ou expirado. Tente o código atual do app.");
    aoConfirmar();
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
