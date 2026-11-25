"""Worker independente: consome a fila persistente `tarefas` (migration 020).

  python -m app.main worker --tipos vitrine.gerar,alertas.processar --max 50

* Reserva com FOR UPDATE SKIP LOCKED: vários workers em paralelo nunca pegam a mesma tarefa.
* Trava com prazo: se o worker morrer, a tarefa volta depois do prazo (ou falha ao esgotar tentativas).
* Retentativa com espera crescente (30 s, 60 s, 120 s… até 1 h), limitada por max_tentativas.
* Idempotência: tarefas enfileiradas com chave (ex.: "vitrine-2026-11-25T14") não duplicam.
* Concluir confere o worker: se a trava venceu e outro worker assumiu, o resultado deste é descartado.
* Handler deve ser idempotente (pode rodar de novo após queda); exceção = falha com retentativa.
"""
from __future__ import annotations

import os
import socket
from typing import Callable

from .erros import ErroBanco


class Worker:
    def __init__(self, repo, handlers: dict[str, Callable[[dict], dict | None]], log, nome: str | None = None,
                 bloqueio_min: int = 10):
        self.repo, self.handlers, self.log = repo, handlers, log
        self.nome = nome or f"{socket.gethostname()}-{os.getpid()}"
        self.bloqueio_min = bloqueio_min
        self.parar = False

    def rodar(self, max_tarefas: int = 50, lote: int = 5) -> dict:
        r = {"concluidas": 0, "falhas": 0, "descartadas": 0}
        tipos = sorted(self.handlers)
        while not self.parar and r["concluidas"] + r["falhas"] < max_tarefas:
            tarefas = self.repo.tarefas_pegar(tipos, min(lote, max_tarefas - r["concluidas"] - r["falhas"]),
                                              self.bloqueio_min, self.nome)
            if not tarefas:
                break
            for t in tarefas:
                try:
                    resultado = self.handlers[t["tipo"]](t.get("payload") or {})
                    ok, erro = True, None
                except ErroBanco:
                    raise   # banco fora: para o worker (a trava devolve a tarefa depois)
                except Exception as e:  # noqa: BLE001 — qualquer erro do handler vira retentativa
                    ok, erro, resultado = False, f"{type(e).__name__}: {e}", None
                if self.repo.tarefa_concluir(t["id"], ok, erro, resultado, self.nome):
                    r["concluidas" if ok else "falhas"] += 1
                    (self.log.info if ok else self.log.aviso)("worker", f"{t['tipo']} #{t['id']}: {'ok' if ok else erro}")
                else:
                    r["descartadas"] += 1
                    self.log.aviso("worker", f"{t['tipo']} #{t['id']}: outro worker assumiu a tarefa; resultado descartado")
        return r
