"""Produtos repetidos entre marketplaces (o mesmo produto vendido na Shopee e no Mercado Livre).

Níveis de confiança (do mais ao menos confiável):
  * ALTA  — mesmo GTIN/EAN válido (dígito verificador conferido). Identificador oficial do produto.
  * MÉDIA — mesma marca E mesmo código de modelo (token com letras e números, ex.: "WH-1000XM4"),
            ambos presentes nos dois produtos.
  * BAIXA — títulos muito parecidos (similaridade ≥ limiar), sem identificador. Só SINALIZA: nunca
            bloqueia nem escolhe entre as ofertas sozinho, porque "Fone X preto" e "Fone X branco"
            podem ter títulos quase iguais.

Uso na seleção: com confiança alta ou média, quando as duas ofertas estão ativas, só a de menor preço
final é selecionada; a outra é recusada com o motivo. Sem GTIN, marca ou modelo, o relatório diz que
não há identificador confiável (nada é presumido).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

ALTA, MEDIA, BAIXA = "alta", "media", "baixa"
PALAVRAS_VAZIAS = {"de", "da", "do", "com", "para", "e", "o", "a", "os", "as", "em", "kit", "novo", "original",
                   "promocao", "oferta", "frete", "gratis", "envio", "imediato", "pronta", "entrega", "demo"}


def gtin_valido(gtin: str | None) -> bool:
    """GTIN-8/12/13/14 com dígito verificador correto (padrão GS1)."""
    g = re.sub(r"\D", "", gtin or "")
    if len(g) not in (8, 12, 13, 14) or len(set(g)) == 1:
        return False
    corpo, dv = g[:-1], int(g[-1])
    soma = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(corpo)))
    return (10 - soma % 10) % 10 == dv


def _normalizar(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def tokens(titulo: str) -> set[str]:
    return {p for p in re.split(r"[^a-z0-9]+", _normalizar(titulo)) if len(p) >= 2 and p not in PALAVRAS_VAZIAS}


def modelo_do_titulo(titulo: str) -> str | None:
    """Código de modelo: token com letras E números e ≥ 4 caracteres (ex.: WH-1000XM4 → wh1000xm4).
    Medidas comuns (128gb, 110v, 4k, 1l) não contam como modelo."""
    candidatos = []
    for bruto in re.findall(r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*", titulo or ""):
        t = re.sub(r"[-/]", "", _normalizar(bruto))
        if len(t) >= 4 and re.search(r"[a-z]", t) and re.search(r"\d", t) \
                and not re.fullmatch(r"\d+(gb|tb|mb|mah|w|v|hz|ml|l|kg|g|cm|mm|pol|k|p)", t):
            candidatos.append(t)
    return max(candidatos, key=len) if candidatos else None


def similaridade(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class Equivalencia:
    produto_a: int
    produto_b: int
    confianca: str
    metodo: str
    evidencia: str
    pontuacao: float


def comparar(a: dict, b: dict, limiar_titulo: float = 0.85) -> Equivalencia | None:
    """a/b: {id, marketplace, titulo, gtin?, marca?, modelo?}. Só compara marketplaces DIFERENTES."""
    if a["id"] == b["id"] or a.get("marketplace") == b.get("marketplace"):
        return None
    x, y = sorted((a, b), key=lambda p: p["id"])
    ga, gb = re.sub(r"\D", "", x.get("gtin") or ""), re.sub(r"\D", "", y.get("gtin") or "")
    if gtin_valido(ga) and gtin_valido(gb):
        if ga.zfill(14) == gb.zfill(14):
            return Equivalencia(x["id"], y["id"], ALTA, "gtin", f"mesmo GTIN {ga}", 1.0)
        return None  # GTINs válidos e diferentes: são produtos diferentes, mesmo com título igual
    marca_a, marca_b = _normalizar(x.get("marca") or "").strip(), _normalizar(y.get("marca") or "").strip()
    mod_a = (x.get("modelo") and re.sub(r"[^a-z0-9]", "", _normalizar(x["modelo"]))) or modelo_do_titulo(x.get("titulo", ""))
    mod_b = (y.get("modelo") and re.sub(r"[^a-z0-9]", "", _normalizar(y["modelo"]))) or modelo_do_titulo(y.get("titulo", ""))
    if marca_a and marca_a == marca_b and mod_a and mod_a == mod_b:
        return Equivalencia(x["id"], y["id"], MEDIA, "marca_modelo", f"mesma marca ({marca_a}) e modelo ({mod_a})", 0.8)
    if mod_a and mod_b and mod_a != mod_b:
        return None  # modelos diferentes informados: não são o mesmo produto
    s = similaridade(x.get("titulo", ""), y.get("titulo", ""))
    if s >= limiar_titulo:
        return Equivalencia(x["id"], y["id"], BAIXA, "titulo",
                            f"títulos {s:.0%} parecidos, sem GTIN nem marca+modelo para confirmar", round(s, 3))
    return None


def encontrar(produtos: list[dict], limiar_titulo: float = 0.85) -> list[Equivalencia]:
    """Compara todos os pares de marketplaces diferentes (lista pequena: candidatos da rodada)."""
    achados = []
    for i, a in enumerate(produtos):
        for b in produtos[i + 1:]:
            e = comparar(a, b, limiar_titulo)
            if e:
                achados.append(e)
    return achados
