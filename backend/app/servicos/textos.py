"""Legendas de cada canal, com identificação de publicidade e aviso de comissão."""
from __future__ import annotations

import html


def reais(valor: float) -> str:
    s = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def mostra_desconto(d: dict) -> bool:
    """"De R$ / -X%" só aparece quando o desconto foi comprovado pela análise."""
    return bool(d.get("desconto_comprovado", True))


HEADLINES_TELEGRAM = [
    "O SITE TÁ FICANDO LOUCO COM ESSE PRECINHO 😵💖",
    "CORRE QUE ESSE PREÇO NÃO DURA 🏃‍♀️💨",
    "ACHADINHO DO DIA PRA VOCÊ 💖",
    "PREÇO DE BANANA DETECTADO 🍌",
    "OLHA ESSE DESCONTO 😱",
    "PROMOÇÃO QUE DÁ GOSTO DE VER 😍",
]


def headline(d: dict, cfg: dict) -> str:
    """Chamada do topo, em rodízio fixo por oferta (a mesma oferta sempre recebe a mesma).
    A lista pode ser trocada no painel (textos.headlines_telegram)."""
    lista = [str(h).strip() for h in (cfg.get("textos.headlines_telegram") or []) if str(h).strip()] or HEADLINES_TELEGRAM
    chave = d.get("promocao_id") or d.get("id") or sum(map(ord, str(d.get("titulo") or "")))
    try:
        n = int(chave)
    except (TypeError, ValueError):
        n = sum(map(ord, str(chave)))
    return lista[n % len(lista)]


def legenda_telegram(d: dict, link: str, cfg: dict, variante: str = "A") -> str:
    """Formato Sonho das Promos: chamada, produto, desconto, de/por, link e aviso de preço/estoque.
    Variante B (teste A/B "preço primeiro"): a linha do preço vem antes da chamada.
    "De" e "% de desconto" só aparecem com desconto comprovado; o #publi fica no fim."""
    linhas = []
    if d.get("demo"):
        linhas += ["🧪 <b>[DEMONSTRAÇÃO — não é uma oferta real]</b>", ""]
    pix = " no Pix" if d.get("preco_no_pix") else ""
    preco = f"💥 <b>Por: {reais(d['preco_promocional'])}{pix}</b>"
    if variante == "B":
        linhas += [preco, ""]
    linhas += [f"<b>{html.escape(d.get('ia_chamada') or headline(d, cfg))}</b>", "",
               f"🛍️ {html.escape(d['titulo'])}", ""]
    if d.get("ia_texto"):  # frase da IA (validada: sem números, links nem promessas fora dos dados)
        linhas += [f"✨ {html.escape(d['ia_texto'])}", ""]
    desconto = float(d.get("desconto_pct") or 0) if mostra_desconto(d) else 0
    if desconto >= 1:
        linhas.append(f"😱🔻 <b>{desconto:.0f}% DE DESCONTO</b>")
    if mostra_desconto(d) and d.get("preco_referencia") and float(d["preco_referencia"]) > float(d["preco_promocional"]):
        linhas.append(f"De: <s>{reais(d['preco_referencia'])}</s>")
    if variante != "B":
        linhas.append(preco)
    if d.get("cupom_codigo") and d.get("preco_com_cupom"):
        linhas.append(f"🎟 Com o cupom <code>{html.escape(d['cupom_codigo'])}</code>: <b>{reais(d['preco_com_cupom'])}</b>")
    elif d.get("cupom_na_pagina") and d.get("preco_com_cupom"):
        linhas.append(f"🎟 Com o cupom da loja: <b>{reais(d['preco_com_cupom'])}</b> (ative o cupom na página do produto)")
    linhas += ["", "🛒 <b>Compre aqui:</b>", html.escape(link), "",
               "⚠️ <b>Promoção sujeita à alteração de preço e estoque do site</b>"]
    if cfg.get("transparencia.marcar_publi", True):  # no Telegram, só o #publi (a dona pediu sem o aviso de comissão)
        linhas += ["", "<i>#publi</i>"]
    return "\n".join(linhas)[:1024]


