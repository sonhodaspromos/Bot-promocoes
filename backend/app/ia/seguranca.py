"""Segurança do motor de IA.

1. Entrada (anti prompt injection): o que vem da loja (título, categoria) é DADO. Vai limpo (sem caracteres
   de controle, sem marcações <...>, tamanho limitado) e dentro de um JSON no bloco <dados>, separado das
   instruções; o prompt de sistema manda ignorar pedidos escritos ali.
2. Saída (a defesa que realmente importa): mesmo que a IA seja enganada, a resposta só é usada se passar
   na validação abaixo — JSON com os campos certos, tamanhos, nenhum link/@/hashtag, nenhum número que não
   esteja nos dados, nenhuma promessa que os dados não sustentam (frete grátis, últimas unidades...).
"""
from __future__ import annotations

import json
import re
import unicodedata

from ..servicos.textos import reais

LIMITE_TITULO = 200
_CONTROLE = re.compile(r"[\u0000-\u001f\u007f-\u009f​-‏ -‮⁦-⁩﻿]")
_MARCACAO = re.compile(r"<[^>]{0,200}>|[<>{}]")
_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
_LINK = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|br|net|org|ly|la|io|app|me|link)\b|@\w|#\w", re.I)
# promessas que só podem aparecer se estiverem no próprio título do produto (dado da loja)
PROMESSAS = ("frete grátis", "frete gratis", "grátis", "gratis", "últimas unidades", "ultimas unidades",
             "estoque limitado", "só hoje", "so hoje", "acaba hoje", "termina hoje", "menor preço", "menor preco",
             "mais barato do brasil", "garantia", "brinde", "original", "lacrado", "entrega em 24")
_ECO_INJECAO = re.compile(r"ignor\w*\s+(as\s+|todas\s+as\s+)?(instru|regra)|system prompt|prompt de sistema", re.I)


def limpar(texto, limite: int = LIMITE_TITULO) -> str:
    t = unicodedata.normalize("NFKC", str(texto or ""))
    t = _CONTROLE.sub(" ", t)
    t = _MARCACAO.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()[:limite]


def fatos_da_oferta(d: dict) -> dict:
    """Os ÚNICOS fatos que a IA recebe. Preço antigo e desconto só quando comprovados (mesma regra do post)."""
    preco = float(d["preco_promocional"])
    fatos = {"titulo": limpar(d.get("titulo")), "loja": limpar(d.get("marketplace_nome") or d.get("marketplace"), 40),
             "categoria": limpar(d.get("categoria") or "", 40), "preco": reais(preco), "pix": bool(d.get("preco_no_pix"))}
    comprovado = bool(d.get("desconto_comprovado", False))
    ref = d.get("preco_referencia")
    if comprovado and ref and float(ref) > preco:
        fatos["preco_antigo"] = reais(ref)
        if d.get("desconto_pct"):
            fatos["desconto"] = f"{float(d['desconto_pct']):.0f}%"
    if d.get("preco_com_cupom"):
        fatos["cupom"] = limpar(d.get("cupom_codigo"), 30) if d.get("cupom_codigo") else "cupom da loja (ativar na página do produto)"
        fatos["preco_com_cupom"] = reais(d["preco_com_cupom"])
    return {k: v for k, v in fatos.items() if v not in ("", None)}


def _normalizar_numero(t: str) -> str:
    """'1.299,90' / '1299.90' / '1299,9' → '1299.9'; '30' → '30'."""
    t = t.strip(".,")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or re.search(r"\.\d{3}$", t):
        t = t.replace(".", "")
    try:
        n = float(t)
    except ValueError:
        return t
    return f"{n:g}"


def numeros_permitidos(fatos: dict) -> set[str]:
    texto = " ".join(str(v) for v in fatos.values())
    return {_normalizar_numero(n) for n in _NUMERO.findall(texto)}


def extrair_json(resposta: str) -> dict | None:
    """Aceita o JSON puro ou dentro de ```json ...```; pega o primeiro objeto {...}."""
    m = re.search(r"\{.*\}", resposta or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def validar(resposta: str | dict, fatos: dict) -> tuple[dict | None, str | None]:
    """→ ({'chamada', 'texto'}, None) se a resposta pode ser usada; senão (None, motivo)."""
    obj = resposta if isinstance(resposta, dict) else extrair_json(resposta)
    if obj is None:
        return None, "resposta não é um JSON válido"
    chamada, texto = obj.get("chamada"), obj.get("texto")
    if not isinstance(chamada, str) or not isinstance(texto, str):
        return None, "faltam os campos 'chamada' e 'texto'"
    chamada = re.sub(r"\s+", " ", _CONTROLE.sub(" ", chamada)).strip()
    texto = re.sub(r"\s+", " ", _CONTROLE.sub(" ", texto)).strip()
    if not 3 <= len(chamada) <= 90:
        return None, f"chamada com tamanho fora do limite ({len(chamada)})"
    if not 10 <= len(texto) <= 300:
        return None, f"texto com tamanho fora do limite ({len(texto)})"
    junto = f"{chamada} {texto}"
    if _LINK.search(junto):
        return None, "contém link, @ ou hashtag"
    if "<" in junto or ">" in junto:
        return None, "contém marcação"
    if _ECO_INJECAO.search(junto):
        return None, "repete instruções (possível prompt injection)"
    permitidos = numeros_permitidos(fatos)
    estranhos = sorted({n for n in _NUMERO.findall(junto) if _normalizar_numero(n) not in permitidos})
    if estranhos:
        return None, f"número que não está nos dados: {', '.join(estranhos[:3])}"
    base = " ".join(str(v) for v in fatos.values()).lower()
    baixo = junto.lower()
    for p in PROMESSAS:
        if p in baixo and p not in base:
            return None, f"promessa sem base nos dados: '{p}'"
    return {"chamada": chamada, "texto": texto}, None
