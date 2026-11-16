"""AliExpress Portals (API oficial de afiliados) — vende pro mundo todo.

Cadastro: portals.aliexpress.com → vira afiliado → em "API" cria o app (App Key/Secret)
e pega o Tracking ID. Endpoint e assinatura seguem o SDK oficial (IOP):
  sign = HMAC_SHA256(secret, concat(chaves ordenadas + valores)).upper()
"""
import hashlib
import hmac
import time

import requests

API = "https://api-sg.aliexpress.com/sync"


def _f(v, padrao=0.0):
    try:
        return float(str(v).replace("%", "").replace(",", "."))
    except (TypeError, ValueError):
        return padrao


def assinar(params: dict, secret: str) -> str:
    base = "".join(f"{k}{params[k]}" for k in sorted(params))
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest().upper()


def _achar(d, chave):
    """Procura uma chave em qualquer nível do JSON (a resposta muda de formato às vezes)."""
    if isinstance(d, dict):
        if chave in d:
            return d[chave]
        for v in d.values():
            r = _achar(v, chave)
            if r is not None:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _achar(v, chave)
            if r is not None:
                return r
    return None


class AliExpress:
    nome = "aliexpress"

    def __init__(self, app_key: str, app_secret: str, tracking_id: str, moeda="USD", idioma="EN", pais="US"):
        self.app_key, self.app_secret, self.tracking_id = app_key, app_secret, tracking_id
        self.moeda, self.idioma, self.pais = moeda, idioma, pais

    @property
    def configurada(self) -> bool:
        return bool(self.app_key and self.app_secret and self.tracking_id)

    def _chamar(self, method: str, **extra) -> dict:
        p = {
            "app_key": self.app_key, "method": method, "sign_method": "sha256",
            "timestamp": str(int(time.time() * 1000)), "format": "json", "v": "2.0",
        }
        p.update({k: str(v) for k, v in extra.items() if v not in (None, "")})
        p["sign"] = assinar(p, self.app_secret)
        r = requests.post(API, data=p, timeout=30)
        r.raise_for_status()
        d = r.json()
        if "error_response" in d:
            e = d["error_response"]
            raise RuntimeError(f"AliExpress: {e.get('msg') or e.get('sub_msg') or e}")
        res = _achar(d, "resp_result") or {}
        if res and str(res.get("resp_code", 200)) not in ("200", "0"):
            raise RuntimeError(f"AliExpress: {res.get('resp_msg', res)}")
        return d

    def buscar(self, palavra: str, limit: int = 40) -> list[dict]:
        d = self._chamar(
            "aliexpress.affiliate.product.query",
            keywords=palavra, page_no=1, page_size=min(limit, 50),
            target_currency=self.moeda, target_language=self.idioma, ship_to_country=self.pais,
            tracking_id=self.tracking_id, sort="LAST_VOLUME_DESC",
        )
        produtos = _achar(d, "product") or []
        ofertas = []
        for p in produtos:
            preco = _f(p.get("target_sale_price") or p.get("sale_price"))
            original = _f(p.get("target_original_price") or p.get("original_price"))
            desconto = _f(p.get("discount")) or (round((1 - preco / original) * 100) if original > preco > 0 else 0)
            nota = _f(p.get("evaluate_rate")) / 20 if p.get("evaluate_rate") else 0  # "96.5%" -> 4.8
            link = p.get("promotion_link")
            if not link:
                continue  # sem link de afiliado não interessa
            ofertas.append({
                "id": f"ali:{p.get('product_id')}",
                "plataforma": "aliexpress",
                "nome": p.get("product_title", ""),
                "preco": preco,
                "moeda": p.get("target_sale_price_currency") or self.moeda,
                "desconto": desconto,
                "nota": nota,
                "vendas": int(_f(p.get("lastest_volume"))),
                "comissao_taxa": _f(p.get("commission_rate")) / 100,
                "imagem": p.get("product_main_image_url"),
                "link": link,
                "pais": self.pais,
            })
        return ofertas
