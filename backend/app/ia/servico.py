"""Etapa "IA" da rodada e uso dos textos na publicação.

Fluxo por oferta (só ofertas na fila, status "aprovada"):
  1. monta os fatos (mesmos dados e regras do post: desconto só comprovado, cupom aprovado, Pix);
  2. chave de idempotência = sha256(id do prompt + fatos). Já existe conteúdo com essa chave → não chama de novo;
  3. disjuntor: N falhas seguidas do provedor → pausa por M minutos;
  4. orçamento: reserva atômica do pior caso (teto diário e o que resta do mensal); estorna a sobra depois;
  5. chama o provedor, valida a resposta (seguranca.validar) e grava o conteúdo ("pendente" com aprovação
     manual, "aprovado" sem ela). Resposta inválida não vira conteúdo; depois de 2 tentativas inválidas a
     oferta não é mais tentada com aquele prompt (evita loop de gasto).
Na publicação, o texto aprovado é validado DE NOVO contra os dados do momento (o preço pode ter mudado).
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

from ..erros import ErroIntegracao
from ..logs import Log
from ..recursos import ligado
from ..repositorio import Repositorio

from .prompts import PromptInvalido, renderizar
from .provedor import ProvedorIA
from .seguranca import fatos_da_oferta, validar

CHAVE_PROMPT = "copy_oferta"
MAX_TENTATIVAS_INVALIDAS = 2


def num(cfg: dict, chave: str, padrao: float) -> float:
    """Número da configuração; valor ausente ou inválido usa o padrão (o mesmo da migration 028)."""
    try:
        v = cfg.get(chave)
        return padrao if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return padrao


def preparar_dados(repo: Repositorio, promocao_id: int) -> dict | None:
    """Dados da oferta exatamente como a publicação os usa (cupom aprovado e desconto da análise)."""
    from ..servicos.cupons import melhor_cupom
    from ..servicos.publicacao import aplicar_desconto_da_analise
    dados = repo.dados_publicacao(promocao_id)
    if not dados:
        return None
    dados = dict(dados)
    try:
        achado = melhor_cupom(repo.cupons_da_promocao(promocao_id), float(dados["preco_promocional"]))
    except Exception:  # noqa: BLE001 — sem a tabela de cupons: segue sem cupom
        achado = None
    if achado and (not achado[0].demo or dados.get("demo")):
        dados["cupom_codigo"], dados["preco_com_cupom"] = achado[0].codigo, achado[1]
        dados["cupom_na_pagina"] = not achado[0].codigo
    aplicar_desconto_da_analise(dados)
    return dados


def chave_idempotencia(prompt_id: int, fatos: dict) -> str:
    return hashlib.sha256(f"{prompt_id}|{json.dumps(fatos, sort_keys=True, ensure_ascii=False)}".encode()).hexdigest()


def estimar_tokens(texto: str) -> int:
    # estimativa conservadora (português ~ 3 caracteres por token); o custo real vem da resposta da API
    return math.ceil(len(texto) / 3) + 20


class ServicoIA:
    def __init__(self, repo: Repositorio, provedor: ProvedorIA | None, log: Log, relogio=None, env=None):
        self.repo, self.provedor, self.log = repo, provedor, log
        self.relogio = relogio or (lambda: datetime.now(timezone.utc))
        self.env = env

    # ------------------------------------------------------------------ configuração
    def ativo(self, cfg: dict) -> bool:
        return ligado(cfg, "ia", self.env)

    @staticmethod
    def custo(cfg: dict, tokens_entrada: int, tokens_saida: int) -> float:
        return round(tokens_entrada * num(cfg, "ia.preco_entrada_usd_mtok", 0.10) / 1e6
                     + tokens_saida * num(cfg, "ia.preco_saida_usd_mtok", 0.50) / 1e6, 6)

    def teto_de_hoje(self, cfg: dict) -> float:
        diario = num(cfg, "ia.orcamento_diario_usd", 0.50)
        restante_mes = num(cfg, "ia.orcamento_mensal_usd", 5) - self.repo.orcamento_gasto_mes_antes_de_hoje("ia")
        return max(0.0, round(min(diario, restante_mes), 6))

    def pausado(self, cfg: dict) -> str | None:
        """Disjuntor: as últimas N chamadas falharam e a mais recente foi há menos de M minutos."""
        n = int(num(cfg, "ia.circuito_falhas", 3))
        pausa = num(cfg, "ia.circuito_pausa_min", 30)
        ultimas = self.repo.ia_ultimas_chamadas(n)
        if len(ultimas) >= n and all(c["resultado"] == "erro" for c in ultimas):
            quando = ultimas[0]["quando"]
            if isinstance(quando, str):
                quando = datetime.fromisoformat(quando.replace("Z", "+00:00"))
            if self.relogio() - quando < timedelta(minutes=pausa):
                return f"{n} falhas seguidas do provedor; IA pausada até {pausa:g} min depois da última"
        return None

    # ------------------------------------------------------------------ etapa da rodada
    def executar(self, cfg: dict) -> dict:
        r = {"geradas": 0, "reaproveitadas": 0, "invalidas": 0, "erros": 0, "sem_orcamento": 0, "motivo": None}
        if not self.ativo(cfg):
            r["motivo"] = "desligada (recursos.ia)"
            return r
        if self.provedor is None:
            r["motivo"] = "sem chave da API (ANTHROPIC_API_KEY ou credencial no painel)"
            self.log.aviso("ia", "IA ligada, mas sem chave da API: nada foi gerado")
            return r
        prompt = self.repo.ia_prompt_ativo(CHAVE_PROMPT)
        if not prompt:
            r["motivo"] = "sem prompt ativo"
            self.log.aviso("ia", f"Nenhuma versão ativa do prompt '{CHAVE_PROMPT}'")
            return r
        motivo = self.pausado(cfg)
        if motivo:
            r["motivo"] = motivo
            self.log.aviso("ia", motivo)
            return r
        limite = int(num(cfg, "ia.max_por_rodada", 5))
        for promocao_id in self.repo.ia_promocoes_sem_conteudo(prompt["id"], limite * 3):
            if r["geradas"] >= limite:
                break
            try:
                situacao = self.gerar_para(promocao_id, prompt, cfg)
            except Exception as e:  # noqa: BLE001 — uma oferta com problema não derruba a etapa
                situacao = "erros"
                self.log.erro("ia", f"Falha inesperada ao gerar texto: {type(e).__name__}: {e}", promocao_id=promocao_id)
            r[situacao] = r.get(situacao, 0) + 1
            if situacao in ("sem_orcamento", "pausado"):
                break
            if situacao == "erros" and self.pausado(cfg):
                break
        if r["geradas"]:
            self.log.info("ia", f"{r['geradas']} texto(s) gerado(s) pela IA"
                          + (" aguardando aprovação no painel" if cfg.get("ia.aprovacao_manual", True) else ""))
        return r

    def gerar_para(self, promocao_id: int, prompt: dict, cfg: dict) -> str:
        dados = preparar_dados(self.repo, promocao_id)
        if not dados:
            return "erros"
        fatos = fatos_da_oferta(dados)
        chave = chave_idempotencia(prompt["id"], fatos)
        if self.repo.ia_conteudo_por_chave(chave):
            return "reaproveitadas"  # cache: mesmos dados e mesmo prompt já têm texto
        if self.repo.ia_tentativas_invalidas(promocao_id, prompt["id"]) >= MAX_TENTATIVAS_INVALIDAS:
            return "invalidas"
        valores = {**fatos, "tom_de_voz": str(cfg.get("ia.tom_de_voz") or "").strip()[:500],
                   "dados": json.dumps(fatos, ensure_ascii=False, indent=1)}
        try:
            sistema = renderizar(prompt["sistema"], valores)
            usuario = renderizar(prompt["modelo"], valores)
        except PromptInvalido as e:
            self.log.erro("ia", f"Prompt inválido: {e}")
            return "erros"
        modelo = str(cfg.get("ia.modelo") or "claude-haiku-5-5")
        max_saida = int(num(cfg, "ia.max_tokens_saida", 300))
        estimativa = self.custo(cfg, estimar_tokens(sistema + usuario), max_saida)
        if not self.repo.orcamento_reservar("ia", estimativa, self.teto_de_hoje(cfg)):
            self.repo.ia_registrar_chamada(prompt["id"], promocao_id, modelo, "sem_orcamento", 0, 0, 0, "teto atingido")
            self.log.aviso("ia", "Teto de gasto com IA atingido: geração pausada até o próximo dia/mês")
            return "sem_orcamento"
        try:
            resp = self.provedor.gerar(sistema, usuario, modelo=modelo, max_tokens=max_saida)
        except ErroIntegracao as e:
            if not e.incerto:  # falha certa (nada foi processado): devolve a reserva; incerta: mantém (conservador)
                self.repo.orcamento_estornar("ia", estimativa)
            self.repo.ia_registrar_chamada(prompt["id"], promocao_id, modelo, "erro", 0, 0, 0, str(e)[:500])
            self.log.aviso("ia", f"Provedor de IA falhou: {str(e)[:200]}", promocao_id=promocao_id)
            return "erros"
        custo = self.custo(cfg, resp.tokens_entrada, resp.tokens_saida)
        if estimativa > custo:
            self.repo.orcamento_estornar("ia", round(estimativa - custo, 6))
        elif custo > estimativa:  # resposta maior que o previsto (não deveria): registra o excesso mesmo assim
            self.repo.orcamento_reservar("ia", round(custo - estimativa, 6), 1e9)
        conteudo, motivo = validar(resp.texto, fatos)
        if conteudo is None:
            self.repo.ia_registrar_chamada(prompt["id"], promocao_id, resp.modelo, "invalido", resp.tokens_entrada,
                                           resp.tokens_saida, custo, motivo)
            self.log.aviso("ia", f"Texto da IA descartado: {motivo}", promocao_id=promocao_id)
            return "invalidas"
        chamada_id = self.repo.ia_registrar_chamada(prompt["id"], promocao_id, resp.modelo, "sucesso",
                                                    resp.tokens_entrada, resp.tokens_saida, custo, None)
        status = "pendente" if cfg.get("ia.aprovacao_manual", True) else "aprovado"
        self.repo.ia_salvar_conteudo(chave, promocao_id, prompt["id"], chamada_id, conteudo["chamada"], conteudo["texto"], status)
        return "geradas"

    # ------------------------------------------------------------------ publicação
    def texto_para_publicar(self, dados: dict, cfg: dict) -> dict | None:
        """Texto aprovado da IA para a oferta, revalidado com os dados de agora; None = usa o texto padrão."""
        if not self.ativo(cfg):
            return None
        try:
            c = self.repo.ia_conteudo_aprovado(dados["id"])
        except Exception:  # noqa: BLE001 — sem as tabelas da IA (migration 028): texto padrão
            return None
        if not c:
            return None
        conteudo, motivo = validar({"chamada": c["chamada"], "texto": c["texto"]}, fatos_da_oferta(dados))
        if conteudo is None:
            self.log.aviso("ia", f"Texto aprovado da IA não confere mais com a oferta ({motivo}): usado o texto padrão",
                           promocao_id=dados["id"])
            return None
        return conteudo
