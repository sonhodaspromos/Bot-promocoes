"""Teste do painel com dados simulados (sem internet e sem banco).

Uso:  python frontend/testes/testar_painel.py   (precisa de: pip install playwright && playwright install chromium)
Gera capturas de tela em frontend/testes/capturas/ e falha se houver erro de JavaScript.
"""
import asyncio
import functools
import http.server
import os
import sys
import threading
from pathlib import Path

from playwright.async_api import async_playwright

RAIZ = Path(__file__).resolve().parents[1]
CAPTURAS = Path(__file__).resolve().parent / "capturas"
NAVEGADOR = os.environ.get("CHROME") or None


def servidor():
    class Silencioso(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Silencioso, directory=str(RAIZ))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def main():
    srv = servidor()
    base = f"http://127.0.0.1:{srv.server_port}/testes/painel.html"
    CAPTURAS.mkdir(exist_ok=True)
    falhas = []

    async with async_playwright() as p:
        nav = await p.chromium.launch(executable_path=NAVEGADOR)

        async def pagina(params):
            pg = await nav.new_page(viewport={"width": 390, "height": 844})
            erros = []
            pg.on("pageerror", lambda e: erros.append(str(e)))
            pg.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
            await pg.goto(f"{base}?{params}")
            await pg.wait_for_timeout(500)
            return pg, erros

        def checar(cond, msg):
            print(("  ✔ " if cond else "  ✖ ") + msg)
            if not cond:
                falhas.append(msg)

        # ---------------------------------------------------------- dono
        print("Dono:")
        pg, erros = await pagina("papel=dono")
        checar(await pg.is_visible("#faixa-demo"), "faixa de MODO DEMONSTRAÇÃO aparece")
        checar("DEMONSTRAÇÃO" in await pg.inner_text("#faixa-demo"), "faixa explica o modo demo")
        checar(await pg.inner_text("#k-encontrados") == "40", "resumo mostra produtos encontrados")
        checar("parcial" in (await pg.inner_text("#s-bot")) or "falharam" in (await pg.inner_text("#s-bot")), "resumo avisa rodada parcial")
        alerta = await pg.inner_text("#alertas")
        checar("telegram" in alerta and "credencial recusada" in alerta, "resumo alerta canal com credencial recusada")
        await pg.screenshot(path=CAPTURAS / "1_resumo.png", full_page=True)
        for tela in ["ofertas", "nova", "comissoes", "logs", "ajustes"]:
            await pg.click(f'nav button[data-tela="{tela}"]')
            await pg.wait_for_timeout(400)
            await pg.screenshot(path=CAPTURAS / f"dono_{tela}.png", full_page=True)
        await pg.click('nav button[data-tela="ofertas"]'); await pg.wait_for_timeout(300)
        checar(await pg.locator("script", has_text="alert(1)").count() == 0 and "<script>" in await pg.inner_text("#lista"),
               "título com HTML aparece como texto (sem injeção)")
        checar(await pg.locator('img[src^="javascript"]').count() == 0, "imagem com URL perigosa é bloqueada")
        checar(await pg.locator("text=link aguardando validação").count() == 1, "mostra link aguardando validação")
        checar(await pg.locator(".selo.cl-excelente", has_text="88").count() == 1, "mostra pontuação e 'Excelente oportunidade'")
        checar(await pg.locator(".selo.cl-nao", has_text="Oferta não recomendada").count() == 1, "mostra 'Oferta não recomendada'")
        await pg.click("details.analise >> nth=0 >> summary"); await pg.wait_for_timeout(200)
        texto = await pg.inner_text("details.analise >> nth=0")
        checar("link quebrado" in texto and "não entra na conta" in texto, "explica por que a oferta foi rejeitada")
        await pg.click("details.analise >> nth=1 >> summary"); await pg.wait_for_timeout(200)
        checar("Menor preço registrado" in await pg.inner_text("details.analise >> nth=1"), "explica por que a oferta foi aprovada")
        await pg.select_option("#f-ordem", "pontuacao"); await pg.wait_for_timeout(300)
        chamadas = await pg.evaluate("window.__chamadas")
        checar(any(c.get("tabela") == "vw_promocoes_painel" and any(f[0] == "order" and f[1] == "pontuacao" for f in c["filtros"]) for c in chamadas),
               "ordenar por maior pontuação")
        await pg.select_option("#f-classe", "sem"); await pg.wait_for_timeout(300)
        chamadas = await pg.evaluate("window.__chamadas")
        checar(any(any(f[0] == "is" and f[1] == "classificacao" for f in c.get("filtros", [])) for c in chamadas), "filtrar ofertas aguardando análise")
        await pg.screenshot(path=CAPTURAS / "dono_ofertas_motor.png", full_page=True)
        await pg.select_option("#f-classe", ""); await pg.select_option("#f-ordem", "recentes"); await pg.wait_for_timeout(300)
        await pg.click('[data-acao="aprovar"] >> nth=0'); await pg.wait_for_timeout(300)
        chamadas = await pg.evaluate("window.__chamadas")
        checar(any(c.get("tabela") == "promocoes" and c.get("operacao") == "update" and c["corpo"].get("status") == "aprovada" for c in chamadas),
               "aprovar envia status 'aprovada'")
        await pg.click('nav button[data-tela="nova"]'); await pg.wait_for_timeout(300)
        await pg.select_option("#n-loja", "mercado_livre")
        await pg.fill("#n-link", "https://www.mercadolivre.com.br/produto")
        await pg.fill("#n-titulo", "Air Fryer"); await pg.fill("#n-preco", "299,90")
        await pg.click("#n-salvar"); await pg.wait_for_timeout(300)
        checar("não é um link de afiliado" in await pg.inner_text("#toast"), "link fora do domínio de afiliado é recusado com mensagem clara")
        await pg.fill("#n-link", "https://mercadolivre.com/sec/abc"); await pg.fill("#n-titulo", "Air Fryer"); await pg.fill("#n-preco", "299,90")
        await pg.click("#n-salvar"); await pg.wait_for_timeout(400)
        checar("Oferta salva" in await pg.inner_text("#toast"), "oferta manual salva e 'publicar agora' disparado")
        chamadas = await pg.evaluate("window.__chamadas")
        checar(any(c.get("rpc") == "cadastrar_oferta_manual" and c["args"]["p_preco"] == 299.9 for c in chamadas), "preço '299,90' enviado como 299.9")
        await pg.click('nav button[data-tela="ajustes"]'); await pg.wait_for_timeout(300)
        checar(await pg.locator("text=Credenciais (cifradas no cofre)").count() == 1, "dono vê a área de credenciais")
        checar(await pg.locator("text=••••a1b2").count() == 1, "credencial mostra só os 4 últimos caracteres")
        await pg.fill('[data-chave="publicacao.max_por_dia"]', "9999")
        await pg.click('#form-cfg button[type="submit"]'); await pg.wait_for_timeout(200)
        invalido = not await pg.evaluate("document.querySelector('#form-cfg').checkValidity()")
        chamadas = await pg.evaluate("window.__chamadas")
        checar(invalido and not any(c.get("tabela") == "configuracoes" and c.get("operacao") == "update" for c in chamadas),
               "valor fora do limite é recusado antes de enviar")
        await pg.click("#btn-publicar"); await pg.wait_for_timeout(300)
        checar("Rodada disparada" in await pg.inner_text("#toast"), "botão Publicar agora funciona")
        checar(not erros, f"sem erros de JavaScript ({erros})")
        await pg.close()

        # ---------------------------------------------------------- leitor
        print("Leitor:")
        pg, erros = await pagina("papel=leitor")
        checar(not await pg.is_visible('nav button[data-tela="nova"]'), "leitor não vê a aba Nova")
        checar(not await pg.is_visible("#btn-publicar"), "leitor não vê Publicar agora")
        await pg.click('nav button[data-tela="ofertas"]'); await pg.wait_for_timeout(300)
        checar(await pg.locator("[data-acao]").count() == 0, "leitor não vê botões de ação nas ofertas")
        await pg.click('nav button[data-tela="ajustes"]'); await pg.wait_for_timeout(300)
        checar(await pg.locator("text=Credenciais (cifradas no cofre)").count() == 0, "leitor não vê credenciais")
        checar(await pg.locator('#form-cfg input:not([disabled])').count() == 0, "ajustes ficam somente leitura")
        await pg.screenshot(path=CAPTURAS / "leitor_ajustes.png", full_page=True)
        checar(not erros, f"sem erros de JavaScript ({erros})")
        await pg.close()

        # ---------------------------------------------------------- sem login e sem acesso
        print("Acesso:")
        pg, erros = await pagina("logado=0")
        checar(await pg.locator("text=Entrar no painel").count() == 1, "sem login mostra a tela de entrada")
        checar(not await pg.is_visible("#nav"), "sem login não mostra a navegação")
        await pg.fill("#email", "x@y.com"); await pg.fill("#senha", "senhaerrada1")
        await pg.click('#form-login button[type="submit"]'); await pg.wait_for_timeout(300)
        checar("incorretos" in await pg.inner_text("#msg-login"), "senha errada mostra mensagem clara")
        await pg.screenshot(path=CAPTURAS / "0_login.png")
        await pg.close()
        pg, erros = await pagina("papel=nenhum")
        checar(await pg.locator("text=Sem acesso").count() == 1 and not await pg.is_visible("#nav"), "conta sem papel vê 'Sem acesso'")
        await pg.close()
        await nav.close()
    srv.shutdown()
    print(f"\n{'TUDO OK' if not falhas else f'{len(falhas)} FALHA(S)'} · capturas em {CAPTURAS}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
