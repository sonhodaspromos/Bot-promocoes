"""Monitor das ofertas publicadas: preço, disponibilidade e link.

Quando a loja tem API (Shopee), consulta preço e estoque; o banco pausa a oferta sozinho se o
preço subir além da tolerância ou o produto esgotar. Sem API (Mercado Livre), lê a página pública do
produto uma vez por dia (monitor.intervalo_pagina_horas) e atualiza preço/estoque do mesmo jeito; se a
página não deixar ler o preço, ao menos confere se o link abre. Link quebrado é invalidado e o banco pausa.
"""
from __future__ import annotations

from ..cliente_http import ClienteHTTP
from ..erros import ErroBanco, ErroIntegracao
from ..integracoes.base import FonteOfertas
from ..logs import Log
from ..repositorio import Repositorio
from datetime import datetime, timedelta, timezone

from .comum import ajuste, num
from .pagina_produto import ler_pagina
from .validacao import LinkTemporariamenteIndisponivel, validar_link

LER_PAGINA = {"mercado_livre"}  # lojas sem API cuja página pública traz o preço


def _recente(quando, horas: float, agora: datetime) -> bool:
    if not quando:
        return False
    if isinstance(quando, str):
        try:
            quando = datetime.fromisoformat(quando.replace("Z", "+00:00"))
        except ValueError:
            return False
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=timezone.utc)
    return agora - quando < timedelta(hours=horas)


class ServicoMonitor:
    def __init__(self, repo: Repositorio, fontes: dict[str, FonteOfertas], log: Log, http: ClienteHTTP | None = None,
                 resolver=None, relogio=None):
        self.repo, self.fontes, self.log = repo, fontes, log
        self.http = http or ClienteHTTP("monitor", tentativas=2)
        self.resolver = resolver
        self.relogio = relogio or (lambda: datetime.now(timezone.utc))

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
                elif (it.get("link_url") and it["marketplace"] in LER_PAGINA and cfg.get("monitor.ler_preco_pagina", True)
                      and self._conferir_pagina(it, cfg)):
                    pass
                elif it.get("link_url") and ajuste(cfg, "monitor.verificar_links"):
                    r = validar_link(it["link_url"], it["dominios"], self.http, resolver=self.resolver,
                                     demo=it["marketplace"] == "demo", verificar_http=True,
                                     dominios_destino=it.get("dominios_destino") or None)
                    if not r.valido:
                        self.repo.invalidar_link(it["link_id"], r.motivo or "link inválido")
                        self.log.info("monitor", f"Link invalidado: {r.motivo}", promocao_id=it["promocao_id"])
                verificadas += 1
            except LinkTemporariamenteIndisponivel as e:
                self.log.aviso("monitor", "Loja fora do ar agora; confiro depois", erro=str(e))
            except (ErroIntegracao, ErroBanco) as e:
                self.log.erro("monitor", "Falha ao conferir oferta", promocao_id=it["promocao_id"], erro=str(e))
        return verificadas

    def _conferir_pagina(self, it: dict, cfg: dict) -> bool:
        """True = resolvido pela leitura da página (atualizou o preço ou invalidou o link).
        False = ainda não é hora (conferida há menos de monitor.intervalo_pagina_horas) ou não deu para ler o
        preço: segue a conferência simples do link, como antes."""
        horas = float(cfg.get("monitor.intervalo_pagina_horas") or 24)
        if _recente(it.get("verificado_em"), horas, self.relogio()):
            return False
        dominios = list(it.get("dominios") or []) + list(it.get("dominios_destino") or [])
        d = ler_pagina(self.http, it["link_url"], dominios, resolver=self.resolver)
        if d.get("quebrado"):
            self.repo.invalidar_link(it["link_id"], d["erro"])
            self.log.info("monitor", f"Link invalidado: {d['erro']}", promocao_id=it["promocao_id"])
            return True
        if not d.get("preco"):
            self.log.aviso("monitor", f"Não deu para ler o preço da página: {d.get('erro')}", promocao_id=it["promocao_id"])
            return False
        self.repo.atualizar_produto(it["produto_id"], d["preco"], d["disponivel"], d.get("preco_antigo"))
        self.log.info("monitor", f"Preço conferido na página: R$ {d['preco']:.2f}", promocao_id=it["promocao_id"])
        return True
