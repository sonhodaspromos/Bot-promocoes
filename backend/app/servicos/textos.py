"""Legendas de cada canal, com identificação de publicidade e aviso de comissão."""
from __future__ import annotations

import html


def reais(valor: float) -> str:
    s = f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def mostra_desconto(d: dict) -> bool:
    """"De R$ / -X%" só aparece quando o desconto foi comprovado pela análise."""
    return bool(d.get("desconto_comprovado", True))


def legenda_telegram(d: dict, link: str, cfg: dict) -> str:
    linhas = []
    if d.get("demo"):
        linhas.append("🧪 <b>[DEMONSTRAÇÃO — não é uma oferta real]</b>")
    if cfg.get("transparencia.marcar_publi", True):
        linhas.append("<i>#publi</i>")
    linhas += [f"🔥 <b>{html.escape(d['titulo'])}</b>", ""]
    if mostra_desconto(d) and d.get("preco_referencia") and float(d["preco_referencia"]) > float(d["preco_promocional"]):
        linhas.append(f"❌ De <s>{reais(d['preco_referencia'])}</s>")
    desconto = float(d.get("desconto_pct") or 0) if mostra_desconto(d) else 0
    linhas.append(f"✅ Por <b>{reais(d['preco_promocional'])}</b>" + (f"  ({desconto:.0f}% OFF)" if desconto else ""))
    if d.get("cupom_codigo") and d.get("preco_com_cupom"):
        linhas.append(f"🎟 Com o cupom <code>{html.escape(d['cupom_codigo'])}</code>: <b>{reais(d['preco_com_cupom'])}</b>")
    linhas += ["", f"🛒 {html.escape(d.get('marketplace_nome') or '')}: {html.escape(link)}"]
    aviso = cfg.get("transparencia.aviso_afiliado")
    if aviso:
        linhas += ["", f"<i>{html.escape(aviso)}</i>"]
    return "\n".join(linhas)[:1024]


def legenda_instagram(d: dict, cfg: dict, destaque: bool) -> str:
    linhas = []
    if d.get("demo"):
        linhas.append("[DEMONSTRAÇÃO — não é uma oferta real]")
    # CONAR (Guia de Influenciadores, maio/2026): a identificação de publicidade precisa ficar visível
    # sem tocar em "mais" — por isso #publi vai na PRIMEIRA linha, não no fim junto das hashtags.
    if cfg.get("transparencia.marcar_publi", True):
        linhas.append("#publi · link de afiliado")
    if destaque:
        linhas += ["🚨 OFERTA IMPERDÍVEL DO DIA 🚨", ""]
    linhas += [f"🔥 {d['titulo']}", ""]
    if mostra_desconto(d) and d.get("preco_referencia") and float(d["preco_referencia"]) > float(d["preco_promocional"]):
        linhas.append(f"De {reais(d['preco_referencia'])}")
    desconto = float(d.get("desconto_pct") or 0) if mostra_desconto(d) else 0
    linhas.append(f"👉 Por {reais(d['preco_promocional'])}" + (f" ({desconto:.0f}% OFF)" if desconto else ""))
    if d.get("cupom_codigo") and d.get("preco_com_cupom"):
        linhas.append(f"🎟 Com o cupom {d['cupom_codigo']}: {reais(d['preco_com_cupom'])}")
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
    linhas.append(f"⏰ Válido até {_validade(c, cfg)}")
    if c.regras:
        linhas.append(f"ℹ️ {html.escape(c.regras)}")
    if c.link_afiliado:
        linhas += ["", f"🛒 Aproveite aqui: {html.escape(c.link_afiliado)}"]
    aviso = cfg.get("transparencia.aviso_afiliado")
    if aviso:
        linhas += ["", f"<i>{html.escape(aviso)}</i>"]
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
    linhas += [f"Válido até {_validade(c, cfg)}", "", cfg.get("textos.chamada_instagram") or ""]
    if cfg.get("transparencia.aviso_afiliado"):
        linhas.append(cfg["transparencia.aviso_afiliado"])
    tags = ("#publi " if cfg.get("transparencia.marcar_publi", True) else "") + "#cupom " + (cfg.get("textos.hashtags") or "")
    linhas += ["", tags.strip()]
    return "\n".join(linhas)[:2200]
