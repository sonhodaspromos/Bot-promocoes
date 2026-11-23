"""Uma rodada completa do bot. Cada etapa é isolada: se uma falhar, as outras continuam.

Proteções para rodar 24 h (auditoria):
  * prazo da rodada (padrão 6 min, o job do GitHub tem 9): se estourar, as etapas opcionais são
    puladas, mas a fila e a publicação sempre rodam;
  * os logs são gravados no banco ao fim de cada etapa (se o job morrer, o histórico fica);
  * rodadas anteriores que ficaram "rodando" (job morto) são encerradas como "erro";
  * alerta crítico (ex.: token vencido) deixa a rodada com saída de erro, e o GitHub avisa o dono por e-mail.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import VERSAO
from .erros import ErroBanco
from .integracoes.registro import Integracoes
from .logs import Log
from .modelos import ResumoExecucao
from .repositorio import Repositorio
from .servicos.busca import ServicoBusca
from .servicos.comissoes import ServicoComissoes
from .servicos.cupons import ServicoCupons
from .servicos.inteligencia import ServicoInteligencia
from .servicos.monitor import ServicoMonitor
from .servicos.publicacao import ServicoPublicacao


def _data(valor):
    if isinstance(valor, str):
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    return valor


class Orquestrador:
    def __init__(self, repo: Repositorio, integ: Integracoes, log: Log, *, url_supabase: str,
                 publicacao: ServicoPublicacao | None = None, busca: ServicoBusca | None = None,
                 monitor: ServicoMonitor | None = None, comissoes: ServicoComissoes | None = None,
                 inteligencia: ServicoInteligencia | None = None, cupons: ServicoCupons | None = None,
                 prazo_segundos: float = 360,
                 cronometro=time.monotonic):
        self.repo, self.integ, self.log = repo, integ, log
        self.publicacao = publicacao or ServicoPublicacao(repo, integ.canais, log, url_supabase=url_supabase)
        self.busca = busca or ServicoBusca(repo, integ.fontes, log)
        self.monitor = monitor or ServicoMonitor(repo, integ.fontes, log)
        self.comissoes = comissoes or ServicoComissoes(repo, integ.fontes, log)
        self.inteligencia = inteligencia or ServicoInteligencia(repo, log)
        self.cupons = cupons or ServicoCupons(repo, integ.fontes, self.publicacao, log)
        self.prazo, self.cronometro = prazo_segundos, cronometro

    def sincronizar_modos(self) -> None:
        """Deixa o banco com o mesmo modo (real/demo) das integrações em uso."""
        shopee_real = "shopee" in self.integ.fontes and self.integ.fontes["shopee"].modo == "real"
        self.repo.definir_modo("marketplaces", "shopee", "real" if shopee_real else "demo")
        for codigo, canal in self.integ.canais.items():
            self.repo.definir_modo("canais", codigo, canal.modo)

    def executar(self, origem: str = "agendada", forcar_comissoes: bool = False) -> tuple[str, ResumoExecucao]:
        resumo = ResumoExecucao()
        inicio = self.cronometro()
        criticos_antes = self.log.contagem.get("CRITICO", 0)
        try:
            orfas = self.repo.encerrar_execucoes_orfas(30)
            if orfas:
                self.log.aviso("bot", f"{orfas} rodada(s) anterior(es) não terminaram (job encerrado ou servidor caiu)")
        except ErroBanco as e:
            self.log.aviso("bot", "Não consegui encerrar rodadas interrompidas (aplique a migration 015)", erro=str(e))
        anterior = self.repo.ultima_execucao()
        desde = _data(anterior["iniciada_em"]) if anterior else datetime.now(timezone.utc) - timedelta(hours=1)
        execucao = self.repo.iniciar_execucao(origem, self.integ.modo_geral, VERSAO)
        self.log.info("bot", f"Rodada iniciada (modo {self.integ.modo_geral.upper()})",
                      fontes={k: f.modo for k, f in self.integ.fontes.items()},
                      canais={k: c.modo for k, c in self.integ.canais.items()})
        cfg: dict = {}

        essenciais = {"configuração", "modos", "fila", "publicação"}

        def etapa(nome, funcao):
            if nome not in essenciais and self.cronometro() - inicio > self.prazo:
                resumo.etapas_puladas.append(nome)
                self.log.aviso("bot", f"Etapa '{nome}' pulada: rodada perto do tempo limite")
                return
            try:
                funcao()
            except Exception as e:  # isolamento: registra e segue para a próxima etapa
                resumo.etapas_com_erro.append(nome)
                self.log.erro("bot", f"Etapa '{nome}' falhou", erro=f"{type(e).__name__}: {e}")
            try:  # grava os logs a cada etapa: se o job morrer depois, o que aconteceu até aqui fica salvo
                self.repo.gravar_logs(execucao, self.log.retirar_pendentes())
            except ErroBanco:
                pass  # ficam para a próxima tentativa de gravação

        def carregar():
            cfg.update(self.repo.configuracoes())

        def monitorar():
            resumo.verificadas = self.monitor.executar(cfg)

        def encerrar():
            self.publicacao.encerrar_pausadas(desde)

        def importar():
            resumo.comissoes = self.comissoes.executar(cfg, forcar=forcar_comissoes)

        def buscar():
            resumo.encontradas, resumo.novas_promocoes = self.busca.executar(cfg)

        def validar():
            resumo.links_validados, resumo.links_recusados = self.publicacao.validar_links(cfg)

        def analisar():
            c = self.inteligencia.executar(cfg)
            resumo.analisadas, resumo.excelentes = c["analisadas"], c["excelente"]
            resumo.boas, resumo.nao_recomendadas = c["boa"], c["nao_recomendada"]

        def enfileirar():
            resumo.enfileiradas = self.publicacao.enfileirar(cfg)

        def publicar():
            resumo.publicadas, resumo.falhas_publicacao = self.publicacao.processar_fila(cfg)

        def cupons():
            r = self.cupons.executar(cfg)
            resumo.campanhas, resumo.sugestoes_cupom, resumo.cupons_publicados = r["campanhas"], r["sugestoes"], r["cupons_publicados"]

        etapas = [("configuração", carregar), ("modos", self.sincronizar_modos), ("monitor", monitorar),
                  ("encerrar pausadas", encerrar), ("comissões", importar), ("busca", buscar),
                  ("validação de links", validar), ("inteligência", analisar), ("fila", enfileirar), ("publicação", publicar),
                  ("cupons", cupons)]
        for nome, funcao in etapas:
            etapa(nome, funcao)

        resumo.canais_bloqueados = dict(self.publicacao.canais_bloqueados)
        resumo.alertas_criticos = self.log.contagem.get("CRITICO", 0) - criticos_antes
        if not resumo.etapas_com_erro and not resumo.alertas_criticos:
            status = "sucesso"
        elif len(resumo.etapas_com_erro) >= len(etapas) - 1 or "configuração" in resumo.etapas_com_erro:
            status = "erro"
        else:
            status = "parcial"
        if resumo.alertas_criticos:
            self.log.critico("bot", "Rodada com alerta crítico: veja os logs e corrija antes da próxima rodada",
                             canais_bloqueados=resumo.canais_bloqueados)
        self.log.info("bot", f"Rodada finalizada: {status}", **resumo.como_dict())
        try:
            self.repo.gravar_logs(execucao, self.log.retirar_pendentes())
            self.repo.finalizar_execucao(execucao, status, resumo.como_dict())
        except ErroBanco as e:
            self.log.critico("bot", "Não consegui gravar o resultado da rodada no banco", erro=str(e))
            status = "erro"
        return status, resumo
