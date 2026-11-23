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
        original = round(preco / (1 - desconto / 100), 2) if 0 < desconto < 100 else None
        comissao = _num(n.get("commissionRate"))
        return OfertaEncontrada(
            marketplace=self.codigo, id_externo=str(n["itemId"]), titulo=str(n.get("productName") or "")[:300],
            preco=preco, preco_original=original, nota=_num(n.get("ratingStar")),
            total_vendas=int(_num(n.get("sales")) or 0) or None, imagem_url=n.get("imageUrl") or None,
            url_produto=n.get("productLink") or None, link_afiliado=link,
            comissao_pct=round(comissao * 100, 2) if comissao is not None and comissao <= 1 else comissao)

    # ------------------------------------------------------------ interface
    def buscar(self, palavra, limite=30):
        q = "{productOfferV2(keyword:%s, sortType:%d, page:1, limit:%d){nodes{%s}}}" % (
            json.dumps(palavra, ensure_ascii=False), int(self.ordenacao), int(limite), CAMPOS)
        nodes = (self.consulta(q).get("productOfferV2") or {}).get("nodes") or []
        return [o for o in (self._oferta(n) for n in nodes) if o]

    def consultar(self, id_externo):
        if not str(id_externo).isdigit():
            raise ValueError("id_externo da Shopee deve ser numérico")
        q = "{productOfferV2(itemId:%s, page:1, limit:1){nodes{%s}}}" % (id_externo, CAMPOS)
        nodes = (self.consulta(q).get("productOfferV2") or {}).get("nodes") or []
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

    def comissoes(self, inicio, fim):
        q = ("{conversionReport(purchaseTimeStart:%d, purchaseTimeEnd:%d, limit:500){nodes{conversionId purchaseTime "
             "orders{orderId orderStatus items{itemId itemName itemPrice itemTotalCommission}}}}}"
             % (int(inicio.timestamp()), int(fim.timestamp())))
        nodes = (self.consulta(q).get("conversionReport") or {}).get("nodes") or []
        itens = []
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
                    itens.append({
                        "referencia_externa": f"{n.get('conversionId')}-{pedido.get('orderId')}-{item.get('itemId')}",
                        "id_externo": str(item.get("itemId")), "descricao": str(item.get("itemName") or "")[:300],
                        "valor_comissao": round(max(valor, 0), 2),
                        "valor_venda": round(venda, 2) if venda and venda >= valor else None,
                        "data_venda": data.strftime("%Y-%m-%d"), "status": status})
        return itens

    def testar(self):
        ofertas = self.buscar("fone", limite=1)
        return f"shopee: REAL ✔ API respondeu ({len(ofertas)} oferta de teste)"


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
