"""Cupons de desconto e campanhas: busca diária, sugestão de produtos e stories diários.

O que é automático e o que não é (nenhuma loja oferece cupons por API):
  * campanhas da Shopee: vêm da API oficial (shopeeOfferV2) uma vez por dia;
  * cupons com código: o dono cadastra no painel (código, desconto, mínimo, validade);
  * produtos do cupom: o dono adiciona, ou aprova as sugestões do bot. Sugestão nunca é publicada
    sem aprovação, porque a loja não informa em quais produtos o cupom vale.

Regras: 1 publicação por cupom, canal e dia; respeita o horário, o limite de stories de cupom e a
cota do Instagram; cupom de demonstração nunca vai para canal real; resultado incerto não é repetido.
"""
from __future__ import annotations

from datetime import datetime

from ..erros import ErroBanco, ErroIntegracao, RegraViolada
from ..logs import Log
from ..modelos import ConteudoPublicacao, Cupom
from ..repositorio import Repositorio
from . import arte, textos
from .comum import agora_utc, ajuste, dentro_do_horario, fuso, inicio_do_dia, num
from .inteligencia import normalizar_titulo

CANAIS_CUPOM = ("instagram_story", "telegram")


class ServicoCupons:
    def __init__(self, repo: Repositorio, fontes: dict, publicacao, log: Log, relogio=agora_utc):
        self.repo, self.fontes, self.pub, self.log = repo, fontes, publicacao, log
        self.relogio = relogio

    # ------------------------------------------------------------ 1. campanhas oficiais (1x por dia)
    def sincronizar_campanhas(self, cfg: dict) -> int:
        if not ajuste(cfg, "cupons.ativo") or not ajuste(cfg, "cupons.buscar_campanhas"):
            return 0
        agora = self.relogio()
        if self.repo.campanhas_atualizadas_desde(inicio_do_dia(cfg, agora)) > 0:
            return 0  # já sincronizou hoje
        total = 0
        for codigo, fonte in self.fontes.items():
            if not getattr(fonte, "suporta_campanhas", False):
                continue
            try:
                campanhas = fonte.campanhas(20)
            except (ErroIntegracao, ValueError) as e:
                self.log.aviso("cupons", f"{codigo}: não consegui buscar campanhas", erro=str(e))
                continue
            for c in campanhas:
                try:
                    self.repo.salvar_campanha(c)
                    total += 1
                except RegraViolada as e:
                    self.log.aviso("cupons", f"Campanha ignorada: {c.titulo}", motivo=str(e), codigo=e.codigo)
        if total:
            self.log.info("cupons", f"{total} campanha(s) oficial(is) atualizada(s)")
        return total

    # ------------------------------------------------------------ 2. sugestões de produtos
    def sugerir_produtos(self, cfg: dict) -> int:
        if not ajuste(cfg, "cupons.ativo") or not ajuste(cfg, "cupons.sugerir_produtos"):
            return 0
        maximo = int(num(cfg, "cupons.max_sugestoes"))
        total = 0
        for c in self.repo.cupons_vigentes():
            palavras = [normalizar_titulo(p) for p in c.palavras_chave if normalizar_titulo(p)]
            if maximo <= 0 or (not palavras and not c.categoria_id):
                continue  # sem palavra-chave nem categoria: o bot não adivinha onde o cupom vale
            n = 0
            for o in self.repo.candidatos_para_cupom(c, maximo):
                if n >= maximo:
                    break
                motivo = self._combina(c, o, palavras)
                if not motivo:
                    continue
                preco = float(o.get("preco_promocional") or 0)
                if c.valor_minimo and preco < c.valor_minimo:
                    continue  # o cupom não se aplica a este preço
                try:
                    self.repo.sugerir_produto_cupom(c.id, o["id"], motivo)
                    n += 1
                except (RegraViolada, ErroBanco) as e:
                    self.log.aviso("cupons", "Não consegui registrar sugestão", erro=str(e))
            if n:
                self.log.info("cupons", f"{n} produto(s) sugerido(s) para o cupom {c.codigo or c.titulo}: aguardando sua aprovação")
            total += n
        return total

    @staticmethod
    def _combina(c: Cupom, o: dict, palavras: list[str]) -> str | None:
        titulo = f" {normalizar_titulo(o.get('titulo', ''))} "
        achou = [p for p in palavras if f" {p} " in titulo]
        if achou:
            return f"título contém: {', '.join(achou[:3])}"
        if c.categoria_id and o.get("categoria_id") == c.categoria_id:
            return "mesma categoria do cupom"
        return None

    # ------------------------------------------------------------ 3. publicação diária
    def publicar(self, cfg: dict) -> int:
        if not ajuste(cfg, "cupons.ativo"):
            return 0
        agora = self.relogio()
        if agora.astimezone(fuso(cfg)).hour < int(num(cfg, "cupons.horario")) or not dentro_do_horario(cfg, agora):
            return 0
        vigentes = self.repo.cupons_vigentes()
        if not vigentes:
            return 0
        dia = agora.astimezone(fuso(cfg)).date().isoformat()
        canais_db = {c["codigo"]: c for c in self.repo.canais()}
        feitos = self.repo.cupons_publicados_no_dia(dia)
        alvo = [cod for cod in CANAIS_CUPOM if cod in self.pub.canais and canais_db.get(cod, {}).get("ativo")
                and canais_db[cod].get("modo") != "desativado"]
        if not ajuste(cfg, "cupons.publicar_telegram"):
            alvo = [c for c in alvo if c != "telegram"]
        if any(self.pub.canais[c].modo == "real" for c in alvo) and self.pub.conferir_requisitos():
            alvo = [c for c in alvo if self.pub.canais[c].modo != "real"]  # banco sem requisitos: só simulados
            self.log.aviso("cupons", f"Cupons em canais reais bloqueados: {self.pub._bloqueio_real}")
        ordem = sorted(vigentes, key=lambda c: (c.origem != "manual", c.fim_em))  # os seus cupons primeiro
        max_stories = int(num(cfg, "cupons.max_stories_dia"))
        publicados = 0
        for codigo in alvo:
            canal_db, canal = canais_db[codigo], self.pub.canais[codigo]
            sucesso_hoje = sum(1 for f in feitos if f["canal_id"] == canal_db["id"] and f["resultado"] == "sucesso")
            for c in ordem:
                if codigo in self.pub.canais_bloqueados:
                    break
                if codigo == "instagram_story" and sucesso_hoje >= max_stories:
                    break
                if not self._pode(c, canal_db, feitos):
                    continue
                if canal.modo == "real" and self.pub.parada_ativa():
                    self.log.aviso("cupons", "Parada de emergência ligada: cupons não publicados")
                    return publicados
                if self.pub._sem_cota(canal, codigo):
                    self.pub.canais_bloqueados[codigo] = "cota diária da API do Instagram esgotada"
                    break
                try:
                    enviado = self._enviar(c, canal, canal_db, cfg)
                except ErroBanco as e:
                    self.log.critico("cupons", "Banco indisponível durante a publicação de cupons; interrompido", erro=str(e))
                    return publicados
                feitos.append({"cupom_id": c.id, "canal_id": canal_db["id"], "resultado": "enviando", "erro": None})
                if enviado:
                    publicados += 1
                    sucesso_hoje += 1
                    feitos.append({"cupom_id": c.id, "canal_id": canal_db["id"], "resultado": "sucesso", "erro": None})
        return publicados

    @staticmethod
    def _pode(c: Cupom, canal_db: dict, feitos: list[dict]) -> bool:
        if c.demo and canal_db.get("modo") == "real":
            return False
        meus = [f for f in feitos if f["cupom_id"] == c.id and f["canal_id"] == canal_db["id"]]
        if any(f["resultado"] == "sucesso" for f in meus):
            return False
        if any((f.get("erro") or "").startswith("incerto") for f in meus):
            return False  # pode ter saído: não repete
        reservas = sum(1 for f in meus if f["resultado"] == "enviando")
        if reservas > sum(1 for f in meus if f["resultado"] in ("sucesso", "erro")):
            return False  # reservado e sem resultado gravado: o envio anterior pode ter saído
        return sum(1 for f in meus if f["resultado"] == "erro") < 2

    def _enviar(self, c: Cupom, canal, canal_db: dict, cfg: dict) -> bool:
        codigo = canal_db["codigo"]
        try:
            conteudo = self._montar(c, cfg)
        except Exception as e:
            self.log.erro("cupons", "Não consegui montar a arte do cupom", erro=f"{type(e).__name__}: {e}", cupom_id=c.id)
            return False
        # reserva gravada ANTES do envio: sem ela, nada sai (e uma reserva sem resultado nunca é reenviada)
        try:
            reserva = self.repo.reservar_envio_cupom(c.id, canal_db["id"])
        except RegraViolada as e:
            self.log.aviso("cupons", f"{codigo}: cupom não publicado ({e})", cupom_id=c.id)
            return False
        if reserva is None:
            self.log.aviso("cupons", f"{codigo}: cupom já publicado hoje ou envio anterior sem resultado; não reenvio",
                           cupom_id=c.id)
            return False
        try:
            id_msg = canal.publicar(conteudo)
        except Exception as e:  # erro inesperado depois de chamar o canal: pode ter saído
            if not isinstance(e, ErroIntegracao):
                e = ErroIntegracao(f"{codigo}: falha inesperada durante o envio ({type(e).__name__}: {e})",
                                   servico=codigo, incerto=True)
            if e.autenticacao:
                self.pub.canais_bloqueados[codigo] = "credencial recusada"
                self.log.critico("cupons", f"{codigo}: o canal recusou a credencial; cupons pausados até corrigir", erro=str(e))
                return False
            if e.limite:
                self.pub.canais_bloqueados[codigo] = "limite da API atingido"
                self.log.aviso("cupons", f"{codigo}: limite da API atingido", erro=str(e))
                return False
            if e.incerto:
                # NÃO grava "erro": a reserva fica sem resultado = incerto = nunca reenviado automaticamente
                self.log.erro("cupons", f"{codigo}: resultado incerto ao publicar cupom — pode ter saído. Não vou repetir; "
                              "confira o canal.", erro=str(e), cupom_id=c.id)
                return False
            self.log.erro("cupons", f"Falha ao publicar cupom em {codigo}", erro=str(e), cupom_id=c.id)
            self._registrar(c, canal_db, False, None, str(e))
            return False
        if not self._registrar(c, canal_db, True, id_msg, None):
            self.log.critico("cupons", f"{codigo}: cupom ENVIADO, mas o resultado não foi gravado. A reserva impede "
                             "reenvio; confira o canal.", cupom_id=c.id, mensagem=id_msg)
        self.log.info("cupons", f"{codigo}: cupom publicado ({'DEMO' if canal.modo != 'real' else 'real'})",
                      cupom=c.codigo or c.titulo, mensagem=id_msg)
        return True

    def _registrar(self, c, canal_db, sucesso, id_msg, erro) -> bool:
        try:
            self.repo.registrar_publicacao_cupom(c.id, canal_db["id"], sucesso, id_msg, erro)
            return True
        except (RegraViolada, ErroBanco) as e:
            self.log.erro("cupons", "Não consegui registrar a publicação do cupom", erro=str(e), cupom_id=c.id)
            return False

    def _montar(self, c: Cupom, cfg: dict) -> ConteudoPublicacao:
        foto = None
        if c.imagem_url:
            dados = self.pub._foto(c.imagem_url, {})
            foto = arte.abrir_foto(dados) if dados else None
        return ConteudoPublicacao(
            promocao_id=0, titulo=c.titulo, preco=0.0, loja=c.marketplace_nome or c.marketplace,
            link_rastreado=c.link_afiliado or "", legenda=textos.legenda_cupom_telegram(c, cfg), demo=c.demo,
            imagem=arte.criar_arte_cupom(c, foto, 1080, 1350, cfg),
            imagem_story=arte.criar_arte_cupom(c, foto, 1080, 1920, cfg))

    def executar(self, cfg: dict) -> dict:
        return {"campanhas": self.sincronizar_campanhas(cfg), "sugestoes": self.sugerir_produtos(cfg),
                "cupons_publicados": self.publicar(cfg)}


def melhor_cupom(cupons: list[Cupom], preco: float) -> tuple[Cupom, float] | None:
    """O cupom que deixa o produto mais barato, e o preço final. Nada é estimado.
    Vale cupom com código ou cupom sem código ativado na página (ex.: "cupom por seguir a loja");
    campanha sem código não entra (não tem desconto aplicável a um produto)."""
    melhor = None
    for c in cupons:
        if not c.codigo and c.tipo != "cupom":
            continue
        final = c.preco_com_cupom(preco)
        if final is not None and (melhor is None or final < melhor[1]):
            melhor = (c, final)
    return melhor
