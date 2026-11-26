"""Seleção comercial: aprende com o desempenho real, mede a saturação, encontra produtos repetidos entre
marketplaces, ordena as ofertas aprovadas e grava a explicação de cada decisão (selecoes_ofertas).
Também avalia o teste A/B ativo. Regras em docs/REGRAS_COMERCIAIS.md.

Falha em qualquer parte daqui NUNCA impede a publicação: o bot volta à ordem por nota do motor e registra
o motivo (sem a migration 019, o banco não tem as tabelas e tudo aparece como "sem dados").
"""
from __future__ import annotations

from datetime import timedelta
from dataclasses import asdict

from ..erros import ErroBanco
from ..inteligencia import aprendizado, equivalencia, experimentos, ranking, saturacao
from ..logs import Log
from ..repositorio import Repositorio
from .comum import agora_utc, num


def _n(cfg, chave, padrao):
    try:
        v = cfg.get(chave)
        return padrao if v is None else float(v)
    except (TypeError, ValueError):
        return padrao


class ServicoSelecao:
    def __init__(self, repo: Repositorio, log: Log, relogio=agora_utc):
        self.repo, self.log, self.relogio = repo, log, relogio
        self._contexto = None
        self._linhas: list[dict] | None = None

    def _desempenho(self) -> list[dict]:
        if self._linhas is None:
            self._linhas = self.repo.desempenho_publicacoes(self.relogio() - timedelta(days=90))
        return self._linhas

    def contexto(self, cfg: dict):
        """Aprendizado (90 dias) e saturação, calculados 1x por rodada e gravados para o painel."""
        if self._contexto is None:
            linhas = self._desempenho()
            ap = aprendizado.aprender(linhas, int(_n(cfg, "inteligencia.amostra_minima_segmento", 20)),
                                      int(_n(cfg, "inteligencia.min_conversoes", 10)))
            sat = saturacao.avaliar(linhas, self.relogio(), queda_pct=_n(cfg, "inteligencia.saturacao_queda_pct", 50),
                                    max_categoria=int(_n(cfg, "inteligencia.saturacao_max_categoria", 3)),
                                    janela_categoria_h=_n(cfg, "inteligencia.saturacao_janela_horas", 6))
            for tipo, dados in (("aprendizado", ap.como_dict()), ("saturacao", sat.como_dict())):
                try:
                    self.repo.salvar_resumo_inteligencia(tipo, dados)
                except ErroBanco as e:
                    self.log.aviso("selecao", f"Não consegui gravar o resumo de {tipo}", erro=str(e))
            for texto in ap.sem_dados:
                self.log.info("selecao", texto)
            self._contexto = (ap, sat)
        return self._contexto

    def ordenar(self, candidatas: list[dict], cfg: dict) -> list[ranking.Decisao]:
        ap, sat = self.contexto(cfg)
        dados = self.repo.dados_ranking([c["id"] for c in candidatas])
        ofertas = [dados[c["id"]] for c in candidatas if c["id"] in dados]
        self._comissao_liquida(ofertas, cfg)
        pares = equivalencia.encontrar(
            [{"id": o["produto_id"], "marketplace": o["marketplace"], "titulo": o.get("titulo") or "",
              "gtin": o.get("gtin"), "marca": o.get("marca"), "modelo": o.get("modelo")} for o in ofertas],
            _n(cfg, "inteligencia.equivalencia_titulo_min", 0.85))
        if pares:
            try:
                self.repo.salvar_equivalencias([{k: v for k, v in asdict(p).items()} for p in pares])
            except ErroBanco as e:
                self.log.aviso("selecao", "Não consegui gravar produtos equivalentes", erro=str(e))
        return ranking.classificar(ofertas, ap, sat, pares, ranking.PesosRanking.de_config(cfg))

    def _comissao_liquida(self, ofertas: list[dict], cfg: dict) -> None:
        """Taxa de cancelamento observada por loja (comissões decididas nos últimos 180 dias). Só é usada
        com amostra mínima; abaixo dela a comissão bruta continua e o motivo aparece em "sem dados"."""
        minimo = int(_n(cfg, "inteligencia.min_comissoes_cancelamento", 20))
        try:
            taxas = self.repo.taxas_cancelamento()
        except (ErroBanco, NotImplementedError) as e:
            self.log.aviso("selecao", "Taxa de cancelamento indisponível: comissão bruta nesta rodada", erro=str(e))
            return
        for o in ofertas:
            t = taxas.get(o.get("marketplace") or "")
            o["cancelamento"] = ({"taxa": t["canceladas"] / t["decididas"], "amostra": t["decididas"]}
                                 if t and t["decididas"] >= minimo else
                                 {"taxa": None, "amostra": (t or {}).get("decididas", 0), "minimo": minimo})

    def registrar(self, decisoes: list[ranking.Decisao]) -> None:
        try:
            self.repo.salvar_selecoes([d.como_dict() for d in decisoes])
        except ErroBanco as e:
            self.log.aviso("selecao", "Não consegui gravar as decisões da seleção", erro=str(e))
        for d in decisoes:
            self.log.info("selecao", d.resumo()[:500], promocao_id=d.promocao_id)

    def canal_saturado(self, cfg: dict, canal: str) -> str | None:
        _, sat = self.contexto(cfg)
        e = sat.canais.get(canal)
        return e.explicacao if e and e.saturado else None

    def avaliar_experimento(self) -> dict | None:
        exp = self.repo.experimento_ativo()
        if not exp:
            return None
        linhas = [l for l in self._desempenho() if l.get("experimento_id") == exp["id"]]
        r = experimentos.avaliar(exp, linhas, int(exp.get("amostra_minima") or 30))
        self.repo.salvar_resultado_experimento(exp["id"], r.como_dict())
        self.log.info("experimentos", r.explicacao, experimento_id=exp["id"])
        return r.como_dict()
