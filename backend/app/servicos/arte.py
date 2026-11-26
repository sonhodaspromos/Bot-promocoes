"""Artes das ofertas (feed/Telegram 4:5 e story 9:16), com selo PUBLI e faixa DEMO quando for o caso."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .textos import reais

RAIZ = Path(__file__).resolve().parents[2]
PASTAS_FONTES = [str(RAIZ / "fontes"), "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation"]
PASTA_MARCA = RAIZ / "marca"  # logo.png (redondo, fundo transparente) e story_fundo.png (1080x1920), opcionais


def fonte(tamanho: int, negrito: bool = False):
    nomes = (["Poppins-Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"] if negrito
             else ["Poppins-Regular.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"])
    for pasta in PASTAS_FONTES:
        for nome in nomes:
            caminho = Path(pasta) / nome
            if caminho.exists():
                return ImageFont.truetype(str(caminho), tamanho)
    return ImageFont.load_default(size=tamanho)


def quebrar(draw, texto, f, largura, max_linhas):
    palavras, linhas, atual = texto.split(), [], ""
    for p in palavras:
        teste = (atual + " " + p).strip()
        if draw.textlength(teste, font=f) <= largura:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = p
        if len(linhas) == max_linhas:
            break
    if atual and len(linhas) < max_linhas:
        linhas.append(atual)
    if len(linhas) == max_linhas and " ".join(linhas) != " ".join(palavras):
        ult = linhas[-1]
        while ult and draw.textlength(ult + "...", font=f) > largura:
            ult = ult[:-1]
        linhas[-1] = ult.rstrip() + "..."
    return linhas


def foto_padrao(texto: str = "sem foto") -> Image.Image:
    img = Image.new("RGB", (800, 800), "#EEEEEE")
    ImageDraw.Draw(img).text((400, 400), texto, fill="#999999", font=fonte(60), anchor="mm")
    return img


def abrir_foto(conteudo: bytes | None) -> Image.Image:
    if not conteudo:
        return foto_padrao()
    try:
        return Image.open(io.BytesIO(conteudo)).convert("RGB")
    except Exception:
        return foto_padrao()


def imagem_marca(nome: str, tamanho: tuple[int, int] | None = None) -> Image.Image | None:
    """Imagem da identidade visual em backend/marca. Sem o arquivo (ou corrompido), a arte sai no layout padrão."""
    caminho = PASTA_MARCA / nome
    if not caminho.exists():
        return None
    try:
        img = Image.open(caminho).convert("RGBA")
    except Exception:
        return None
    return img.resize(tamanho, Image.LANCZOS) if tamanho and img.size != tamanho else img


def criar_arte(d: dict, foto: Image.Image, largura: int, altura: int, cfg: dict, destaque: bool = False) -> bytes:
    principal = cfg.get("visual.cor_principal") or "#EE4D2D"
    realce = cfg.get("visual.cor_destaque") or "#FFD400"
    img = Image.new("RGB", (largura, altura), principal)
    m = 60
    story = altura > largura * 1.5
    # story com o fundo da marca: o modelo traz o padrão do @ e a faixa de chamada embaixo (de ~72% da altura
    # para baixo); o cartão do produto ocupa só a parte de cima, sem cobrir a faixa
    fundo = imagem_marca("story_fundo.png", (largura, altura)) if story and cfg.get("visual.usar_fundo_story", True) else None
    if fundo is not None:
        img.paste(fundo.convert("RGB"))
    dr = ImageDraw.Draw(img)

    logo = imagem_marca("logo.png")
    if fundo is not None:
        topo = 70
    else:
        topo = 150 if story else 120
        titulo_topo = "OFERTA IMPERDÍVEL" if destaque else (cfg.get("visual.nome_perfil") or "Ofertas")
        tam_logo = 0
        if logo is not None:
            tam_logo = topo - 20
            img.paste(logo.resize((tam_logo, tam_logo), Image.LANCZOS), (m, (topo - tam_logo) // 2 + (40 if story else 0)),
                      logo.resize((tam_logo, tam_logo), Image.LANCZOS))
        dr.text((largura // 2 + tam_logo // 2, topo // 2 + (40 if story else 10)), titulo_topo[:28],
                fill=realce if destaque else "white", font=fonte(56 if destaque else 50, True), anchor="mm")

    y0 = topo + (60 if story and fundo is None else 20)
    y1 = int(altura * 0.70) if fundo is not None else altura - (260 if story else 40)
    dr.rounded_rectangle((m, y0, largura - m, y1), radius=40, fill="white",
                         outline=principal if fundo is not None else None, width=6 if fundo is not None else 0)

    area = largura - 4 * m
    alt_foto = int(area * (0.9 if fundo is not None else 1.1 if story else 0.86))
    f = foto.copy()
    f.thumbnail((area, alt_foto))
    img.paste(f, ((largura - f.width) // 2, y0 + 40 + (alt_foto - f.height) // 2))

    desconto = float(d.get("desconto_pct") or 0) if d.get("desconto_comprovado", True) else 0
    if desconto >= 1:
        r = 95
        cx, cy = largura - m - r + 25, y0 + r - 25
        dr.ellipse((cx - r, cy - r, cx + r, cy + r), fill=realce)
        dr.text((cx, cy - 12), f"-{desconto:.0f}%", fill="black", font=fonte(54, True), anchor="mm")
        dr.text((cx, cy + 38), "OFF", fill="black", font=fonte(30, True), anchor="mm")

    selos = []
    if d.get("demo"):
        selos.append(("DEMONSTRAÇÃO", "#6B21A8"))
    if cfg.get("transparencia.marcar_publi", True):
        selos.append(("PUBLI", "#222222"))
    x = m + 20
    for texto, cor in selos:
        fs = fonte(26, True)
        w = dr.textlength(texto, font=fs) + 30
        dr.rounded_rectangle((x, y0 + 20, x + w, y0 + 62), radius=20, fill=cor)
        dr.text((x + 15, y0 + 41), texto, fill="white", font=fs, anchor="lm")
        x += w + 10

    y = y0 + 40 + alt_foto + 30
    ft = fonte(44 if story else 40, True)
    for linha in quebrar(dr, d["titulo"], ft, largura - 4 * m, 3 if story else 2):
        dr.text((2 * m, y), linha, fill="#222222", font=ft)
        y += ft.size + 12

    y += 15
    if d.get("desconto_comprovado", True) and d.get("preco_referencia") and float(d["preco_referencia"]) > float(d["preco_promocional"]):
        fa = fonte(40)
        txt = f"de {reais(d['preco_referencia'])}"
        dr.text((2 * m, y), txt, fill="#888888", font=fa)
        dr.line((2 * m + dr.textlength("de ", font=fa), y + fa.size // 2 + 4, 2 * m + dr.textlength(txt, font=fa),
                 y + fa.size // 2 + 4), fill="#888888", width=4)
        y += fa.size + 10
    fp = fonte(96 if story else 84, True)
    dr.text((2 * m, y), reais(d["preco_promocional"]), fill=principal, font=fp)
    if d.get("preco_no_pix"):
        x_pix = 2 * m + dr.textlength(reais(d["preco_promocional"]), font=fp) + 16
        dr.text((x_pix, y + fp.size - 4), "no Pix", fill="#555555", font=fonte(36 if story else 32, True), anchor="ls")
    y_loja = y + fp.size + 18
    if d.get("preco_com_cupom") and (d.get("cupom_codigo") or d.get("cupom_na_pagina")):
        fc = fonte(36 if story else 32, True)
        txt = (f"COM O CUPOM {d['cupom_codigo']}: {reais(d['preco_com_cupom'])}" if d.get("cupom_codigo")
               else f"COM CUPOM DA LOJA: {reais(d['preco_com_cupom'])}")
        largura_txt = min(dr.textlength(txt, font=fc) + 40, largura - 4 * m)
        dr.rounded_rectangle((2 * m, y_loja, 2 * m + largura_txt, y_loja + fc.size + 24), radius=16, fill=realce)
        dr.text((2 * m + 20, y_loja + (fc.size + 24) // 2), txt, fill="black", font=fc, anchor="lm")
        y_loja += fc.size + 40
    dr.text((2 * m, y_loja), (d.get("marketplace_nome") or "")[:30], fill="#555555", font=fonte(34, True))

    if story and fundo is None:
        bx0, by0, bx1, by1 = m + 40, altura - 210, largura - m - 40, altura - 90
        dr.rounded_rectangle((bx0, by0, bx1, by1), radius=60, fill=realce)
        dr.text((largura // 2, (by0 + by1) // 2), (cfg.get("textos.rodape_story") or "")[:30], fill="black",
                font=fonte(46, True), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def criar_arte_cupom(c, foto: Image.Image | None, largura: int, altura: int, cfg: dict) -> bytes:
    """Arte do cupom/campanha: código em destaque (é o que a pessoa digita no checkout), desconto, mínimo e validade."""
    principal = cfg.get("visual.cor_principal") or "#EE4D2D"
    realce = cfg.get("visual.cor_destaque") or "#FFD400"
    story = altura > largura * 1.5
    img = Image.new("RGB", (largura, altura), principal)
    dr = ImageDraw.Draw(img)
    m = 60
    cx = largura // 2

    y = 140 if story else 90
    chamada = "CUPOM DE DESCONTO" if c.tipo == "cupom" else "CAMPANHA DA LOJA"
    dr.text((cx, y), chamada, fill=realce, font=fonte(64 if story else 56, True), anchor="mm")
    y += 80
    dr.text((cx, y), (c.marketplace_nome or c.marketplace or "")[:30], fill="white", font=fonte(44, True), anchor="mm")
    y += 70

    caixa_y0, caixa_y1 = y, altura - (300 if story else 70)
    dr.rounded_rectangle((m, caixa_y0, largura - m, caixa_y1), radius=40, fill="white")

    selos = []
    if c.demo:
        selos.append(("DEMONSTRAÇÃO", "#6B21A8"))
    if cfg.get("transparencia.marcar_publi", True):
        selos.append(("PUBLI", "#222222"))
    x = m + 20
    for texto, cor in selos:
        fs = fonte(26, True)
        w = dr.textlength(texto, font=fs) + 30
        dr.rounded_rectangle((x, caixa_y0 + 20, x + w, caixa_y0 + 62), radius=20, fill=cor)
        dr.text((x + 15, caixa_y0 + 41), texto, fill="white", font=fs, anchor="lm")
        x += w + 10

    # o conteúdo é desenhado 2 vezes: a 1ª só mede a altura, a 2ª centraliza no cartão
    rascunho = ImageDraw.Draw(Image.new("RGB", (largura, altura)))
    topo_util = caixa_y0 + 80
    altura_conteudo = _conteudo_cupom(rascunho, None, c, foto, largura, topo_util, story, principal, cfg) - topo_util
    sobra = (caixa_y1 - 40) - topo_util - altura_conteudo
    _conteudo_cupom(dr, img, c, foto, largura, topo_util + max(0, sobra // 2), story, principal, cfg)

    if story:
        bx0, by0, bx1, by1 = m + 40, altura - 230, largura - m - 40, altura - 110
        dr.rounded_rectangle((bx0, by0, bx1, by1), radius=60, fill=realce)
        dr.text((cx, (by0 + by1) // 2), (cfg.get("textos.rodape_story") or "")[:30], fill="black",
                font=fonte(46, True), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _conteudo_cupom(dr, img, c, foto, largura, y, story, principal, cfg) -> int:
    m, cx = 60, largura // 2
    if foto is not None:
        f = foto.copy()
        f.thumbnail((largura - 4 * m, 520 if story else 300))
        if img is not None:
            img.paste(f, ((largura - f.width) // 2, y))
        y += f.height + 30

    ft = fonte(52 if story else 42, True)
    for linha in quebrar(dr, c.titulo, ft, largura - 4 * m, 2):
        dr.text((cx, y), linha, fill="#222222", font=ft, anchor="ma")
        y += ft.size + 10
    y += 24

    principal_txt, condicoes = _desconto_e_condicoes(c)
    if principal_txt:
        fd = fonte(96 if story else 72, True)
        dr.text((cx, y), principal_txt, fill=principal, font=fd, anchor="ma")
        y += fd.size + 12
    if condicoes:
        # condições (teto, valor mínimo) nunca são cortadas: a fonte diminui até caber
        tamanho = 40 if story else 34
        while True:
            fcnd = fonte(tamanho, True)
            linhas = _quebrar_sem_cortar(dr, condicoes, fcnd, largura - 4 * m)
            if len(linhas) <= 3 or tamanho <= 24:
                break
            tamanho -= 2
        for linha in linhas:
            dr.text((cx, y), linha, fill="#222222", font=fcnd, anchor="ma")
            y += fcnd.size + 8
    if principal_txt or condicoes:
        y += 30

    if c.codigo:
        fcod = fonte(110 if story else 80, True)
        w = min(dr.textlength(c.codigo, font=fcod) + 120, largura - 4 * m)
        h = fcod.size + 80
        x0 = cx - w // 2
        dr.rounded_rectangle((x0, y, x0 + w, y + h), radius=24, fill="#FFF7D6", outline="#222222", width=5)
        dr.text((cx, y + h // 2), c.codigo, fill="#222222", font=fcod, anchor="mm")
        y += h + 18
        dr.text((cx, y), "use este código no pagamento", fill="#555555", font=fonte(34 if story else 30), anchor="ma")
        y += 70

    fv = fonte(40 if story else 36, True)
    fim_local = c.fim_em.astimezone(fuso_cfg(cfg))
    dr.text((cx, y), f"Válido até {fim_local:%d/%m} às {fim_local:%H:%M}", fill="#222222", font=fv, anchor="ma")
    y += fv.size + 18
    if c.regras:
        fr = fonte(32 if story else 28)
        for linha in _quebrar_sem_cortar(dr, c.regras, fr, largura - 4 * m):
            dr.text((cx, y), linha, fill="#555555", font=fr, anchor="ma")
            y += fr.size + 8
    return y


def _desconto_e_condicoes(c) -> tuple[str, str]:
    if not c.desconto_tipo:
        principal = ""
    elif c.desconto_tipo == "percentual":
        principal = f"{c.desconto_valor:g}% OFF".replace(".", ",")
    else:
        principal = f"{reais(c.desconto_valor)} OFF"
    partes = []
    if c.desconto_maximo:
        partes.append(f"desconto máximo de {reais(c.desconto_maximo)}")
    if c.valor_minimo:
        partes.append(f"em compras acima de {reais(c.valor_minimo)}")
    return principal, " · ".join(partes)


def _quebrar_sem_cortar(draw, texto, f, largura):
    linhas, atual = [], ""
    for p in texto.split():
        teste = (atual + " " + p).strip()
        if draw.textlength(teste, font=f) <= largura or not atual:
            atual = teste
        else:
            linhas.append(atual)
            atual = p
    if atual:
        linhas.append(atual)
    return linhas


def fuso_cfg(cfg: dict):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(cfg.get("sistema.fuso_horario") or "America/Recife")
    except Exception:
        return ZoneInfo("America/Recife")
