"""Monitor das ofertas publicadas: preço, disponibilidade e link.

Quando a loja tem API (Shopee), consulta preço e estoque; o banco pausa a oferta sozinho se o
preço subir além da tolerância ou o produto esgotar. Sem API (Mercado Livre), confere se o
link continua abrindo; link quebrado é invalidado e o banco pausa a oferta.
"""
from __future__ import annotations

from ..cliente_http import ClienteHTTP
from ..erros import ErroBanco, ErroIntegracao
from ..integracoes.base import FonteOfertas
from ..logs import Log
from ..repositorio import Repositorio
from .comum import ajuste, num
from .validacao import LinkTemporariamenteIndisponivel, validar_link


class ServicoMonitor:
    def __init__(self, repo: Repositorio, fontes: dict[str, FonteOfertas], log: Log, http: ClienteHTTP | None = None):
        self.repo, self.fontes, self.log = repo, fontes, log
        self.http = http or ClienteHTTP("monitor", tentativas=2)

    def executar(self, cfg: dict, limite: int = 15) -> int:
        if not ajuste(cfg, "monitor.ativo"):
            return 0
        verificadas = 0
        for it in self.repo.publicadas_para_monitorar(int(num(cfg, "monitor.acompanhar_dias")), limite):
            fonte = self.fontes.get(it["marketplace"])
            try:
                if fonte and fonte.suporta_consulta:
                    atual = fonte.consultar(it["id_externo"])
                    if atual is None:
                        self.repo.atualizar_produto(it["produto_id"], it["preco_promocional"], False, None)
                        self.log.info("monitor", "Produto indisponível: oferta será pausada", promocao_id=it["promocao_id"])
                    else:
                        self.repo.atualizar_produto(it["produto_id"], atual.preco, atual.disponivel, atual.preco_original)
                elif it.get("link_url") and ajuste(cfg, "monitor.verificar_links"):
                    r = validar_link(it["link_url"], it["dominios"], self.http,
                                     demo=it["marketplace"] == "demo", verificar_http=True)
                    if not r.valido:
                        self.repo.invalidar_link(it["link_id"], r.motivo or "link inválido")
                        self.log.info("monitor", f"Link invalidado: {r.motivo}", promocao_id=it["promocao_id"])
                verificadas += 1
            except LinkTemporariamenteIndisponivel as e:
                self.log.aviso("monitor", "Loja fora do ar agora; confiro depois", erro=str(e))
            except (ErroIntegracao, ErroBanco) as e:
                self.log.erro("monitor", "Falha ao conferir oferta", promocao_id=it["promocao_id"], erro=str(e))
        return verificadas