def legenda_instagram(d: dict, cfg: dict, destaque: bool, variante: str = "A") -> str:
    linhas = []
    if d.get("demo"):
        linhas.append("[DEMONSTRAÇÃO — não é uma oferta real]")
    # CONAR (Guia de Influenciadores, maio/2026): a identificação de publicidade precisa ficar visível
    # sem tocar em "mais" — por isso #publi vai na PRIMEIRA linha, não no fim junto das hashtags.
    if cfg.get("transparencia.marcar_publi", True):
        linhas.append("#publi · link de afiliado")
    if destaque:
        linhas += ["🚨 OFERTA IMPERDÍVEL DO DIA 🚨", ""]
    desconto = float(d.get("desconto_pct") or 0) if mostra_desconto(d) else 0
    preco = f"👉 Por {reais(d['preco_promocional'])}" + (f" ({desconto:.0f}% OFF)" if desconto else "")
    if variante == "B":
        linhas += [preco, ""]
    linhas += [f"🔥 {d['titulo']}", ""]
    if d.get("ia_texto"):
        linhas += [f"✨ {d['ia_texto']}", ""]
    if mostra_desconto(d) and d.get("preco_referencia") and float(d["preco_referencia"]) > float(d["preco_promocional"]):
        linhas.append(f"De {reais(d['preco_referencia'])}")
    if variante != "B":
        linhas.append(preco)
    if d.get("cupom_codigo") and d.get("preco_com_cupom"):
        linhas.append(f"🎟 Com o cupom {d['cupom_codigo']}: {reais(d['preco_com_cupom'])}")
    elif d.get("cupom_na_pagina") and d.get("preco_com_cupom"):
        linhas.append(f"🎟 Com o cupom da loja: {reais(d['preco_com_cupom'])} (ative na página do produto)")
    linhas += ["", cfg.get("textos.chamada_instagram") or ""]
    if cfg.get("transparencia.aviso_afiliado"):
        linhas.append(cfg["transparencia.aviso_afiliado"])
    tags = (cfg.get("textos.hashtags") or "").strip()
    if tags:
        linhas += ["", tags]
    return "\n".join(linhas)[:2200]


def _validade(c, cfg) -> str:
    from zoneinfo import ZoneInfo
    fim = c.fim_em.astimezone(ZoneInfo(cfg.get("sistema.fuso_horario") or "America/Recife"))
    return f"{fim:%d/%m} às {fim:%H:%M}"


def legenda_cupom_telegram(c, cfg: dict) -> str:
    linhas = []
    if c.demo:
        linhas.append("🧪 <b>[DEMONSTRAÇÃO — não é um cupom real]</b>")
    if cfg.get("transparencia.marcar_publi", True):
        linhas.append("<i>#publi</i>")
    tipo = "CUPOM" if c.tipo == "cupom" else "CAMPANHA"
    linhas += [f"🎟 <b>{tipo} {html.escape(c.marketplace_nome or c.marketplace)}</b>", f"<b>{html.escape(c.titulo)}</b>", ""]
    if c.descricao():
        linhas.append(f"💸 {html.escape(c.descricao())}")
    if c.codigo:
        linhas.append(f"🔑 Código: <code>{html.escape(c.codigo)}</code> (toque para copiar e use no pagamento)")
    elif c.tipo == "cupom":
        linhas.append("🔑 Sem código: ative o cupom na página do produto")
    linhas.append(f"⏰ Válido até {_validade(c, cfg)}")
    if c.regras:
        linhas.append(f"ℹ️ {html.escape(c.regras)}")
    if c.link_afiliado:
        linhas += ["", f"🛒 Aproveite aqui: {html.escape(c.link_afiliado)}"]
    return "\n".join(linhas)[:1024]


def legenda_cupom_instagram(c, cfg: dict) -> str:
    linhas = []
    if c.demo:
        linhas.append("[DEMONSTRAÇÃO — não é um cupom real]")
    linhas += [f"🎟 {c.titulo}"]
    if c.descricao():
        linhas.append(c.descricao())
    if c.codigo:
        linhas.append(f"Código: {c.codigo}")
    elif c.tipo == "cupom":
        linhas.append("Sem código: ative o cupom na página do produto")
    linhas += [f"Válido até {_validade(c, cfg)}", "", cfg.get("textos.chamada_instagram") or ""]
    if cfg.get("transparencia.aviso_afiliado"):
        linhas.append(cfg["transparencia.aviso_afiliado"])
    tags = ("#publi " if cfg.get("transparencia.marcar_publi", True) else "") + "#cupom " + (cfg.get("textos.hashtags") or "")
    linhas += ["", tags.strip()]
    return "\n".join(linhas)[:2200]
