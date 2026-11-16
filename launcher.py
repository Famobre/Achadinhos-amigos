"""Launcher desktop do bot de achadinhos (janela própria, sem navegador).

Rodar:  pythonw launcher.py   (ou dar dois cliques no Iniciar.bat)
"""
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import webview

import canais
import config
import ia
from bot import Bot
from db import DB
from loja import Loja


# console do Windows (cp1252) quebra com emoji: força UTF-8 e nunca deixa um print derrubar uma ação
for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _pasta_base() -> Path:
    # funciona tanto rodando o .py quanto empacotado em .exe (PyInstaller)
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


class Api:
    """Tudo aqui pode ser chamado pela interface (window.pywebview.api.*)."""

    def __init__(self):
        self._db = DB()
        self._bot = Bot(self._db)
        self._loja = Loja(self._db)
        self._janela = None
        self._instalando = False
        cfg = config.carregar()
        if cfg.get("ligar_ao_abrir"):
            self._bot.ligar()

    # ----- janela -----
    def minimizar(self):
        self._janela.minimize()

    def fechar(self):
        self._bot.desligar()
        self._janela.destroy()

    # ----- painel -----
    def estado(self):
        try:  # ofertas que o Claude deixou na pasta entram na fila sozinhas
            self._bot.ofertas.importar_caixa(config.PASTA, log=self._db.log)
        except Exception as e:
            if not getattr(self, "_avisou_caixa", False):
                self._avisou_caixa = True
                self._db.log(f"Caixa de entrada: {e}", "erro")
        return {
            "rodando": self._bot.rodando,
            "proximo_post": self._bot.proximo_post_ts,
            "agora": int(time.time()),
            "credenciais": config.status_credenciais(),
            "ganhos": self._db.resumo_ganhos(),
            "ganhos_dia": self._db.ganhos_por_dia(14),
            "posts": self._db.contagem_posts(),
            "ultimos_posts": self._db.ultimos_posts(12),
            "ultimos_ganhos": self._db.ultimos_ganhos(20),
            "logs": self._db.ultimos_logs(40),
            "n_canais": len(canais.canais_ativos(config.carregar())),
            "ml_conectado": self._bot.ml.conectado,
            "autostart": self.autostart_status(),
            "ofertas_fila": self._bot.ofertas.na_fila(),
        }

    def ligar(self):
        self._bot.ligar()
        return True

    def desligar(self):
        self._bot.desligar()
        return True

    def postar_agora(self):
        try:
            o = self._bot.postar_agora()
            return {"ok": True, "msg": f"Postado: {o['nome'][:50]}" if o else "Nenhuma oferta nova encontrada"}
        except Exception as e:
            self._db.log(f"Erro ao postar: {e}", "erro")
            return {"ok": False, "msg": str(e)}

    def sincronizar_shopee(self):
        try:
            n = self._bot.sincronizar_shopee()
            return {"ok": True, "msg": f"{n} vendas da Shopee atualizadas"}
        except Exception as e:
            self._db.log(f"Erro ao sincronizar Shopee: {e}", "erro")
            return {"ok": False, "msg": str(e)}

    def adicionar_ganho(self, plataforma, valor, descricao, data):
        """Ganho manual (o ML não tem API de comissão). data = 'AAAA-MM-DD'."""
        try:
            valor = float(str(valor).replace(",", "."))
            ts = int(datetime.strptime(data, "%Y-%m-%d").replace(hour=12).timestamp()) if data else int(time.time())
        except ValueError:
            return {"ok": False, "msg": "Valor ou data inválidos"}
        self._db.salvar_ganho(f"manual:{uuid.uuid4().hex[:10]}", plataforma, valor,
                             descricao or "Lançamento manual", ts, "manual")
        return {"ok": True, "msg": "Ganho adicionado"}

    def apagar_ganho(self, id_):
        self._db.apagar_ganho(id_)
        return True

    # ----- feed de produtos da Shopee -----
    def feed_resumo(self):
        self._bot.feed.affiliate_id = config.cred("SHOPEE_AFFILIATE_ID")
        return self._bot.feed.resumo()

    def feed_importar_arquivo(self):
        tipo = getattr(webview, "OPEN_DIALOG", None)
        if tipo is None:
            tipo = webview.FileDialog.OPEN
        arq = self._janela.create_file_dialog(tipo, allow_multiple=False, file_types=("Feed CSV (*.csv)",))
        if not arq:
            return {"ok": False, "msg": "Nenhum arquivo escolhido"}
        arq = arq[0] if isinstance(arq, (list, tuple)) else arq
        try:
            r = self._bot.feed.importar_arquivo(arq, config.carregar()["feed_shopee"], log=self._db.log)
        except Exception as e:
            self._db.log(f"Erro ao importar feed: {e}", "erro")
            return {"ok": False, "msg": str(e)[:200]}
        msg = f"{r['guardadas']} ofertas boas guardadas (de {r['lidas']} produtos)"
        self._db.log(f"Feed Shopee importado: {msg}")
        return {"ok": True, "msg": msg}

    def feed_baixar(self):
        import threading

        def rodar():
            try:
                self._bot.atualizar_feed()
            except Exception as e:
                self._db.log(f"Erro ao baixar feed: {e}", "erro")
        threading.Thread(target=rodar, daemon=True).start()
        return {"ok": True, "msg": "Baixando o feed em segundo plano — acompanhe nos Logs"}

    # ----- importar relatório de vendas (CSV/Excel baixado das lojas) -----
    def relatorio_importar(self, plataforma):
        import relatorios
        tipo = getattr(webview, "OPEN_DIALOG", None)
        if tipo is None:
            tipo = webview.FileDialog.OPEN
        arq = self._janela.create_file_dialog(
            tipo, allow_multiple=False, file_types=("Relatórios (*.csv;*.xlsx;*.txt;*.tsv)", "Todos (*.*)"))
        if not arq:
            return {"ok": False, "msg": "Nenhum arquivo escolhido"}
        arq = arq[0] if isinstance(arq, (list, tuple)) else arq
        try:
            r = relatorios.ler(arq, plataforma)
        except Exception as e:
            self._db.log(f"Erro ao importar relatório: {e}", "erro")
            return {"ok": False, "msg": str(e)[:200]}
        novas = 0
        for v in r["vendas"]:
            if not self._db._q("SELECT 1 FROM ganhos WHERE id=?", (v["id"],)):
                novas += 1
            self._db.salvar_ganho(v["id"], v["plataforma"], v["valor"], v["descricao"], v["data_ts"], "import")
        total = sum(v["valor"] for v in r["vendas"])
        msg = (f"{len(r['vendas'])} vendas lidas ({novas} novas), R$ {total:.2f} em comissões"
               + (f" — {r['ignoradas']} canceladas ignoradas" if r.get("ignoradas") else ""))
        self._db.log(f"Relatório importado ({plataforma}): {msg} | coluna de comissão: {r['colunas']['comissao']}")
        return {"ok": True, "msg": msg}

    # ----- ofertas manuais (links de afiliado colados) -----
    def ofertas_listar(self):
        return {"lista": self._bot.ofertas.listar(), "fila": self._bot.ofertas.na_fila()}

    def ofertas_salvar(self, dados, oid=None):
        return self._bot.ofertas.salvar(dados or {}, int(oid) if oid else None)

    def ofertas_apagar(self, oid):
        return self._bot.ofertas.apagar(int(oid))

    def ofertas_repostar(self, oid):
        return self._bot.ofertas.repostar(int(oid))

    def ofertas_escolher_foto(self):
        tipo = getattr(webview, "OPEN_DIALOG", None)
        if tipo is None:
            tipo = webview.FileDialog.OPEN
        caminhos = self._janela.create_file_dialog(
            tipo, allow_multiple=False, file_types=("Imagens (*.jpg;*.jpeg;*.png;*.webp)",))
        if not caminhos:
            return None
        return self._bot.ofertas.importar_foto(caminhos[0] if isinstance(caminhos, (list, tuple)) else caminhos)

    def ofertas_extrair(self, link):
        """Lê a página do produto pelo link e devolve os dados pra preencher o formulário."""
        import extrator
        try:
            d = extrator.extrair(link)
        except Exception as e:
            return {"ok": False, "msg": str(e)[:150], "dados": {}}
        if not (d.get("nome") and d.get("preco") and d.get("imagem")):
            # leitura simples não bastou (Amazon/Shopee): abre num navegador invisível
            try:
                nav = extrator.extrair_navegador(link, webview)
                d = {**d, **{k: v for k, v in nav.items() if v}}
            except Exception as e:
                self._db.log(f"Navegador invisível falhou: {str(e)[:120]}", "aviso")
        achou = [n for n, k in (("nome", "nome"), ("preço", "preco"), ("foto", "imagem"), ("preço antigo", "preco_antigo"),
                                ("nota", "nota"), ("vendidos", "vendas")) if d.get(k)]
        faltou = [n for n, k in (("nome", "nome"), ("preço", "preco"), ("foto", "imagem")) if not d.get(k)]
        msg = ("Preenchi: " + ", ".join(achou)) if achou else "A loja não liberou os dados dessa página"
        if faltou:
            msg += " — complete: " + ", ".join(faltou)
        self._db.log(f"Link lido ({d.get('plataforma')}): {msg}")
        return {"ok": bool(achou), "msg": msg, "dados": d}

    def ofertas_previa(self, dados, idioma="pt"):
        """Gera o card na hora pra mostrar no painel (data URI)."""
        import base64
        import cards
        from ofertas import PASTA_FOTOS, _f
        d = dados or {}
        preco = _f(d.get("preco"))
        if preco <= 0 and config.carregar().get("estilo_imagem", "foto") == "card":
            return None
        preco = preco or 0.01
        img = (d.get("imagem") or "").strip()
        antigo = _f(d.get("preco_antigo"))
        o = {"plataforma": d.get("plataforma") or "outra", "nome": d.get("nome") or "Nome do produto",
             "preco": preco, "preco_antigo": antigo, "moeda": d.get("moeda") or "BRL",
             "nota": _f(d.get("nota")), "vendas": int(_f(d.get("vendas"))), "frete_gratis": bool(d.get("frete_gratis")),
             "cupom": (d.get("cupom") or "").strip().upper(), "condicao": (d.get("condicao") or "").strip(),
             "imagem": img if img.startswith("http") else (str(PASTA_FOTOS / img) if img else None)}
        assinatura = config.cred("TELEGRAM_CANAL")
        estilo = config.carregar().get("estilo_imagem", "foto")
        jpg = cards.imagem_do_post(o, idioma, assinatura if assinatura.startswith("@") else "", estilo)
        if not jpg:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(jpg).decode()

    def ofertas_postar(self, oid):
        try:
            o = self._bot.postar_agora(manual_id=int(oid))
            return {"ok": bool(o), "msg": f"Postado: {o['nome'][:50]}" if o else "Não foi possível postar"}
        except Exception as e:
            self._db.log(f"Erro ao postar oferta: {e}", "erro")
            return {"ok": False, "msg": str(e)[:200]}

    # ----- abrir junto com o Windows -----
    def _arq_autostart(self):
        import os
        pasta = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
        return pasta / "Achadinhos.bat"

    def autostart_status(self):
        return sys.platform == "win32" and self._arq_autostart().exists()

    def autostart_definir(self, ligar):
        if sys.platform != "win32":
            return {"ok": False, "msg": "Só funciona no Windows"}
        arq = self._arq_autostart()
        if not ligar:
            arq.unlink(missing_ok=True)
            return {"ok": True, "msg": "Não abre mais com o Windows"}
        pasta = config.PASTA
        if getattr(sys, "frozen", False):
            cmd = f'start "" "{sys.executable}"'
        else:
            pyw = Path(sys.executable).with_name("pythonw.exe")
            cmd = f'start "" "{pyw}" "{pasta / "launcher.py"}"'
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(f'@echo off\r\ncd /d "{pasta}"\r\n{cmd}\r\n', encoding="utf-8")
        config.salvar({"ligar_ao_abrir": True})
        return {"ok": True, "msg": "Vai abrir com o Windows e ligar o bot sozinho"}

    # ----- contas (chaves digitadas no painel) -----
    def contas_listar(self):
        return config.segredos_para_painel()

    def contas_salvar(self, valores):
        config.salvar_segredos(valores)
        self._db.log("Chaves das contas atualizadas")
        return {"ok": True, "msg": "Contas salvas"}

    # ----- canais -----
    def canais_listar(self):
        cfg = config.carregar()
        principal = {"id": "principal", "tipo": "telegram", "nome": "Telegram principal (Contas)",
                     "chat": config.cred("TELEGRAM_CANAL"), "idioma": "pt", "ativo": True, "fixo": True,
                     "plataformas": []} if config.cred("TELEGRAM_CANAL") else None
        return ([principal] if principal else []) + cfg.get("canais", [])

    def canais_salvar(self, canal):
        cfg = config.carregar()
        lista = cfg.get("canais", [])
        canal = dict(canal or {})
        if canal.get("tipo") not in canais.ENVIAR:
            return {"ok": False, "msg": "Tipo de canal inválido"}
        canal["id"] = canal.get("id") or uuid.uuid4().hex[:8]
        lista = [c for c in lista if c.get("id") != canal["id"]] + [canal]
        config.salvar({"canais": lista})
        return {"ok": True, "msg": f"Canal \"{canal.get('nome') or canal['tipo']}\" salvo"}

    def canais_apagar(self, cid):
        cfg = config.carregar()
        config.salvar({"canais": [c for c in cfg.get("canais", []) if c.get("id") != cid]})
        return {"ok": True, "msg": "Canal removido"}

    def canal_boas_vindas(self):
        try:
            canais.enviar_principal(canais.boas_vindas(), fixar=True)
            self._db.log("📌 Boas-vindas postada e fixada no Telegram")
            return {"ok": True, "msg": "Boas-vindas postada e fixada ✅"}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:200]}

    def canal_top_semana(self):
        try:
            config.salvar_estado(top_semana="")
            limite = int(time.time()) - 7 * 86400
            rows = [dict(r) for r in self._db._q(
                "SELECT nome, preco, desconto, link FROM posts WHERE postado_em>? AND link LIKE 'http%' "
                "ORDER BY desconto DESC LIMIT 5", (limite,))]
            if len(rows) < 3:
                return {"ok": False, "msg": "Ainda tem poucos posts na semana (precisa de 3+)"}
            canais.enviar_principal(canais.top_da_semana(rows))
            return {"ok": True, "msg": "TOP 5 postado ✅"}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:200]}

    def canais_testar(self, canal):
        try:
            return {"ok": True, "msg": canais.testar(canal)}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:200]}

    def postiz_integracoes(self, url, api_key):
        try:
            lista = canais.postiz_integracoes({"url": url, "api_key": api_key})
            return {"ok": True, "lista": [{"id": i.get("id"), "nome": i.get("name"),
                                           "tipo": i.get("identifier") or i.get("providerIdentifier")} for i in lista]}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:200], "lista": []}

    # ----- IA -----
    def ia_status(self):
        return {"crewai": ia.crewai_instalado(), "chave": bool(config.cred("ANTHROPIC_API_KEY")),
                "instalando": self._instalando}

    def ia_instalar_crewai(self):
        if self._instalando:
            return {"ok": False, "msg": "Já está instalando…"}
        import subprocess
        import threading

        def rodar():
            self._instalando = True
            self._db.log("Instalando CrewAI (pode levar alguns minutos)…")
            try:
                flags = 0x08000000 if sys.platform == "win32" else 0  # sem janela preta
                r = subprocess.run([sys.executable.replace("pythonw", "python"), "-m", "pip", "install", "-q", "crewai[anthropic]"],
                                   capture_output=True, text=True, creationflags=flags)
                if r.returncode == 0:
                    import importlib
                    importlib.invalidate_caches()
                    self._db.log("CrewAI instalado ✅")
                else:
                    self._db.log(f"Erro ao instalar CrewAI: {(r.stderr or r.stdout)[-300:]}", "erro")
            finally:
                self._instalando = False

        threading.Thread(target=rodar, daemon=True).start()
        return {"ok": True, "msg": "Instalando CrewAI em segundo plano — acompanhe nos Logs"}

    # ----- login do Mercado Livre -----
    def ml_status(self):
        return {"conectado": self._bot.ml.conectado}

    def ml_abrir_login(self):
        import webbrowser
        self._bot.plataformas(config.carregar())  # pega as chaves mais novas
        if not (self._bot.ml.client_id and self._bot.ml.client_secret):
            return {"ok": False, "msg": "Preencha ML_CLIENT_ID e ML_CLIENT_SECRET no .env primeiro"}
        webbrowser.open(self._bot.ml.url_login())
        return {"ok": True, "msg": "Autorize no navegador e depois copie o link da página do Google"}

    def ml_conectar(self, link):
        self._bot.plataformas(config.carregar())
        try:
            self._bot.ml.conectar(link)
            self._db.log("Mercado Livre conectado com a sua conta ✅")
            return {"ok": True, "msg": "Mercado Livre conectado! Clique em Postar agora pra testar."}
        except Exception as e:
            self._db.log(f"Erro ao conectar ML: {e}", "erro")
            return {"ok": False, "msg": str(e)}

    def ml_desconectar(self):
        self._bot.ml.desconectar()
        return {"ok": True, "msg": "Mercado Livre desconectado"}

    # ----- Loja da Mãe -----
    def loja_estado(self):
        return self._loja.estado()

    def loja_grupos(self):
        return self._loja.grupos()

    def loja_salvar_grupo(self, nome, url):
        return self._loja.salvar_grupo(nome, url)

    def loja_apagar_grupo(self, gid):
        return self._loja.apagar_grupo(int(gid))

    def loja_anuncios(self):
        return self._loja.anuncios()

    def loja_escolher_fotos(self):
        tipo = getattr(webview, "OPEN_DIALOG", None)
        if tipo is None:
            tipo = webview.FileDialog.OPEN
        caminhos = self._janela.create_file_dialog(
            tipo, allow_multiple=True, file_types=("Imagens (*.jpg;*.jpeg;*.png;*.webp)",))
        return self._loja.importar_fotos(caminhos)

    def loja_salvar_anuncio(self, titulo, texto, fotos, aid=None):
        return self._loja.salvar_anuncio(titulo, texto, fotos, aid)

    def loja_apagar_anuncio(self, aid):
        return self._loja.apagar_anuncio(int(aid))

    def loja_montar_fila(self, anuncio_id, grupo_ids, intervalo):
        return self._loja.montar_fila(int(anuncio_id), grupo_ids, intervalo)

    def loja_preparar(self, fid):
        try:
            return self._loja.preparar(int(fid))
        except Exception as e:
            return {"ok": False, "msg": f"Erro: {e}"}

    def loja_concluir(self, fid, status):
        return self._loja.concluir(int(fid), status)

    def loja_limpar_fila(self):
        return self._loja.limpar_fila()

    def loja_historico(self):
        return self._loja.historico()

    def chamar_atencao(self):
        """Traz a janela pra frente quando chega a hora de postar."""
        try:
            self._janela.restore()
            self._janela.on_top = True
            time.sleep(0.4)
            self._janela.on_top = False
        except Exception:
            pass

    def ler_config(self):
        return config.carregar()

    def salvar_config(self, novo):
        config.salvar(novo)
        self._db.log("Configurações salvas")
        return {"ok": True, "msg": "Configurações salvas"}


def main():
    api = Api()
    janela = webview.create_window(
        "Achadinhos • Launcher",
        str(_pasta_base() / "ui" / "index.html"),
        js_api=api,
        width=1120, height=720, min_size=(980, 640),
        frameless=True, easy_drag=False,
        background_color="#070203",
    )
    api._janela = janela
    webview.start(debug="--debug" in sys.argv)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        (config.PASTA / "erro.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
