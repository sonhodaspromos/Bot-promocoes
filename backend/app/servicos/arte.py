"""Artes das ofertas (feed/Telegram 4:5 e story 9:16), com selo PUBLI e faixa DEMO quando for o caso."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .textos import reais

PASTAS_FONTES = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation",
                 str(Path(__file__).resolve().parents[2] / "fontes")]


def fonte(tamanho: int, negrito: bool = False):
    nomes = ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"] if negrito else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
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


def criar_arte(d: dict, foto: Image.Image, largura: int, altura: int, cfg: dict, destaque: bool = False) -> bytes:
    principal = cfg.get("visual.cor_principal") or "#EE4D2D"
    realce = cfg.get("visual.cor_destaque") or "#FFD400"
    img = Image.new("RGB", (largura, altura), principal)
    dr = ImageDraw.Draw(img)
    m = 60
    story = altura > largura * 1.5

    topo = 150 if story else 120
    titulo_topo = "OFERTA IMPERDÍVEL" if destaque else (cfg.get("visual.nome_perfil") or "Ofertas")
    dr.text((largura // 2, topo // 2 + (40 if story else 10)), titulo_topo[:28],
            fill=realce if destaque else "white", font=fonte(60 if destaque else 54, True), anchor="mm")

    y0, y1 = topo + (60 if story else 20), altura - (260 if story else 40)
    dr.rounded_rectangle((m, y0, largura - m, y1), radius=40, fill="white")

    area = largura - 4 * m
    alt_foto = int(area * (1.1 if story else 0.86))
    f = foto.copy()
    f.thumbnail((area, alt_foto))
    img.paste(f, ((largura - f.width) // 2, y0 + 40 + (alt_foto - f.height) // 2))

    desconto = float(d.get("desconto_pct") or 0)
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
    if d.get("preco_referencia") and float(d["preco_referencia"]) > float(d["preco_promocional"]):
        fa = fonte(40)
        txt = f"de {reais(d['preco_referencia'])}"
        dr.text((2 * m, y), txt, fill="#888888", font=fa)
        dr.line((2 * m + dr.textlength("de ", font=fa), y + fa.size // 2 + 4, 2 * m + dr.textlength(txt, font=fa),
                 y + fa.size // 2 + 4), fill="#888888", width=4)
        y += fa.size + 10
    fp = fonte(96 if story else 84, True)
    dr.text((2 * m, y), reais(d["preco_promocional"]), fill=principal, font=fp)
    dr.text((2 * m, y + fp.size + 18), (d.get("marketplace_nome") or "")[:30], fill="#555555", font=fonte(34, True))

    if story:
        bx0, by0, bx1, by1 = m + 40, altura - 210, largura - m - 40, altura - 90
        dr.rounded_rectangle((bx0, by0, bx1, by1), radius=60, fill=realce)
        dr.text((largura // 2, (by0 + by1) // 2), (cfg.get("textos.rodape_story") or "")[:30], fill="black",
                font=fonte(46, True), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()
