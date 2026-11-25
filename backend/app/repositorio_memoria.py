"""Repositório em memória: usado nos testes e no comando `simular` (sem banco, sem internet).

Reproduz as regras essenciais do banco (sem duplicidade, trava de link validado, demo isolado,
pausa por preço) para que os testes dos serviços sejam realistas. As regras completas e
definitivas estão no PostgreSQL (supabase/migrations) e têm testes próprios.
"""
from __future__ import annotations

import itertools
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .erros import RegraViolada
from .modelos import CampanhaEncontrada, Cupom, ItemFila, OfertaEncontrada, ResultadoValidacao
from .repositorio import Repositorio, agora

CONFIG_PADRAO = {
    "publicacao.aprovacao_manual": False, "publicacao.horario_inicio": 0, "publicacao.horario_fim": 24,
    "publicacao.intervalo_minutos": 10, "publicacao.por_rodada": 2, "publicacao.max_por_dia": 60,
    "publicacao.feed_horario": 19, "filtros.desconto_minimo": 20, "filtros.nota_minima": 4.5,
    "filtros.repetir_apos_dias": 30, "monitor.ativo": True, "monitor.tolerancia_preco_pct": 5,
    "monitor.acompanhar_dias": 7, "monitor.verificar_links": True, "transparencia.marcar_publi": True,
    "transparencia.aviso_afiliado": "Link de afiliado: posso ganhar comissão, sem custo extra para você.",
    "textos.chamada_instagram": "🔗 Link no nosso canal do Telegram (link na bio)",
    "textos.rodape_story": "Link no canal do Telegram", "textos.hashtags": "#achadinhos #promoção",
    "visual.nome_perfil": "Sonho das Promos", "visual.cor_principal": "#EE4D2D", "visual.cor_destaque": "#FFD400",
    "sistema.fuso_horario": "America/Recife", "logs.retencao_dias": 30,
    "cupons.ativo": True, "cupons.max_stories_dia": 3, "cupons.publicar_telegram": True, "cupons.horario": 9,
    "cupons.buscar_campanhas": True, "cupons.sugerir_produtos": True, "cupons.max_sugestoes": 10,
}


def _dominio_ok(url: str, dominios: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in dominios)


from .requisitos import COLUNAS_PUBLICACAO, CONFIGS_PUBLICACAO, RPCS_PUBLICACAO  # noqa: E402


