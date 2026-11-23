"""Legendas de cada canal, com identificação de publicidade e aviso de comissão."""
from __future__ import annotations

import html


def reais(valor: float) -> str:
    s = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def legenda_telegram(d: dict, link: str, cfg: dict) -> str:
    linhas = []
    if d.get("demo"):
        linhas.append("🧪 <b>[DEMONSTRAÇÃO — não é uma oferta real]</b>")
    if cfg.get("transparencia.marcar_publi", True):
        linhas.append("<i>#publi</i>")
    linhas += [f"🔥 <b>{html.escape(d['titulo'])}</b>", ""]
    if d.get("preco_referencia") and float(d["preco_referencia"]) > float(d["preco_promocional"]):
        linhas.append(f"❌ De <s>{reais(d['preco_referencia'])}</s>")
    desconto = float(d.get("desconto_pct") or 0)
    linhas.append(f"✅ Por <b>{reais(d['preco_promocional'])}</b>" + (f"  ({desconto:.0f}% OFF)" if desconto else ""))
    linhas += ["", f"🛒 {html.escape(d.get('marketplace_nome') or '')}: {html.escape(link)}"]
    aviso = cfg.get("transparencia.aviso_afiliado")
    if aviso:
        linhas += ["", f"<i>{html.escape(aviso)}</i>"]
    return "\n".join(linhas)[:1024]


def legenda_instagram(d: dict, cfg: dict, destaque: bool) -> str:
    linhas = []
    if d.get("demo"):
        linhas.append("[DEMONSTRAÇÃO — não é uma oferta real]")
    if destaque:
        linhas += ["🚨 OFERTA IMPERDÍVEL DO DIA 🚨", ""]
    linhas += [f"🔥 {d['titulo']}", ""]
    if d.get("preco_referencia") and float(d["preco_referencia"]) > float(d["preco_promocional"]):
        linhas.append(f"De {reais(d['preco_referencia'])}")
    desconto = float(d.get("desconto_pct") or 0)
    linhas.append(f"👉 Por {reais(d['preco_promocional'])}" + (f" ({desconto:.0f}% OFF)" if desconto else ""))
    linhas += ["", cfg.get("textos.chamada_instagram") or ""]
    if cfg.get("transparencia.aviso_afiliado"):
        linhas.append(cfg["transparencia.aviso_afiliado"])
    tags = ("#publi " if cfg.get("transparencia.marcar_publi", True) else "") + (cfg.get("textos.hashtags") or "")
    linhas += ["", tags.strip()]
    return "\n".join(linhas)[:2200]
