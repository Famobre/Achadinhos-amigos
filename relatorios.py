"""Importa relatórios de vendas/comissões baixados das lojas (CSV ou Excel).

Cada loja usa nomes de coluna diferentes, então a gente procura pelas palavras:
data, pedido, produto, comissão e status. Pedidos cancelados/inválidos ficam de fora.
Reimportar o mesmo relatório não duplica (cada linha vira um id fixo).
"""
import csv
import hashlib
import io
import re
import unicodedata
from datetime import datetime
from pathlib import Path


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def _num(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if not s or s in "-.,":
        return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


FORMATOS_DATA = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                 "%d/%m/%Y", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
                 "%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"]


def _data(v) -> int | None:
    if isinstance(v, datetime):
        return int(v.timestamp())
    s = str(v or "").strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 10:  # timestamp (s ou ms)
        n = int(s)
        return n // 1000 if n > 10**11 else n
    s = s.replace(" UTC", "").replace("T", " ").split(".")[0].replace("Z", "")
    for f in FORMATOS_DATA:
        try:
            return int(datetime.strptime(s.replace(" ", " ", 1), f.replace("T", " ")).timestamp())
        except ValueError:
            continue
    return None


def _coluna(cab: list[str], incluir: list[str], preferir: list[str] = (), excluir: list[str] = ()) -> int | None:
    cands = []
    for i, c in enumerate(cab):
        n = _norm(c)
        if any(p in n for p in incluir) and not any(e in n for e in excluir):
            pontos = sum(3 for p in preferir if p in n) + (1 if "(r$)" in n or "brl" in n else 0)
            cands.append((pontos, -i, i))
    return max(cands)[2] if cands else None


def _linhas(caminho: Path) -> list[list]:
    if caminho.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(caminho, read_only=True, data_only=True)
        return [list(r) for r in wb.active.iter_rows(values_only=True)]
    bruto = caminho.read_bytes()
    for cod in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            texto = bruto.decode(cod)
            break
        except UnicodeDecodeError:
            continue
    # escolhe o separador que aparece na mesma quantidade em mais linhas (ignora linhas de título)
    amostra = [l for l in texto.splitlines()[:30] if l.strip()]
    melhor, nota = ",", -1
    for sep in (";", "\t", ",", "|"):
        contagens = [len(next(csv.reader([l], delimiter=sep))) for l in amostra]
        if not contagens:
            continue
        moda = max(set(contagens), key=contagens.count)
        pontos = contagens.count(moda) * (moda > 1) * moda
        if pontos > nota:
            melhor, nota = sep, pontos
    return [r for r in csv.reader(io.StringIO(texto), delimiter=melhor)]


def _achar_cabecalho(linhas: list[list]) -> int:
    """Alguns relatórios têm umas linhas de título antes da tabela."""
    for i, r in enumerate(linhas[:15]):
        n = " ".join(_norm(c) for c in r if c)
        if ("comiss" in n or "commission" in n or "earning" in n or "ganho" in n or "ad fees" in n) and len([c for c in r if c]) >= 3:
            return i
    return 0


def ler(caminho: str, plataforma: str) -> dict:
    caminho = Path(caminho)
    linhas = [r for r in _linhas(caminho) if any(str(c or "").strip() for c in r)]
    if len(linhas) < 2:
        return {"vendas": [], "aviso": "Relatório vazio"}
    h = _achar_cabecalho(linhas)
    cab = [str(c or "") for c in linhas[h]]
    i_com = _coluna(cab, ["comiss", "commission", "ganho", "earning", "ad fees", "receita de publicidade", "bounty"],
                    preferir=["total", "estimad", "afiliado", "affiliate", "net", "liquid"],
                    excluir=["taxa", "rate", "%", "percent", "porcentagem"])
    if i_com is None:
        raise RuntimeError("Não achei a coluna de comissão nesse arquivo. Confere se é o relatório de vendas/comissões.")
    i_data = _coluna(cab, ["data", "date", "hora", "time", "dia"], preferir=["pedido", "compra", "order", "purchase", "conclus"])
    i_ped = _coluna(cab, ["pedido", "order", "conversion", "transac", "id"], preferir=["id do pedido", "order id", "order sn", "numero"],
                    excluir=["data", "date", "status", "valor", "value", "hora", "item id"])
    i_prod = _coluna(cab, ["nome do item", "produto", "item name", "product", "titulo", "title", "name", "nome", "item"],
                     preferir=["nome", "name", "titulo", "title", "produto", "product"],
                     excluir=["id", "quant", "qty", "preco", "price", "valor", "comiss", "categoria", "loja", "shop",
                              "shipped", "enviad", "asin", "sku", "link", "url"])
    i_st = _coluna(cab, ["status", "situacao", "estado"])

    vendas, ignoradas = [], 0
    for r in linhas[h + 1:]:
        r = list(r) + [None] * (len(cab) - len(r))
        valor = _num(r[i_com])
        status = _norm(r[i_st]) if i_st is not None else ""
        if any(x in status for x in ("cancel", "invalid", "recus", "devolv", "reembols", "refund", "rejected")):
            ignoradas += 1
            continue
        if valor == 0:
            continue
        ts = _data(r[i_data]) if i_data is not None else None
        pedido = str(r[i_ped] or "").strip() if i_ped is not None else ""
        produto = str(r[i_prod] or "").strip()[:200] if i_prod is not None else ""
        chave = hashlib.sha1("|".join(str(c) for c in r).encode()).hexdigest()[:16]
        vendas.append({
            "id": f"imp:{plataforma}:{pedido or chave}:{chave[:6]}",
            "plataforma": plataforma, "valor": round(valor, 2),
            "descricao": produto or (f"Pedido {pedido}" if pedido else "Venda importada"),
            "data_ts": ts or int(datetime.now().timestamp()),
        })
    return {"vendas": vendas, "ignoradas": ignoradas, "colunas": {
        "comissao": cab[i_com], "data": cab[i_data] if i_data is not None else None,
        "pedido": cab[i_ped] if i_ped is not None else None, "produto": cab[i_prod] if i_prod is not None else None}}
