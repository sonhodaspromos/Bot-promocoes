import { test } from "node:test";
import assert from "node:assert/strict";
import { acoes, assinaturaValida, intencao, mensagens, telefoneE164 } from "../_compartilhado/whatsapp.ts";

test("consentimento só com pedido explícito; saída com qualquer palavra de saída", () => {
  assert.equal(intencao("QUERO OFERTAS"), "entrar");
  assert.equal(intencao("Quero ofertas!"), "entrar");
  assert.equal(intencao("oi"), null);
  assert.equal(intencao("quero"), null);              // ambíguo: não inscreve
  for (const t of ["SAIR", "Parar", "stop", "cancelar por favor"]) assert.equal(intencao(t), "sair", t);
});

test("telefone em E.164", () => {
  assert.equal(telefoneE164("5581999990000"), "+5581999990000");
  assert.equal(telefoneE164("0812"), null);
  assert.equal(telefoneE164(null), null);
});

test("assinatura HMAC do webhook", async () => {
  const corpo = '{"a":1}';
  const chave = await crypto.subtle.importKey("raw", new TextEncoder().encode("segredo"), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = [...new Uint8Array(await crypto.subtle.sign("HMAC", chave, new TextEncoder().encode(corpo)))].map((b) => b.toString(16).padStart(2, "0")).join("");
  assert.equal(await assinaturaValida("segredo", corpo, `sha256=${sig}`), true);
  assert.equal(await assinaturaValida("segredo", corpo + " ", `sha256=${sig}`), false);
  assert.equal(await assinaturaValida("segredo", corpo, null), false);
  assert.equal(await assinaturaValida("", corpo, `sha256=${sig}`), false);
});

test("extrai ações só de mensagens de texto", () => {
  const p = { entry: [{ changes: [{ value: { messages: [
    { from: "5581911112222", type: "text", text: { body: "QUERO OFERTAS" } },
    { from: "5581933334444", type: "text", text: { body: "sair" } },
    { from: "5581955556666", type: "image" },
    { from: "5581977778888", type: "text", text: { body: "quanto custa?" } },
  ] } }] }] };
  assert.deepEqual(acoes(p), [{ telefone: "+5581911112222", acao: "entrar" }, { telefone: "+5581933334444", acao: "sair" }]);
  assert.deepEqual(acoes(null), []);
});

test("mensagens trazem id e hora de envio; sem id ou timestamp são descartadas (não dá para deduplicar)", () => {
  const p = { entry: [{ changes: [{ value: { messages: [
    { id: "wamid.AAA1", timestamp: "1764100000", from: "5581911112222", type: "text", text: { body: "QUERO OFERTAS" } },
    { from: "5581933334444", type: "text", text: { body: "sair" } },
    { id: "wamid.AAA2", timestamp: "abc", from: "5581933334444", type: "text", text: { body: "sair" } },
  ] } }] }] };
  assert.deepEqual(mensagens(p), [{ telefone: "+5581911112222", acao: "entrar", id: "wamid.AAA1", enviadaEm: "2025-11-25T19:46:40.000Z" }]);
  assert.deepEqual(mensagens({}), []);
});
