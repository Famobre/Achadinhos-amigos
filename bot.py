"""O bot em si: roda numa thread que o painel liga e desliga.

Ciclo: escolhe a plataforma da vez → junta candidatas boas → a IA escolhe a melhor e
escreve nos idiomas dos canais → posta em todos os canais que aceitam aquela loja.
"""
import random
import sys
import threading
import time
from datetime import datetime

import canais
import config
import ia
from db import DB
from plataformas.aliexpress import AliExpress
from plataformas.amazon import Amazon
from plataformas.mercadolivre import MercadoLivre
from plataformas.shopee import Shopee
from selecao import melhores

def _manter_acordado(ligar: bool):
    """Impede o Windows de suspender enquanto o bot roda (a tela ainda pode apagar)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if ligar else 0))
    except Exception:
        pass


NOMES = {"shopee": "Shopee", "mercadolivre": "Mercado Livre", "aliexpress": "AliExpress", "amazon": "Amazon",
         "outra": "Oferta"}


class Bot:
    def __init__(self, db: DB):
        self.db = db
        from ofertas import Ofertas
        from plataformas.shopee_feed import FeedShopee
        self.ofertas = Ofertas(db)
        self.feed = FeedShopee(db)
        self.feed_ultima = 0
        self.pausadas = {}
        self.ml = MercadoLivre(config.cred("ML_CLIENT_ID"), config.cred("ML_CLIENT_SECRET"),
                               config.cred("ML_AFILIADO_PARAMS"))
        self._amazon = None
        self._thread = None
        self._parar = threading.Event()
        self._acordar = threading.Event()
        self.proximo_post_ts = None
        self.vez = 0  # revezamento entre plataformas
        from collections import deque
        self.ultimas_cats = deque(maxlen=3)  # categorias do feed postadas por último (pra variar)

    # ---------- plataformas (lidas na hora, então mudar chave no painel já vale) ----------
    @property
    def shopee(self):
        return Shopee(config.cred("SHOPEE_APP_ID"), config.cred("SHOPEE_SECRET"))

    def plataformas(self, cfg) -> list:
        c = config.cred
        self.ml.client_id, self.ml.client_secret = c("ML_CLIENT_ID"), c("ML_CLIENT_SECRET")
        from urllib.parse import parse_qsl
        self.ml.afiliado_params = dict(parse_qsl(c("ML_AFILIADO_PARAMS").lstrip("?")))
        ali = cfg["aliexpress"]
        mk = cfg["amazon"].get("marketplace", "www.amazon.com")
        if not self._amazon or (self._amazon.cid, self._amazon.marketplace) != (c("AMZ_CREDENTIAL_ID"), mk):
            self._amazon = Amazon(c("AMZ_CREDENTIAL_ID"), c("AMZ_CREDENTIAL_SECRET"), c("AMZ_PARTNER_TAG"), mk)
        self._amazon.secret, self._amazon.tag = c("AMZ_CREDENTIAL_SECRET"), c("AMZ_PARTNER_TAG")
        self.feed.affiliate_id = c("SHOPEE_AFFILIATE_ID")
        shopee = self.shopee if self.shopee.configurada else self.feed  # sem Open API, usa o feed
        todas = [
            shopee, self.ml,
            AliExpress(c("ALI_APP_KEY"), c("ALI_APP_SECRET"), c("ALI_TRACKING_ID"),
                       ali.get("moeda", "USD"), ali.get("idioma", "EN"), ali.get("pais", "US")),
            self._amazon,
        ]
        return [p for p in todas if cfg["plataformas"].get(p.nome) and p.configurada]

    def _palavras(self, plat, cfg) -> list[str]:
        brasil = (plat.nome in ("shopee", "mercadolivre")
                  or (plat.nome == "aliexpress" and cfg["aliexpress"].get("pais") == "BR")
                  or (plat.nome == "amazon" and cfg["amazon"].get("marketplace", "").endswith(".br")))
        lista = (cfg["palavras"] if brasil else cfg.get("palavras_global")) or cfg["palavras"]
        lista = lista[:]
        random.shuffle(lista)
        return lista

    # ---------- controle ----------
    @property
    def rodando(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def ligar(self):
        if self.rodando:
            return
        self._parar.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.db.log("Bot ligado ▶️")

    def desligar(self):
        if not self.rodando:
            return
        self._parar.set()
        self._acordar.set()
        self.proximo_post_ts = None
        self.db.log("Bot desligado ⏸️")

    # ---------- lógica ----------
    def candidatas(self, cfg, lista_canais) -> list[dict]:
        plats = self.plataformas(cfg)
        # só plataformas que pelo menos um canal aceita
        def aceita(p):
            return any(not c.get("plataformas") or p.nome in c["plataformas"] for c in lista_canais)
        plats = [p for p in plats if aceita(p)]
        if not plats:
            raise RuntimeError("Nenhuma loja ativa e configurada. Preencha as chaves na aba Contas.")
        # loja que bloqueou a API (403) fica de molho por 12h, sem ficar tentando a cada post
        plats = [p for p in plats if time.time() >= self.pausadas.get(p.nome, 0)] or plats
        self.vez += 1
        k = self.vez % len(plats)
        for plat in plats[k:] + plats[:k]:
            achadas = []
            buscas = self._palavras(plat, cfg)[:3]
            if hasattr(plat, "buscar_variado") and self.vez % 3 != 0:
                buscas = ["*variado*"] + buscas  # 2 de cada 3 posts do feed vêm de uma categoria sorteada
            recentes = {self._chave_nome(p["nome"]) for p in self.db.ultimos_posts(6)}
            for palavra in buscas:
                try:
                    if palavra == "*variado*":
                        ofertas = plat.buscar_variado(list(self.ultimas_cats),
                                                      pesos=cfg.get("feed_shopee", {}).get("pesos"))
                    else:
                        ofertas = plat.buscar(palavra)
                    pesos = cfg.get("feed_shopee", {}).get("pesos") or {}
                    ofertas = [o for o in ofertas if self._chave_nome(o["nome"]) not in recentes
                               and float(pesos.get(o.get("_categoria", ""), 1)) > 0]
                except Exception as e:
                    msg = str(e)
                    if any(k in msg for k in ("403", "UNAUTHORIZED", "negou", "venceu", "login")):
                        self.pausadas[plat.nome] = time.time() + 12 * 3600
                        self.db.log(f"{NOMES[plat.nome]}: a loja bloqueou a busca automática — pausei ela por 12h. "
                                    f"Use a aba Ofertas pra postar links dela.", "aviso")
                        break
                    self.db.log(f"{NOMES[plat.nome]}: erro na busca ({msg[:150]})", "erro")
                    continue
                achadas += [o for o in melhores(ofertas, cfg) if not self.db.ja_postado(o["id"])]
                if len(achadas) >= 6:
                    break
            if achadas:
                vistos, unicas = set(), []
                for o in achadas:
                    if o["id"] not in vistos:
                        vistos.add(o["id"])
                        unicas.append(o)
                return unicas[:8]
        return []

    @staticmethod
    def _chave_nome(nome: str) -> str:
        """2 primeiras palavras relevantes do nome — pra não postar 2 smartwatches seguidos."""
        p = [w for w in (nome or "").lower().split() if len(w) > 3][:2]
        return " ".join(p)

    def postar_agora(self, manual_id: int | None = None) -> dict | None:
        cfg = config.carregar()
        lista = canais.canais_ativos(cfg)
        if not lista:
            raise RuntimeError("Nenhum canal configurado. Adicione um na aba Canais.")
        # 1º as ofertas que você cadastrou na aba Ofertas; depois as que o bot busca nas APIs
        try:  # ofertas que o Claude deixou na pasta do painel
            self.ofertas.importar_caixa(config.PASTA, log=self.db.log)
        except Exception as e:
            self.db.log(f"Caixa de entrada: {e}", "erro")
        manual = self.ofertas.pegar(manual_id) if manual_id else None
        # fila manual (Mercado Livre etc.) intercalada com o feed: 1 manual a cada N posts (config "manual_a_cada")
        a_cada = max(1, int(cfg.get("manual_a_cada", 2)))
        desde = getattr(self, "_desde_manual", a_cada)
        if not manual and desde >= a_cada - 1:
            manual = self.ofertas.proxima()
        if not manual_id:
            self._desde_manual = 0 if manual else desde + 1
        if manual:
            cands = [manual]
        else:
            try:
                cands = self.candidatas(cfg, lista)
            except RuntimeError as e:
                proxima = None if manual_id else self.ofertas.proxima()
                if not proxima:
                    self.db.log(f"{e} Cadastre links na aba Ofertas pra postar mesmo assim.", "aviso")
                    return None
                cands = [proxima]
        if not cands and not manual_id:
            proxima = self.ofertas.proxima()  # feed vazio: usa a fila manual mesmo assim
            if proxima:
                cands = [proxima]
        if not cands:
            self.db.log("Nenhuma oferta nova passou nos filtros.", "aviso")
            return None
        # canais que aceitam a loja dessas candidatas
        destino = [c for c in lista if not c.get("plataformas") or cands[0]["plataforma"] in c["plataformas"]]
        idiomas = [i for c in destino for i in canais.idioma_de(c)]
        o, textos = ia.escolher_e_escrever(cands, idiomas, log=self.db.log)
        ok, falhas = [], []
        for c in destino:
            try:
                canais.ENVIAR[c["tipo"]](c, o, textos)
                ok.append(c.get("nome") or c["tipo"])
            except Exception as e:
                falhas.append(c.get("nome") or c["tipo"])
                self.db.log(f"{c.get('nome') or c['tipo']}: {e}", "erro")
        if not ok:
            raise RuntimeError("Nenhum canal aceitou o post")
        self.db.registrar_post(o)
        self._zap_cont = getattr(self, "_zap_cont", 0) + 1
        a_cada = max(1, int(cfg.get("zap_a_cada", 6)))
        vez_zap = self._zap_cont >= a_cada or (o["plataforma"] == "mercadolivre" and self._zap_cont >= a_cada // 2)
        if cfg.get("whatsapp_assistido", True) and vez_zap:
            self._zap_cont = 0  # só 1 a cada N posts vai pro seu privado (pra dar conta de postar no Zap)
            try:
                canais.zap_para_admin(o, textos)
            except Exception as e:
                self.db.log(f"Zap assistido: {e}", "erro")
        if o.get("_categoria"):
            self.ultimas_cats.append(o["_categoria"])
        if o.get("_manual_id"):
            self.ofertas.marcar_postada(o["_manual_id"])
        self.db.log(f"Postado [{NOMES.get(o['plataforma'], 'Oferta')}] {o['nome'][:50]} → {', '.join(ok)}")
        return o

    def atualizar_feed(self, cfg=None) -> dict:
        cfg = cfg or config.carregar()
        url = config.cred("SHOPEE_FEED_URL")
        if not url:
            raise RuntimeError("Cole o link do feed em Contas → Shopee")
        r = self.feed.baixar_e_importar(url, cfg["feed_shopee"], config.PASTA, log=self.db.log)
        self.feed_ultima = time.time()
        self.db.log(f"Feed Shopee atualizado: {r['guardadas']} ofertas boas de {r['lidas']} produtos")
        return r

    def atualizar_feed_se_precisar(self, cfg):
        """Baixa o feed 1x por dia (a Shopee atualiza diariamente)."""
        if not config.cred("SHOPEE_FEED_URL"):
            if not getattr(self, "_avisou_sem_feed", False):
                self._avisou_sem_feed = True
                self.db.log("⚠️ SHOPEE_FEED_URL não está configurada — sem feed da Shopee pra postar", "erro")
            return
        if time.time() - self.feed_ultima < 20 * 3600:
            return
        resumo = self.feed.resumo()
        if resumo["atualizado"] and time.time() - resumo["atualizado"] < 20 * 3600:
            self.feed_ultima = resumo["atualizado"]
            return
        try:
            self.atualizar_feed(cfg)
        except Exception as e:
            self.feed_ultima = time.time() - 19.5 * 3600  # tenta de novo em ~30 min
            self.db.log(f"Não consegui baixar o feed da Shopee: {e}", "erro")
            try:
                canais.avisar_admin(f"⚠️ Não consegui baixar o feed da Shopee: {str(e)[:300]}")
            except Exception:
                pass

    def sincronizar_shopee(self, dias=30) -> int:
        shopee = self.shopee
        if not shopee.configurada:
            raise RuntimeError("Shopee não configurada (aba Contas)")
        fim = int(time.time())
        vendas = shopee.comissoes(fim - dias * 86400, fim)
        for v in vendas:
            self.db.salvar_ganho(v["id"], "shopee", v["valor"], v["descricao"], v["data_ts"], "api")
        self.db.log(f"Shopee sincronizada: {len(vendas)} vendas nos últimos {dias} dias")
        return len(vendas)

    @staticmethod
    def _fator_hora(h: int, cfg) -> float:
        """Horário de pico (almoço e noite) posta mais seguido; começo da manhã e fim da noite, menos."""
        if not cfg.get("ritmo_pico", True):
            return 1.0
        if h in (12, 13, 19, 20, 21):
            return 0.65
        if h in (7, 8, 22):
            return 1.4
        return 1.0

    @staticmethod
    def _extra_campanha(d) -> int:
        """Dias de campanha grande (11.11 de 4 a 14/11, Black Friday 27/11 a 30/11): +6 posts no dia."""
        if d.month == 11 and (4 <= d.day <= 14 or d.day >= 27):
            return 6
        return 0

    def resumo_diario_se_precisar(self, cfg):
        """Depois do último horário de post: manda pro dono (privado do Telegram) como foi o dia."""
        agora = datetime.now()
        if agora.hour < cfg["hora_fim"] or not cfg.get("resumo_diario", True):
            return
        dia = agora.strftime("%Y-%m-%d")
        if config.estado().get("resumo_dia") == dia:
            return
        config.salvar_estado(resumo_dia=dia)
        meia_noite = int(agora.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        por_loja = self.db._q("SELECT plataforma, COUNT(*) n FROM posts WHERE postado_em>=? GROUP BY plataforma",
                              (meia_noite,))
        lojas = ", ".join(f"{NOMES.get(r['plataforma'], r['plataforma'])}: {r['n']}" for r in por_loja) or "nenhum"
        feed = self.feed.resumo()
        idade_feed = (time.time() - (feed["atualizado"] or 0)) / 3600
        avisos = []
        if self.ofertas.na_fila() < 3:
            avisos.append("📭 A fila manual está quase vazia — mande links de oferta aqui no privado.")
        if config.cred("SHOPEE_FEED_URL") and idade_feed > 48:
            avisos.append("⚠️ O feed da Shopee não atualiza há mais de 2 dias — confira o link do feed.")
        texto = (f"📊 Resumo de hoje\n\n📤 Posts: {self.db.posts_hoje()} ({lojas})\n"
                 f"📋 Na fila manual: {self.ofertas.na_fila()}\n🛍️ Ofertas no feed: {feed['total']}"
                 + ("\n\n" + "\n".join(avisos) if avisos else "\n\n✅ Tudo certo, amanhã tem mais!"))
        canais.avisar_admin(texto)
        self.db.log("📊 Resumo do dia enviado pro dono")

    def top_semanal_se_precisar(self, cfg):
        """Sexta a partir das 20h: posta o TOP 5 da semana no canal principal (1x por semana)."""
        agora = datetime.now()
        if not cfg.get("top_semanal", True) or agora.weekday() != 4 or agora.hour < 20 or agora.hour >= 23:
            return
        semana = agora.strftime("%G-%V")
        estado = config.estado()
        if estado.get("top_semana") == semana:
            return
        limite = int(time.time()) - 7 * 86400
        rows = self.db._q("SELECT nome, preco, desconto, link FROM posts WHERE postado_em>? AND link LIKE 'http%' "
                          "ORDER BY desconto DESC, postado_em DESC LIMIT 20", (limite,))
        vistos, top = set(), []
        for r in rows:
            k = self._chave_nome(r["nome"])
            if k not in vistos:
                vistos.add(k)
                top.append(dict(r))
        config.salvar_estado(top_semana=semana)
        if len(top) < 3:
            return
        canais.enviar_principal(canais.top_da_semana(top[:5]))
        self.db.log("⭐ TOP 5 da semana postado no Telegram")

    def _loop(self):
        erros = 0
        ultima_sync = 0
        while not self._parar.is_set():
            cfg = config.carregar()
            _manter_acordado(cfg.get("manter_acordado", True))
            agora = datetime.now()
            try:
                if time.time() - ultima_sync > 3 * 3600 and self.shopee.configurada:
                    self.sincronizar_shopee()
                    ultima_sync = time.time()
                self.atualizar_feed_se_precisar(cfg)
                if not (cfg["hora_inicio"] <= agora.hour < cfg["hora_fim"]):
                    self.db.log("Fora do horário de postagem, esperando…")
                elif self.db.posts_hoje() >= cfg["max_posts_dia"] + self._extra_campanha(agora):
                    self.db.log("Limite de posts do dia atingido.")
                else:
                    self.postar_agora()
                erros = 0
            except Exception as e:
                erros += 1
                self.db.log(f"Erro: {e}", "erro")
                if erros == 3:
                    try:
                        canais.avisar_admin(f"⚠️ Bot de achadinhos com erro repetido: {e}")
                    except Exception:
                        pass
            try:
                self.resumo_diario_se_precisar(cfg)
            except Exception as e:
                self.db.log(f"Resumo do dia: {e}", "erro")
            try:
                self.top_semanal_se_precisar(cfg)
            except Exception as e:
                self.db.log(f"Top da semana: {e}", "erro")
            intervalo = float(cfg["intervalo_min"])
            if cfg.get("intervalo_sem_fila_min") and self.ofertas.na_fila() == 0:
                intervalo = float(cfg["intervalo_sem_fila_min"])  # fila manual vazia: só feed, no ritmo dele
            base = int(intervalo * 60 * self._fator_hora(datetime.now().hour, cfg))
            folga = min(240, base // 8)  # variação pequena pra não parecer robô
            espera = max(base + random.randint(-folga, folga), 90)
            self.proximo_post_ts = int(time.time() + espera)
            self._acordar.wait(espera)
            self._acordar.clear()
        self.proximo_post_ts = None
        _manter_acordado(False)
