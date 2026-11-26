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
            pg.on("dialog", lambda d: asyncio.ensure_future(d.accept("teste de emergência") if d.type == "prompt" else d.accept()))
            await pg.goto(f"{base}?{params}")
            await pg.wait_for_timeout(500)
            return pg, erros

        def checar(cond, msg):
            print(("  ✔ " if cond else "  ✖ ") + msg)
            if not cond:
                falhas.append(msg)

        # ---------------------------------------------------------- dono
        print("Dono:")
        import re as _re
        def _nav(caminho):
            return _re.search(r"<nav.*?</nav>", (RAIZ / caminho).read_text(encoding="utf-8"), _re.S).group(0)
        checar(_nav("painel/index.html") == _nav("testes/painel.html"), "página de teste tem o mesmo menu do painel real")
        pg, erros = await pagina("papel=dono")
        checar(await pg.is_visible("#faixa-demo"), "faixa de MODO DEMONSTRAÇÃO aparece")
        checar("DEMONSTRAÇÃO" in await pg.inner_text("#faixa-demo"), "faixa explica o modo demo")
        checar(await pg.inner_text("#k-encontrados") == "40", "resumo mostra produtos encontrados")
        checar("parcial" in (await pg.inner_text("#s-bot")) or "falharam" in (await pg.inner_text("#s-bot")), "resumo avisa rodada parcial")
        alerta = await pg.inner_text("#alertas")
        checar("telegram" in alerta and "credencial recusada" in alerta, "resumo alerta canal com credencial recusada")
        await pg.screenshot(path=CAPTURAS / "1_resumo.png", full_page=True)
        largas = []
        for tela in ["resumo", "ofertas", "nova", "cupons", "comissoes", "financeiro", "desempenho", "logs", "ajustes"]:
            await pg.click(f'nav button[data-tela="{tela}"]')
            await pg.wait_for_timeout(400)
            if await pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
                largas.append(tela)
            sobrepostos = await pg.evaluate("""() => [...document.querySelectorAll('.linha')].filter((l) => {
                const [a, b] = [...l.children].map((c) => c.getBoundingClientRect()); return a && b && a.right > b.left + 1; }).length""")
            if sobrepostos:
                largas.append(tela + " (campos sobrepostos)")
            await pg.screenshot(path=CAPTURAS / f"dono_{tela}.png", full_page=True)
        checar(not largas, f"celular: nenhuma tela passa da largura nem tem campos sobrepostos {largas or ''}")
        # ---- cupons
        await pg.click('nav button[data-tela="cupons"]'); await pg.wait_for_timeout(400)
        lista = await pg.inner_text("#c-lista")
        checar("ACHADO15" in lista and "15% OFF" in lista and "acima de R$" in lista, "cupons: mostra código, desconto e mínimo")
        checar("campanha oficial (automática)" in lista and "Remover" not in await pg.inner_text("#cupom-42"),
               "cupons: campanha automática aparece e não pode ser removida pelo painel")
        checar(await pg.locator("#c-lista b", has_text="selecionados").count() == 0, "cupons: regras com HTML aparecem como texto")
        await pg.click('#cupom-41 [data-acao="produtos"]'); await pg.wait_for_timeout(400)
        prod = await pg.inner_text("#prod-41")
        checar("vale o cupom" in prod and "sugestão do bot" in prod, "cupons: separa produto confirmado de sugestão")
        await pg.click('#prod-41 [data-acao="aprovar"]'); await pg.wait_for_timeout(300)
        chamadas = await pg.evaluate("window.__chamadas")
        checar(any(c.get("tabela") == "cupons_produtos" and c.get("operacao") == "update" and (c.get("corpo") or {}).get("aprovado") is True
                   for c in chamadas), "cupons: aprovar sugestão")
        await pg.click("#c-novo"); await pg.wait_for_timeout(400)
        await pg.fill("#cp-codigo", "fim de semana15"); await pg.fill("#cp-titulo", "Cupom teste")
        await pg.fill("#cp-valor", "95"); await pg.click("#cp-salvar"); await pg.wait_for_timeout(300)
        checar("90%" in await pg.inner_text("#toast"), "cupons: recusa desconto acima de 90%")
        await pg.fill("#cp-valor", "15"); await pg.fill("#cp-minimo", "79,90"); await pg.click("#cp-salvar"); await pg.wait_for_timeout(400)
        chamadas = await pg.evaluate("window.__chamadas")
        novo = [c for c in chamadas if c.get("tabela") == "cupons" and c.get("operacao") == "insert"]
        checar(novo and novo[-1]["corpo"]["codigo"] == "FIMDESEMANA15" and novo[-1]["corpo"]["valor_minimo"] == 79.9,
               "cupons: salva código em maiúsculas e valor mínimo")
        await pg.screenshot(path=CAPTURAS / "dono_cupons.png", full_page=True)
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
        checar(await pg.locator('[data-acao="story"]').count() == 0, "story manual: botão não aparece em oferta aguardando aprovação")
        await pg.click('#f-status .chip[data-s="aprovada"]'); await pg.wait_for_timeout(400)
        await pg.evaluate("navigator.clipboard.writeText = async (t) => { window.__copiado = t; }")
        await pg.click('[data-acao="story"] >> nth=0')
        await pg.wait_for_selector(".janela-story img", timeout=8000); await pg.wait_for_timeout(300)
        info = await pg.evaluate("""() => { const i = document.querySelector('.janela-story img'); const c = document.createElement('canvas');
            c.width = i.naturalWidth; c.height = i.naturalHeight; const x = c.getContext('2d'); x.drawImage(i, 0, 0);
            return { w: i.naturalWidth, h: i.naturalHeight, faixa: [...x.getImageData(540, 1450, 1, 1).data], cartao: [...x.getImageData(950, 1250, 1, 1).data] }; }""")
        checar(info["w"] == 1080 and info["h"] == 1920, "story manual: arte 1080x1920")
        checar(info["cartao"][:3] == [255, 255, 255] or min(info["cartao"][:3]) > 240, "story manual: cartão branco do produto")
        checar(info["faixa"][0] > 230 and info["faixa"][1] < 140 and info["faixa"][2] > 160, "story manual: faixa rosa do fundo da marca aparece embaixo")
        checar(await pg.locator(".janela-story", has_text="não liberou a foto").count() == 0, "story manual: sem aviso de foto quando a oferta não tem foto")
        await pg.screenshot(path=CAPTURAS / "dono_story_manual.png")
        await pg.click('.janela-story [data-x="copiar"]'); await pg.wait_for_timeout(200)
        checar(await pg.evaluate("window.__copiado") == "https://supabase.falso/functions/v1/r?l=77&c=instagram_story"
               or (await pg.evaluate("window.__copiado") or "").endswith("/functions/v1/r?l=77&c=instagram_story"),
               "story manual: copia o link rastreado do story")
        await pg.click('.janela-story [data-x="fechar"]'); await pg.wait_for_timeout(200)
        checar(await pg.locator(".janela-story").count() == 0, "story manual: fecha a janela")
        await pg.click('#f-status .chip[data-s="encontrada"]'); await pg.wait_for_timeout(400)
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
        # ---- financeiro (custos por serviço; estimada x confirmada x lucro)
        await pg.click('nav button[data-tela="financeiro"]'); await pg.wait_for_timeout(400)
        fin = await pg.inner_text("#conteudo") if await pg.locator("#conteudo").count() else await pg.inner_text("main")
        checar("Sem cotação informada" in fin and "—" in fin, "financeiro: sem cotação do dólar o lucro não é calculado")
        fin = fin.replace("\xa0", " ")
        checar("R$ 40,00" in fin and "R$ 120,00" in fin and "(90%)" in fin, "financeiro: estimada separada da confirmada e alerta de 90% do teto")
        await pg.fill("#f-cot", "5"); await pg.dispatch_event("#f-cot", "change"); await pg.wait_for_timeout(400)
        fin = (await pg.inner_text("main")).replace("\xa0", " ")
        checar("R$ 60,00" in fin, "financeiro: lucro líquido = confirmada − (custos + WhatsApp) × cotação informada")
        # ---- desempenho (inteligência comercial)
        await pg.click('nav button[data-tela="desempenho"]'); await pg.wait_for_timeout(400)
        kpis = await pg.inner_text("#kpis")
        checar("12" in kpis and "R$" in kpis, "desempenho mostra cliques e comissão atribuída")
        checar("<b>" not in await pg.inner_html("#tabela-seg") or "&lt;b&gt;" in await pg.inner_html("#tabela-seg"), "segmentos escapam HTML")
        await pg.click('[data-dim="categoria"]'); await pg.wait_for_timeout(150)
        checar("Casa" in await pg.inner_text("#tabela-seg") and "poucos dados" in await pg.inner_text("#tabela-seg"),
               "segmento com pouca amostra é sinalizado")
        checar("aprendizado começa com 20" in await pg.inner_text("#aprendizado"), "falta de dados do aprendizado é dita")
        checar("sem repasse após 75 dias" in await pg.inner_text("#conciliacao"), "conciliação mostra aprovadas sem repasse")
        sel = await pg.inner_text("#selecoes")
        checar("Recusada" in sel and "GTIN" in sel and "1º lugar" in sel, "motivos de escolha e recusa aparecem")
        await pg.fill("#rp-mes", "2026-09"); await pg.fill("#rp-valor", "20,00")
        await pg.click('#form-repasse button[type="submit"]'); await pg.wait_for_timeout(300)
        checar("Divergência" in await pg.inner_text("#toast"), "repasse divergente não marca nada como pago")
        await pg.fill("#ab-nome", "Preço primeiro"); await pg.click("#ab-criar"); await pg.wait_for_timeout(300)
        chamadas = await pg.evaluate("window.__chamadas")
        checar(any(c.get("tabela") == "experimentos" and c.get("operacao") == "insert" for c in chamadas), "editor/dono inicia teste A/B")
        await pg.screenshot(path=CAPTURAS / "7_desempenho.png", full_page=True)
        await pg.click('nav button[data-tela="ajustes"]'); await pg.wait_for_timeout(400)

        # ---- convites (cadastro fechado)
        checar(await pg.locator("#convites").count() == 1 and "editora@teste.com" in await pg.inner_text("#cv-lista"), "dono vê os convites")
        await pg.fill("#cv-email", "nova@teste.com"); await pg.select_option("#cv-papel", "editor")
        await pg.click("#cv-criar"); await pg.wait_for_timeout(300)
        chamadas = await pg.evaluate("window.__chamadas")
        checar(any(c.get("tabela") == "convites" and c.get("operacao") == "insert" and c["corpo"]["email"] == "nova@teste.com" for c in chamadas), "dono cria convite")
        checar(await pg.locator('[data-chave="sistema.parada_emergencia"]').is_disabled(), "parada de emergência não é editável pelo formulário (só pelo botão)")
        # ---- botão de emergência
        checar(await pg.is_visible("#btn-parar") and not await pg.is_visible("#faixa-parada"), "botão Parar tudo visível, sem faixa de parada")
        await pg.click("#btn-parar"); await pg.wait_for_timeout(400)
        chamadas = await pg.evaluate("window.__chamadas")
        checar(any(c.get("rpc") == "ativar_parada_emergencia" and c["args"]["p_motivo"] for c in chamadas), "Parar tudo chama a RPC com motivo")
        checar(await pg.is_visible("#faixa-parada") and "INTERROMPIDAS" in await pg.inner_text("#faixa-parada"), "faixa vermelha avisa publicações interrompidas")
        checar("Retomar" in await pg.inner_text("#btn-parar"), "dono pode retomar")
        await pg.click("#btn-parar"); await pg.wait_for_timeout(400)
        checar(not await pg.is_visible("#faixa-parada"), "dono retomou as publicações")
        checar(not erros, f"sem erros de JavaScript ({erros})")
        await pg.close()

        # ---------------------------------------------------------- editor
        print("Editor:")
        pg, erros = await pagina("papel=editor")
        await pg.click('nav button[data-tela="ajustes"]'); await pg.wait_for_timeout(300)
        checar(await pg.locator("[data-mkt]:not([disabled])").count() == 0, "editor não altera domínios de afiliado")
        checar(await pg.locator("#convites").count() == 0, "editor não vê convites")
        await pg.click("#btn-parar"); await pg.wait_for_timeout(400)
        checar(await pg.is_visible("#faixa-parada") and not await pg.is_visible("#btn-parar"), "editor para tudo, mas só o dono retoma")
        checar(not erros, f"sem erros de JavaScript ({erros})")
        await pg.close()

        # ---------------------------------------------------------- leitor
        print("Leitor:")
        pg, erros = await pagina("papel=leitor")
        checar(not await pg.is_visible('nav button[data-tela="nova"]'), "leitor não vê a aba Nova")
        checar(not await pg.is_visible("#btn-publicar"), "leitor não vê Publicar agora")
        checar(not await pg.is_visible("#btn-parar"), "leitor não vê Parar tudo")
        await pg.click('nav button[data-tela="desempenho"]'); await pg.wait_for_timeout(300)
        checar(await pg.locator("#form-repasse").count() == 0 and await pg.locator("#ab-criar").count() == 0,
               "leitor vê o desempenho, mas não registra repasse nem cria teste")
        await pg.click('nav button[data-tela="ofertas"]'); await pg.wait_for_timeout(300)
        checar(await pg.locator("[data-acao]").count() == 0, "leitor não vê botões de ação nas ofertas")
        await pg.click('nav button[data-tela="cupons"]'); await pg.wait_for_timeout(300)
        checar(await pg.locator("#c-novo").count() == 0 and await pg.locator("#c-lista [data-acao]").count() == 0,
               "leitor vê cupons, mas não cadastra nem altera")
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
        # ---- 2º fator
        pg, erros = await pagina("papel=dono&aal=aal1")
        checar(await pg.locator("text=Verificação em duas etapas").count() == 1 and not await pg.is_visible("#nav"), "senha sem 2º fator não abre o painel")
        await pg.fill("#mfa-codigo", "000000"); await pg.click('#form-mfa button[type="submit"]'); await pg.wait_for_timeout(300)
        checar("incorreto" in await pg.inner_text("#msg-mfa"), "código errado é recusado")
        await pg.fill("#mfa-codigo", "123456"); await pg.click('#form-mfa button[type="submit"]'); await pg.wait_for_timeout(600)
        checar(await pg.is_visible("#nav"), "código certo libera o painel")
        await pg.close()
        pg, erros = await pagina("papel=dono&aal=aal1&fator=nenhum")
        checar(await pg.locator("#mfa-qr").count() == 1 and "JBSWY3DP" in await pg.inner_text("#mfa-segredo"), "1º acesso mostra QR code e chave do autenticador")
        await pg.screenshot(path=CAPTURAS / "0_mfa_cadastro.png")
        checar(not erros, f"sem erros de JavaScript no 2º fator ({erros})")
        await pg.close()
        pg, erros = await pagina("papel=dono&aal=aal1&fator=pendente")
        checar(await pg.locator("#mfa-qr").count() == 1 and "already exists" not in await pg.inner_text("body"),
               "cadastro anterior não concluído é limpo e o QR code aparece (sem 'already exists')")
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
