import { test } from "node:test";
import assert from "node:assert/strict";
import { enderecoPermitido, extrairProduto, numeroPreco, paginaDeBloqueio } from "../_compartilhado/produto.ts";

const ML = ["meli.la", "mercadolivre.com", "mercadolivre.com.br"];

test("só abre https dos domínios da loja", () => {
  assert.ok(enderecoPermitido("https://meli.la/29AfLW4", ML));
  assert.ok(enderecoPermitido("https://produto.mercadolivre.com.br/MLB-123-x", ML));
  for (const ruim of ["http://meli.la/x", "https://meli.la.evil.com/x", "https://evilmeli.la/x", "https://user:s@meli.la/x",
    "https://meli.la:8080/x", "https://127.0.0.1/x", "https://[::1]/x", "javascript:alert(1)", "https://localhost/x", "nada"]) {
    assert.equal(enderecoPermitido(ruim, ML), false, ruim);
  }
});

test("preços em vários formatos", () => {
  assert.equal(numeroPreco("1.234,56"), 1234.56);
  assert.equal(numeroPreco("241"), 241);
  assert.equal(numeroPreco("241.90"), 241.9);
  assert.equal(numeroPreco("1.299"), 1299);
  assert.equal(numeroPreco(89.9), 89.9);
  assert.equal(numeroPreco("0"), null);
  assert.equal(numeroPreco(-5), null);
  assert.equal(numeroPreco("abc"), null);
});

const PAGINA_ML = `<html><head><title>Perfume Teste 30ml | Mercado Livre</title>
<meta property="og:title" content="Perfume Teste 30ml &amp; Brinde">
<meta property="og:image" content="https://http2.mlstatic.com/D_NQ_NP_123-O.webp">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Perfume Teste 30ml","sku":"MLB123456789",
"image":"https://http2.mlstatic.com/D_NQ_NP_999-O.webp","offers":{"@type":"Offer","price":241,"priceCurrency":"BRL"}}</script>
</head><body><s class="andes-money-amount andes-money-amount--previous" aria-label="Antes: 419 reais com 90 centavos">R$ 419,90</s></body></html>`;

test("lê título, foto, preço, preço antigo e código do Mercado Livre", () => {
  assert.deepEqual(extrairProduto(PAGINA_ML), {
    titulo: "Perfume Teste 30ml", imagem_url: "https://http2.mlstatic.com/D_NQ_NP_999-O.webp",
    preco: 241, preco_antigo: 419.9, id_externo: "MLB123456789",
  });
});

test("sem JSON-LD usa as metatags; preço antigo menor que o atual é descartado", () => {
  const html = `<meta property="og:title" content="Fone X | Mercado Livre"><meta property="og:image" content="https://img/x.jpg">
    <meta itemprop="price" content="99.90"><span aria-label="Antes: 50 reais"></span>`;
  const d = extrairProduto(html, "https://produto.mercadolivre.com.br/MLB-555666777-fone");
  assert.equal(d.titulo, "Fone X"); assert.equal(d.preco, 99.9); assert.equal(d.preco_antigo, null);
  assert.equal(d.imagem_url, "https://img/x.jpg"); assert.equal(d.id_externo, "MLB555666777");
});

test("foto que não é https é ignorada", () => {
  assert.equal(extrairProduto(`<meta property="og:image" content="javascript:alert(1)">`).imagem_url, null);
});

test("reconhece página de verificação/robô", () => {
  assert.ok(paginaDeBloqueio("<html>Verifique que você não é um robô captcha</html>"));
  assert.equal(paginaDeBloqueio(PAGINA_ML), false);
});
