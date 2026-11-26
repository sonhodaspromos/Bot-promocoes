// Testes das regras das Edge Functions. Rodam com:
//   node --experimental-strip-types --test supabase/functions/testes/regras.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { destinoSeguro, ehRobo, ipDoVisitante, podeDisparar, salValido, textoDoVisitante } from "../_compartilhado/regras.ts";

const cab = (o: Record<string, string>) => ({ get: (n: string) => o[n.toLowerCase()] ?? null });

test("IP: ignora o primeiro X-Forwarded-For (forjável) e usa o do proxy", () => {
  assert.equal(ipDoVisitante(cab({ "x-forwarded-for": "6.6.6.6, 200.10.20.30" })), "200.10.20.30");
  assert.equal(ipDoVisitante(cab({ "cf-connecting-ip": "177.1.2.3", "x-forwarded-for": "6.6.6.6" })), "177.1.2.3");
  assert.equal(ipDoVisitante(cab({ "x-forwarded-for": "lixo" })), null);
  assert.equal(ipDoVisitante(cab({})), null);
});

test("robôs e clientes automáticos não contam clique", () => {
  for (const ua of ["", "curl/8.0", "python-requests/2.31", "Mozilla/5.0 HeadlessChrome", "TelegramBot (like TwitterBot)", "okhttp/4.9"]) {
    assert.ok(ehRobo(ua), ua);
  }
  assert.ok(!ehRobo("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/131 Mobile Safari/537.36"));
});

test("sal precisa ser secreto e longo (senão o IP poderia ser descoberto pelo hash)", () => {
  assert.ok(!salValido(undefined, "https://x.supabase.co"));
  assert.ok(!salValido("https://x.supabase.co", "https://x.supabase.co"));
  assert.ok(!salValido("curto", "https://x.supabase.co"));
  assert.ok(salValido("a".repeat(40), "https://x.supabase.co"));
  assert.notEqual(textoDoVisitante("s", "1.1.1.1", "A"), textoDoVisitante("s", "1.1.1.1", "B"));
});

test("redireciona só para https", () => {
  assert.equal(destinoSeguro("https://s.shopee.com.br/AbC"), "https://s.shopee.com.br/AbC");
  for (const u of ["javascript:alert(1)", "http://s.shopee.com.br/x", "data:text/html,oi", "https://u:s@x.com/", "", null]) {
    assert.equal(destinoSeguro(u as string), null, String(u));
  }
});

test("Publicar agora exige dono/editor COM 2º fator", () => {
  assert.equal(podeDisparar({ papel: "editor", aal: "aal2", mfa_exigido: true }).ok, true);
  assert.equal(podeDisparar({ papel: "dono", aal: "aal1", mfa_exigido: true }).ok, false);
  assert.equal(podeDisparar({ papel: "leitor", aal: "aal2", mfa_exigido: true }).ok, false);
  assert.equal(podeDisparar(null).ok, false);
  assert.equal(podeDisparar({ papel: "dono", aal: "aal1", mfa_exigido: false }).ok, true);
});
