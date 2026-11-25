"""Shopee Afiliados · API oficial (GraphQL) — https://open-api.affiliate.shopee.com.br/graphql

Autenticação: cabeçalho `Authorization: SHA256 Credential=<AppId>, Timestamp=<ts>, Signature=<sha256(AppId+ts+corpo+Secret)>`.

⚠ A documentação oficial só abre para afiliados logados. As consultas abaixo seguem as
referências públicas da API (productOfferV2, generateShortLink, conversionReport). Antes de
ligar em produção, rode `python -m app.main testar-integracoes`: ele faz uma consulta real
e mostra o erro exato se algum campo tiver outro nome.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ...erros import ErroIntegracao
from ...cliente_http import ClienteHTTP
from ...modelos import CampanhaEncontrada, OfertaEncontrada
from ..base import REAL, FonteOfertas

URL = "https://open-api.affiliate.shopee.com.br/graphql"
CAMPOS_CAMPANHA = "offerName offerLink imageUrl commissionRate collectionId offerType periodStartTime periodEndTime"
CAMPOS = "itemId productName price priceMin priceMax priceDiscountRate imageUrl offerLink productLink ratingStar sales commissionRate"
# dados do vendedor (Open API de afiliados). Se a API recusar algum campo, a busca refaz sem eles e o
# vendedor fica "sem dados" — nunca é presumido. shopType: 1 = loja oficial (Shopee Mall).
CAMPOS_VENDEDOR = "shopId shopName shopType"


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


class ShopeeFonte(FonteOfertas):
    codigo = "shopee"
    modo = REAL
    suporta_consulta = True
    suporta_comissoes = True
    suporta_campanhas = True

    def __init__(self, app_id: str, secret: str, http: ClienteHTTP | None = None, ordenacao: int = 2,
                 relogio=time.time):
        self.app_id, self.secret = app_id, secret
        self.http = http or ClienteHTTP("shopee")
        self.ordenacao = ordenacao
        self.relogio = relogio

    # ------------------------------------------------------------ chamada assinada
    def assinatura(self, timestamp: str, corpo: str) -> str:
        return hashlib.sha256((self.app_id + timestamp + corpo + self.secret).encode("utf-8")).hexdigest()

    def consulta(self, query: str) -> dict:
        corpo = json.dumps({"query": query}, separators=(",", ":"), ensure_ascii=False)
        ts = str(int(self.relogio()))
        cab = {"Content-Type": "application/json",
               "Authorization": f"SHA256 Credential={self.app_id}, Timestamp={ts}, Signature={self.assinatura(ts, corpo)}"}
        dados = self.http.post(URL, data=corpo.encode("utf-8"), headers=cab).json()
        if dados.get("errors"):
            msg = "; ".join(str(e.get("message", e)) for e in dados["errors"])
            raise ErroIntegracao(f"Shopee recusou a consulta: {msg}", servico="shopee")
        return dados.get("data") or {}

    # ------------------------------------------------------------ conversão
    def _oferta(self, n: dict) -> OfertaEncontrada | None:
        preco = _num(n.get("priceMin")) or _num(n.get("price"))
        link = n.get("offerLink") or ""
        if not preco or not link or not n.get("itemId"):
            return None
        desconto = _num(n.get("priceDiscountRate")) or 0
        # a API não informa o "preço de": ele é DERIVADO da taxa de desconto (não comprovado)
        original = round(preco / (1 - desconto / 100), 2) if 0 < desconto < 100 else None
        maximo = _num(n.get("priceMax"))
        comissao = _num(n.get("commissionRate"))
        tipos = n.get("shopType")
        oficial = None
        if isinstance(tipos, list) and tipos:
            oficial = 1 in [int(t) for t in tipos if str(t).isdigit()]
        elif isinstance(tipos, (int, str)) and str(tipos).isdigit():
            oficial = int(tipos) == 1
        return OfertaEncontrada(
            vendedor_id=str(n["shopId"]) if n.get("shopId") else None,
            vendedor_nome=str(n.get("shopName"))[:120] if n.get("shopName") else None, loja_oficial=oficial,
            marketplace=self.codigo, id_externo=str(n["itemId"]), titulo=str(n.get("productName") or "")[:300],
            preco=preco, preco_original=original, nota=_num(n.get("ratingStar")),
            total_vendas=int(_num(n.get("sales")) or 0) or None, imagem_url=n.get("imageUrl") or None,
            url_produto=n.get("productLink") or None, link_afiliado=link,
            preco_maximo=maximo if maximo and maximo > preco else None, referencia_derivada=original is not None,
            comissao_pct=round(comissao * 100, 2) if comissao is not None and comissao <= 1 else comissao)

    # ------------------------------------------------------------ interface
    _campos_vendedor_ok = True

    def _produtos(self, filtro: str) -> list[dict]:
        """productOfferV2 com os campos do vendedor; se a API recusar esses campos, refaz sem eles."""
        if self._campos_vendedor_ok:
            try:
                q = "{productOfferV2(%s){nodes{%s %s}}}" % (filtro, CAMPOS, CAMPOS_VENDEDOR)
                return (self.consulta(q).get("productOfferV2") or {}).get("nodes") or []
            except ErroIntegracao as e:
                if not any(t in str(e) for t in ("Cannot query field", "shopType", "shopId", "shopName", "Unknown")):
                    raise
                self._campos_vendedor_ok = False
        q = "{productOfferV2(%s){nodes{%s}}}" % (filtro, CAMPOS)
        return (self.consulta(q).get("productOfferV2") or {}).get("nodes") or []

    def buscar(self, palavra, limite=30):
        nodes = self._produtos("keyword:%s, sortType:%d, page:1, limit:%d" % (
            json.dumps(palavra, ensure_ascii=False), int(self.ordenacao), int(limite)))
        return [o for o in (self._oferta(n) for n in nodes) if o]

    def consultar(self, id_externo):
        if not str(id_externo).isdigit():
            raise ValueError("id_externo da Shopee deve ser numérico")
        nodes = self._produtos("itemId:%s, page:1, limit:1" % id_externo)
        return self._oferta(nodes[0]) if nodes else None

    def gerar_link_afiliado(self, url):
        q = 'mutation{generateShortLink(input:{originUrl:%s, subIds:["bot"]}){shortLink}}' % json.dumps(url)
        return ((self.consulta(q).get("generateShortLink") or {}).get("shortLink")) or None

    def campanhas(self, limite=20):
        """Campanhas oficiais da Shopee (shopeeOfferV2): nome, link de afiliado, imagem e período."""
        q = f"{{ shopeeOfferV2(sortType: 2, page: 1, limit: {int(limite)}) {{ nodes {{ {CAMPOS_CAMPANHA} }} }} }}"
        agora = datetime.fromtimestamp(self.relogio(), tz=timezone.utc)
        nos = ((self.consulta(q).get("shopeeOfferV2") or {}).get("nodes")) or []
        return [c for c in (campanha_de_no(n, agora) for n in nos) if c]

    def comissoes(self, inicio, fim, max_paginas: int = 40):
        """Relatório de conversões, lido página a página (scrollId) até o fim ou até o limite de segurança."""
        nodes, scroll, vistos = [], None, set()
        for _ in range(max_paginas):
            extra = f', scrollId:{json.dumps(scroll)}' if scroll else ""
            q = ("{conversionReport(purchaseTimeStart:%d, purchaseTimeEnd:%d, limit:500%s){nodes{conversionId purchaseTime "
                 "orders{orderId orderStatus items{itemId itemName itemPrice itemTotalCommission}}} "
                 "pageInfo{hasNextPage scrollId}}}" % (int(inicio.timestamp()), int(fim.timestamp()), extra))
            rel = self.consulta(q).get("conversionReport") or {}
            nodes += rel.get("nodes") or []
            info = rel.get("pageInfo") or {}
            scroll = info.get("scrollId")
            if not info.get("hasNextPage") or not scroll or scroll in vistos:
                break
            vistos.add(scroll)
        itens, refs = [], set()
        for n in nodes:
            data = datetime.fromtimestamp(int(n.get("purchaseTime") or fim.timestamp()), timezone.utc) - timedelta(hours=3)
            for pedido in n.get("orders") or []:
                status_api = str(pedido.get("orderStatus") or "").upper()
                status = "cancelada" if status_api in ("CANCELLED", "CANCELED", "INVALID") else \
                         "confirmada" if status_api in ("COMPLETED", "SETTLED") else "pendente"
                for item in pedido.get("items") or []:
                    valor = _num(item.get("itemTotalCommission"))
                    if valor is None:
                        continue
                    venda = _num(item.get("itemPrice"))
                    ref = f"{n.get('conversionId')}-{pedido.get('orderId')}-{item.get('itemId')}"
                    if ref in refs:
                        continue  # a mesma conversão repetida em páginas diferentes
                    refs.add(ref)
                    itens.append({
                        "referencia_externa": f"{n.get('conversionId')}-{pedido.get('orderId')}-{item.get('itemId')}",
                        "id_externo": str(item.get("itemId")), "descricao": str(item.get("itemName") or "")[:300],
                        "valor_comissao": round(max(valor, 0), 2),
                        "valor_venda": round(venda, 2) if venda and venda >= valor else None,
                        "data_venda": data.strftime("%Y-%m-%d"), "status": status})
        return itens

    def testar(self):
        ofertas = self.buscar("fone", limite=1)
        if not ofertas:
            raise ErroIntegracao("a API respondeu, mas sem ofertas (conta de afiliado ativa?)", servico="shopee")
        self.amostra_link = ofertas[0].link_afiliado  # conferido depois pelo validar-integracoes (atribuição)
        agora = datetime.now(timezone.utc)
        n = len(self.comissoes(agora - timedelta(days=1), agora))
        return f"shopee: REAL ✔ busca e relatório de conversões responderam ({n} itens nas últimas 24h)"


def _data_unix(v) -> datetime | None:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    if n > 10**11:  # milissegundos
        n //= 1000
    return datetime.fromtimestamp(n, tz=timezone.utc)


def campanha_de_no(n: dict, agora: datetime) -> CampanhaEncontrada | None:
    """Converte um item de shopeeOfferV2. Sem nome ou sem link de afiliado = descartado."""
    titulo, link = (n.get("offerName") or "").strip(), (n.get("offerLink") or "").strip()
    if len(titulo) < 3 or not link.startswith("https://"):
        return None
    inicio, fim = _data_unix(n.get("periodStartTime")), _data_unix(n.get("periodEndTime"))
    if fim is None:
        # a API não informou o fim: vale só até o fim do dia; amanhã a sincronização confirma de novo
        local = agora.astimezone(ZoneInfo("America/Recife"))
        fim = local.replace(hour=23, minute=59, second=0, microsecond=0).astimezone(timezone.utc)
    if fim <= agora or (inicio and inicio > agora + timedelta(days=1)):
        return None
    ident = str(n.get("collectionId") or "").strip() or hashlib.sha256(link.encode()).hexdigest()[:20]
    comissao = _num(n.get("commissionRate"))
    if comissao is not None and comissao <= 1:  # a API às vezes devolve fração (0.07 = 7%)
        comissao *= 100
    return CampanhaEncontrada(marketplace="shopee", id_externo=f"camp-{ident}", titulo=titulo[:120], link_afiliado=link,
                              fim_em=fim, inicio_em=inicio, imagem_url=(n.get("imageUrl") or None), comissao_pct=comissao)
