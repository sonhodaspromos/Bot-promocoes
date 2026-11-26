"""Leitura da página pública do produto (lojas sem API, como o Mercado Livre): título, foto, preço atual,
preço antigo, disponibilidade e código. Mesma lógica do "Puxar dados" do painel
(supabase/functions/_compartilhado/produto.ts); os testes dos dois lados usam as mesmas páginas de exemplo.
"""
from __future__ import annotations

import html as _html
import json
import re

from ..cliente_http import ClienteHTTP
from ..erros import ErroIntegracao
from ..rede import UrlRecusada, _fechar, seguir
from .validacao import LinkTemporariamenteIndisponivel

LIMITE_BYTES = 3_000_000
_RE_LD = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>([\s\S]*?)</script>", re.I)
_RE_TITULO = re.compile(r"<title[^>]*>([^<]{3,300})</title>", re.I)
_RE_PRECO_TITULO = re.compile(r"[-–|]\s*R\$\s*([\d.]+(?:,\d{1,2})?)\s*$")
_RE_BLOQUEIO = re.compile(r"captcha|account-verification|verifique que (?:você|voce) n[aã]o (?:é|e) um rob[oô]|access denied", re.I)


def numero_preco(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2) if 0 < v < 10_000_000 else None
    if not isinstance(v, str):
        return None
    t = re.sub(r"[^\d.,]", "", v)
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or re.search(r"\.\d{3}$", t):
        t = t.replace(".", "")
    try:
        n = float(t)
    except ValueError:
        return None
    return round(n, 2) if 0 < n < 10_000_000 else None


def _meta(html: str, nome: str) -> str | None:
    tag = re.search(rf"<meta[^>]+(?:property|name|itemprop)=[\"']{re.escape(nome)}[\"'][^>]*>", html, re.I)
    if not tag:
        return None
    v = re.search(r"content=[\"']([^\"']*)[\"']", tag.group(0), re.I)
    return _html.unescape(v.group(1)).strip() if v and v.group(1) else None


def _produtos_ld(html: str) -> list[dict]:
    saida = []
    for m in _RE_LD.finditer(html):
        try:
            j = json.loads(m.group(1).strip())
        except ValueError:
            continue
        fila = list(j) if isinstance(j, list) else [j]
        while fila:
            x = fila.pop(0)
            if not isinstance(x, dict):
                continue
            if isinstance(x.get("@graph"), list):
                fila.extend(x["@graph"])
            tipo = x.get("@type")
            if tipo == "Product" or (isinstance(tipo, list) and "Product" in tipo):
                saida.append(x)
    return saida


def _primeiro(v):
    return (v[0] if v else None) if isinstance(v, list) else v


def _por_extenso(t: str) -> float | None:
    m = re.search(r"(\d[\d.]*)\s*reais(?:\s*com\s*(\d{1,2})\s*centavos?)?", t, re.I)
    if not m:
        return None
    return numero_preco(f"{m.group(1).replace('.', '')}.{(m.group(2) or '0').zfill(2)}")


