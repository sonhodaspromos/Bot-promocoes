"""Acesso ao banco.

`Repositorio` define o que os serviços podem pedir ao banco. Há duas implementações:
  * SupabaseRepositorio — produção (PostgREST com a chave secreta).
  * MemoriaRepositorio  — testes e simulação local (repositorio_memoria.py).
Os serviços só conhecem a interface, então trocar o banco no futuro não mexe nas regras.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .erros import ErroBanco, ErroIntegracao, RegraViolada
from .cliente_http import ClienteHTTP
from .modelos import CampanhaEncontrada, Cupom, ItemFila, OfertaEncontrada, ResultadoValidacao


def agora() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def agora_iso_menos(minutos: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(minutes=minutos)).isoformat()


CAMPOS_OPCIONAIS_PRODUTO = ("gtin", "marca", "modelo", "vendedor_id", "vendedor_nome", "loja_oficial", "estoque")


class Repositorio(ABC):
    # ---- leitura de cadastro
    @abstractmethod
    def configuracoes(self) -> dict[str, Any]: ...
    @abstractmethod
    def marketplaces(self) -> list[dict]: ...
    @abstractmethod
    def canais(self) -> list[dict]: ...
    @abstractmethod
    def categorias(self) -> list[dict]: ...
    @abstractmethod
    def definir_modo(self, tabela: str, codigo: str, modo: str) -> None: ...

    # ---- catálogo e promoções
    @abstractmethod
    def salvar_produto(self, oferta: OfertaEncontrada, marketplace_id: int, categoria_id: int | None) -> tuple[int, bool]: ...
    @abstractmethod
    def criar_promocao(self, produto_id: int, oferta: OfertaEncontrada, status: str, origem: str) -> int | None: ...
    @abstractmethod
    def promocao_ativa(self, produto_id: int) -> dict | None: ...
    @abstractmethod
    def expirar_promocao(self, promocao_id: int, motivo: str) -> None: ...
    @abstractmethod
    def salvar_link(self, produto_id: int, promocao_id: int | None, url: str) -> int: ...
    @abstractmethod
    def links_pendentes(self, limite: int, revalidar_atribuicao_horas: float | None = None) -> list[dict]:
        """Links a validar. `url` é o destino EFETIVO do clique (url_curta, se houver). Com
        revalidar_atribuicao_horas, inclui links já validados sem comprovação recente de atribuição."""
    @abstractmethod
    def registrar_validacao(self, link_id: int, resultado: ResultadoValidacao) -> None: ...

    # ---- inteligência
    @abstractmethod
    def promocoes_para_analisar(self, limite: int) -> list[dict]: ...
    @abstractmethod
    def historico_precos(self, produto_ids: list[int], desde: datetime) -> dict[int, list[dict]]: ...
    @abstractmethod
    def titulos_ativos(self, desde: datetime) -> list[dict]: ...
    @abstractmethod
    def salvar_analise(self, promocao_id: int, pontuacao: int, classificacao: str, confianca: int, analise: dict) -> None: ...
    @abstractmethod
    def publicado_no_canal_recentemente(self, produto_id: int, canal_id: int, horas: float) -> bool: ...

    # ---- publicação
    @abstractmethod
    def promocoes_para_enfileirar(self, limite: int) -> list[dict]: ...
    @abstractmethod
    def enfileirar(self, promocao_id: int, canal_id: int, link_id: int, prioridade: int = 5) -> bool: ...
    @abstractmethod
    def pegar_fila(self, limite: int) -> list[ItemFila]: ...
    @abstractmethod
    def parada_emergencia(self) -> bool:
        """Lê o botão de emergência direto do banco (sem cache)."""
    @abstractmethod
    def fila_atrasada(self, minutos: int) -> int:
        """Itens pendentes cujo horário passou há mais de `minutos` (ofertas que deveriam ter saído)."""
    @abstractmethod
    def produto(self, produto_id: int) -> dict | None:
        """id, id_externo, marketplace (código), preco_atual, disponivel, verificado_em."""
    @abstractmethod
    def reservar_envio(self, fila_id: int) -> bool:
        """Reserva exclusiva do item ANTES do envio externo. False ou erro = não enviar."""
    @abstractmethod
    def reservar_envio_cupom(self, cupom_id: int, canal_id: int) -> int | None:
        """Grava 'enviando' antes de publicar o cupom. None = já publicado hoje ou envio anterior incerto."""
    @abstractmethod
    def esquema_publicacao(self) -> dict:
        """Versão do esquema, RPCs, configurações e colunas exigidas para publicar de verdade."""
    @abstractmethod
    def link_atribuicao(self, link_id: int) -> datetime | None:
        """Quando a atribuição do link foi comprovada (None = nunca)."""
    @abstractmethod
    def marcar_publicando(self, fila_id: int) -> None:
        """Marca o item logo antes de enviar ao canal (proteção contra post duplicado se o job morrer)."""
    @abstractmethod
    def devolver_fila(self, fila_id: int, minutos: int, motivo: str) -> None:
        """Devolve o item para a fila sem gastar tentativa (token vencido, limite da API, banco fora)."""
    @abstractmethod
    def marcar_incerta(self, fila_id: int, motivo: str) -> None:
        """O canal pode ter publicado: encerra o item sem nova tentativa automática."""
    @abstractmethod
    def dados_publicacao(self, promocao_id: int) -> dict | None: ...
    @abstractmethod
    def registrar_publicacao(self, item: ItemFila, sucesso: bool, id_mensagem: str | None, erro: str | None,
                             extras: dict | None = None) -> None:
        """extras: experimento_id e variante do teste A/B (opcional)."""
    @abstractmethod
    def publicacoes_desde(self, desde: datetime, tipos: list[str]) -> list[dict]: ...
    @abstractmethod
    def melhor_publicada_desde(self, desde: datetime) -> dict | None: ...

    # ---- cupons e campanhas
    @abstractmethod
    def cupons_vigentes(self) -> list[Cupom]:
        """Cupons/campanhas ativos e dentro da validade, do que vence primeiro ao último."""
    @abstractmethod
    def cupons_publicados_no_dia(self, dia: str) -> list[dict]:
        """Publicações de cupom do dia (data local AAAA-MM-DD): cupom_id, canal_id, resultado, erro."""
    @abstractmethod
    def registrar_publicacao_cupom(self, cupom_id: int, canal_id: int, sucesso: bool, id_mensagem: str | None,
                                   erro: str | None) -> None: ...
    @abstractmethod
    def salvar_campanha(self, c: CampanhaEncontrada) -> None:
        """Cria ou atualiza a campanha (sem mexer no status escolhido pelo dono)."""
    @abstractmethod
    def campanhas_atualizadas_desde(self, desde: datetime) -> int: ...
    @abstractmethod
    def candidatos_para_cupom(self, cupom: Cupom, limite: int) -> list[dict]:
        """Ofertas recomendadas pelo motor, da mesma loja, ainda não ligadas ao cupom."""
    @abstractmethod
    def sugerir_produto_cupom(self, cupom_id: int, promocao_id: int, motivo: str) -> None: ...
    @abstractmethod
    def cupons_da_promocao(self, promocao_id: int) -> list[Cupom]:
        """Cupons vigentes APROVADOS para a oferta."""

    # ---- monitor
    @abstractmethod
    def publicadas_para_monitorar(self, dias: int, limite: int) -> list[dict]: ...
    @abstractmethod
    def atualizar_produto(self, produto_id: int, preco: float, disponivel: bool, preco_original: float | None) -> None: ...
    @abstractmethod
    def invalidar_link(self, link_id: int, motivo: str) -> None: ...
    @abstractmethod
    def pausadas_com_mensagens(self, desde: datetime) -> list[dict]: ...

    # ---- comissões, execuções, logs, credenciais
    @abstractmethod
    def salvar_comissoes(self, marketplace_id: int, itens: list[dict]) -> dict:
        """Cria ou ATUALIZA (status e valores mudam: pendente → confirmada/cancelada). Comissão já 'paga' não volta atrás.
        Devolve {"novos", "atualizados", "ignorados"}."""
    @abstractmethod
    def comissoes_api_periodo(self, marketplace_id: int, inicio: str, fim: str) -> list[dict]: ...
    @abstractmethod
    def registrar_importacao(self, dados: dict) -> None: ...
    @abstractmethod
    def ultima_importacao(self, marketplace_id: int, tipo: str) -> datetime | None: ...
    # ---- inteligência comercial (migration 019; sem ela, tudo devolve "sem dados")
    @abstractmethod
    def dados_ranking(self, promocao_ids: list[int]) -> dict[int, dict]:
        """Por oferta: produto, marketplace, título, preço, comissão (e se é oficial), categoria, faixa,
        vendedor, estoque, GTIN/marca/modelo e o resumo da análise (pontuação, desconto comprovado)."""
    @abstractmethod
    def desempenho_publicacoes(self, desde: datetime) -> list[dict]:
        """Linhas de vw_desempenho_publicacoes desde a data (vazio sem a migration 019)."""
    @abstractmethod
    def salvar_selecoes(self, decisoes: list[dict]) -> None: ...
    @abstractmethod
    def salvar_equivalencias(self, achados: list[dict]) -> None: ...
    @abstractmethod
    def experimento_ativo(self) -> dict | None: ...
    @abstractmethod
    def salvar_resultado_experimento(self, experimento_id: int, resultado: dict) -> None: ...
    @abstractmethod
    def salvar_resumo_inteligencia(self, tipo: str, dados: dict) -> None: ...
    @abstractmethod
    def iniciar_execucao(self, origem: str, modo: str, versao: str) -> str: ...
    @abstractmethod
    def finalizar_execucao(self, execucao_id: str, status: str, resumo: dict) -> None: ...
    @abstractmethod
    def encerrar_execucoes_orfas(self, minutos: int = 30) -> int:
        """Rodadas que ficaram 'rodando' (job morto) viram 'erro' com o motivo."""
    @abstractmethod
    def ultima_execucao(self) -> dict | None: ...
    @abstractmethod
    def gravar_logs(self, execucao_id: str | None, itens: list[dict]) -> None: ...
    @abstractmethod
    def obter_credencial(self, integracao: str, nome: str) -> str | None: ...


class SupabaseRepositorio(Repositorio):
    """Fala com o PostgREST do Supabase usando a chave secreta (ignora RLS)."""

    def __init__(self, url: str, chave: str, http: ClienteHTTP | None = None):
        self.base = url.rstrip("/") + "/rest/v1"
        self.cab = {"apikey": chave, "Content-Type": "application/json"}
        if chave.startswith("eyJ"):  # chave antiga (JWT service_role)
            self.cab["Authorization"] = f"Bearer {chave}"
        self.http = http or ClienteHTTP("supabase", tentativas=3)

    # ------------------------------------------------------------ baixo nível
    def _req(self, metodo: str, caminho: str, *, params=None, corpo=None, prefer: str | None = None,
             ok_status: tuple[int, ...] = ()) -> Any:
        cab = dict(self.cab)
        if prefer:
            cab["Prefer"] = prefer
        try:
            resp = self.http.requisitar(metodo, f"{self.base}/{caminho}", params=params, json=corpo,
                                        headers=cab, ok_status=ok_status + (400, 401, 403, 404, 409))
        except ErroIntegracao as e:
            raise ErroBanco(str(e), status=e.status) from e
        if resp.status_code >= 400 and resp.status_code not in ok_status:
            try:
                info = resp.json()
            except ValueError:
                info = {"message": resp.text}
            codigo = str(info.get("code") or "")
            msg = f"{metodo} {caminho}: {info.get('message') or resp.text}"
            if codigo.startswith("P00"):
                raise RegraViolada(msg, codigo=codigo, status=resp.status_code)
            raise ErroBanco(msg, codigo=codigo, status=resp.status_code)
        if not resp.text:
            return None
        return resp.json()

    def _get(self, tabela, **params):
        return self._req("GET", tabela, params=params) or []

    def _rpc(self, funcao, **args):
        return self._req("POST", f"rpc/{funcao}", corpo=args)

    @staticmethod
    def _in(valores) -> str:
        return "in.(" + ",".join(str(v) for v in valores) + ")"

    # ------------------------------------------------------------ cadastro
    def configuracoes(self):
        return {l["chave"]: l["valor"] for l in self._get("configuracoes", select="chave,valor")}

    def marketplaces(self):
        return self._get("marketplaces", select="id,codigo,nome,dominios_afiliado,modo,ativo,possui_api")

    def canais(self):
        return self._get("canais", select="id,codigo,tipo,nome,modo,ativo,identificador_externo,limite_diario")

    def categorias(self):
        return self._get("categorias", select="id,slug,nome,palavras_chave,ativa", order="ordem")

    def definir_modo(self, tabela, codigo, modo):
        if tabela not in ("marketplaces", "canais"):
            raise ValueError(tabela)
        self._req("PATCH", tabela, params={"codigo": f"eq.{codigo}", "modo": f"neq.{modo}"},
                  corpo={"modo": modo}, prefer="return=minimal")

    # ------------------------------------------------------------ catálogo
    def salvar_produto(self, oferta, marketplace_id, categoria_id):
        dados = {
            "titulo": oferta.titulo[:300], "preco_atual": round(oferta.preco, 2),
            "preco_original": round(oferta.preco_original, 2) if oferta.preco_original else None,
            "nota": oferta.nota, "total_vendas": oferta.total_vendas, "imagem_url": oferta.imagem_url,
            "url_produto": oferta.url_produto, "disponivel": oferta.disponivel,
            "verificado_em": iso(agora()),
            "preco_maximo_variacao": round(oferta.preco_maximo, 2) if oferta.preco_maximo else None,
            "referencia_derivada": bool(oferta.referencia_derivada),
        }
        # dados opcionais: só gravados quando a loja informou (não apaga o que já se sabia)
        for campo in CAMPOS_OPCIONAIS_PRODUTO:
            valor = getattr(oferta, campo, None)
            if valor is not None:
                dados[campo] = valor
        existente = self._get("produtos", select="id", marketplace_id=f"eq.{marketplace_id}",
                              id_externo=f"eq.{oferta.id_externo}")
        if existente:
            pid = existente[0]["id"]
            self._gravar_produto("PATCH", {"id": f"eq.{pid}"}, dados, "return=minimal")
            return pid, False
        dados.update({"marketplace_id": marketplace_id, "id_externo": oferta.id_externo,
                      "categoria_id": categoria_id, "demo": oferta.demo})
        linhas = self._gravar_produto("POST", {"select": "id"}, dados, "return=representation")
        return linhas[0]["id"], True

    def _gravar_produto(self, metodo, params, dados, prefer):
        try:
            return self._req(metodo, "produtos", params=params, corpo=dados, prefer=prefer)
        except ErroBanco as e:
            if e.codigo not in ("PGRST204", "42703"):
                raise
            # banco sem a migration 017: grava sem os campos novos (variação e "preço de" derivado)
            novos = ("preco_maximo_variacao", "referencia_derivada") if e.codigo == "PGRST204" and \
                not any(c in dados for c in CAMPOS_OPCIONAIS_PRODUTO) else ()
            enxuto = {k: v for k, v in dados.items() if k not in CAMPOS_OPCIONAIS_PRODUTO and k not in novos}
            if enxuto == dados:
                enxuto = {k: v for k, v in dados.items() if k not in ("preco_maximo_variacao", "referencia_derivada")}
            return self._req(metodo, "produtos", params=params, corpo=enxuto, prefer=prefer)

    def criar_promocao(self, produto_id, oferta, status, origem):
        try:
            linhas = self._req("POST", "promocoes", params={"select": "id"}, prefer="return=representation", corpo={
                "produto_id": produto_id, "preco_promocional": round(oferta.preco, 2),
                "preco_referencia": round(oferta.preco_original, 2) if oferta.preco_original else None,
                "comissao_pct": oferta.comissao_pct, "status": status, "origem": origem,
            })
        except ErroBanco as e:
            if e.codigo == "23505":  # já existe promoção ativa para o produto
                return None
            raise
        return linhas[0]["id"]

    def promocao_ativa(self, produto_id):
        linhas = self._get("promocoes", select="id,status,publicada_em", produto_id=f"eq.{produto_id}",
                           status="in.(encontrada,aprovada,publicada)", limit="1")
        return linhas[0] if linhas else None

    def expirar_promocao(self, promocao_id, motivo):
        self._req("PATCH", "promocoes", params={"id": f"eq.{promocao_id}"}, prefer="return=minimal",
                  corpo={"status": "expirada", "motivo_status": motivo[:300]})

    def salvar_link(self, produto_id, promocao_id, url):
        existente = self._get("links_afiliados", select="id,produto_id", url=f"eq.{url}")
        if existente:
            return existente[0]["id"]
        linhas = self._req("POST", "links_afiliados", params={"select": "id"}, prefer="return=representation",
                           corpo={"produto_id": produto_id, "promocao_id": promocao_id, "url": url})
        return linhas[0]["id"]

    def links_pendentes(self, limite, revalidar_atribuicao_horas=None):
        # links nunca verificados (e, em produção, validados sem atribuição comprovada recentemente),
        # de produtos com promoção aprovada ou publicada
        filtro = {"validado": "is.false", "status_http": "is.null"}
        if revalidar_atribuicao_horas:
            limite_iso = agora_iso_menos(int(float(revalidar_atribuicao_horas) * 60))
            filtro = {"or": f"(and(validado.is.false,status_http.is.null),and(validado.is.true,or(atribuicao_verificada_em.is.null,"
                            f"atribuicao_verificada_em.lt.{limite_iso})))"}

        def buscar(colunas):
            return self._get(
                "links_afiliados",
                select=f"id,url,url_curta,produto_id,produtos!inner(demo,marketplaces!inner({colunas}),promocoes!inner(status))",
                ativo="is.true", **filtro,
                **{"produtos.promocoes.status": "in.(aprovada,publicada)"}, limit=str(limite))
        linhas = None
        for colunas in ("codigo,dominios_afiliado,padrao_afiliado,dominios_destino,parametros_afiliado,id_afiliado",
                        "codigo,dominios_afiliado,padrao_afiliado",   # banco sem a migration 017
                        "codigo,dominios_afiliado"):                   # banco sem a migration 015
            try:
                linhas = buscar(colunas)
                break
            except ErroBanco as e:
                if e.codigo not in ("42703", "PGRST200", "PGRST204"):
                    raise
        return [{"link_id": l["id"], "url": l.get("url_curta") or l["url"], "produto_id": l["produto_id"],
                 "demo": l["produtos"]["demo"], "marketplace": l["produtos"]["marketplaces"]["codigo"],
                 "dominios": l["produtos"]["marketplaces"]["dominios_afiliado"],
                 "padrao": l["produtos"]["marketplaces"].get("padrao_afiliado"),
                 "dominios_destino": l["produtos"]["marketplaces"].get("dominios_destino"),
                 "parametros_afiliado": l["produtos"]["marketplaces"].get("parametros_afiliado") or [],
                 "id_afiliado": l["produtos"]["marketplaces"].get("id_afiliado")} for l in linhas]

    def registrar_validacao(self, link_id, resultado):
        corpo = {"validado": resultado.valido, "status_http": resultado.status_http,
                 "motivo_invalido": None if resultado.valido else (resultado.motivo or "inválido")[:300],
                 "atribuicao_verificada_em": datetime.now(timezone.utc).isoformat()
                 if resultado.valido and resultado.atribuicao else None}
        try:
            self._req("PATCH", "links_afiliados", params={"id": f"eq.{link_id}"}, prefer="return=minimal", corpo=corpo)
        except ErroBanco as e:
            if e.codigo not in ("PGRST204", "42703"):
                raise
            corpo.pop("atribuicao_verificada_em")  # banco sem a migration 018: atribuição nunca conta como comprovada
            self._req("PATCH", "links_afiliados", params={"id": f"eq.{link_id}"}, prefer="return=minimal", corpo=corpo)

    def link_atribuicao(self, link_id):
        linhas = self._get("links_afiliados", select="atribuicao_verificada_em", id=f"eq.{link_id}")
        valor = linhas[0].get("atribuicao_verificada_em") if linhas else None
        return datetime.fromisoformat(valor.replace("Z", "+00:00")) if valor else None

    # ------------------------------------------------------------ publicação
    def promocoes_para_enfileirar(self, limite):
        linhas = self._get("vw_promocoes_painel",
                           select="id,produto_id,link_id,desconto_pct,demo,marketplace,origem,pontuacao,classificacao",
                           status="eq.aprovada", link_validado="is.true", classificacao="in.(excelente,boa)",
                           order="pontuacao.desc,desconto_pct.desc", limit=str(limite * 3))
        if not linhas:
            return []
        na_fila = {f["promocao_id"] for f in self._get(
            "fila_publicacoes", select="promocao_id", promocao_id=self._in(l["id"] for l in linhas),
            status="in.(pendente,processando)")}
        return [l for l in linhas if l["id"] not in na_fila][:limite]

    def enfileirar(self, promocao_id, canal_id, link_id, prioridade=5):
        try:
            self._req("POST", "fila_publicacoes", prefer="return=minimal", corpo={
                "promocao_id": promocao_id, "canal_id": canal_id, "link_afiliado_id": link_id, "prioridade": prioridade})
        except ErroBanco as e:
            if e.codigo == "23505" or isinstance(e, RegraViolada):
                return False
            raise
        return True

    def pegar_fila(self, limite):
        linhas = self._rpc("pegar_proximas_publicacoes", p_limite=limite, p_bloqueio_min=10) or []
        return [ItemFila(id=l["id"], promocao_id=l["promocao_id"], canal_id=l["canal_id"],
                         link_afiliado_id=l["link_afiliado_id"], tentativas=l["tentativas"]) for l in linhas]

    def parada_emergencia(self):
        linhas = self._get("configuracoes", select="valor", chave="eq.sistema.parada_emergencia")
        return bool(linhas and linhas[0]["valor"] is True)

    def fila_atrasada(self, minutos):
        limite = (agora_iso_menos(minutos))
        return len(self._get("fila_publicacoes", select="id", status="eq.pendente", agendado_para=f"lt.{limite}", limit="500"))

    def produto(self, produto_id):
        linhas = self._get("produtos", select="id,id_externo,preco_atual,disponivel,verificado_em,marketplaces!inner(codigo)",
                           id=f"eq.{produto_id}")
        if not linhas:
            return None
        p = linhas[0]
        return {"id": p["id"], "id_externo": p["id_externo"], "marketplace": p["marketplaces"]["codigo"],
                "preco_atual": float(p["preco_atual"]), "disponivel": p["disponivel"], "verificado_em": p["verificado_em"]}

    def marcar_publicando(self, fila_id):
        self._rpc("fila_marcar_publicando", p_id=fila_id)

    def reservar_envio(self, fila_id):
        # sem fallback: banco sem a RPC (migration 018) = ErroBanco = nada é enviado
        return self._rpc("fila_reservar_envio", p_id=fila_id) is True

    def reservar_envio_cupom(self, cupom_id, canal_id):
        r = self._rpc("cupom_reservar_envio", p_cupom=cupom_id, p_canal=canal_id)
        return int(r) if r is not None else None

    def esquema_publicacao(self):
        return self._rpc("esquema_publicacao") or {}

    def devolver_fila(self, fila_id, minutos, motivo):
        self._rpc("fila_devolver", p_id=fila_id, p_minutos=int(minutos), p_motivo=motivo[:900])

    def marcar_incerta(self, fila_id, motivo):
        self._rpc("fila_marcar_incerta", p_id=fila_id, p_motivo=motivo[:700])

    def dados_publicacao(self, promocao_id):
        linhas = self._get("vw_promocoes_painel", id=f"eq.{promocao_id}", select=(
            "id,status,titulo,imagem_url,preco_promocional,preco_referencia,desconto_pct,marketplace,"
            "marketplace_nome,link_id,link_validado,demo,pontuacao,classificacao,analise,produto_id"))
        return linhas[0] if linhas else None

    def registrar_publicacao(self, item, sucesso, id_mensagem, erro, extras=None):
        corpo = {"fila_id": item.id, "promocao_id": item.promocao_id, "canal_id": item.canal_id,
                 "link_afiliado_id": item.link_afiliado_id, "resultado": "sucesso" if sucesso else "erro",
                 "id_mensagem_externa": id_mensagem, "erro": None if sucesso else (erro or "erro")[:2000]}
        corpo.update({k: v for k, v in (extras or {}).items() if k in ("experimento_id", "variante") and v is not None})
        try:
            self._req("POST", "historico_publicacoes", prefer="return=minimal", corpo=corpo)
        except ErroBanco as e:
            if e.codigo not in ("PGRST204", "42703") or "variante" not in corpo:
                raise
            corpo.pop("experimento_id", None); corpo.pop("variante", None)   # banco sem a migration 019
            self._req("POST", "historico_publicacoes", prefer="return=minimal", corpo=corpo)

    def publicacoes_desde(self, desde, tipos):
        canais = {c["id"]: c["tipo"] for c in self.canais() if c["tipo"] in tipos}
        if not canais:
            return []
        return self._get("historico_publicacoes", select="promocao_id,canal_id,publicado_em",
                         resultado="eq.sucesso", demo="is.false", canal_id=self._in(canais),
                         publicado_em=f"gte.{iso(desde)}", order="publicado_em.desc")

    def melhor_publicada_desde(self, desde):
        linhas = self._get("vw_promocoes_painel", select="id,produto_id,link_id,desconto_pct,demo,pontuacao,classificacao",
                           status="eq.publicada", link_validado="is.true", classificacao="in.(excelente,boa)",
                           publicada_em=f"gte.{iso(desde)}", order="pontuacao.desc,desconto_pct.desc", limit="1")
        return linhas[0] if linhas else None

    # ------------------------------------------------------------ inteligência
    def promocoes_para_analisar(self, limite):
        linhas = self._get(
            "promocoes",
            select="id,status,origem,demo,preco_promocional,preco_referencia,comissao_pct,produto_id,"
                   "produtos!inner(titulo,preco_atual,disponivel,nota,total_vendas,imagem_url,categoria_id,"
                   "preco_maximo_variacao,referencia_derivada,categorias(ativa),marketplaces!inner(codigo,modo))",
            status="in.(encontrada,aprovada,publicada)", analisada_em="is.null", order="criado_em.asc", limit=str(limite))
        if not linhas:
            return []
        links = {}
        for l in self._get("links_afiliados", select="produto_id,validado,status_http,motivo_invalido,validado_em",
                           produto_id=self._in(sorted({x["produto_id"] for x in linhas})), ativo="is.true",
                           order="validado.desc,validado_em.desc.nullslast"):
            links.setdefault(l["produto_id"], l)
        saida = []
        for x in linhas:
            p = x["produtos"]
            saida.append({
                "promocao_id": x["id"], "produto_id": x["produto_id"], "status": x["status"], "origem": x["origem"],
                "demo": x["demo"], "preco": float(x["preco_promocional"]),
                "preco_referencia": float(x["preco_referencia"]) if x["preco_referencia"] is not None else None,
                "comissao_pct": float(x["comissao_pct"]) if x["comissao_pct"] is not None else None,
                "titulo": p["titulo"], "preco_produto": float(p["preco_atual"]), "disponivel": p["disponivel"],
                "nota": float(p["nota"]) if p["nota"] is not None else None, "total_vendas": p["total_vendas"],
                "imagem_url": p["imagem_url"],
                "preco_maximo_variacao": float(p["preco_maximo_variacao"]) if p.get("preco_maximo_variacao") is not None else None,
                "referencia_derivada": bool(p.get("referencia_derivada")),
                "categoria_ativa": (p.get("categorias") or {}).get("ativa") if p.get("categoria_id") else None,
                "marketplace": p["marketplaces"]["codigo"], "marketplace_modo": p["marketplaces"]["modo"],
                "link": links.get(x["produto_id"])})
        return saida

    def historico_precos(self, produto_ids, desde):
        """Histórico desde `desde` + o último preço ANTES (âncora), para saber o preço no início da janela."""
        if not produto_ids:
            return {}
        saida: dict[int, list[dict]] = {}
        try:
            linhas = self._rpc("historico_precos_janela", p_produtos=sorted(set(produto_ids)), p_desde=iso(desde)) or []
            for h in linhas:
                saida.setdefault(h["produto_id"], []).append(
                    {"preco": float(h["preco"]), "disponivel": h["disponivel"], "quando": h["registrado_em"],
                     "ancora": bool(h.get("ancora"))})
            return saida
        except ErroBanco as e:
            if e.codigo not in ("PGRST202", "42883") and e.status != 404:
                raise
            # banco sem a migration 019: sem âncora (janelas que começam antes do 1º registro ficam "insuficientes")
        for h in self._get("historico_precos", select="produto_id,preco,disponivel,registrado_em",
                           produto_id=self._in(sorted(set(produto_ids))), registrado_em=f"gte.{iso(desde)}",
                           order="registrado_em.asc", limit="5000"):
            saida.setdefault(h["produto_id"], []).append(
                {"preco": float(h["preco"]), "disponivel": h["disponivel"], "quando": h["registrado_em"]})
        return saida

    def titulos_ativos(self, desde):
        return [{"promocao_id": x["id"], "produto_id": x["produto_id"], "titulo": x["produtos"]["titulo"],
                 "marketplace": x["produtos"]["marketplaces"]["nome"]}
                for x in self._get("promocoes", select="id,produto_id,produtos!inner(titulo,marketplaces!inner(nome))",
                                   status="in.(encontrada,aprovada,publicada)", criado_em=f"gte.{iso(desde)}",
                                   limit="5000")]

    def salvar_analise(self, promocao_id, pontuacao, classificacao, confianca, analise):
        self._req("PATCH", "promocoes", params={"id": f"eq.{promocao_id}"}, prefer="return=minimal", corpo={
            "pontuacao": pontuacao, "classificacao": classificacao, "confianca": confianca,
            "analise": analise, "analisada_em": iso(agora())})

    def publicado_no_canal_recentemente(self, produto_id, canal_id, horas):
        return bool(self._get(
            "historico_publicacoes", select="id,promocoes!inner(produto_id)", canal_id=f"eq.{canal_id}",
            resultado="eq.sucesso", demo="is.false", publicado_em=f"gte.{iso(agora() - timedelta(hours=horas))}",
            **{"promocoes.produto_id": f"eq.{produto_id}"}, limit="1"))

    # ------------------------------------------------------------ monitor
    def publicadas_para_monitorar(self, dias, limite):
        linhas = self._get(
            "promocoes",
            select="id,produto_id,preco_promocional,produtos!inner(id_externo,verificado_em,"
                   "marketplaces!inner(codigo,dominios_afiliado))",
            status="eq.publicada", publicada_em=f"gte.{iso(agora() - timedelta(days=dias))}",
            order="produtos(verificado_em).asc.nullsfirst", limit=str(limite))
        resultado = []
        for l in linhas:
            link = self._get("links_afiliados", select="id,url,url_curta", produto_id=f"eq.{l['produto_id']}",
                             validado="is.true", ativo="is.true", limit="1")
            resultado.append({
                "promocao_id": l["id"], "produto_id": l["produto_id"], "preco_promocional": float(l["preco_promocional"]),
                "id_externo": l["produtos"]["id_externo"], "marketplace": l["produtos"]["marketplaces"]["codigo"],
                "dominios": l["produtos"]["marketplaces"]["dominios_afiliado"],
                "link_id": link[0]["id"] if link else None,
                "link_url": (link[0]["url_curta"] or link[0]["url"]) if link else None})
        return resultado

    def atualizar_produto(self, produto_id, preco, disponivel, preco_original):
        corpo = {"preco_atual": round(preco, 2), "disponivel": disponivel, "verificado_em": iso(agora())}
        if preco_original:
            corpo["preco_original"] = round(preco_original, 2)
        self._req("PATCH", "produtos", params={"id": f"eq.{produto_id}"}, corpo=corpo, prefer="return=minimal")

    def invalidar_link(self, link_id, motivo):
        self._req("PATCH", "links_afiliados", params={"id": f"eq.{link_id}"}, prefer="return=minimal",
                  corpo={"validado": False, "motivo_invalido": motivo[:300]})

    def pausadas_com_mensagens(self, desde):
        promos = self._get("promocoes", select="id,motivo_status,produtos(titulo)",
                           status="eq.pausada", pausada_em=f"gte.{iso(desde)}")
        if not promos:
            return []
        pubs = self._get("historico_publicacoes", select="promocao_id,canal_id,id_mensagem_externa",
                         promocao_id=self._in(p["id"] for p in promos), resultado="eq.sucesso",
                         demo="is.false", id_mensagem_externa="not.is.null")
        por_id = {p["id"]: p for p in promos}
        return [{"promocao_id": p["promocao_id"], "canal_id": p["canal_id"], "id_mensagem": p["id_mensagem_externa"],
                 "motivo": por_id[p["promocao_id"]]["motivo_status"] or "oferta encerrada",
                 "titulo": (por_id[p["promocao_id"]].get("produtos") or {}).get("titulo", "")} for p in pubs]

    # ------------------------------------------------------------ comissões, execução, logs
    def salvar_comissoes(self, marketplace_id, itens):
        resultado = {"novos": 0, "atualizados": 0, "ignorados": 0}
        unicos = {i["referencia_externa"]: i for i in itens if i.get("referencia_externa")}
        if not unicos:
            return resultado
        existentes = {}
        refs = sorted(unicos)
        for k in range(0, len(refs), 100):
            for e in self._get("comissoes", select="referencia_externa,status,valor_comissao",
                               marketplace_id=f"eq.{marketplace_id}", referencia_externa=self._in(refs[k:k + 100])):
                existentes[e["referencia_externa"]] = e
        corpo = []
        for ref, i in unicos.items():
            antes = existentes.get(ref)
            if antes and antes["status"] == "paga":
                resultado["ignorados"] += 1
                continue
            if antes is None:
                resultado["novos"] += 1
            elif antes["status"] != i.get("status") or float(antes["valor_comissao"]) != float(i["valor_comissao"]):
                resultado["atualizados"] += 1
            corpo.append({**i, "marketplace_id": marketplace_id, "origem": "api"})
        if corpo:
            try:
                self._req("POST", "comissoes", params={"on_conflict": "marketplace_id,referencia_externa"},
                          corpo=corpo, prefer="return=minimal,resolution=merge-duplicates")
            except ErroBanco as e:
                if e.codigo not in ("PGRST204", "42703"):
                    raise
                corpo = [{k: v for k, v in c.items() if k != "id_externo_item"} for c in corpo]  # sem a migration 019
                self._req("POST", "comissoes", params={"on_conflict": "marketplace_id,referencia_externa"},
                          corpo=corpo, prefer="return=minimal,resolution=merge-duplicates")
        return resultado

    def comissoes_api_periodo(self, marketplace_id, inicio, fim):
        return self._get("comissoes", select="referencia_externa,status,valor_comissao", marketplace_id=f"eq.{marketplace_id}",
                         origem="eq.api", data_venda=f"gte.{inicio}", **{"and": f"(data_venda.lte.{fim})"}, limit="10000")

    # ---------------------------------------------------------------- inteligência comercial (019)
    def _sem_019(self, e: ErroBanco) -> bool:
        return e.codigo in ("PGRST205", "PGRST204", "PGRST202", "42P01", "42703", "42883") or e.status == 404

    def dados_ranking(self, promocao_ids):
        if not promocao_ids:
            return {}
        campos = "id,produto_id,preco_promocional,comissao_pct,pontuacao,classificacao,analise,origem,demo,produtos!inner({})"
        produto = "id,titulo,marca,disponivel,demo,{}marketplaces!inner(codigo,modo),categorias(nome)"
        try:
            linhas = self._get("promocoes", select=campos.format(produto.format(
                "gtin,modelo,loja_oficial,estoque,vendedor_nome,")), id=self._in(promocao_ids))
        except ErroBanco as e:
            if not self._sem_019(e):
                raise
            linhas = self._get("promocoes", select=campos.format(produto.format("")), id=self._in(promocao_ids))
        return {l["id"]: dados_de_ranking(l) for l in linhas}

    def desempenho_publicacoes(self, desde):
        try:
            return self._get("vw_desempenho_publicacoes", publicado_em=f"gte.{iso(desde)}", order="publicado_em.desc",
                             limit="20000")
        except ErroBanco as e:
            if self._sem_019(e):
                return []
            raise

    def salvar_selecoes(self, decisoes):
        if not decisoes:
            return
        try:
            self._req("POST", "selecoes_ofertas", prefer="return=minimal", corpo=[{
                "promocao_id": d["promocao_id"], "selecionada": d["selecionada"], "posicao": d.get("posicao"),
                "prioridade": d.get("prioridade"), "receita_esperada": d.get("receita_esperada"), "detalhes": d}
                for d in decisoes])
        except ErroBanco as e:
            if not self._sem_019(e):
                raise

    def salvar_equivalencias(self, achados):
        if not achados:
            return
        try:
            self._req("POST", "produtos_equivalentes", params={"on_conflict": "produto_a,produto_b"},
                      prefer="return=minimal,resolution=merge-duplicates", corpo=achados)
        except ErroBanco as e:
            if not self._sem_019(e):
                raise

    def experimento_ativo(self):
        try:
            linhas = self._get("experimentos", select="id,nome,tipo,amostra_minima,inicio_em", ativo="is.true", limit="1")
        except ErroBanco as e:
            if self._sem_019(e):
                return None
            raise
        return linhas[0] if linhas else None

    def salvar_resultado_experimento(self, experimento_id, resultado):
        self._req("PATCH", "experimentos", params={"id": f"eq.{experimento_id}"}, prefer="return=minimal",
                  corpo={"resultado": resultado, "resultado_em": iso(agora())})

    def salvar_resumo_inteligencia(self, tipo, dados):
        try:
            self._req("POST", "inteligencia_resumos", params={"on_conflict": "tipo"},
                      prefer="return=minimal,resolution=merge-duplicates",
                      corpo={"tipo": tipo, "dados": dados, "calculado_em": iso(agora())})
        except ErroBanco as e:
            if not self._sem_019(e):
                raise

    def registrar_importacao(self, dados):
        try:
            self._req("POST", "comissoes_importacoes", corpo=dados, prefer="return=minimal")
        except ErroBanco as e:
            if e.codigo not in ("42P01", "PGRST205"):
                raise  # tabela ausente (migration 017 não aplicada): segue sem o registro

    def ultima_importacao(self, marketplace_id, tipo):
        try:
            linhas = self._get("comissoes_importacoes", select="criado_em", marketplace_id=f"eq.{marketplace_id}",
                               tipo=f"eq.{tipo}", status="neq.erro", order="criado_em.desc", limit="1")
        except ErroBanco as e:
            if e.codigo in ("42P01", "PGRST205"):
                return None
            raise
        return datetime.fromisoformat(linhas[0]["criado_em"].replace("Z", "+00:00")) if linhas else None

    def iniciar_execucao(self, origem, modo, versao):
        linhas = self._req("POST", "execucoes", params={"select": "id"}, prefer="return=representation",
                           corpo={"origem": origem, "modo": modo, "versao": versao})
        return linhas[0]["id"]

    def finalizar_execucao(self, execucao_id, status, resumo):
        self._req("PATCH", "execucoes", params={"id": f"eq.{execucao_id}"}, prefer="return=minimal",
                  corpo={"status": status, "finalizada_em": iso(agora()), "resumo": resumo})

    # ---- cupons
    def cupons_vigentes(self):
        agora_ = iso(agora())
        linhas = self._get("vw_cupons_painel", select="*", status="eq.ativo", inicio_em=f"lte.{agora_}",
                           fim_em=f"gt.{agora_}", order="fim_em.asc", limit="100")
        return [Cupom.de_linha(l) for l in linhas]

    def cupons_publicados_no_dia(self, dia):
        return self._get("cupons_publicacoes", select="cupom_id,canal_id,resultado,erro", dia=f"eq.{dia}")

    def registrar_publicacao_cupom(self, cupom_id, canal_id, sucesso, id_mensagem, erro):
        self._req("POST", "cupons_publicacoes", prefer="return=minimal", corpo={
            "cupom_id": cupom_id, "canal_id": canal_id, "resultado": "sucesso" if sucesso else "erro",
            "id_mensagem_externa": id_mensagem, "erro": None if sucesso else (erro or "erro")[:1000]})

    def salvar_campanha(self, c):
        mkt = next((m for m in self.marketplaces() if m["codigo"] == c.marketplace), None)
        if not mkt:
            raise RegraViolada(f"loja {c.marketplace} não cadastrada", codigo="P0020")
        corpo = {"marketplace_id": mkt["id"], "tipo": "campanha", "origem": "automatica", "id_externo": c.id_externo,
                 "titulo": c.titulo, "link_afiliado": c.link_afiliado, "fim_em": iso(c.fim_em),
                 "imagem_url": c.imagem_url if (c.imagem_url or "").startswith("https://") else None}
        if c.inicio_em and c.inicio_em < c.fim_em:
            corpo["inicio_em"] = iso(c.inicio_em)
        self._req("POST", "cupons", params={"on_conflict": "marketplace_id,id_externo"},
                  prefer="resolution=merge-duplicates,return=minimal", corpo=corpo)

    def campanhas_atualizadas_desde(self, desde):
        return len(self._get("cupons", select="id", origem="eq.automatica", atualizado_em=f"gte.{iso(desde)}"))

    def candidatos_para_cupom(self, cupom, limite):
        ja = {l["promocao_id"] for l in self._get("cupons_produtos", select="promocao_id", cupom_id=f"eq.{cupom.id}")}
        linhas = self._get("vw_promocoes_painel", select="id,titulo,categoria_id,preco_promocional,pontuacao,demo",
                           marketplace=f"eq.{cupom.marketplace}", status="in.(encontrada,aprovada,publicada)",
                           classificacao="in.(excelente,boa)", demo=f"is.{str(cupom.demo).lower()}",
                           order="pontuacao.desc", limit=str(max(limite * 20, 100)))
        return [l for l in linhas if l["id"] not in ja]

    def sugerir_produto_cupom(self, cupom_id, promocao_id, motivo):
        self._req("POST", "cupons_produtos", params={"on_conflict": "cupom_id,promocao_id"},
                  prefer="resolution=ignore-duplicates,return=minimal",
                  corpo={"cupom_id": cupom_id, "promocao_id": promocao_id, "origem": "sugestao", "aprovado": False,
                         "motivo": motivo[:200]})

    def cupons_da_promocao(self, promocao_id):
        agora_ = iso(agora())
        ids = [l["cupom_id"] for l in self._get("cupons_produtos", select="cupom_id", promocao_id=f"eq.{promocao_id}",
                                                aprovado="is.true")]
        if not ids:
            return []
        linhas = self._get("vw_cupons_painel", select="*", id=self._in(ids), status="eq.ativo",
                           inicio_em=f"lte.{agora_}", fim_em=f"gt.{agora_}")
        return [Cupom.de_linha(l) for l in linhas]

    def encerrar_execucoes_orfas(self, minutos=30):
        return int(self._rpc("encerrar_execucoes_orfas", p_minutos=minutos) or 0)

    def ultima_execucao(self):
        linhas = self._get("execucoes", select="id,iniciada_em,status", status="neq.rodando",
                           order="iniciada_em.desc", limit="1")
        return linhas[0] if linhas else None

    def gravar_logs(self, execucao_id, itens):
        if itens:
            self._req("POST", "logs", prefer="return=minimal",
                      corpo=[{**i, "execucao_id": execucao_id} for i in itens])

    def obter_credencial(self, integracao, nome):
        try:
            return self._rpc("obter_credencial", p_integracao=integracao, p_nome=nome)
        except ErroBanco:
            return None


def dados_de_ranking(l: dict) -> dict:
    """Converte a linha de promoção (com produto, loja, categoria e análise) no formato do ranking."""
    from .inteligencia.aprendizado import faixa_de_preco
    prod = l.get("produtos") or {}
    mkt = prod.get("marketplaces") or {}
    analise = l.get("analise") or {}
    preco = float(l["preco_promocional"]) if l.get("preco_promocional") is not None else None
    return {
        "promocao_id": l["id"], "produto_id": l["produto_id"], "marketplace": mkt.get("codigo"),
        "titulo": prod.get("titulo"), "preco": preco,
        "comissao_pct": float(l["comissao_pct"]) if l.get("comissao_pct") is not None else None,
        "dados_confiaveis": mkt.get("modo") == "real" and l.get("origem") == "automatica" and not l.get("demo"),
        "pontuacao": l.get("pontuacao"), "classificacao": l.get("classificacao"),
        "desconto_real": analise.get("desconto_real"), "desconto_comprovado": bool(analise.get("desconto_comprovado")),
        "categoria": (prod.get("categorias") or {}).get("nome"), "faixa_preco": faixa_de_preco(preco),
        "marca": prod.get("marca"), "gtin": prod.get("gtin"), "modelo": prod.get("modelo"),
        "loja_oficial": prod.get("loja_oficial"), "estoque": prod.get("estoque"), "origem": l.get("origem"),
    }
