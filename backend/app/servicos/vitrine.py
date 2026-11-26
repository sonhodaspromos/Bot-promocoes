"""Vitrine pública de ofertas: site ESTÁTICO gerado pelo bot (sem acesso público ao banco).

* Páginas: início (com busca e filtros), uma por categoria, uma por oferta, cupons, sitemap.xml, robots.txt
  e ofertas.json. Responsiva, sem dependências externas.
* SEO: título e descrição por página, URL canônica, Open Graph e dados estruturados (schema.org):
  ItemList no início e Product/Offer em cada oferta — só com preço e disponibilidade reais.
* Só entram ofertas reais publicadas ou aprovadas, com link validado (nada de demonstração).
  "De R$" e % de desconto só aparecem quando o desconto foi comprovado pelo histórico/cupom.
* Todo link passa pela função de cliques (?c=vitrine): mesmo antifraude e métricas dos canais.
* Transparência: aviso de publicidade/afiliado no topo de todas as páginas (CONAR) e link de privacidade.
* Preço: cada card mostra quando o preço foi conferido e avisa que pode ter mudado (exigência da Amazon
  quando o preço não é atualizado de hora em hora; boa prática para as demais lojas). O banco só entrega
  oferta vigente, com preço conferido nas últimas 24 h e sem alta desde a publicação.
* Com oferta da Amazon na página, o aviso do Programa de Associados aparece (Contrato Operacional, seção 5).
* Acessibilidade: um único h1 por página, foco visível, contraste AA nos botões, imagem quebrada some.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .textos import reais

CSS = """*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:linear-gradient(180deg,#fce0f5 0,#fdeef7 320px) fixed;color:#1f2328}
header{background:linear-gradient(90deg,#f12d78,#dc1b63);color:#fff;padding:14px 16px}header a{color:#fff;text-decoration:none}.marca{font-size:20px;font-weight:700;margin:0}h1{font-size:20px;margin:8px 0}
.publi{background:#fff3d6;color:#7a5200;font-size:13px;padding:8px 16px}main{max-width:1100px;margin:0 auto;padding:12px}
nav.cats{display:flex;gap:8px;overflow-x:auto;padding:8px 0}nav.cats a{background:#fff;border:1px solid #f6cde1;border-radius:999px;padding:6px 12px;color:#b9033f;text-decoration:none;white-space:nowrap;font-size:14px}
.filtros{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}.filtros input,.filtros select{padding:10px;border:1px solid #d0d7de;border-radius:10px;font-size:15px;flex:1;min-width:140px}
.grade{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
.card{background:#fff;border:1px solid #f6cde1;border-radius:14px;padding:10px;display:flex;flex-direction:column;gap:6px}.card img{width:100%;aspect-ratio:1;object-fit:contain;border-radius:10px;background:#fdeef7}
.card h2,.card h3{font-size:14px;margin:0;line-height:1.3}.preco{font-size:18px;font-weight:700;color:#b9033f}.de{color:#57606a;text-decoration:line-through;font-size:13px}
.off{background:#1a7f37;color:#fff;border-radius:6px;padding:1px 6px;font-size:12px}.loja{color:#57606a;font-size:12px}
.botao{display:block;text-align:center;background:#dc1b63;color:#fff;border-radius:10px;padding:10px;text-decoration:none;font-weight:600;margin-top:auto}
.cupom{background:#fff;border:2px dashed #dc1b63;border-radius:12px;padding:12px;margin-bottom:10px}code{background:#f0f0f0;padding:2px 6px;border-radius:6px}
footer{color:#57606a;font-size:12px;text-align:center;padding:24px 12px}[hidden]{display:none!important}
a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #b9033f;outline-offset:2px}.conferido{color:#57606a;font-size:11px}"""

JS = """const q=document.getElementById('busca'),c=document.getElementById('categoria'),m=document.getElementById('loja'),o=document.getElementById('ordem');
function norm(t){return (t||'').normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase()}
function aplicar(){const termo=norm(q.value).split(/\\s+/).filter(Boolean);const cards=[...document.querySelectorAll('.card[data-titulo]')];
cards.forEach(el=>{const ok=termo.every(t=>el.dataset.titulo.includes(t))&&(!c.value||el.dataset.categoria===c.value)&&(!m.value||el.dataset.loja===m.value);el.hidden=!ok});
const g=document.getElementById('grade');const chave={preco:(a,b)=>a.dataset.preco-b.dataset.preco,desconto:(a,b)=>b.dataset.desconto-a.dataset.desconto,recentes:(a,b)=>b.dataset.ordem-a.dataset.ordem}[o.value];
cards.sort(chave).forEach(el=>g.appendChild(el));document.getElementById('vazio').hidden=cards.some(el=>!el.hidden)}
[q,c,m,o].forEach(e=>e&&e.addEventListener('input',aplicar));"""


def slug(texto: str) -> str:
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:60] or "item"


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


def _e(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def _https(url) -> str | None:
    return url if isinstance(url, str) and url.startswith("https://") else None


AVISO_AMAZON = "Como Associado Amazon, ganho com compras qualificadas."
FUSO = timezone(timedelta(hours=-3))


def _conferido(valor) -> str | None:
    if not valor:
        return None
    try:
        t = valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone(FUSO).strftime("%d/%m às %H:%M")


class GeradorVitrine:
    def __init__(self, url_base: str, url_cliques: str, nome: str = "Ofertas"):
        self.base = url_base.rstrip("/")
        self.cliques = url_cliques
        self.nome = nome

    def link(self, o: dict) -> str:
        return f"{self.cliques}?l={int(o['link_id'])}&c=vitrine"

    def _pagina(self, titulo, descricao, caminho, corpo, json_ld=None, extra_head="", amazon=False):
        canon = f"{self.base}/{caminho}".rstrip("/") if caminho else self.base + "/"
        ld = f'<script type="application/ld+json">{json.dumps(json_ld, ensure_ascii=False).replace("</", "<\\/")}</script>' if json_ld else ""
        return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(titulo)}</title>
<meta name="description" content="{_e(descricao[:160])}"><link rel="canonical" href="{_e(canon)}">
<meta property="og:title" content="{_e(titulo)}"><meta property="og:description" content="{_e(descricao[:200])}">
<meta property="og:type" content="website"><meta property="og:url" content="{_e(canon)}">{extra_head}
<style>{CSS}</style>{ld}</head><body>
<header><p class="marca"><a href="{_e(self.base)}/">{_e(self.nome)}</a></p></header>
<div class="publi" role="note">#publi · Esta página contém links de afiliado: podemos ganhar comissão nas compras, sem custo extra para você. Preços e estoque podem mudar na loja.{(" " + AVISO_AMAZON) if amazon else ""}</div>
<main>{corpo}</main>
<footer>Preços conferidos pelo bot; confirme na loja antes de comprar. <a href="{_e(self.base)}/privacidade.html">Privacidade</a> · <a href="{_e(self.base)}/cupons.html">Cupons</a></footer>
</body></html>"""

    def _card(self, o: dict, nivel: str = "h2") -> str:
        img = _https(o.get("imagem_url"))
        de = (f'<span class="de">{reais(o["preco_referencia"])}</span> <span class="off">{o["desconto_pct"]:.0f}% OFF</span>'
              if o.get("desconto_comprovado") and o.get("preco_referencia") and o.get("desconto_pct") else "")
        return (f'<article class="card" data-titulo="{_e(_norm(o["titulo"]))}" data-categoria="{_e(o.get("categoria") or "")}" '
                f'data-loja="{_e(o.get("marketplace_nome") or "")}" data-preco="{float(o["preco"]):.2f}" '
                f'data-desconto="{float(o.get("desconto_pct") or 0) if o.get("desconto_comprovado") else 0:.0f}" data-ordem="{int(o.get("ordem", 0))}">'
                + (f'<img src="{_e(img)}" alt="{_e(o["titulo"])}" loading="lazy" width="300" height="300" '
                   f'referrerpolicy="no-referrer" onerror="this.remove()">' if img else "")
                + f'<{nivel}><a href="oferta/{int(o["promocao_id"])}-{slug(o["titulo"])}.html">{_e(o["titulo"])}</a></{nivel}>'
                f'<div>{de}</div><div class="preco">{reais(o["preco"])}</div>'
                + (f'<div class="conferido">preço conferido em {_e(_conferido(o.get("conferido_em")))}; pode ter mudado na loja</div>'
                   if _conferido(o.get("conferido_em")) else "")
                + (f'<div>🎟 cupom <code>{_e(o["cupom_codigo"])}</code></div>' if o.get("cupom_codigo")
                   else '<div>🎟 com cupom da loja (ative na página)</div>' if o.get("cupom_na_pagina") else "")
                + f'<div class="loja">{_e(o.get("marketplace_nome") or "")}</div>'
                f'<a class="botao" href="{_e(self.link(o))}" rel="sponsored nofollow noopener" target="_blank">Ver na loja</a></article>')

    def gerar(self, ofertas: list[dict], cupons: list[dict], saida: Path, agora: datetime | None = None) -> dict:
        agora = agora or datetime.now(timezone.utc)
        saida.mkdir(parents=True, exist_ok=True)
        (saida / "oferta").mkdir(exist_ok=True)
        (saida / "categoria").mkdir(exist_ok=True)
        ofertas = [o for o in ofertas if o.get("link_id") and o.get("preco") and not o.get("demo")]
        amz = lambda lista: any((o.get("marketplace_codigo") or "").lower() == "amazon" for o in lista)  # noqa: E731
        for i, o in enumerate(ofertas):
            o.setdefault("ordem", len(ofertas) - i)
        categorias = sorted({o["categoria"] for o in ofertas if o.get("categoria")})
        lojas = sorted({o["marketplace_nome"] for o in ofertas if o.get("marketplace_nome")})
        nav = '<nav class="cats" aria-label="Categorias">' + "".join(
            f'<a href="categoria/{slug(c)}.html">{_e(c)}</a>' for c in categorias) + "</nav>"
        filtros = (f'<div class="filtros" role="search"><input id="busca" type="search" placeholder="Buscar oferta" aria-label="Buscar oferta">'
                   f'<select id="categoria" aria-label="Categoria"><option value="">Todas as categorias</option>'
                   + "".join(f'<option>{_e(c)}</option>' for c in categorias) + '</select>'
                   f'<select id="loja" aria-label="Loja"><option value="">Todas as lojas</option>'
                   + "".join(f'<option>{_e(l)}</option>' for l in lojas) + '</select>'
                   '<select id="ordem" aria-label="Ordenar"><option value="recentes">Mais recentes</option>'
                   '<option value="preco">Menor preço</option><option value="desconto">Maior desconto comprovado</option></select></div>')
        grade = f'<div class="grade" id="grade">{"".join(self._card(o) for o in ofertas)}</div><p id="vazio" hidden>Nenhuma oferta encontrada.</p>'
        lista_ld = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "url": f"{self.base}/oferta/{int(o['promocao_id'])}-{slug(o['titulo'])}.html"}
            for i, o in enumerate(ofertas[:50])]}
        vazio = "" if ofertas else "<p>Nenhuma oferta no momento. Volte mais tarde!</p>"
        (saida / "index.html").write_text(self._pagina(
            f"{self.nome} · ofertas do dia", f"{len(ofertas)} ofertas com preço conferido e cupons de desconto.", "",
            "<h1>Ofertas do dia</h1>" + nav + filtros + vazio + grade + f"<script>{JS}</script>", lista_ld,
            amazon=amz(ofertas)), encoding="utf-8")
        for cat in categorias:
            da_cat = [o for o in ofertas if o.get("categoria") == cat]
            (saida / "categoria" / f"{slug(cat)}.html").write_text(self._pagina(
                f"Ofertas de {cat} · {self.nome}", f"{len(da_cat)} ofertas de {cat} com preço conferido.",
                f"categoria/{slug(cat)}.html",
                f'<p><a href="../index.html">← todas</a></p><h1>{_e(cat)}</h1><div class="grade">'
                + "".join(self._card(o).replace('href="oferta/', 'href="../oferta/') for o in da_cat) + "</div>",
                amazon=amz(da_cat)), encoding="utf-8")
        for o in ofertas:
            caminho = f"oferta/{int(o['promocao_id'])}-{slug(o['titulo'])}.html"
            produto = {"@context": "https://schema.org", "@type": "Product", "name": o["titulo"],
                       "offers": {"@type": "Offer", "price": f"{float(o['preco']):.2f}", "priceCurrency": "BRL",
                                  "availability": "https://schema.org/InStock", "url": self.link(o)}}
            if _https(o.get("imagem_url")):
                produto["image"] = o["imagem_url"]
            if o.get("marca"):
                produto["brand"] = {"@type": "Brand", "name": o["marca"]}
            (saida / caminho).write_text(self._pagina(
                f"{o['titulo'][:60]} por {reais(o['preco'])} · {self.nome}",
                f"{o['titulo']} por {reais(o['preco'])} na {o.get('marketplace_nome') or 'loja'}.", caminho,
                '<p><a href="../index.html">← todas as ofertas</a></p><div class="grade" style="grid-template-columns:1fr">'
                + self._card(o, "h1").replace('href="oferta/', 'href="../oferta/') + "</div>", produto,
                amazon=amz([o])), encoding="utf-8")
        blocos = "".join(f'<div class="cupom"><b>{_e(c["titulo"])}</b> · {_e(c.get("marketplace_nome") or "")}<br>'
                         + (f'Código: <code>{_e(c["codigo"])}</code><br>' if c.get("codigo") else "")
                         + (f'<span class="loja">{_e(c["condicoes"])}</span><br>' if c.get("condicoes") else "")
                         + f'<span class="loja">válido até {_e(c["fim_em"])}</span></div>' for c in cupons)
        (saida / "cupons.html").write_text(self._pagina(
            f"Cupons de desconto · {self.nome}", f"{len(cupons)} cupons válidos.", "cupons.html",
            '<p><a href="index.html">← ofertas</a></p><h1>Cupons</h1>' + (blocos or "<p>Nenhum cupom válido agora.</p>")), encoding="utf-8")
        (saida / "privacidade.html").write_text(self._pagina(
            f"Privacidade · {self.nome}", "Como tratamos seus dados.", "privacidade.html",
            "<h1>Privacidade</h1><p>Esta vitrine não usa cookies nem formulários. Ao clicar em uma oferta, registramos o "
            "clique de forma pseudonimizada (resumo criptográfico do IP e do navegador, nunca o IP) para contar acessos "
            "e evitar fraude. Os registros são apagados em até 365 dias. Contato: veja o perfil oficial.</p>"), encoding="utf-8")
        urls = [""] + [f"categoria/{slug(c)}.html" for c in categorias] + \
               [f"oferta/{int(o['promocao_id'])}-{slug(o['titulo'])}.html" for o in ofertas] + ["cupons.html"]
        (saida / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{_e(self.base + '/' + u)}</loc><lastmod>{agora.date().isoformat()}</lastmod></url>" for u in urls)
            + "</urlset>", encoding="utf-8")
        (saida / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {self.base}/sitemap.xml\n", encoding="utf-8")
        (saida / "ofertas.json").write_text(json.dumps([{
            "id": o["promocao_id"], "titulo": o["titulo"], "preco": float(o["preco"]), "categoria": o.get("categoria"),
            "loja": o.get("marketplace_nome"), "link": self.link(o)} for o in ofertas], ensure_ascii=False), encoding="utf-8")
        return {"ofertas": len(ofertas), "categorias": len(categorias), "cupons": len(cupons), "paginas": len(urls) + 1}