class MemoriaRepositorio(Repositorio):
    def __init__(self):
        self._ids = itertools.count(1)
        self.config = dict(CONFIG_PADRAO)
        # espelho de public.esquema_publicacao() de um banco com a migration 018
        self.esquema = {"versao": 18, "rpcs": sorted(RPCS_PUBLICACAO), "configuracoes": sorted(CONFIGS_PUBLICACAO),
                        "colunas": sorted(COLUNAS_PUBLICACAO)}
        self.t = {k: [] for k in ("marketplaces", "canais", "categorias", "produtos", "promocoes", "links",
                                  "fila", "historico", "comissoes", "execucoes", "logs", "precos", "cupons", "cupons_produtos", "cupons_pub",
                                  "comissoes_importacoes")}
        self.cofre: dict[tuple[str, str], str] = {}
        self.relogio = agora  # os testes trocam para simular a passagem do tempo
        for codigo, nome, doms, modo, padrao in [
                ("shopee", "Shopee", ["s.shopee.com.br", "shope.ee"], "demo", r"^https://(s\.shopee\.com\.br|shope\.ee)/[A-Za-z0-9_-]+"),
                ("mercado_livre", "Mercado Livre", ["mercadolivre.com"], "manual", r"^https://mercadolivre\.com/sec/[A-Za-z0-9]+"),
                ("demo", "Loja de Demonstração", ["demo.invalid"], "demo", r"^https://demo\.invalid/")]:
            destino = {"shopee": ["shopee.com.br"], "mercado_livre": ["mercadolivre.com.br", "mercadolivre.com"]}.get(codigo, [])
            self.t["marketplaces"].append({"id": self._id(), "codigo": codigo, "nome": nome, "padrao_afiliado": padrao,
                                           "dominios_afiliado": doms, "modo": modo, "ativo": True, "dominios_destino": destino,
                                           "parametros_afiliado": [], "id_afiliado": None})
        for codigo, tipo, limite in [("telegram", "telegram", 200), ("instagram_story", "instagram_story", 45),
                                     ("instagram_feed", "instagram_feed", 1)]:
            self.t["canais"].append({"id": self._id(), "codigo": codigo, "tipo": tipo, "nome": codigo,
                                     "modo": "demo", "ativo": True, "limite_diario": limite})
        for slug, nome, palavras in [("eletronicos", "Eletrônicos", ["fone bluetooth", "smartwatch"]),
                                     ("casa", "Casa", ["air fryer", "organizador"]),
                                     ("beleza", "Beleza", ["maquiagem"])]:
            self.t["categorias"].append({"id": self._id(), "slug": slug, "nome": nome,
                                         "palavras_chave": palavras, "ativa": True})

    def _id(self):
        return next(self._ids)

    def _um(self, tabela, **filtro):
        return next((r for r in self.t[tabela] if all(r.get(k) == v for k, v in filtro.items())), None)

    # ---------------------------------------------------------------- cadastro
    def configuracoes(self): return dict(self.config)
    def marketplaces(self): return [dict(m) for m in self.t["marketplaces"]]
    def canais(self): return [dict(c) for c in self.t["canais"]]
    def categorias(self): return [dict(c) for c in self.t["categorias"]]

    def definir_modo(self, tabela, codigo, modo):
        alvo = self._um("marketplaces" if tabela == "marketplaces" else "canais", codigo=codigo)
        if alvo:
            alvo["modo"] = modo

    # ---------------------------------------------------------------- catálogo
    def salvar_produto(self, oferta, marketplace_id, categoria_id):
        mkt = self._um("marketplaces", id=marketplace_id)
        p = self._um("produtos", marketplace_id=marketplace_id, id_externo=oferta.id_externo)
        if p:
            self.atualizar_produto(p["id"], oferta.preco, oferta.disponivel, oferta.preco_original)
            p.update(titulo=oferta.titulo, imagem_url=oferta.imagem_url, preco_maximo_variacao=oferta.preco_maximo,
                     referencia_derivada=oferta.referencia_derivada)
            return p["id"], False
        p = {"id": self._id(), "marketplace_id": marketplace_id, "id_externo": oferta.id_externo,
             "categoria_id": categoria_id, "titulo": oferta.titulo, "preco_atual": oferta.preco,
             "preco_original": oferta.preco_original, "imagem_url": oferta.imagem_url, "disponivel": oferta.disponivel,
             "demo": oferta.demo or mkt["modo"] == "demo", "verificado_em": self.relogio(),
             "nota": oferta.nota, "total_vendas": oferta.total_vendas,
             "preco_maximo_variacao": oferta.preco_maximo, "referencia_derivada": oferta.referencia_derivada}
        self.t["produtos"].append(p)
        self._registrar_preco(p)
        return p["id"], True

    def _registrar_preco(self, p):
        self.t["precos"].append({"produto_id": p["id"], "preco": p["preco_atual"], "disponivel": p["disponivel"],
                                 "quando": self.relogio()})

    def _invalidar_analise(self, produto_id):
        for pr in self.t["promocoes"]:
            if pr["produto_id"] == produto_id and pr["status"] in ("encontrada", "aprovada", "publicada", "pausada"):
                pr.update(pontuacao=None, classificacao=None, confianca=None, analise=None, analisada_em=None)

    def criar_promocao(self, produto_id, oferta, status, origem):
        if any(pr["produto_id"] == produto_id and pr["status"] in ("encontrada", "aprovada", "publicada")
               for pr in self.t["promocoes"]):
            return None
        prod = self._um("produtos", id=produto_id)
        pr = {"id": self._id(), "produto_id": produto_id, "preco_promocional": oferta.preco,
              "preco_referencia": oferta.preco_original, "desconto_pct": oferta.desconto_pct, "status": status,
              "origem": origem, "demo": prod["demo"] or origem == "demo", "motivo_status": None,
              "publicada_em": None, "pausada_em": None, "criado_em": self.relogio(), "comissao_pct": oferta.comissao_pct,
              "pontuacao": None, "classificacao": None, "confianca": None, "analise": None, "analisada_em": None}
        self.t["promocoes"].append(pr)
        return pr["id"]

    def promocao_ativa(self, produto_id):
        pr = next((p for p in self.t["promocoes"] if p["produto_id"] == produto_id
                   and p["status"] in ("encontrada", "aprovada", "publicada")), None)
        return dict(pr) if pr else None

    def expirar_promocao(self, promocao_id, motivo):
        pr = self._um("promocoes", id=promocao_id)
        pr.update(status="expirada", motivo_status=motivo)
        for f in self.t["fila"]:
            if f["promocao_id"] == promocao_id and f["status"] in ("pendente", "processando"):
                f["status"] = "cancelada"

    def salvar_link(self, produto_id, promocao_id, url):
        l = self._um("links", url=url)
        if l:
            return l["id"]
        l = {"id": self._id(), "produto_id": produto_id, "promocao_id": promocao_id, "url": url,
             "validado": False, "ativo": True, "status_http": None, "motivo_invalido": None}
        self.t["links"].append(l)
        return l["id"]

    def _mkt_do_produto(self, produto_id):
        return self._um("marketplaces", id=self._um("produtos", id=produto_id)["marketplace_id"])

    def links_pendentes(self, limite, revalidar_atribuicao_horas=None):
        saida = []
        for l in self.t["links"]:
            if not l["ativo"]:
                continue
            nunca = not l["validado"] and l["status_http"] is None
            vencida = False
            if revalidar_atribuicao_horas and l["validado"]:
                quando = l.get("atribuicao_verificada_em")
                vencida = quando is None or self.relogio() - quando > timedelta(hours=float(revalidar_atribuicao_horas))
            if not (nunca or vencida):
                continue
            if not any(pr["produto_id"] == l["produto_id"] and pr["status"] in ("aprovada", "publicada")
                       for pr in self.t["promocoes"]):
                continue
            mkt = self._mkt_do_produto(l["produto_id"])
            saida.append({"link_id": l["id"], "url": l.get("url_curta") or l["url"], "produto_id": l["produto_id"],
                          "demo": self._um("produtos", id=l["produto_id"])["demo"],
                          "marketplace": mkt["codigo"], "dominios": mkt["dominios_afiliado"],
                          "padrao": mkt.get("padrao_afiliado"), "dominios_destino": mkt.get("dominios_destino"),
                          "parametros_afiliado": mkt.get("parametros_afiliado") or [], "id_afiliado": mkt.get("id_afiliado")})
        return saida[:limite]

    def registrar_validacao(self, link_id, resultado):
        l = self._um("links", id=link_id)
        mkt = self._mkt_do_produto(l["produto_id"])
        if resultado.valido and not _dominio_ok(l["url"], mkt["dominios_afiliado"]):
            raise RegraViolada("domínio não é de afiliado", codigo="P0011")
        if resultado.valido and mkt.get("padrao_afiliado") and not re.match(mkt["padrao_afiliado"], l["url"]):
            raise RegraViolada("link sem atribuição de afiliado", codigo="P0024")
        mudou = l["validado"] != resultado.valido
        l.update(validado=resultado.valido, status_http=resultado.status_http,
                 motivo_invalido=None if resultado.valido else resultado.motivo,
                 atribuicao_verificada_em=self.relogio() if resultado.valido and resultado.atribuicao else None)
        if mudou:
            self._invalidar_analise(l["produto_id"])

    # ---------------------------------------------------------------- publicação
    def _link_valido(self, produto_id):
        return next((l for l in self.t["links"] if l["produto_id"] == produto_id and l["validado"] and l["ativo"]), None)

    def _view(self, pr):
        prod = self._um("produtos", id=pr["produto_id"])
        mkt = self._um("marketplaces", id=prod["marketplace_id"])
        link = self._link_valido(pr["produto_id"])
        return {"id": pr["id"], "status": pr["status"], "titulo": prod["titulo"], "imagem_url": prod["imagem_url"],
                "preco_promocional": pr["preco_promocional"], "preco_referencia": pr["preco_referencia"],
                "desconto_pct": pr["desconto_pct"], "marketplace": mkt["codigo"], "marketplace_nome": mkt["nome"],
                "link_id": link["id"] if link else None, "link_validado": bool(link), "demo": pr["demo"],
                "origem": pr["origem"], "publicada_em": pr["publicada_em"], "produto_id": prod["id"],
                "pontuacao": pr.get("pontuacao"), "classificacao": pr.get("classificacao"), "analise": pr.get("analise")}

    def promocoes_para_enfileirar(self, limite):
        na_fila = {f["promocao_id"] for f in self.t["fila"] if f["status"] in ("pendente", "processando")}
        lista = [self._view(pr) for pr in self.t["promocoes"] if pr["status"] == "aprovada" and pr["id"] not in na_fila]
        lista = [v for v in lista if v["link_validado"] and v["classificacao"] in ("excelente", "boa")]
        return sorted(lista, key=lambda v: (v["pontuacao"] or 0, v["desconto_pct"]), reverse=True)[:limite]

    def enfileirar(self, promocao_id, canal_id, link_id, prioridade=5):
        pr = self._um("promocoes", id=promocao_id)
        canal = self._um("canais", id=canal_id)
        link = self._um("links", id=link_id)
        if not (link and link["validado"] and link["ativo"] and link["produto_id"] == pr["produto_id"]):
            return False
        if pr["status"] not in ("aprovada", "publicada") or not canal["ativo"] or (pr["demo"] and canal["modo"] == "real"):
            return False
        if pr.get("classificacao") not in ("excelente", "boa"):
            return False  # banco: P0019
        horas = float(self.config.get("inteligencia.janela_repeticao_horas", 24))
        if self.publicado_no_canal_recentemente(pr["produto_id"], canal_id, horas):
            return False  # banco: P0018
        if any(f["promocao_id"] == promocao_id and f["canal_id"] == canal_id and f["status"] in ("pendente", "processando")
               for f in self.t["fila"]):
            return False
        self.t["fila"].append({"id": self._id(), "promocao_id": promocao_id, "canal_id": canal_id,
                               "link_afiliado_id": link_id, "prioridade": prioridade, "status": "pendente",
                               "tentativas": 0, "max_tentativas": 3})
        return True

    def pegar_fila(self, limite):
        agora_ = self.relogio()
        for f in self.t["fila"]:
            vencida = f["status"] == "processando" and f.get("bloqueado_ate") and f["bloqueado_ate"] < agora_
            if vencida and f.get("publicando_desde"):
                f.update(status="falhou", bloqueado_ate=None,
                         ultimo_erro="Resultado incerto: o bot parou durante o envio — confira o canal antes de publicar de novo.")
            elif vencida and f["tentativas"] >= f["max_tentativas"]:
                f.update(status="falhou", bloqueado_ate=None, ultimo_erro=f.get("ultimo_erro") or "Tempo esgotado")
        livres = [f for f in self.t["fila"] if f["tentativas"] < f["max_tentativas"] and (
            (f["status"] == "pendente" and f.get("agendado_para", agora_) <= agora_)
            or (f["status"] == "processando" and f.get("bloqueado_ate") and f["bloqueado_ate"] < agora_))]
        itens = sorted(livres, key=lambda f: f["prioridade"])[:limite]
        for f in itens:
            f.update(status="processando", tentativas=f["tentativas"] + 1, publicando_desde=None,
                     bloqueado_ate=agora_ + timedelta(minutes=10))
        return [ItemFila(f["id"], f["promocao_id"], f["canal_id"], f["link_afiliado_id"], f["tentativas"]) for f in itens]

    def fila_atrasada(self, minutos):
        from datetime import timedelta
        limite = self.relogio() - timedelta(minutes=minutos)
        return sum(1 for f in self.t["fila"] if f["status"] == "pendente" and f.get("agendado_para", self.relogio()) < limite)

    def parada_emergencia(self):
        return bool(self.config.get("sistema.parada_emergencia"))

    def produto(self, produto_id):
        p = self._um("produtos", id=produto_id)
        if not p:
            return None
        mkt = self._um("marketplaces", id=p["marketplace_id"])
        return {"id": p["id"], "id_externo": p["id_externo"], "marketplace": mkt["codigo"], "preco_atual": p["preco_atual"],
                "disponivel": p["disponivel"], "verificado_em": p.get("verificado_em")}

    def marcar_publicando(self, fila_id):
        f = self._um("fila", id=fila_id)
        if f and f["status"] == "processando":
            f["publicando_desde"] = self.relogio()

    def reservar_envio(self, fila_id):
        f = self._um("fila", id=fila_id)
        if not f or f["status"] != "processando" or f.get("publicando_desde"):
            return False
        if f.get("bloqueado_ate") and f["bloqueado_ate"] <= self.relogio():
            return False
        f["publicando_desde"] = self.relogio()
        return True

    def reservar_envio_cupom(self, cupom_id, canal_id):
        c, canal = self._um("cupons", id=cupom_id), self._um("canais", id=canal_id)
        dia = self.relogio().astimezone(timezone(timedelta(hours=-3))).date().isoformat()
        meus = [p for p in self.t["cupons_pub"] if p["cupom_id"] == cupom_id and p["canal_id"] == canal_id and p["dia"] == dia]
        reservas = sum(1 for p in meus if p["resultado"] == "enviando")
        fim = sum(1 for p in meus if p["resultado"] in ("sucesso", "erro"))
        if any(p["resultado"] == "sucesso" for p in meus) or reservas > fim:
            return None
        if not self._vigente(c):
            raise RegraViolada("cupom fora da validade ou pausado", codigo="P0025")
        if c["demo"] and canal["modo"] == "real":
            raise RegraViolada("cupom demo em canal real", codigo="P0015")
        r = {"id": self._id(), "cupom_id": cupom_id, "canal_id": canal_id, "dia": dia, "resultado": "enviando",
             "id_mensagem": None, "erro": None, "demo": c["demo"] or canal["modo"] == "demo"}
        self.t["cupons_pub"].append(r)
        return r["id"]

    def esquema_publicacao(self):
        return dict(self.esquema)

    def link_atribuicao(self, link_id):
        l = self._um("links", id=link_id)
        return l.get("atribuicao_verificada_em") if l else None

    def devolver_fila(self, fila_id, minutos, motivo):
        f = self._um("fila", id=fila_id)
        if f and f["status"] == "processando":
            f.update(status="pendente", tentativas=max(f["tentativas"] - 1, 0), bloqueado_ate=None, publicando_desde=None,
                     agendado_para=self.relogio() + timedelta(minutes=max(1, min(int(minutos), 1440))), ultimo_erro=motivo)

    def marcar_incerta(self, fila_id, motivo):
        f = self._um("fila", id=fila_id)
        if f and f["status"] in ("pendente", "processando"):
            f.update(status="falhou", bloqueado_ate=None,
                     ultimo_erro=f"Resultado incerto: {motivo} — confira o canal antes de publicar de novo.")

    def dados_publicacao(self, promocao_id):
        pr = self._um("promocoes", id=promocao_id)
        return self._view(pr) if pr else None

    def registrar_publicacao(self, item, sucesso, id_mensagem, erro):
        pr = self._um("promocoes", id=item.promocao_id)
        canal = self._um("canais", id=item.canal_id)
        link = self._um("links", id=item.link_afiliado_id)
        demo = canal["modo"] == "demo" or pr["demo"]
        if sucesso and not (link["validado"] and link["ativo"]):
            raise RegraViolada("link não validado", codigo="P0010")
        if sucesso and pr["demo"] and canal["modo"] == "real":
            raise RegraViolada("demo em canal real", codigo="P0015")
        self.t["historico"].append({"id": self._id(), "fila_id": item.id, "promocao_id": item.promocao_id,
                                    "canal_id": item.canal_id, "resultado": "sucesso" if sucesso else "erro",
                                    "id_mensagem_externa": id_mensagem, "erro": erro, "demo": demo,
                                    "publicado_em": self.relogio()})
        if sucesso and (not demo or pr["demo"]) and pr["status"] != "publicada":
            pr.update(status="publicada", publicada_em=self.relogio())
        f = self._um("fila", id=item.id)
        if f:
            f["status"] = "concluida" if sucesso else ("falhou" if f["tentativas"] >= f["max_tentativas"] else "pendente")
            f.update(bloqueado_ate=None, publicando_desde=None, ultimo_erro=None if sucesso else erro)
            if not sucesso:
                f["agendado_para"] = self.relogio() + timedelta(minutes=2 ** max(f["tentativas"], 1))

    def publicacoes_desde(self, desde, tipos):
        ids = {c["id"] for c in self.t["canais"] if c["tipo"] in tipos}
        return [h for h in self.t["historico"] if h["canal_id"] in ids and h["resultado"] == "sucesso"
                and not h["demo"] and h["publicado_em"] >= desde]

    def melhor_publicada_desde(self, desde):
        lista = [self._view(pr) for pr in self.t["promocoes"]
                 if pr["status"] == "publicada" and pr["publicada_em"] and pr["publicada_em"] >= desde]
        lista = [v for v in lista if v["link_validado"] and v["classificacao"] in ("excelente", "boa")]
        return max(lista, key=lambda v: (v["pontuacao"] or 0, v["desconto_pct"])) if lista else None

    # ---------------------------------------------------------------- inteligência
    def promocoes_para_analisar(self, limite):
        saida = []
        for pr in self.t["promocoes"]:
            if pr["status"] not in ("encontrada", "aprovada", "publicada") or pr["analisada_em"] is not None:
                continue
            prod = self._um("produtos", id=pr["produto_id"])
            mkt = self._um("marketplaces", id=prod["marketplace_id"])
            cat = self._um("categorias", id=prod["categoria_id"]) if prod.get("categoria_id") else None
            links = sorted([l for l in self.t["links"] if l["produto_id"] == prod["id"] and l["ativo"]],
                           key=lambda l: l["validado"], reverse=True)
            saida.append({
                "promocao_id": pr["id"], "produto_id": prod["id"], "status": pr["status"], "origem": pr["origem"],
                "demo": pr["demo"], "preco": pr["preco_promocional"], "preco_referencia": pr["preco_referencia"],
                "comissao_pct": pr.get("comissao_pct"), "titulo": prod["titulo"], "preco_produto": prod["preco_atual"],
                "disponivel": prod["disponivel"], "nota": prod.get("nota"), "total_vendas": prod.get("total_vendas"),
                "imagem_url": prod.get("imagem_url"), "categoria_ativa": cat["ativa"] if cat else None,
                "preco_maximo_variacao": prod.get("preco_maximo_variacao"), "referencia_derivada": bool(prod.get("referencia_derivada")),
                "marketplace": mkt["codigo"], "marketplace_modo": mkt["modo"], "link": links[0] if links else None})
        return saida[:limite]

    def historico_precos(self, produto_ids, desde):
        saida = {}
        for h in self.t["precos"]:
            if h["produto_id"] in produto_ids and h["quando"] >= desde:
                saida.setdefault(h["produto_id"], []).append(dict(h))
        return saida

    def titulos_ativos(self, desde):
        saida = []
        for pr in self.t["promocoes"]:
            if pr["status"] in ("encontrada", "aprovada", "publicada") and pr["criado_em"] >= desde:
                prod = self._um("produtos", id=pr["produto_id"])
                saida.append({"promocao_id": pr["id"], "produto_id": prod["id"], "titulo": prod["titulo"],
                              "marketplace": self._um("marketplaces", id=prod["marketplace_id"])["nome"]})
        return saida

    def salvar_analise(self, promocao_id, pontuacao, classificacao, confianca, analise):
        self._um("promocoes", id=promocao_id).update(pontuacao=pontuacao, classificacao=classificacao,
                                                    confianca=confianca, analise=analise, analisada_em=self.relogio())

    def publicado_no_canal_recentemente(self, produto_id, canal_id, horas):
        limite = self.relogio() - timedelta(hours=horas)
        promos = {p["id"] for p in self.t["promocoes"] if p["produto_id"] == produto_id}
        return any(h["promocao_id"] in promos and h["canal_id"] == canal_id and h["resultado"] == "sucesso"
                   and not h["demo"] and h["publicado_em"] >= limite for h in self.t["historico"])

    # ---------------------------------------------------------------- monitor
    def publicadas_para_monitorar(self, dias, limite):
        saida = []
        for pr in self.t["promocoes"]:
            if pr["status"] != "publicada":
                continue
            prod = self._um("produtos", id=pr["produto_id"])
            mkt = self._um("marketplaces", id=prod["marketplace_id"])
            link = self._link_valido(prod["id"])
            saida.append({"promocao_id": pr["id"], "produto_id": prod["id"], "preco_promocional": pr["preco_promocional"],
                          "id_externo": prod["id_externo"], "marketplace": mkt["codigo"],
                          "dominios": mkt["dominios_afiliado"], "link_id": link["id"] if link else None,
                          "link_url": link["url"] if link else None})
        return saida[:limite]

    def atualizar_produto(self, produto_id, preco, disponivel, preco_original):
        p = self._um("produtos", id=produto_id)
        antes_preco, antes_disp = p["preco_atual"], p["disponivel"]
        antes_original = p.get("preco_original")
        p.update(preco_atual=preco, disponivel=disponivel, verificado_em=self.relogio())
        if preco_original:
            p["preco_original"] = preco_original
        if preco != antes_preco or disponivel != antes_disp or p.get("preco_original") != antes_original:
            self._registrar_preco(p)
            self._invalidar_analise(produto_id)
        if not self.config.get("monitor.ativo", True):
            return
        tol = float(self.config.get("monitor.tolerancia_preco_pct", 5))
        for pr in self.t["promocoes"]:
            if pr["produto_id"] != produto_id or pr["status"] not in ("encontrada", "aprovada", "publicada"):
                continue
            if antes_disp and not disponivel:
                self._pausar(pr, "Produto indisponível")
            elif preco > antes_preco and preco > pr["preco_promocional"] * (1 + tol / 100):
                self._pausar(pr, f"Preço subiu para R$ {preco:.2f}")

    def _pausar(self, pr, motivo):
        pr.update(status="pausada", motivo_status=motivo, pausada_em=self.relogio())
        for f in self.t["fila"]:
            if f["promocao_id"] == pr["id"] and f["status"] in ("pendente", "processando"):
                f["status"] = "cancelada"

    def invalidar_link(self, link_id, motivo):
        l = self._um("links", id=link_id)
        l.update(validado=False, motivo_invalido=motivo)
        self._invalidar_analise(l["produto_id"])
        if not self._link_valido(l["produto_id"]):
            for pr in self.t["promocoes"]:
                if pr["produto_id"] == l["produto_id"] and pr["status"] in ("aprovada", "publicada"):
                    self._pausar(pr, "Sem link de afiliado validado")

    def pausadas_com_mensagens(self, desde):
        saida = []
        for pr in self.t["promocoes"]:
            if pr["status"] == "pausada" and pr["pausada_em"] and pr["pausada_em"] >= desde:
                for h in self.t["historico"]:
                    if h["promocao_id"] == pr["id"] and h["resultado"] == "sucesso" and h["id_mensagem_externa"] and not h["demo"]:
                        saida.append({"promocao_id": pr["id"], "canal_id": h["canal_id"],
                                      "id_mensagem": h["id_mensagem_externa"], "motivo": pr["motivo_status"],
                                      "titulo": self._um("produtos", id=pr["produto_id"])["titulo"]})
        return saida

    # ---------------------------------------------------------------- comissões, execução, logs
    def salvar_comissoes(self, marketplace_id, itens):
        r = {"novos": 0, "atualizados": 0, "ignorados": 0}
        for i in {i["referencia_externa"]: i for i in itens if i.get("referencia_externa")}.values():
            antes = self._um("comissoes", marketplace_id=marketplace_id, referencia_externa=i["referencia_externa"])
            if antes and antes["status"] == "paga":
                r["ignorados"] += 1
            elif antes:
                if antes["status"] != i.get("status") or antes["valor_comissao"] != i["valor_comissao"]:
                    r["atualizados"] += 1
                antes.update({k: v for k, v in i.items()})
            else:
                self.t["comissoes"].append(dict(i, marketplace_id=marketplace_id, origem="api"))
                r["novos"] += 1
        return r

    def comissoes_api_periodo(self, marketplace_id, inicio, fim):
        return [c for c in self.t["comissoes"] if c["marketplace_id"] == marketplace_id and c.get("origem") == "api"
                and inicio <= c["data_venda"] <= fim]

    def registrar_importacao(self, dados):
        self.t["comissoes_importacoes"].append(dict(dados, criado_em=self.relogio()))

    def ultima_importacao(self, marketplace_id, tipo):
        datas = [i["criado_em"] for i in self.t["comissoes_importacoes"]
                 if i["marketplace_id"] == marketplace_id and i["tipo"] == tipo and i["status"] != "erro"]
        return max(datas) if datas else None

    def iniciar_execucao(self, origem, modo, versao):
        e = {"id": str(uuid.uuid4()), "origem": origem, "modo": modo, "versao": versao, "status": "rodando",
             "iniciada_em": self.relogio(), "resumo": {}}
        self.t["execucoes"].append(e)
        return e["id"]

    def finalizar_execucao(self, execucao_id, status, resumo):
        e = self._um("execucoes", id=execucao_id)
        e.update(status=status, resumo=resumo, finalizada_em=self.relogio())

    # ---- cupons (espelha a migration 016)
    def criar_cupom(self, marketplace: str, titulo: str, fim_em, **campos) -> int:
        """Como o cadastro do painel. Aplica as mesmas regras do banco."""
        mkt = self._um("marketplaces", codigo=marketplace)
        c = {"id": self._id(), "marketplace_id": mkt["id"], "tipo": "cupom", "origem": "manual", "id_externo": None,
             "titulo": titulo.strip(), "codigo": None, "desconto_tipo": None, "desconto_valor": None, "valor_minimo": None,
             "desconto_maximo": None, "link_afiliado": None, "imagem_url": None, "palavras_chave": [], "categoria_id": None,
             "regras": None, "inicio_em": self.relogio() - timedelta(minutes=1), "fim_em": fim_em, "status": "ativo",
             "demo": mkt["modo"] == "demo", "atualizado_em": self.relogio()}
        c.update(campos)
        c["codigo"] = (c["codigo"] or "").strip().upper() or None
        self._regras_cupom(c, mkt)
        self.t["cupons"].append(c)
        return c["id"]

    def _regras_cupom(self, c, mkt):
        if c["tipo"] == "cupom" and not c["codigo"]:
            raise RegraViolada("cupom precisa de código", codigo="23514")
        if c["tipo"] == "campanha" and not c["link_afiliado"]:
            raise RegraViolada("campanha precisa de link", codigo="23514")
        if c["fim_em"] <= c["inicio_em"]:
            raise RegraViolada("validade invertida", codigo="23514")
        if c["desconto_tipo"] == "percentual" and (c["desconto_valor"] or 0) > 90:
            raise RegraViolada("desconto acima de 90%", codigo="23514")
        if c["link_afiliado"]:
            if not _dominio_ok(c["link_afiliado"], mkt["dominios_afiliado"]):
                raise RegraViolada("domínio não é de afiliado", codigo="P0011")
            if mkt.get("padrao_afiliado") and not re.match(mkt["padrao_afiliado"], c["link_afiliado"]):
                raise RegraViolada("link sem atribuição de afiliado", codigo="P0024")
        if c["status"] == "ativo" and c["fim_em"] <= self.relogio():
            c["status"] = "expirado"

    def _cupom_obj(self, c) -> Cupom:
        mkt = self._um("marketplaces", id=c["marketplace_id"])
        return Cupom.de_linha({**c, "marketplace_codigo": mkt["codigo"], "marketplace_nome": mkt["nome"]})

    def _vigente(self, c) -> bool:
        return c["status"] == "ativo" and c["inicio_em"] <= self.relogio() < c["fim_em"]

    def cupons_vigentes(self):
        return [self._cupom_obj(c) for c in sorted(self.t["cupons"], key=lambda c: c["fim_em"]) if self._vigente(c)]

    def cupons_publicados_no_dia(self, dia):
        return [{k: p[k] for k in ("cupom_id", "canal_id", "resultado", "erro")} for p in self.t["cupons_pub"] if p["dia"] == dia]

    def registrar_publicacao_cupom(self, cupom_id, canal_id, sucesso, id_mensagem, erro):
        c, canal = self._um("cupons", id=cupom_id), self._um("canais", id=canal_id)
        dia = self.relogio().astimezone(timezone(timedelta(hours=-3))).date().isoformat()
        if sucesso:
            if not self._vigente(c):
                raise RegraViolada("cupom fora da validade ou pausado", codigo="P0025")
            if c["demo"] and canal["modo"] == "real":
                raise RegraViolada("cupom demo em canal real", codigo="P0015")
            if any(p["cupom_id"] == cupom_id and p["canal_id"] == canal_id and p["dia"] == dia and p["resultado"] == "sucesso"
                   for p in self.t["cupons_pub"]):
                raise RegraViolada("cupom já publicado hoje neste canal", codigo="23505")
        self.t["cupons_pub"].append({"id": self._id(), "cupom_id": cupom_id, "canal_id": canal_id, "dia": dia,
                                     "resultado": "sucesso" if sucesso else "erro", "id_mensagem": id_mensagem,
                                     "erro": None if sucesso else erro, "demo": c["demo"] or canal["modo"] == "demo"})

    def salvar_campanha(self, cp):
        mkt = self._um("marketplaces", codigo=cp.marketplace)
        existente = self._um("cupons", marketplace_id=mkt["id"], id_externo=cp.id_externo)
        dados = {"tipo": "campanha", "origem": "automatica", "id_externo": cp.id_externo, "titulo": cp.titulo,
                 "link_afiliado": cp.link_afiliado, "fim_em": cp.fim_em, "imagem_url": cp.imagem_url}
        if existente:
            novo = {**existente, **dados, "atualizado_em": self.relogio()}
            self._regras_cupom(novo, mkt)
            existente.update(novo)
        else:
            self.criar_cupom(cp.marketplace, cp.titulo, cp.fim_em, **{k: v for k, v in dados.items() if k not in ("titulo", "fim_em")},
                             **({"inicio_em": cp.inicio_em} if cp.inicio_em and cp.inicio_em < cp.fim_em else {}))

    def campanhas_atualizadas_desde(self, desde):
        return sum(1 for c in self.t["cupons"] if c["origem"] == "automatica" and c["atualizado_em"] >= desde)

    def vincular_produto_cupom(self, cupom_id, promocao_id, origem="manual"):
        """Como o painel (manual = aprovado na hora)."""
        c = self._um("cupons", id=cupom_id)
        pr = self._um("promocoes", id=promocao_id)
        if self._um("produtos", id=pr["produto_id"])["marketplace_id"] != c["marketplace_id"]:
            raise RegraViolada("cupom de outra loja", codigo="P0026")
        if not self._um("cupons_produtos", cupom_id=cupom_id, promocao_id=promocao_id):
            self.t["cupons_produtos"].append({"cupom_id": cupom_id, "promocao_id": promocao_id, "origem": origem,
                                              "aprovado": origem == "manual", "motivo": None})

    def candidatos_para_cupom(self, cupom, limite):
        ja = {l["promocao_id"] for l in self.t["cupons_produtos"] if l["cupom_id"] == cupom.id}
        saida = []
        for pr in self.t["promocoes"]:
            if pr["id"] in ja or pr["status"] not in ("encontrada", "aprovada", "publicada"):
                continue
            if pr.get("classificacao") not in ("excelente", "boa") or bool(pr["demo"]) != cupom.demo:
                continue
            v = self._view(pr)
            if v["marketplace"] != cupom.marketplace:
                continue
            prod = self._um("produtos", id=pr["produto_id"])
            saida.append({"id": pr["id"], "titulo": prod["titulo"], "categoria_id": prod.get("categoria_id"),
                          "preco_promocional": pr["preco_promocional"], "pontuacao": pr.get("pontuacao"), "demo": pr["demo"]})
        return sorted(saida, key=lambda x: x["pontuacao"] or 0, reverse=True)[: max(limite * 20, 100)]

    def sugerir_produto_cupom(self, cupom_id, promocao_id, motivo):
        if not self._um("cupons_produtos", cupom_id=cupom_id, promocao_id=promocao_id):
            self.vincular_produto_cupom(cupom_id, promocao_id, origem="sugestao")
            self._um("cupons_produtos", cupom_id=cupom_id, promocao_id=promocao_id)["motivo"] = motivo

    def cupons_da_promocao(self, promocao_id):
        ids = {l["cupom_id"] for l in self.t["cupons_produtos"] if l["promocao_id"] == promocao_id and l["aprovado"]}
        return [self._cupom_obj(c) for c in self.t["cupons"] if c["id"] in ids and self._vigente(c)]

    def encerrar_execucoes_orfas(self, minutos=30):
        n = 0
        for e in self.t["execucoes"]:
            if e["status"] == "rodando" and e["iniciada_em"] < self.relogio() - timedelta(minutes=max(minutos, 10)):
                e.update(status="erro", finalizada_em=self.relogio(), resumo={**e["resumo"], "interrompida": True})
                n += 1
        return n

    def ultima_execucao(self):
        feitas = [e for e in self.t["execucoes"] if e["status"] != "rodando"]
        return max(feitas, key=lambda e: e["iniciada_em"]) if feitas else None

    def gravar_logs(self, execucao_id, itens):
        self.t["logs"] += [dict(i, execucao_id=execucao_id) for i in itens]

    def obter_credencial(self, integracao, nome):
        return self.cofre.get((integracao, nome))