def extrair_produto(html: str, url_final: str = "") -> dict:
    lds = _produtos_ld(html)
    ld = lds[0] if lds else {}
    oferta = _primeiro(ld.get("offers")) if ld else None
    oferta = oferta if isinstance(oferta, dict) else {}
    img_ld = _primeiro(ld.get("image")) if ld else None
    img_ld = img_ld.get("url") if isinstance(img_ld, dict) else img_ld

    titulo = _html.unescape(ld["name"]).strip() if isinstance(ld.get("name"), str) and ld["name"].strip() else None
    if not titulo:
        titulo = _meta(html, "og:title")
    if not titulo:
        m = _RE_TITULO.search(html)
        titulo = _html.unescape(m.group(1)).strip() if m else None
    imagem = img_ld if isinstance(img_ld, str) and img_ld else _meta(html, "og:image")
    if imagem and not imagem.lower().startswith("https://"):
        imagem = None

    preco_titulo = _RE_PRECO_TITULO.search(titulo or "")
    agora = re.search(r"aria-label=[\"']Agora[:\s]+([^\"']+)[\"']", html, re.I)
    preco = (numero_preco(oferta.get("price", oferta.get("lowPrice"))) or numero_preco(_meta(html, "product:price:amount"))
             or numero_preco(_meta(html, "price")) or (_por_extenso(_html.unescape(agora.group(1))) if agora else None)
             or numero_preco(preco_titulo.group(1) if preco_titulo else None))

    antigo = None
    riscado = re.search(r"aria-label=[\"'](?:Antes|Antes de|De)[:\s]+([^\"']+)[\"']", html, re.I)
    if riscado:
        antigo = _por_extenso(_html.unescape(riscado.group(1))) or numero_preco(riscado.group(1))
    if not antigo:
        m = re.search(r"\"original_price\"\s*:\s*\"?([\d.]+)\"?", html)
        antigo = numero_preco(m.group(1)) if m else None
    if antigo and preco and antigo <= preco:
        antigo = None

    disponibilidade = str(oferta.get("availability") or "")
    disponivel = not re.search(r"OutOfStock|SoldOut|Discontinued", disponibilidade, re.I)

    ident = ld.get("sku") if isinstance(ld.get("sku"), str) and ld.get("sku") else None
    if not ident and isinstance(ld.get("productID"), str):
        ident = ld["productID"]
    if not ident:
        m = re.search(r"\bMLB-?\d{6,}\b", f"{url_final} {html[:200000]}")
        ident = m.group(0).replace("-", "") if m else None

    if titulo:
        titulo = re.sub(r"\s*\|\s*Mercado Livre.*$", "", titulo, flags=re.I)
        titulo = re.sub(r"\s*[-–|]\s*R\$\s*[\d.]+(?:,\d{1,2})?\s*$", "", titulo)[:300]
    return {"titulo": titulo, "imagem_url": imagem, "preco": preco, "preco_antigo": antigo,
            "disponivel": disponivel, "id_externo": str(ident)[:120] if ident else None}


def pagina_de_bloqueio(html: str) -> bool:
    return bool(_RE_BLOQUEIO.search(html[:50000])) and not re.search(r"application/ld\+json", html, re.I)


def ler_pagina(http: ClienteHTTP, url: str, dominios: list[str], *, resolver=None) -> dict:
    """Abre o link (só dentro dos domínios da loja, conferindo cada redirecionamento) e lê o produto.
    Devolve os dados ou {"quebrado": True} (404/410) ou {"erro": motivo}. Loja fora do ar → LinkTemporariamenteIndisponivel."""
    try:
        resp, cadeia = seguir(http, url, resolver=resolver, hosts_permitidos=list(dominios))
    except UrlRecusada as e:
        return {"erro": f"link recusado: {e}"}
    except ErroIntegracao as e:
        if e.temporario:
            raise LinkTemporariamenteIndisponivel(str(e)) from e
        return {"erro": f"não abriu: {e}"}
    try:
        status = resp.status_code
        if status in (404, 410):
            return {"quebrado": True, "erro": f"link quebrado (HTTP {status})"}
        if status >= 500:
            raise LinkTemporariamenteIndisponivel(f"loja respondeu HTTP {status}")
        if not 200 <= status < 300:
            return {"erro": f"a loja não mostrou a página (HTTP {status})"}
        partes, total = [], 0
        for bloco in resp.iter_content(65536):
            total += len(bloco)
            partes.append(bloco)
            if total >= LIMITE_BYTES:
                break
    finally:
        _fechar(resp)
    html = b"".join(partes)[:LIMITE_BYTES].decode("utf-8", errors="replace")
    if pagina_de_bloqueio(html):
        return {"erro": "a loja pediu verificação de robô"}
    dados = extrair_produto(html, cadeia[-1])
    if not dados["preco"]:
        return {"erro": "preço não encontrado na página", **dados}
    return dados
