"""Busca de ofertas nas fontes ativas, com filtros e sem duplicidade."""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from ..erros import ErroBanco, ErroIntegracao
from ..integracoes.base import DEMO, FonteOfertas
from ..logs import Log
from ..modelos import OfertaEncontrada
from ..repositorio import Repositorio
from .comum import agora_utc, ajuste, num
from .validacao import dominio_permitido


class ServicoBusca:
    def __init__(self, repo: Repositorio, fontes: dict[str, FonteOfertas], log: Log,
                 aleatorio: random.Random | None = None, relogio=agora_utc):
        self.repo, self.fontes, self.log = repo, fontes, log
        self.aleatorio = aleatorio or random.Random()
        self.relogio = relogio

    def passa_nos_filtros(self, o: OfertaEncontrada, cfg: dict) -> bool:
        if o.preco <= 0 or not o.disponivel or not o.link_afiliado:
            return False
        if o.desconto_pct < num(cfg, "filtros.desconto_minimo"):
            return False
        if o.nota is not None and o.nota < num(cfg, "filtros.nota_minima"):
            return False
        return True

    def executar(self, cfg: dict) -> tuple[int, int]:
        marketplaces = {m["codigo"]: m for m in self.repo.marketplaces()}
        categorias = [c for c in self.repo.categorias() if c.get("ativa")]
        pares = [(c, p) for c in categorias for p in (c.get("palavras_chave") or [])]
        if not pares:
            self.log.aviso("busca", "Nenhuma categoria ativa com palavras-chave.")
            return 0, 0
        self.aleatorio.shuffle(pares)
        pares = pares[: int(num(cfg, "busca.palavras_por_execucao"))]
        aprovar_direto = not bool(ajuste(cfg, "publicacao.aprovacao_manual"))
        encontradas = novas = 0

        from ..recursos import ligado
        for fonte in self.fontes.values():
            if fonte.codigo in ("amazon", "aliexpress") and not ligado(cfg, f"fonte_{fonte.codigo}"):
                continue   # módulo novo: só busca com a flag ligada (além das credenciais)
            if not fonte.suporta_busca:
                continue
            mkt = marketplaces.get(fonte.codigo)
            if not mkt or not mkt.get("ativo") or mkt.get("modo") == "desativado":
                self.log.aviso("busca", f"Marketplace {fonte.codigo} ausente ou desativado no banco; busca pulada.")
                continue
            for categoria, palavra in pares:
                try:
                    ofertas = fonte.buscar(palavra, int(num(cfg, "busca.ofertas_por_palavra")))
                except ErroIntegracao as e:
                    self.log.erro("busca", f"{fonte.codigo}: falha ao buscar '{palavra}'", erro=str(e))
                    continue
                boas = [o for o in ofertas if self.passa_nos_filtros(o, cfg)]
                encontradas += len(ofertas)
                for oferta in boas:
                    try:
                        if self._registrar(oferta, fonte, mkt, categoria, aprovar_direto, cfg):
                            novas += 1
                    except ErroBanco as e:
                        self.log.erro("busca", f"Não consegui salvar '{oferta.titulo[:60]}'", erro=str(e))
                self.log.info("busca", f"{fonte.codigo}: '{palavra}' ({categoria['nome']})",
                              encontradas=len(ofertas), aprovadas_no_filtro=len(boas), modo=fonte.modo)
        return encontradas, novas

    def _registrar(self, oferta, fonte, mkt, categoria, aprovar_direto, cfg) -> bool:
        oferta.demo = oferta.demo or fonte.modo == DEMO
        produto_id, _ = self.repo.salvar_produto(oferta, mkt["id"], categoria["id"])

        ativa = self.repo.promocao_ativa(produto_id)
        if ativa:
            limite = self.relogio() - timedelta(days=num(cfg, "filtros.repetir_apos_dias"))
            publicada = ativa.get("publicada_em")
            if ativa["status"] == "publicada" and publicada and _data(publicada) < limite:
                self.repo.expirar_promocao(ativa["id"], "Substituída por oferta nova")
            else:
                return False  # já existe uma promoção ativa: não duplica

        # garante link de afiliado do domínio certo (algumas APIs devolvem link comum)
        link = oferta.link_afiliado
        if not dominio_permitido(link, mkt["dominios_afiliado"]):
            try:
                link = fonte.gerar_link_afiliado(link) or ""
            except ErroIntegracao as e:
                self.log.aviso("busca", "Falha ao gerar link de afiliado", erro=str(e))
                link = ""
            if not dominio_permitido(link, mkt["dominios_afiliado"]):
                self.log.aviso("busca", f"Oferta ignorada: sem link de afiliado válido ({oferta.titulo[:60]})")
                return False

        status = "aprovada" if aprovar_direto else "encontrada"
        origem = "demo" if oferta.demo else "automatica"
        promocao_id = self.repo.criar_promocao(produto_id, oferta, status, origem)
        if promocao_id is None:
            return False
        self.repo.salvar_link(produto_id, promocao_id, link)
        return True


def _data(valor):
    if isinstance(valor, str):
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    return valor
