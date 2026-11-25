"""Vitrine pública no navegador (Chromium/Playwright): responsividade, acessibilidade básica, busca,
filtros, XSS, imagem quebrada, dados estruturados e aviso de afiliado. Sem internet e sem banco."""
import asyncio
import functools
import http.server
import json
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.servicos.vitrine import AVISO_AMAZON, GeradorVitrine  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

OFERTAS = [
    {"promocao_id": 1, "titulo": "Fone Bluetooth <script>alert(1)</script>", "preco": 99.9, "preco_referencia": 149.9,
     "desconto_pct": 33, "desconto_comprovado": True, "link_id": 11, "categoria": "Áudio", "marketplace_nome": "Shopee",
     "marketplace_codigo": "shopee", "conferido_em": "2026-11-25T14:00:00+00:00", "imagem_url": "https://imagem.invalid/quebrada.jpg"},
    {"promocao_id": 2, "titulo": "Air Fryer 4L", "preco": 299.9, "link_id": 12, "categoria": "Casa", "marketplace_nome": "Amazon",
     "marketplace_codigo": "amazon", "conferido_em": "2026-11-25T13:00:00+00:00"},
    {"promocao_id": 3, "titulo": "Kindle 11ª geração", "preco": 499.0, "link_id": 13, "categoria": "Eletrônicos",
     "marketplace_nome": "Amazon", "marketplace_codigo": "amazon", "conferido_em": "2026-11-25T12:00:00+00:00"},
]


def luminancia(rgb):
    def c(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * c(r) + 0.7152 * c(g) + 0.0722 * c(b)


def contraste(a, b):
    la, lb = sorted((luminancia(a), luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def rgb(txt):
    return tuple(int(float(x)) for x in txt[txt.index("(") + 1:txt.index(")")].split(",")[:3])


async def main():
    pasta = Path(tempfile.mkdtemp())
    GeradorVitrine("http://127.0.0.1", "https://x.supabase.co/functions/v1/r", "Vitrine Teste").gerar(
        OFERTAS, [{"titulo": "10% OFF", "codigo": "PROMO10", "fim_em": "2026-12-01", "marketplace_nome": "Shopee"}],
        pasta, datetime(2026, 11, 25, 15, tzinfo=timezone.utc))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        type("S", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None}), directory=str(pasta)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    falhas = []

    def checar(cond, msg):
        print(("  ✔ " if cond else "  ✖ ") + msg)
        if not cond:
            falhas.append(msg)

    async with async_playwright() as p:
        nav = await p.chromium.launch()
        for largura in (360, 1280):
            pg = await nav.new_page(viewport={"width": largura, "height": 800})
            alertas = []
            pg.on("dialog", lambda d: (alertas.append(d.message), asyncio.ensure_future(d.dismiss())))
            await pg.goto(base + "/index.html"); await pg.wait_for_timeout(600)
            larg = await pg.evaluate("document.documentElement.scrollWidth")
            checar(larg <= largura, f"{largura}px: sem rolagem horizontal (largura do conteúdo {larg}px)")
            if largura == 360:
                checar(not alertas and "&lt;script&gt;" in await pg.content(), "título com <script> aparece como texto (sem XSS)")
                checar(await pg.locator("article img").count() == 0, "imagem quebrada é removida (sem ícone de erro)")
                checar(await pg.locator("h1").count() == 1, "um único h1 na página")
                sem_nome = await pg.evaluate("""[...document.querySelectorAll('input,select')].filter(e=>!e.getAttribute('aria-label')&&!e.labels?.length).length""")
                checar(sem_nome == 0, "busca e filtros têm nome acessível")
                cor = await pg.eval_on_selector(".botao", "e=>[getComputedStyle(e).color,getComputedStyle(e).backgroundColor]")
                c = contraste(rgb(cor[0]), rgb(cor[1]))
                checar(c >= 4.5, f"botão 'Ver na loja' com contraste AA ({c:.1f}:1)")
                await pg.keyboard.press("Tab")
                foco = await pg.evaluate("getComputedStyle(document.activeElement).outlineStyle")
                checar(foco not in ("none", ""), "foco do teclado visível")
                rel = await pg.eval_on_selector_all(".botao", "es=>es.map(e=>e.rel)")
                checar(all("sponsored" in r and "nofollow" in r for r in rel), "links de afiliado com rel=sponsored nofollow")
                txt = await pg.inner_text("body")
                checar("#publi" in txt and AVISO_AMAZON in txt, "aviso de afiliado (#publi) e aviso do Programa de Associados Amazon")
                checar("preço conferido em 25/11 às 11:00; pode ter mudado na loja" in txt, "cada card mostra quando o preço foi conferido")
                await pg.fill("#busca", "kindle"); await pg.wait_for_timeout(200)
                visiveis = await pg.locator("article.card:not([hidden])").count()
                checar(visiveis == 1, "busca filtra (1 resultado para 'kindle')")
                await pg.fill("#busca", ""); await pg.select_option("#categoria", "Casa"); await pg.wait_for_timeout(200)
                checar(await pg.locator("article.card:not([hidden])").count() == 1, "filtro de categoria")
                await pg.select_option("#categoria", ""); await pg.select_option("#ordem", "preco"); await pg.wait_for_timeout(200)
                precos = await pg.eval_on_selector_all("article.card", "es=>es.map(e=>+e.dataset.preco)")
                checar(precos == sorted(precos), "ordenar por menor preço")
                await pg.goto(base + "/" + next(pasta.glob("oferta/2-*.html")).relative_to(pasta).as_posix())
                ld = json.loads(await pg.eval_on_selector('script[type="application/ld+json"]', "e=>e.textContent"))
                checar(ld["@type"] == "Product" and ld["offers"]["priceCurrency"] == "BRL", "página da oferta com JSON-LD Product/Offer válido")
                await pg.goto(base + "/" + next(pasta.glob("oferta/1-*.html")).relative_to(pasta).as_posix())
                checar(AVISO_AMAZON not in await pg.inner_text("body"), "aviso Amazon só nas páginas com produto Amazon")
            await pg.close()
        await nav.close()
    srv.shutdown()
    print("TUDO OK" if not falhas else f"{len(falhas)} FALHA(S)")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
