"""Shopee Affiliate Open API (Brasil): busca ofertas e puxa comissões.

Assinatura: SHA256(appId + timestamp + payload + secret)
Header: Authorization: SHA256 Credential={appId}, Timestamp={ts}, Signature={sig}
"""
import hashlib
import json
import time

import requests

ENDPOINT = "https://open-api.affiliate.shopee.com.br/graphql"

# sortType: 1=relevância, 2=vendas, 3=maior preço, 4=menor preço, 5=maior comissão
QUERY_OFERTAS = """
query ($keyword: String, $sortType: Int, $page: Int, $limit: Int) {
  productOfferV2(keyword: $keyword, sortType: $sortType, page: $page, limit: $limit) {
    nodes {
      itemId productName price priceMin priceMax priceDiscountRate
      commissionRate commission sales ratingStar imageUrl shopName offerLink
    }
  }
}
"""

QUERY_CONVERSOES = """
query ($start: Int64, $end: Int64, $limit: Int, $scrollId: String) {
  conversionReport(purchaseTimeStart: $start, purchaseTimeEnd: $end, limit: $limit, scrollId: $scrollId) {
    nodes {
      purchaseTime
      conversionId
      totalCommission
      orders { orderId orderStatus items { itemName itemTotalCommission } }
    }
    pageInfo { hasNextPage scrollId }
  }
}
"""


def _f(v, padrao=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return padrao


class Shopee:
    nome = "shopee"

    def __init__(self, app_id: str, secret: str):
        self.app_id = app_id
        self.secret = secret

    @property
    def configurada(self) -> bool:
        return bool(self.app_id and self.secret)

    def _post(self, query: str, variables: dict) -> dict:
        payload = json.dumps({"query": query, "variables": variables}, separators=(",", ":"))
        ts = str(int(time.time()))
        sig = hashlib.sha256((self.app_id + ts + payload + self.secret).encode()).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={self.app_id}, Timestamp={ts}, Signature={sig}",
        }
        r = requests.post(ENDPOINT, data=payload, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            raise RuntimeError(f"Shopee: {data['errors'][0].get('message', data['errors'])}")
        return data["data"]

    def buscar(self, palavra: str, limit: int = 40) -> list[dict]:
        sort = 2 if int(time.time()) % 2 else 5   # alterna mais vendidos / maior comissão
        data = self._post(QUERY_OFERTAS, {"keyword": palavra, "sortType": sort, "page": 1, "limit": limit})
        ofertas = []
        for n in data["productOfferV2"]["nodes"] or []:
            preco = _f(n.get("priceMin") or n.get("price"))
            ofertas.append({
                "id": f"shopee:{n['itemId']}",
                "plataforma": "shopee",
                "nome": n.get("productName", ""),
                "preco": preco,
                "desconto": _f(n.get("priceDiscountRate")),
                "nota": _f(n.get("ratingStar")),
                "vendas": int(_f(n.get("sales"))),
                "comissao_taxa": _f(n.get("commissionRate")),
                "imagem": n.get("imageUrl"),
                "link": n.get("offerLink"),
            })
        return ofertas

    def comissoes(self, desde_ts: int, ate_ts: int) -> list[dict]:
        """Retorna [{id, data_ts, valor, descricao}] das vendas no período."""
        resultado, scroll = [], None
        for _ in range(50):  # trava de segurança
            vars_ = {"start": desde_ts, "end": ate_ts, "limit": 100}
            if scroll:
                vars_["scrollId"] = scroll
            data = self._post(QUERY_CONVERSOES, vars_)["conversionReport"]
            for n in data.get("nodes") or []:
                itens = [i.get("itemName", "") for o in n.get("orders") or [] for i in o.get("items") or []]
                resultado.append({
                    "id": f"shopee:{n.get('conversionId')}",
                    "data_ts": int(_f(n.get("purchaseTime"))),
                    "valor": _f(n.get("totalCommission")),
                    "descricao": ", ".join(itens)[:200] or "Venda Shopee",
                })
            info = data.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            scroll = info.get("scrollId")
        return resultado
