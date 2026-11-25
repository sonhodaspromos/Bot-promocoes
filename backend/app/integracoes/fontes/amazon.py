"""Amazon · Creators API (sucessora oficial da Product Advertising API 5.0).

Requisitos da Amazon para liberar o acesso (documentação oficial do Creators API):
  * conta aprovada no Programa de Associados do marketplace (amazon.com.br);
  * pelo menos 10 vendas qualificadas nos últimos 30 dias;
  * credenciais criadas em Associates Central → Tools → Creators API (Credential ID + Secret, OAuth 2.0).

Os endereços do token e da API NÃO estão fixos no código: preencha AMAZON_TOKEN_URL e AMAZON_API_URL
com os valores da documentação oficial da sua região quando o acesso for liberado. Sem eles a fonte fica
desativada. Os nomes dos campos seguem o guia de migração (camelCase: keywords, itemIds, partnerTag...).

ATENÇÃO: implementado e testado só com dublês. NÃO validado com credenciais reais.
"""
from __future__ import annotations

import time

from ...cliente_http import ClienteHTTP
from ...erros import ErroIntegracao
from ...modelos import OfertaEncontrada
from ..base import REAL, FonteOfertas


def _caminho(d, *chaves):
    for c in chaves:
        if isinstance(d, list):
            d = d[0] if d else None
        if not isinstance(d, dict):
            return None
        d = d.get(c)
    return d


class AmazonFonte(FonteOfertas):
    codigo = "amazon"
    modo = REAL
    suporta_consulta = True

    def __init__(self, credencial_id: str, segredo: str, partner_tag: str, token_url: str, api_url: str,
                 http: ClienteHTTP | None = None, relogio=time.time):
        if not token_url.startswith("https://") or not api_url.startswith("https://"):
            raise ErroIntegracao("AMAZON_TOKEN_URL e AMAZON_API_URL precisam ser https (da documentação oficial)",
                                 servico="amazon")
        self.credencial_id, self.segredo, self.tag = credencial_id, segredo, partner_tag
        self.token_url, self.api_url = token_url, api_url.rstrip("/")
        self.http = http or ClienteHTTP("amazon")
        self.relogio = relogio
        self._token, self._expira = None, 0.0

    def _bearer(self) -> str:
        if self._token and self.relogio() < self._expira - 60:
            return self._token
        r = self.http.post(self.token_url, data={"grant_type": "client_credentials", "client_id": self.credencial_id,
                                                  "client_secret": self.segredo}).json()
        if not r.get("access_token"):
            raise ErroIntegracao("Amazon não devolveu access_token (credencial ou elegibilidade)", servico="amazon")
        self._token, self._expira = r["access_token"], self.relogio() + float(r.get("expires_in") or 3600)
        return self._token

    def _chamar(self, operacao: str, corpo: dict) -> dict:
        corpo = {"partnerTag": self.tag, "partnerType": "Associates", "marketplace": "www.amazon.com.br", **corpo}
        r = self.http.post(f"{self.api_url}/{operacao}", json=corpo,
                           headers={"Authorization": f"Bearer {self._bearer()}"}).json()
        if r.get("errors"):
            raise ErroIntegracao(f"Amazon recusou: {r['errors'][0].get('message') or r['errors'][0]}", servico="amazon")
        return r

    def _oferta(self, item: dict) -> OfertaEncontrada | None:
        preco = _caminho(item, "offersV2", "listings", "price", "money", "amount")
        link = item.get("detailPageURL") or ""
        if not preco or not link or not item.get("asin"):
            return None
        anterior = _caminho(item, "offersV2", "listings", "price", "savingBasis", "money", "amount")
        disponivel = (_caminho(item, "offersV2", "listings", "availability", "type") or "IN_STOCK") == "IN_STOCK"
        return OfertaEncontrada(
            marketplace="amazon", id_externo=str(item["asin"]),
            titulo=str(_caminho(item, "itemInfo", "title", "displayValue") or "")[:300], preco=float(preco),
            preco_original=float(anterior) if anterior and float(anterior) > float(preco) else None, link_afiliado=link,
            imagem_url=_caminho(item, "images", "primary", "large", "url"), disponivel=disponivel,
            marca=_caminho(item, "itemInfo", "byLineInfo", "brand", "displayValue"),
            loja_oficial=None, comissao_pct=None)  # a API não informa comissão por item: "sem dados" (nada inventado)

    def buscar(self, palavra, limite=30):
        r = self._chamar("searchItems", {"keywords": palavra, "itemCount": min(int(limite), 10),
                                         "resources": ["itemInfo.title", "itemInfo.byLineInfo", "images.primary.large",
                                                       "offersV2.listings.price", "offersV2.listings.availability"]})
        itens = _caminho(r, "searchResult", "items") or []
        return [o for o in (self._oferta(i) for i in itens) if o]

    def consultar(self, id_externo):
        r = self._chamar("getItems", {"itemIds": [str(id_externo)],
                                      "resources": ["itemInfo.title", "offersV2.listings.price", "offersV2.listings.availability"]})
        itens = _caminho(r, "itemsResult", "items") or []
        return self._oferta(itens[0]) if itens else None

    def testar(self):
        ofertas = self.buscar("fone bluetooth", limite=1)
        if not ofertas:
            raise ErroIntegracao("a API respondeu sem itens", servico="amazon")
        self.amostra_link = ofertas[0].link_afiliado
        return "amazon: REAL ✔ Creators API respondeu (busca de 1 item)"
