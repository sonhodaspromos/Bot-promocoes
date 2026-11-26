"""Publicação: valida links, enfileira ofertas e publica nos canais.

Travas (além das do banco):
  * só enfileira oferta com link validado;
  * antes de publicar, confere de novo se o link continua validado;
  * oferta de demonstração nunca vai para canal real.

Recuperação de falhas (auditoria):
  * token vencido / bot sem permissão → o canal é pausado nesta rodada, os itens voltam para a
    fila SEM gastar tentativa e um alerta CRÍTICO é registrado;
  * limite da API (Telegram 429, cota do Instagram) → itens voltam para a fila mais tarde;
  * resultado incerto (timeout depois de enviar) → o item NÃO é repetido (evita post duplicado);
  * banco fora do ar → para de publicar na hora; o que já foi enviado fica marcado como
    "publicando" e o banco nunca o publica de novo sozinho.

Auditoria corretiva P0:
  * antes de QUALQUER envio, o item é reservado com exclusividade no banco (fila_reservar_envio).
    Se a reserva não for gravada (erro, RPC ausente, trava vencida), NADA é enviado;
  * antes do primeiro envio real da rodada, o esquema do banco é conferido (migrations e RPCs);
  * qualquer falha DEPOIS de chamar o canal (5xx, conexão caída, resposta ilegível, erro ao gravar o
    resultado) é tratada como resultado incerto: o item nunca é reenviado automaticamente;
  * em produção, só publica link com atribuição de afiliado comprovada recentemente.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..cliente_http import ClienteHTTP
from ..erros import ErroBanco, ErroIntegracao, RegraViolada
from ..integracoes.base import CanalPublicacao
from ..logs import Log
from ..modelos import ConteudoPublicacao, ItemFila, ResultadoValidacao
from ..repositorio import Repositorio
from . import arte, textos
from .cupons import melhor_cupom
from ..rede import ImagemRecusada, baixar_imagem
from .comum import agora_utc, ajuste, dentro_do_horario, fuso, inicio_do_dia, num
from .validacao import LinkTemporariamenteIndisponivel, validar_link
from ..requisitos import faltando_para_publicar
from ..recursos import ligado
from ..inteligencia.experimentos import atribuir as atribuir_variante

CANAIS_POR_OFERTA = ("telegram", "instagram_story")  # o feed recebe só a oferta imperdível do dia


class ServicoPublicacao:
    def __init__(self, repo: Repositorio, canais: dict[str, CanalPublicacao], log: Log, *,
                 url_supabase: str, http: ClienteHTTP | None = None, relogio=agora_utc, fontes: dict | None = None):
        self.repo, self.canais, self.log = repo, canais, log
        self.fontes = fontes or {}  # para reconferir o preço na loja imediatamente antes de publicar
        self.url_cliques = url_supabase.rstrip("/") + "/functions/v1/r"
        self.http = http or ClienteHTTP("validacao", tentativas=2)
        self.relogio = relogio
        self.canais_bloqueados: dict[str, str] = {}  # canal → motivo (token vencido nesta rodada)
        self._cota: dict[str, int | None] = {}       # cota restante do Instagram, consultada 1x por rodada
        self._bloqueio_real: str | None = None       # motivo para não publicar em canal real nesta rodada
        from .selecao import ServicoSelecao
        self.selecao = ServicoSelecao(repo, log, relogio=self.relogio)
        self._experimento: dict | None | bool = False   # False = ainda não consultado nesta rodada
        self._extras: dict[int, dict] = {}              # variante A/B de cada item publicado
        self._requisitos_conferidos = False

    # ------------------------------------------------------------ 1. validar links
    def validar_links(self, cfg: dict, limite: int = 30) -> tuple[int, int]:
        ok = recusados = 0
        exigir = bool(cfg.get("links.exigir_atribuicao"))
        horas = float(cfg.get("links.validade_atribuicao_horas") or 72) if exigir else None
        for p in self.repo.links_pendentes(limite, revalidar_atribuicao_horas=horas):
            try:
                r = validar_link(p["url"], p["dominios"], self.http, demo=p["demo"], padrao=p.get("padrao"),
                                 verificar_http=bool(ajuste(cfg, "monitor.verificar_links")),
                                 dominios_destino=p.get("dominios_destino"), parametros_afiliado=p.get("parametros_afiliado"),
                                 id_afiliado=p.get("id_afiliado"),
                                 exigir_atribuicao=bool(cfg.get("links.exigir_atribuicao")) and not p["demo"])
            except LinkTemporariamenteIndisponivel as e:
                self.log.aviso("validacao", "Link não pôde ser verificado agora; tento na próxima rodada", erro=str(e))
                continue
            try:
                self.repo.registrar_validacao(p["link_id"], r)
            except RegraViolada as e:  # o banco recusou (ex.: domínio): fica como inválido
                self.repo.registrar_validacao(p["link_id"], ResultadoValidacao(False, e.descricao))
                r = ResultadoValidacao(False, e.descricao)
            if r.valido:
                ok += 1
            else:
                recusados += 1
                self.log.aviso("validacao", f"Link recusado: {r.motivo}", link_id=p["link_id"])
        return ok, recusados

    # ------------------------------------------------------------ 2. enfileirar
    def _canais_db(self) -> dict[str, dict]:
        return {c["codigo"]: c for c in self.repo.canais()
                if c.get("ativo") and c.get("modo") != "desativado" and c["codigo"] in self.canais}

    def enfileirar(self, cfg: dict) -> int:
        agora = self.relogio()
        canais = self._canais_db()
        candidatas = self.repo.promocoes_para_enfileirar(50)
        manuais = [c for c in candidatas if c.get("origem") == "manual"]
        automaticas = [c for c in candidatas if c.get("origem") != "manual"]

        selecionadas = list(manuais)  # cadastradas no painel: saem a qualquer hora
        if automaticas:
            motivo = self._motivo_para_esperar(cfg, agora)
            if motivo:
                self.log.info("publicacao", f"Publicação automática aguardando: {motivo}")
            else:
                hoje = {p["promocao_id"] for p in self.repo.publicacoes_desde(inicio_do_dia(cfg, agora), list(CANAIS_POR_OFERTA))}
                vagas = int(num(cfg, "publicacao.max_por_dia")) - len(hoje)
                quantas = max(0, min(int(num(cfg, "publicacao.por_rodada")), vagas))
                selecionadas += self._selecionar(automaticas, quantas, cfg)

        n = 0
        alvos, regras = self._canais_alvo(cfg, canais)
        agendar = ligado(cfg, "agendamento_inteligente")
        ultimos: dict[int, datetime | None] = {}
        for promo in selecionadas:
            for codigo in alvos:
                canal = canais.get(codigo)
                if not canal or not self._categoria_permitida(promo, canal["id"], regras):
                    continue
                if self._pode_ir(promo, codigo, canal["id"], cfg) and not self._saturado(promo, codigo, cfg):
                    prioridade = 1 if promo.get("origem") == "manual" else 5
                    quando = None
                    if agendar and promo.get("origem") != "manual":
                        quando = self._agendar(cfg, agora, canal["id"], ultimos, promo["id"], codigo)
                    if self.repo.enfileirar(promo["id"], canal["id"], promo["link_id"], prioridade,
                                            *((quando,) if quando else ())):
                        n += 1
                        if quando:
                            ultimos[canal["id"]] = quando
        n += self._enfileirar_feed(cfg, agora, canais)
        return n

    def _canais_alvo(self, cfg: dict, canais: dict) -> tuple[list[str], dict[int, list[int]]]:
        """Padrão: telegram + story. Com recursos.canais_por_categoria: também os canais extras de Telegram
        cadastrados (ex.: telegram_casa) e as regras de categoria de cada canal."""
        if not ligado(cfg, "canais_por_categoria"):
            return list(CANAIS_POR_OFERTA), {}
        extras = [c for c, row in canais.items() if c not in CANAIS_POR_OFERTA and c != "instagram_feed"
                  and row.get("tipo") == "telegram"]
        try:
            regras = self.repo.canais_categorias()
        except ErroBanco as e:
            # sem as regras não dá para saber o que cada canal extra aceita: publica só nos canais padrão
            self.log.aviso("publicacao", "Regras de canais por categoria indisponíveis; canais extras pulados nesta rodada",
                           erro=str(e))
            return list(CANAIS_POR_OFERTA), {}
        return list(CANAIS_POR_OFERTA) + sorted(extras), regras

    @staticmethod
    def _categoria_permitida(promo: dict, canal_id: int, regras: dict[int, list[int]]) -> bool:
        permitidas = regras.get(canal_id)
        return not permitidas or promo.get("categoria_id") in permitidas

    def _agendar(self, cfg: dict, agora, canal_id: int, ultimos: dict, promocao_id: int | None = None,
                 canal_codigo: str | None = None):
        from .agendamento import proximo_horario
        if canal_id not in ultimos:
            try:
                ultimos[canal_id] = self.repo.ultimo_agendado(canal_id)
            except ErroBanco:
                ultimos[canal_id] = None
        try:
            ap, _ = self.selecao.contexto(cfg)
        except Exception:
            ap = None
        exp = self._experimento_ativo()
        if exp and exp.get("tipo") == "horario" and promocao_id is not None and \
                atribuir_variante(exp["id"], promocao_id, canal_codigo or str(canal_id)) == "B":
            ap = None   # variante B do A/B de horário: próximo horário livre, sem o aprendizado
        h = proximo_horario(cfg, agora, ultimos[canal_id], ap)
        self.log.info("agendamento", f"Canal {canal_id}: post agendado para {h.quando.isoformat()} ({h.explicacao})")
        return h.quando

    def _selecionar(self, automaticas: list[dict], quantas: int, cfg: dict) -> list[dict]:
        """Ranking comercial (receita esperada sem sacrificar qualidade) com explicação de cada decisão.
        Se a seleção falhar, usa a ordem por nota do motor (comportamento anterior) e avisa."""
        try:
            decisoes = self.selecao.ordenar(automaticas, cfg)
        except Exception as e:  # nunca impede a publicação
            self.log.aviso("selecao", "Seleção comercial indisponível nesta rodada; uso a ordem por nota do motor",
                           erro=f"{type(e).__name__}: {e}")
            return automaticas[:quantas]
        por_id = {c["id"]: c for c in automaticas}
        escolhidas = []
        for d in decisoes:
            if not d.selecionada:
                continue
            if len(escolhidas) < quantas:
                escolhidas.append(por_id[d.promocao_id])
            else:
                d.selecionada = False
                d.recusa = f"sem vaga nesta rodada ({d.posicao}º lugar; limite de {quantas} por rodada/dia)"
        self.selecao.registrar(decisoes)
        return escolhidas

    def _saturado(self, promo: dict, codigo: str, cfg: dict) -> bool:
        """Canal com queda forte de cliques: nesta rodada só ofertas excelentes (manuais sempre passam)."""
        if promo.get("origem") == "manual" or promo.get("classificacao") == "excelente":
            return False
        try:
            motivo = self.selecao.canal_saturado(cfg, codigo)
        except Exception:
            return False
        if motivo:
            self.log.info("selecao", f"Oferta não enviada a {codigo}: {motivo}", promocao_id=promo.get("id"))
        return bool(motivo)

    def _pode_ir(self, promo: dict, codigo: str, canal_id: int | None = None, cfg: dict | None = None) -> bool:
        if promo.get("demo") and self.canais[codigo].modo == "real":
            return False
        if promo.get("classificacao") not in ("excelente", "boa"):
            return False  # só o que o motor de inteligência aprovou
        if canal_id is not None and promo.get("produto_id"):
            horas = num(cfg or {}, "inteligencia.janela_repeticao_horas")
            if self.repo.publicado_no_canal_recentemente(promo["produto_id"], canal_id, horas):
                return False  # mesma oferta no mesmo canal dentro da janela (padrão 24 h)
        return bool(promo.get("link_id"))

    def _motivo_para_esperar(self, cfg, agora) -> str | None:
        if not dentro_do_horario(cfg, agora):
            return f"fora do horário ({int(num(cfg, 'publicacao.horario_inicio'))}h–{int(num(cfg, 'publicacao.horario_fim'))}h)"
        intervalo = num(cfg, "publicacao.intervalo_minutos")
        recentes = self.repo.publicacoes_desde(agora - timedelta(minutes=intervalo - 1), list(CANAIS_POR_OFERTA))
        if recentes:
            return f"intervalo mínimo de {intervalo:.0f} min"
        return None

    def _enfileirar_feed(self, cfg, agora, canais) -> int:
        feed = canais.get("instagram_feed")
        if not feed:
            return 0
        if agora.astimezone(fuso(cfg)).hour < int(num(cfg, "publicacao.feed_horario")):
            return 0
        if self.repo.publicacoes_desde(inicio_do_dia(cfg, agora), ["instagram_feed"]):
            return 0  # já houve o post do dia
        melhor = self.repo.melhor_publicada_desde(agora - timedelta(hours=24))
        if not melhor or not self._pode_ir(melhor, "instagram_feed", feed["id"], cfg):
            return 0
        ok = self.repo.enfileirar(melhor["id"], feed["id"], melhor["link_id"], prioridade=3)
        if ok:
            self.log.info("publicacao", "Oferta imperdível do dia enfileirada para o feed", promocao_id=melhor["id"],
                          desconto=melhor.get("desconto_pct"))
        return int(ok)

    # ------------------------------------------------------------ 3. publicar
    def processar_fila(self, cfg: dict, limite: int = 10) -> tuple[int, int]:
        canais_por_id = {c["id"]: c for c in self.repo.canais()}
        ok = falhas = 0
        self._cfg = cfg
        fotos: dict[int, bytes | None] = {}
        self.conferir_requisitos()
        itens = self.repo.pegar_fila(limite)
        for n, item in enumerate(itens):
            canal_db = canais_por_id.get(item.canal_id) or {}
            codigo = canal_db.get("codigo", "?")
            canal = self.canais.get(codigo)
            if codigo in self.canais_bloqueados:
                self._devolver(item, 60, f"canal pausado nesta rodada: {self.canais_bloqueados[codigo]}")
                continue
            if canal is not None and canal.modo == "real" and self._bloqueio_real:
                self._devolver(item, 60, f"publicação real bloqueada: {self._bloqueio_real}")
                continue
            if canal is not None and canal.modo == "real" and self.parada_ativa():
                self._devolver(item, 30, "parada de emergência ligada: nenhuma publicação real")
                continue
            try:
                resultado = self._publicar_item(item, canal, codigo, cfg, fotos)
            except ErroBanco as e:
                # banco fora do ar: parar já. Os itens não enviados voltam sozinhos quando a trava vencer.
                self.log.critico("publicacao", "Banco indisponível durante a publicação; rodada de publicação interrompida",
                                 erro=str(e), itens_restantes=len(itens) - n - 1)
                falhas += 1
                break
            if resultado:
                ok += 1
            else:
                falhas += 1
        return ok, falhas

    def _publicar_item(self, item: ItemFila, canal, codigo: str, cfg: dict, fotos: dict) -> bool:
        """Publica um item. True = publicado. Lança ErroBanco se o banco cair (o chamador interrompe)."""
        dados = self.repo.dados_publicacao(item.promocao_id)
        if dados and dados.get("classificacao") is None and dados.get("status") in ("aprovada", "publicada"):
            self._devolver(item, 15, "aguardando nova análise (preço, estoque ou link mudou)")
            return False
        erro = self._checar(item, dados, canal)
        if erro:
            return self._registrar_falha(item, codigo, erro)
        if canal.modo == "real":
            motivo = self._atribuicao_pendente(dados, cfg)
            if motivo:
                self.log.aviso("publicacao", f"{codigo}: não publicado — {motivo}", promocao_id=item.promocao_id)
                self._devolver(item, 60, motivo)
                return False
            motivo = self._preco_mudou(dados, cfg)
            if motivo:
                self.log.aviso("publicacao", f"{codigo}: não publicado — {motivo}", promocao_id=item.promocao_id)
                self._devolver(item, 15, motivo)
                return False
        try:
            conteudo = self._montar(item, dados, canal, cfg, fotos)
        except Exception as e:  # arte/legenda com problema: falha registrada, nada foi enviado
            return self._registrar_falha(item, codigo, f"não consegui montar a publicação ({type(e).__name__}: {e})")

        if self._sem_cota(canal, codigo):
            self.canais_bloqueados[codigo] = "cota diária da API do Instagram esgotada"
            self.log.aviso("publicacao", f"{codigo}: cota de publicações da API esgotada; volta mais tarde")
            self._devolver(item, 60, "cota diária da API do Instagram esgotada")
            return False
        if not self._reservar_envio(item, canal, codigo):
            return False
        # ---- a partir daqui o canal pode ter recebido o post: nada volta para a fila automaticamente
        try:
            id_msg = canal.publicar(conteudo)
        except ErroIntegracao as e:
            return self._tratar_erro_canal(item, canal, codigo, e)
        except Exception as e:  # resposta inesperada do canal depois do envio: pode ter publicado
            return self._tratar_erro_canal(item, canal, codigo, ErroIntegracao(
                f"{codigo}: falha inesperada durante o envio ({type(e).__name__}: {e})", servico=codigo, incerto=True))
        try:
            extras = self._extras.get(item.id)   # só passa quando há teste A/B (compatível com repositórios antigos)
            self.repo.registrar_publicacao(item, True, id_msg, None, *((extras,) if extras else ()))
        except RegraViolada as e:
            self.log.critico("publicacao", "O banco recusou registrar a publicação (o post SAIU no canal)",
                             codigo=e.codigo, erro=str(e), mensagem=id_msg, promocao_id=item.promocao_id)
            return False
        except ErroBanco as e:
            # post enviado, resultado não gravado: o item continua reservado e o banco o encerra como
            # "resultado incerto" — nunca é reenviado. Registro no log para conferência manual.
            self.log.critico("publicacao", f"{codigo}: post ENVIADO, mas o banco não gravou o resultado. "
                             "Não será reenviado; confira o canal.", mensagem=id_msg, promocao_id=item.promocao_id,
                             fila_id=item.id, erro=str(e))
            raise
        self.log.info("publicacao", f"{canal.codigo}: publicado ({'DEMO' if canal.modo != 'real' else 'real'})",
                      promocao_id=item.promocao_id, mensagem=id_msg)
        return True

    def parada_ativa(self) -> bool:
        """Lida do banco na hora (não do cache da rodada): o botão de emergência vale no envio seguinte."""
        try:
            return bool(self.repo.parada_emergencia())
        except ErroBanco:
            return True  # sem conseguir confirmar, não publica de verdade

    def _preco_mudou(self, dados: dict, cfg: dict) -> str | None:
        """Reconfere preço e estoque na loja imediatamente antes de publicar. Devolve o motivo para NÃO publicar."""
        prod = self.repo.produto(dados["produto_id"]) if dados.get("produto_id") else None
        if not prod:
            return "produto não encontrado para reconferir o preço"
        fonte = self.fontes.get(prod["marketplace"])
        preco_post = float(dados["preco_promocional"])
        if fonte is not None and getattr(fonte, "suporta_consulta", False) and fonte.modo == "real":
            try:
                atual = fonte.consultar(prod["id_externo"])
            except (ErroIntegracao, ValueError) as e:
                return f"não consegui reconferir o preço na loja ({e})"
            if atual is None or not atual.disponivel:
                self.repo.atualizar_produto(prod["id"], preco_post, False, None)
                return "produto indisponível na loja no momento da publicação"
            if abs(atual.preco - preco_post) > max(0.01, preco_post * 0.005):
                self.repo.atualizar_produto(prod["id"], atual.preco, True, atual.preco_original)
                return f"preço mudou na loja ({preco_post:.2f} → {atual.preco:.2f}): nova análise antes de publicar"
            return None
        # loja sem consulta (ex.: Mercado Livre manual): o preço precisa ter sido conferido recentemente
        horas = float(cfg.get("publicacao.validade_preco_horas") or 24)
        conferido = prod.get("verificado_em")
        if isinstance(conferido, str):
            conferido = datetime.fromisoformat(conferido.replace("Z", "+00:00"))
        if not conferido or self.relogio() - conferido > timedelta(hours=horas):
            return f"preço sem conferência há mais de {horas:.0f} h: confirme o preço no painel"
        return None

    def _sem_cota(self, canal, codigo) -> bool:
        consultar = getattr(canal, "cota_restante", None)
        if consultar is None or getattr(canal, "modo", "") != "real":
            return False
        if codigo not in self._cota:
            self._cota[codigo] = consultar()
        restante = self._cota[codigo]
        if restante is None:
            return False  # não deu para consultar: segue (o erro de limite, se vier, é tratado)
        if restante <= 1:  # guarda 1 para o post do feed
            return True
        self._cota[codigo] = restante - 1
        return False

    def _tratar_erro_canal(self, item, canal, codigo, e: ErroIntegracao) -> bool:
        if e.autenticacao:
            self.canais_bloqueados[codigo] = "credencial recusada"
            self.log.critico("publicacao", f"{codigo}: o canal recusou a credencial (token vencido, revogado ou bot sem "
                             "permissão). Canal pausado até a credencial ser corrigida; as ofertas continuam na fila.",
                             erro=str(e))
            self._devolver(item, 60, f"credencial recusada: {e}")
            return False
        if e.limite:
            self.canais_bloqueados[codigo] = "limite da API atingido"
            self.log.aviso("publicacao", f"{codigo}: limite da API atingido; tento de novo em {e.esperar_min} min", erro=str(e))
            self._devolver(item, e.esperar_min, f"limite da API: {e}")
            return False
        if e.incerto:
            self.log.erro("publicacao", f"{codigo}: resultado incerto — o post pode ter saído. Não vou repetir "
                          "automaticamente para não duplicar; confira o canal.", erro=str(e), promocao_id=item.promocao_id)
            try:
                self.repo.marcar_incerta(item.id, str(e))
            except ErroBanco as be:
                self.log.erro("publicacao", "Não consegui marcar o item como incerto", erro=str(be))
            return False
        return self._registrar_falha(item, codigo, str(e))

    def _registrar_falha(self, item, codigo, erro) -> bool:
        self.log.erro("publicacao", f"Falha ao publicar em {codigo}", erro=erro, promocao_id=item.promocao_id)
        try:
            self.repo.registrar_publicacao(item, False, None, erro)
        except RegraViolada as e:
            self.log.erro("publicacao", "Não consegui registrar a falha", erro=str(e))
        return False

    def _experimento_ativo(self) -> dict | None:
        if self._experimento is False:
            try:
                self._experimento = self.repo.experimento_ativo()
            except ErroBanco:
                self._experimento = None
        return self._experimento or None

    def conferir_requisitos(self) -> str | None:
        """Antes do primeiro envio real da rodada: o banco tem as migrations e RPCs exigidas?"""
        if self._requisitos_conferidos:
            return self._bloqueio_real
        self._requisitos_conferidos = True
        if not any(getattr(c, "modo", "") == "real" for c in self.canais.values()):
            return None
        faltas = faltando_para_publicar(self.repo)
        if faltas:
            self._bloqueio_real = "banco sem os requisitos de publicação (" + "; ".join(faltas[:5]) + ")"
            self.log.critico("publicacao", "Publicações REAIS bloqueadas: o banco não tem as migrations/RPCs exigidas. "
                             "Aplique as migrations pendentes.", faltando=faltas)
        return self._bloqueio_real

    def _reservar_envio(self, item, canal, codigo) -> bool:
        """Reserva exclusiva gravada no banco ANTES do envio. Sem reserva, sem envio (nunca permissivo)."""
        try:
            reservado = self.repo.reservar_envio(item.id)
        except ErroBanco as e:
            self.log.critico("publicacao", f"{codigo}: não consegui gravar a reserva do envio; NADA foi enviado. "
                             "Publicação interrompida até o banco responder.", erro=str(e), fila_id=item.id)
            raise
        if not reservado:
            self.log.erro("publicacao", f"{codigo}: o item não está mais reservado para este envio (trava vencida, "
                          "já enviado ou cancelado). NADA foi enviado.", fila_id=item.id, promocao_id=item.promocao_id)
            return False
        return True

    def _atribuicao_pendente(self, dados: dict, cfg: dict) -> str | None:
        """Em produção (links.exigir_atribuicao), o link precisa ter a atribuição comprovada recentemente."""
        if not cfg.get("links.exigir_atribuicao"):
            return None
        link_id = (dados or {}).get("link_id")
        if not link_id:
            return "oferta sem link de afiliado"
        quando = self.repo.link_atribuicao(link_id)
        horas = float(cfg.get("links.validade_atribuicao_horas") or 72)
        if quando is None:
            return "atribuição de afiliado não comprovada: aguardando o link ser conferido até a loja"
        if self.relogio() - quando > timedelta(hours=horas):
            return f"atribuição de afiliado conferida há mais de {horas:.0f} h: aguardando nova conferência"
        return None

    def _devolver(self, item, minutos, motivo) -> None:
        try:
            self.repo.devolver_fila(item.id, minutos, motivo)
        except ErroBanco as e:
            if e.status == 404 or (e.codigo or "").startswith("PGRST"):  # migration 015 ausente: comportamento antigo
                self.repo.registrar_publicacao(item, False, None, motivo)
            else:
                raise

    def _checar(self, item: ItemFila, dados: dict | None, canal: CanalPublicacao | None) -> str | None:
        if canal is None:
            return "canal sem integração configurada"
        if not dados:
            return "promoção não encontrada"
        if not dados.get("link_validado"):
            return "link de afiliado não está validado — publicação bloqueada"
        if dados.get("demo") and canal.modo == "real":
            return "oferta de demonstração não pode ir para canal real"
        if dados.get("status") not in ("aprovada", "publicada"):
            return f"promoção está {dados.get('status')}"
        if dados.get("classificacao") not in ("excelente", "boa"):
            return "oferta não aprovada pelo motor de inteligência"
        return None

    def _foto(self, url: str | None, cache: dict) -> bytes | None:
        if not url:
            return None
        if url not in cache:
            try:
                cache[url] = baixar_imagem(self.http, url)   # https, IP público, ≤ 5 MB, JPEG/PNG/WEBP, ≤ 15 s
            except ImagemRecusada as e:
                self.log.aviso("publicacao", "Imagem do produto recusada; uso a arte padrão", motivo=str(e))
                cache[url] = None
        return cache[url]

    def _montar(self, item, dados, canal, cfg, cache) -> ConteudoPublicacao:
        destaque = canal.codigo == "instagram_feed"
        dados = dict(dados)
        try:
            achado = melhor_cupom(self.repo.cupons_da_promocao(item.promocao_id), float(dados["preco_promocional"]))
        except ErroBanco:
            achado = None  # sem a tabela de cupons (migration 016 ausente): publica sem cupom
        if achado and (not achado[0].demo or dados.get("demo")):
            dados["cupom_codigo"], dados["preco_com_cupom"] = achado[0].codigo, achado[1]
            dados["cupom_na_pagina"] = not achado[0].codigo  # cupom sem código: ativado na página do produto
        aplicar_desconto_da_analise(dados)
        # Amazon: a imagem do produto não é baixada nem re-hospedada na arte (regras de uso de conteúdo do
        # Programa de Associados); a arte sai com o fundo padrão.
        url_foto = None if (dados.get("marketplace") or "").lower() == "amazon" else dados.get("imagem_url")
        foto = arte.abrir_foto(self._foto(url_foto, cache))
        link = f"{self.url_cliques}?l={item.link_afiliado_id}&c={canal.codigo}"
        exp = self._experimento_ativo()
        variante_legenda = variante_arte = "A"
        if exp:
            variante = atribuir_variante(exp["id"], item.promocao_id, canal.codigo)
            self._extras[item.id] = {"experimento_id": exp["id"], "variante": variante}
            if exp.get("tipo") == "legenda":
                variante_legenda = variante
            elif exp.get("tipo") == "arte":
                variante_arte = variante
            # "horario": a variante já decidiu o agendamento na fila; aqui só é registrada
        if variante_arte == "B":
            cfg = dict(cfg, **{"visual.cor_principal": cfg.get("experimentos.arte_b_cor") or "#1F2937"})
        if canal.codigo.startswith("instagram"):
            legenda = textos.legenda_instagram(dados, cfg, destaque, variante_legenda)
        else:
            legenda = textos.legenda_telegram(dados, link, cfg, variante_legenda)
        return ConteudoPublicacao(
            promocao_id=item.promocao_id, titulo=dados["titulo"], preco=float(dados["preco_promocional"]),
            preco_original=float(dados["preco_referencia"]) if dados.get("preco_referencia") else None,
            desconto_pct=float(dados.get("desconto_pct") or 0), loja=dados.get("marketplace_nome") or "",
            link_rastreado=link, legenda=legenda, destaque=destaque, demo=bool(dados.get("demo")),
            imagem=arte.criar_arte(dados, foto, 1080, 1350, cfg, destaque),
            imagem_story=arte.criar_arte(dados, foto, 1080, 1920, cfg) if canal.codigo == "instagram_story" else None)

    # ------------------------------------------------------------ 4. encerrar publicações pausadas
    def encerrar_pausadas(self, desde) -> int:
        canais_por_id = {c["id"]: c["codigo"] for c in self.repo.canais()}
        n = 0
        for p in self.repo.pausadas_com_mensagens(desde):
            canal = self.canais.get(canais_por_id.get(p["canal_id"], ""))
            if not canal:
                continue
            try:
                canal.encerrar(p["id_mensagem"], p["titulo"], p["motivo"])
                n += 1
            except ErroIntegracao as e:
                self.log.aviso("publicacao", "Não consegui marcar a oferta como encerrada", erro=str(e))
        return n


def aplicar_desconto_da_analise(dados: dict) -> None:
    """O post usa o desconto que a ANÁLISE comprovou, nunca o "preço de" bruto da loja.
    Ex.: loja diz "de R$ 300", mas o maior preço visto foi R$ 82 → o post mostra o desconto sobre R$ 82
    (ou nenhum, se não houver comprovação)."""
    analise = dados.get("analise")
    if analise is None:
        return  # sem análise (só acontece em testes antigos/simulação): mantém os dados como estão
    comprovado = bool(analise.get("desconto_comprovado"))
    real = analise.get("desconto_real")
    base = str(analise.get("base_desconto") or "")
    if "cupom" in base:  # desconto vindo do cupom: a linha do cupom já mostra o preço final
        comprovado = False
    dados["desconto_comprovado"] = comprovado
    preco = float(dados.get("preco_promocional") or 0)
    if comprovado and real and 0 < float(real) < 100 and preco > 0:
        dados["desconto_pct"] = round(float(real), 0)
        dados["preco_referencia"] = round(preco / (1 - float(real) / 100), 2)
