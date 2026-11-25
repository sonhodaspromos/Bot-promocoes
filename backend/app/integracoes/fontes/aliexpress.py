"""AliExpress · Open Platform oficial de afiliados (portals.aliexpress.com / openservice.aliexpress.com).

Métodos usados (todos oficiais do programa de afiliados):
  * aliexpress.affiliate.product.query   busca por palavra-chave (já devolve promotion_link de afiliado)
  * aliexpress.affiliate.productdetail.get  consulta de preço/disponibilidade de itens
  * aliexpress.affiliate.order.list      relatório de pedidos/comissões

Autenticação: app_key + app_secret (assinatura HMAC-SHA256 dos parâmetros ordenados) + tracking_id
do afiliado. Gateway: https://api-sg.aliexpress.com/sync.

ATENÇÃO: implementado conforme a documentação pública e testado só com dublês. NÃO está validado com
credenciais reais — só considere funcional depois do `validar-integracoes` aprovado.
Sem "preço de" inventado: o original vem de target_original_price (se a API não mandar, fica vazio).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from ...cliente_http import ClienteHTTP
from ...erros import ErroIntegracao
from ...modelos import OfertaEncontrada
from ..base import REAL, FonteOfertas

GATEWAY = "https://api-sg.aliexpress.com/sync"
CAMPOS = ("product_id,product_title,target_sale_price,target_original_price,product_main_image_url,"
          "promotion_link,commission_rate,evaluate_rate,lastest_volume,shop_id,product_detail_url")


def _num(v):
    try:
        return float(str(v).replace("%", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def assinar(params: dict, segredo: str) -> str:
    """Assinatura do gateway (sign_method=sha256): HMAC-SHA256 de chave+valor ordenados, em hexadecimal maiúsculo."""
    texto = "".join(f"{k}{params[k]}" for k in sorted(params) if k != "sign" and params[k] is not None)
    return hmac.new(segredo.encode(), texto.encode(), hashlib.sha256).hexdigest().upper()


class AliExpressFonte(FonteOfertas):
    codigo = "aliexpress"
    modo = REAL
    suporta_consulta = True
    suporta_comissoes = True

    def __init__(self, app_key: str, app_secret: str, tracking_id: str, http: ClienteHTTP | None = None,
                 relogio=time.time):
        self.app_key, self.app_secret, self.tracking_id = app_key, app_secret, tracking_id
        self.http = http or ClienteHTTP("aliexpress")
        self.relogio = relogio

    def chamar(self, metodo: str, **extra) -> dict:
        params = {"app_key": self.app_key, "method": metodo, "sign_method": "sha256", "format": "json", "v": "2.0",
                  "timestamp": str(int(self.relogio() * 1000)), "tracking_id": self.tracking_id,
                  "target_currency": "BRL", "target_language": "PT", "ship_to_country": "BR", **extra}
        params["sign"] = assinar(params, self.app_secret)
        try:
            dados = self.http.post(GATEWAY, data=params).json()
        except ValueError as e:
            raise ErroIntegracao("AliExpress respondeu algo que não é JSON", servico="aliexpress") from e
        if "error_response" in dados:
            erro = dados["error_response"]
            raise ErroIntegracao(f"AliExpress recusou: {erro.get('msg') or erro.get('code')}", servico="aliexpress")
        chave = metodo.replace(".", "_") + "_response"
        resp = (dados.get(chave) or {}).get("resp_result") or {}
        if str(resp.get("resp_code", "200")) != "200":
            raise ErroIntegracao(f"AliExpress: {resp.get('resp_msg') or resp.get('resp_code')}", servico="aliexpress")
        return resp.get("result") or {}

    @staticmethod
    def _oferta(p: dict) -> OfertaEncontrada | None:
        preco = _num(p.get("target_sale_price"))
        link = p.get("promotion_link") or ""
        if not preco or not link or not p.get("product_id"):
            return None
        original = _num(p.get("target_original_price"))
        vendas = _num(p.get("lastest_volume"))
        aval = _num(p.get("evaluate_rate"))   # % de avaliações positivas (0–100) → escala 0–5 não é equivalente: não converte
        return OfertaEncontrada(
            marketplace="aliexpress", id_externo=str(p["product_id"]), titulo=str(p.get("product_title") or "")[:300],
            preco=preco, preco_original=original if original and original > preco else None, link_afiliado=link,
            imagem_url=p.get("product_main_image_url") or None, url_produto=p.get("product_detail_url") or None,
            comissao_pct=_num(p.get("commission_rate")), total_vendas=int(vendas) if vendas is not None else None,
            nota=None,  # "evaluate_rate" é % de avaliações positivas, não nota de 0 a 5: fica sem dados
            vendedor_id=str(p["shop_id"]) if p.get("shop_id") else None)

    def buscar(self, palavra, limite=30):
        r = self.chamar("aliexpress.affiliate.product.query", keywords=palavra, page_size=min(int(limite), 50),
                        page_no=1, fields=CAMPOS, sort="LAST_VOLUME_DESC")
        produtos = ((r.get("products") or {}).get("product")) or []
        return [o for o in (self._oferta(p) for p in produtos) if o]

    def consultar(self, id_externo):
        if not str(id_externo).isdigit():
            raise ValueError("id do produto AliExpress deve ser numérico")
        r = self.chamar("aliexpress.affiliate.productdetail.get", product_ids=str(id_externo), fields=CAMPOS)
        produtos = ((r.get("products") or {}).get("product")) or []
        return self._oferta(produtos[0]) if produtos else None

    def comissoes(self, inicio, fim, max_paginas: int = 20):
        itens, pagina = [], 1
        while pagina <= max_paginas:
            r = self.chamar("aliexpress.affiliate.order.list", start_time=inicio.strftime("%Y-%m-%d %H:%M:%S"),
                            end_time=fim.strftime("%Y-%m-%d %H:%M:%S"), page_no=pagina, page_size=50,
                            status="Payment Completed,Buyer Confirmed Receipt,Order Settled,Invalid")
            pedidos = ((r.get("orders") or {}).get("order")) or []
            for o in pedidos:
                st = str(o.get("order_status") or "")
                status = "cancelada" if st == "Invalid" else "confirmada" if st == "Order Settled" else "pendente"
                quando = datetime.strptime(str(o.get("created_time"))[:19], "%Y-%m-%d %H:%M:%S") if o.get("created_time") \
                    else datetime.now(timezone.utc)
                itens.append({"referencia_externa": f"{o.get('order_id')}-{o.get('sub_order_id') or o.get('product_id')}",
                              "id_externo": str(o.get("product_id") or ""), "descricao": str(o.get("product_title") or "")[:300],
                              "valor_venda": _num(o.get("paid_amount")), "valor_comissao": _num(o.get("estimated_paid_commission")) or 0,
                              "data_venda": quando.strftime("%Y-%m-%d"), "status": status})
            if len(pedidos) < 50:
                break
            pagina += 1
        return itens

    def testar(self):
        ofertas = self.buscar("fone bluetooth", limite=1)
        if not ofertas:
            raise ErroIntegracao("a API respondeu, mas sem produtos (tracking_id aprovado?)", servico="aliexpress")
        self.amostra_link = ofertas[0].link_afiliado
        return f"aliexpress: REAL ✔ busca de afiliado respondeu ({len(ofertas)} produto de teste)"
