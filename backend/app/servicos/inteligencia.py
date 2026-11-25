"""Serviço do motor de inteligência: reúne os dados REAIS de cada promoção, analisa e grava o resultado.

Só promoções sem análise válida são analisadas (o banco apaga a análise quando preço,
disponibilidade ou link mudam). O resultado decide quem pode entrar na fila e em que ordem.
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime, timedelta

from PIL import Image

from ..cliente_http import ClienteHTTP
from ..erros import ErroBanco, ErroIntegracao
from ..inteligencia import EntradaAnalise, PontoPreco, Regras, analisar
from ..logs import Log
from ..rede import ImagemRecusada, baixar_imagem
from ..repositorio import Repositorio
from .comum import agora_utc, ajuste

LIMITE_BYTES_IMAGEM = 8_000_000


def normalizar_titulo(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"\[demo\]", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _data(v) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if isinstance(v, str) else v


def situacao_do_link(link: dict | None) -> tuple[str, str | None]:
    if not link:
        return "invalido", "oferta sem link de afiliado"
    if link.get("validado"):
        return "validado", None
    if link.get("status_http") is not None or link.get("motivo_invalido"):
        return "invalido", link.get("motivo_invalido") or f"HTTP {link.get('status_http')}"
    return "pendente", None


class ServicoInteligencia:
    def __init__(self, repo: Repositorio, log: Log, http: ClienteHTTP | None = None, relogio=agora_utc,
                 max_imagens_por_rodada: int = 15):
        self.repo, self.log = repo, log
        self.http = http or ClienteHTTP("imagem", tentativas=1, timeout=15)
        self.relogio = relogio
        self.max_imagens = max_imagens_por_rodada
        self._medidas: dict[str, tuple[int | None, int | None]] = {}

    def medir_imagem(self, url: str | None) -> tuple[int | None, int | None]:
        """Largura e altura reais da imagem (None se não deu para medir). Nunca inventa."""
        if not url or not url.lower().startswith("https://"):
            return None, None
        if url in self._medidas:
            return self._medidas[url]
        if len(self._medidas) >= self.max_imagens:
            return None, None
        try:
            conteudo = baixar_imagem(self.http, url, max_bytes=LIMITE_BYTES_IMAGEM)
            with Image.open(io.BytesIO(conteudo)) as img:
                self._medidas[url] = img.size
        except (ImagemRecusada, OSError, ValueError):
            self._medidas[url] = (None, None)
        return self._medidas[url]

    def executar(self, cfg: dict, limite: int = 40) -> dict:
        regras = Regras.de_config(cfg)
        agora = self.relogio()
        itens = self.repo.promocoes_para_analisar(limite)
        contagem = {"analisadas": 0, "excelente": 0, "boa": 0, "nao_recomendada": 0}
        if not itens:
            return contagem

        historicos = self.repo.historico_precos([i["produto_id"] for i in itens], agora - timedelta(days=regras.historico_dias))
        indice: dict[str, list[dict]] = {}
        for t in self.repo.titulos_ativos(agora - timedelta(days=30)):
            indice.setdefault(normalizar_titulo(t["titulo"]), []).append(t)
        verificar_imagem = bool(ajuste(cfg, "inteligencia.verificar_imagem"))
        from .cupons import melhor_cupom  # (import aqui evita ciclo entre os módulos)

        for it in itens:
            try:
                largura, altura = self.medir_imagem(it.get("imagem_url")) if verificar_imagem else (None, None)
                situacao, motivo_link = situacao_do_link(it.get("link"))
                confiaveis = it.get("marketplace_modo") == "real" and it.get("origem") == "automatica" and not it.get("demo")
                origem_loja = "manual" if it.get("origem") == "manual" else "loja"
                preco, preco_produto = it["preco"], it.get("preco_produto")
                referencia = it.get("preco_referencia")
                origem_ref = origem_loja if referencia else None
                cupom = self._cupom(it, melhor_cupom)
                if cupom:
                    # com cupom: o preço final é o da loja menos o desconto do cupom (regras do próprio cupom)
                    if not referencia:
                        referencia, origem_ref = preco, origem_loja
                    preco, preco_produto = cupom[1], None
                entrada = EntradaAnalise(
                    titulo=it["titulo"], preco=preco, preco_produto=preco_produto,
                    preco_referencia=referencia, origem_referencia=origem_ref,
                    preco_maximo_variacao=it.get("preco_maximo_variacao") if not cupom else None,
                    referencia_derivada=bool(it.get("referencia_derivada")) and not cupom,
                    desconto_por_cupom=bool(cupom),
                    historico=[PontoPreco(h["preco"], _data(h["quando"]), h.get("disponivel", True))
                               for h in historicos.get(it["produto_id"], [])],
                    disponivel=bool(it.get("disponivel")),
                    comissao_pct=it.get("comissao_pct"), nota=it.get("nota"), total_vendas=it.get("total_vendas"),
                    dados_da_loja_confiaveis=confiaveis, categoria_ativa=it.get("categoria_ativa"),
                    imagem_url=it.get("imagem_url"), imagem_largura=largura, imagem_altura=altura,
                    link_situacao=situacao, link_motivo=motivo_link,
                    duplicada=self._duplicada(it, indice), demo=bool(it.get("demo")))
                r = analisar(entrada, regras, agora)
                if cupom:
                    r.motivos.append(f"Preço com o cupom {cupom[0].codigo}: R$ {cupom[1]:.2f}".replace(".", ",")
                                     + " (o comprador digita o código no pagamento)")
                self.repo.salvar_analise(it["promocao_id"], r.pontuacao, r.classificacao, r.confianca, r.como_dict())
                contagem["analisadas"] += 1
                contagem[r.classificacao] += 1
                self.log.info("inteligencia", r.resumo()[:500], promocao_id=it["promocao_id"],
                              pontuacao=r.pontuacao, confianca=r.confianca)
            except (ErroBanco, ValueError) as e:
                self.log.erro("inteligencia", "Falha ao analisar oferta", promocao_id=it.get("promocao_id"), erro=str(e))
        return contagem

    def _cupom(self, it: dict, melhor_cupom):
        try:
            return melhor_cupom(self.repo.cupons_da_promocao(it["promocao_id"]), float(it["preco"]))
        except ErroBanco:
            return None

    @staticmethod
    def _duplicada(it: dict, indice: dict[str, list[dict]]) -> str | None:
        """Mesmo título (normalizado) em OUTRO produto com oferta ativa mais antiga = duplicada."""
        chave = normalizar_titulo(it["titulo"])
        if len(chave) < 12:
            return None
        for outro in indice.get(chave, []):
            if outro["produto_id"] != it["produto_id"] and outro["promocao_id"] < it["promocao_id"]:
                return f"produto com o mesmo título já tem oferta ativa ({outro['marketplace']}, oferta #{outro['promocao_id']})"
        return None
