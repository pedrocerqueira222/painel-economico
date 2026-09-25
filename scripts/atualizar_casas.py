#!/usr/bin/env python3
"""
Vai buscar ao INE, para Portugal e 5 concelhos:
  - o preço mediano por m² das casas vendidas (indicador 0012234, últimos 12 meses, trimestral) -> dados/casas.json
  - a renda mediana por m² dos novos contratos de arrendamento (últimos 12 meses, trimestral;
    se não for possível, a versão anual 0014711) -> dados/rendas.json
  - fogos licenciados e concluídos em construções novas para habitação (Portugal) -> dados/construcao.json
  - número de casas vendidas (Portugal) -> dados/transacoes.json
  - imigrantes e emigrantes por ano (Eurostat) -> dados/migracao.json
  - mercados: petróleo, gás, ouro, prata, bolsas (Yahoo Finance / Stooq) e combustíveis em Portugal
    (boletim semanal da Comissão Europeia) -> dados/mercados.json  [a cada corrida, sem a regra das 20 h]
  - previsões do FMI para Portugal (World Economic Outlook) -> dados/previsoes.json
  - inflação medida pelo IPC (BPstat, Banco de Portugal) -> dados/ipc.json  [a cada corrida]
  - taxa de juro da Fed, EUA (FRED) -> dados/fed.json  [a cada corrida]
Corre todos os dias no GitHub (ver .github/workflows).

Só usa a biblioteca padrão do Python: não é preciso instalar nada.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

INE_URL = os.environ.get("INE_URL", "https://www.ine.pt/ine/json_indicador/pindica.jsp")
INDICADOR = "0012234"
PASTA = os.path.join(os.path.dirname(__file__), "..", "dados")
FICHEIRO = os.path.join(PASTA, "casas.json")

# nome -> códigos geográficos do INE (NUTS 2024 primeiro; NUTS 2013 por segurança)
LOCALIDADES = {
    "Portugal":   ["PT"],
    "Lisboa":     ["1A01106", "1701106"],
    "Porto":      ["11A1312"],
    "Matosinhos": ["11A1308"],
    "Maia":       ["11A1306"],
    "Valongo":    ["11A1315"],
}
TRIMESTRES = 28  # 7 anos

CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


INICIO = time.time()
ORCAMENTO = float(os.environ.get("ORCAMENTO_MINUTOS", "35")) * 60


def sem_tempo() -> bool:
    """True quando já se gastou o tempo disponível: o programa pára de pedir e grava o que tem."""
    return time.time() - INICIO > ORCAMENTO


# formatos de período que já funcionaram, por indicador: {"0012234": {"prefixo": "S3A", "tipo": "trimestral"}}
# confirmado no registo do GitHub (INE, set. 2026): trimestres do indicador 0012234 = S5A + ano + trimestre, desde 2019-Q4
FORMATOS = {"0012234": {"prefixo": "S5A", "tipo": "trimestral"}}
INCREMENTAL = False
FALHAS_REDE, LIMITE_FALHAS = 0, 8


class INEEmBaixo(RuntimeError):
    pass


def rede_ok():
    global FALHAS_REDE
    FALHAS_REDE = 0


def rede_falhou():
    global FALHAS_REDE
    FALHAS_REDE += 1
    if FALHAS_REDE == LIMITE_FALHAS:
        print(f"O INE não respondeu a {LIMITE_FALHAS} pedidos seguidos: paro de o contactar nesta corrida "
              "(os dados guardados ficam como estão).", file=sys.stderr)


def ine_em_baixo() -> bool:
    return FALHAS_REDE >= LIMITE_FALHAS
LEVE_TRIMESTRES, LEVE_MESES = 8, 15


def carregar_formatos():
    pasta = os.path.join(os.path.dirname(__file__), "..", "dados")
    if not os.path.isdir(pasta):
        return
    for nome in os.listdir(pasta):
        if nome.endswith(".json"):
            try:
                with open(os.path.join(pasta, nome), encoding="utf-8") as f:
                    FORMATOS.update(json.load(f).get("formatos") or {})
            except Exception:
                pass


def registar_formato(indicador: str, codigo: str):
    m = re.match(r"^(S\dA)(\d{4})(\d+)$", codigo or "")
    if m:
        FORMATOS[indicador] = {"prefixo": m.group(1), "tipo": "mensal" if len(m.group(3)) == 2 else "trimestral"}


def codigos_guardados(indicador: str, hoje: datetime, quantos: int) -> list:
    f = FORMATOS.get(indicador)
    if not f:
        return []
    p, mensal = f["prefixo"], f["tipo"] == "mensal"
    y, k = hoje.year, (hoje.month if mensal else (hoje.month - 1) // 3 + 1)
    out = []
    for _ in range(quantos):
        out.append(f"{p}{y}{k:02d}" if mensal else f"{p}{y}{k}")
        k -= 1
        if k < 1:
            k, y = (12 if mensal else 4), y - 1
    print(f"{indicador}: a usar o formato guardado ({p}, {f['tipo']})")
    return out


def ler_url(req, limite: float = 75, pausa: float = 30) -> bytes:
    """Abre e lê um endereço, com limite de tempo total: se o INE mandar a resposta aos pinguinhos, desiste."""
    inicio = time.time()
    with urllib.request.urlopen(req, timeout=pausa) as r:
        partes = []
        while True:
            if time.time() - inicio > limite:
                raise TimeoutError(f"resposta demasiado lenta (mais de {limite:.0f} s)")
            bloco = r.read(65536)
            if not bloco:
                break
            partes.append(bloco)
    return b"".join(partes)


class ErroINE(Exception):
    """O INE respondeu, mas com uma mensagem de erro (por exemplo, trimestre ainda não publicado)."""


def pedir(params: dict, tentativas: int = 2) -> list:
    url = INE_URL + "?" + urllib.parse.urlencode({"op": "2", "lang": "PT", **params})
    ultimo = None
    for n in range(tentativas):
        if ine_em_baixo():
            raise INEEmBaixo("o INE não está a responder")
        try:
            req = urllib.request.Request(url, headers=CABECALHOS)
            dados = json.loads(ler_url(req).decode("utf-8"))
            rede_ok()
            raiz = dados[0] if isinstance(dados, list) else dados
            if isinstance(raiz, dict) and "Sucesso" in raiz and "Falso" in raiz["Sucesso"]:
                raise ErroINE(raiz["Sucesso"]["Falso"][0].get("Msg", "erro desconhecido"))
            return dados
        except ErroINE:
            raise
        except urllib.error.HTTPError as e:  # respondeu, mas com erro HTTP
            rede_ok()
            ultimo = e
            print(f"  tentativa {n + 1} falhou: {e}", file=sys.stderr)
            continue
        except Exception as e:  # sem ligação, tempo esgotado…
            rede_falhou()
            ultimo = e
            print(f"  tentativa {n + 1} falhou: {e}", file=sys.stderr)
            if not sem_tempo():
                time.sleep(5 * (n + 1))
    raise RuntimeError(f"o INE não respondeu: {ultimo}")


def trimestre(rotulo: str):
    m = re.search(r"(\d)\s*\.?\s*[ºo°]?\s*trimestre\s*(?:de\s*)?(\d{4})", rotulo, re.I)
    if m:
        return f"{m.group(2)}-Q{m.group(1)}"
    m = re.search(r"(\d{4})\s*[-\s]?T\s*(\d)", rotulo, re.I)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"
    return None


def registos(dados, codigo: str) -> dict:
    raiz = dados[0] if isinstance(dados, list) else dados
    out = {}
    for rotulo, linhas in (raiz.get("Dados") or {}).items():
        q = trimestre(rotulo)
        if not q:
            continue
        for l in linhas:
            if l.get("geocod") not in (codigo, None):
                continue
            cat = l.get("dim_3_t") or l.get("dim_3") or "Total"
            if not (str(cat).lower().startswith("total") or l.get("dim_3") == "T"):
                continue
            try:
                out[q] = float(str(l.get("valor", "")).replace(" ", "").replace(",", "."))
            except ValueError:
                pass
    return out


def quarter_de_codigo(codigo: str):
    """'S5A20261' -> '2026-Q1' (aceita também '...202601')."""
    m = re.match(r"^S\dA(\d{4})0?([1-4])$", codigo)
    return f"{m.group(1)}-Q{m.group(2)}" if m else None


def ficha(indicador: str, timeout: int = 45) -> str:
    if ine_em_baixo():
        raise INEEmBaixo("o INE não está a responder")
    url = INE_URL.replace("pindica.jsp", "pindicaMeta.jsp") + "?" + urllib.parse.urlencode({"varcd": indicador, "lang": "PT"})
    req = urllib.request.Request(url, headers=CABECALHOS)
    try:
        texto = ler_url(req, limite=timeout, pausa=min(30, timeout)).decode("utf-8", "replace")
        rede_ok()
        return texto
    except urllib.error.HTTPError:
        rede_ok()
        raise
    except Exception:
        rede_falhou()
        raise


def codigos_da_ficha(indicador: str = INDICADOR) -> list:
    """Tenta ler da ficha (metainformação) do indicador os códigos de período que existem."""
    try:
        texto = ficha(indicador)
    except Exception as e:
        print(f"Ficha do indicador indisponível: {e}", file=sys.stderr)
        return []
    codigos = sorted(set(re.findall(r"S\dA\d{4}0?[1-4](?!\d)", texto)))
    codigos = [c for c in codigos if quarter_de_codigo(c)]
    print(f"Ficha do indicador: {len(codigos)} códigos de trimestre encontrados" + (f" ({codigos[0]} a {codigos[-1]})" if codigos else ""))
    return codigos


def tem_dados(dados) -> bool:
    raiz = dados[0] if isinstance(dados, list) else dados
    return bool(raiz.get("Dados"))


def descobrir_formato(hoje: datetime, indicador: str = INDICADOR):
    """Experimenta formatos de código num trimestre já publicado até o INE aceitar um."""
    y, t = hoje.year, (hoje.month - 1) // 3 + 1
    candidatos_q = []
    for _ in range(6):  # recua até 6 trimestres (o mais recente pode ainda não estar publicado)
        t -= 1
        if t < 1:
            t, y = 4, y - 1
        candidatos_q.append((y, t))
    formatos = [lambda p, y, t: f"{p}{y}{t}", lambda p, y, t: f"{p}{y}0{t}"]
    prefixos = ["S5A", "S3A", "S4A", "S6A", "S2A", "S1A", "S7A", "S8A", "S9A"]
    for (yy, tt) in candidatos_q[1:3]:  # trimestres quase certamente já publicados
        for f in formatos:
            for p in prefixos:
                if sem_tempo() or ine_em_baixo():
                    return None
                codigo = f(p, yy, tt)
                try:
                    dados = pedir({"varcd": indicador, "Dim1": codigo, "Dim2": "PT"}, tentativas=1)
                    if tem_dados(dados):
                        print(f"Formato aceite pelo INE: {codigo}")
                        return lambda y, t, f=f, p=p: f(p, y, t)
                except ErroINE:
                    pass
                except Exception as e:
                    print(f"  {codigo}: {e}", file=sys.stderr)
    return None


def lista_trimestres(hoje: datetime, formato) -> list:
    y, t = hoje.year, (hoje.month - 1) // 3 + 1
    out = []
    for _ in range(TRIMESTRES + 2):
        out.append(formato(y, t))
        t -= 1
        if t < 1:
            t, y = 4, y - 1
    return out  # do mais recente para o mais antigo


def categoria_total(l) -> bool:
    cat = l.get("dim_3_t") or l.get("dim_3") or "Total"
    return str(cat).lower().startswith("total") or l.get("dim_3") == "T"


def recolher(hoje: datetime, indicador: str = INDICADOR, codigos=None, aceitar=categoria_total) -> dict:
    """Pede um período de cada vez (todas as localidades de uma vez) e junta os que interessam."""
    quantos = LEVE_TRIMESTRES if INCREMENTAL and indicador in FORMATOS else TRIMESTRES
    if codigos is None:
        # no modo leve, se o formato já é conhecido, nem precisa da ficha
        codigos = codigos_guardados(indicador, hoje, quantos + 2) if INCREMENTAL else []
        if not codigos:
            codigos = sorted(codigos_da_ficha(indicador), reverse=True)[:quantos]
        if not codigos:
            codigos = codigos_guardados(indicador, hoje, quantos + 2)
            if not codigos:
                formato = descobrir_formato(hoje, indicador)
                if not formato:
                    raise RuntimeError("não foi possível descobrir o formato dos trimestres aceite pelo INE")
                codigos = lista_trimestres(hoje, formato)

    alvo = {c: nome for nome, cs in LOCALIDADES.items() for c in cs}
    nomes = {n.lower(): n for n in LOCALIDADES}
    por_local = {n: {} for n in LOCALIDADES}
    obtidos, falhas_seguidas = 0, 0
    for codigo in codigos:
        if ine_em_baixo():
            break
        if sem_tempo():
            print(f"{indicador}: sem tempo, a gravar o que já tenho.", file=sys.stderr)
            break
        try:
            dados = pedir({"varcd": indicador, "Dim1": codigo})
        except ErroINE as e:
            falhas_seguidas += 1
            print(f"{codigo}: INE diz '{e}'", file=sys.stderr)
            if obtidos and falhas_seguidas >= 3:
                break  # chegámos ao início da série
            continue
        falhas_seguidas = 0
        registar_formato(indicador, codigo)
        raiz = dados[0] if isinstance(dados, list) else dados
        n_antes = sum(len(v) for v in por_local.values())
        for rotulo, linhas in (raiz.get("Dados") or {}).items():
            q = trimestre(rotulo) or quarter_de_codigo(codigo) or (re.search(r"\d{4}", rotulo) or re.search(r"\d{4}", codigo)).group(0)
            for l in linhas:
                nome = alvo.get(l.get("geocod"))
                if not nome and l.get("geocod") == "PT":
                    nome = "Portugal"
                if not nome:
                    continue
                if not aceitar(l):
                    continue
                try:
                    por_local[nome][q] = float(str(l.get("valor", "")).replace(" ", "").replace(",", "."))
                except ValueError:
                    pass
        novos = sum(len(v) for v in por_local.values()) - n_antes
        if novos:
            obtidos += 1
        print(f"{codigo}: {novos} valores")
        time.sleep(1)
    return por_local


def gravar(ficheiro: str, por_local: dict, extra: dict, hoje: datetime) -> bool:
    antigo = {}
    if os.path.exists(ficheiro):
        with open(ficheiro, encoding="utf-8") as f:
            antigo = json.load(f)
    antigos = {nome: dict(zip(antigo.get("periodos", []), serie)) for nome, serie in (antigo.get("valores") or {}).items()}
    if antigo.get("frequencia", "trimestral") == extra.get("frequencia"):
        for nome in LOCALIDADES:  # juntar ao que já havia: os valores novos substituem, os antigos que faltam ficam
            velhos = {k: v for k, v in (antigos.get(nome) or {}).items() if v is not None}
            por_local[nome] = {**velhos, **(por_local.get(nome) or {})}
    periodos = sorted({q for s in por_local.values() for q in s})
    novo = {**extra, "formatos": dict(FORMATOS), "periodos": periodos,
            "valores": {n: [por_local.get(n, {}).get(q) for q in periodos] for n in LOCALIDADES}}
    chaves = [k for k in novo if k not in ("atualizado", "formatos")]
    if {k: antigo.get(k) for k in chaves} == {k: novo[k] for k in chaves} and antigo.get("formatos"):
        print(f"{os.path.basename(ficheiro)}: sem alterações.")
        return True
    novo["atualizado"] = hoje.strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(ficheiro), exist_ok=True)
    with open(ficheiro, "w", encoding="utf-8") as f:
        json.dump(novo, f, ensure_ascii=False, indent=1)
    print(f"{os.path.basename(ficheiro)}: gravado, {len(periodos)} períodos, último {periodos[-1]}.")
    return True


def atualizar_casas(hoje: datetime) -> bool:
    try:
        por_local = recolher(hoje)
    except Exception as e:
        print(f"Casas: falhou ({e})", file=sys.stderr)
        return False
    for nome, serie in por_local.items():
        print(f"Casas, {nome}: {len(serie)} trimestres")
    if not any(por_local.values()):
        print("Casas: nenhum dado obtido; o ficheiro fica como estava.", file=sys.stderr)
        return False
    return gravar(FICHEIRO, por_local, {"fonte": "INE, indicador 0012234 (valor mediano das vendas por m², últimos 12 meses)",
                                        "indicador": INDICADOR, "frequencia": "trimestral"}, hoje)


# ---------------------------------------------------------------- rendas
RENDAS = os.path.join(PASTA, "rendas.json")
RENDAS_ANUAL = "0014711"


def nome_indicador(texto: str) -> str:
    try:
        j = json.loads(texto)
        raiz = j[0] if isinstance(j, list) else j
        return str(raiz.get("IndicadorNome") or raiz.get("IndicadorDsg") or "")
    except Exception:
        m = re.search(r'"IndicadorNome"\s*:\s*"([^"]+)"', texto)
        return m.group(1) if m else ""


def tipo_rendas(nome: str):
    """Classifica os indicadores trimestrais de rendas (€/m², últimos 12 meses) do INE."""
    n = nome.lower()
    if not ("rendas" in n and "12 meses" in n and "trimestral" in n and "€" in n):
        return None
    if any(x in n for x in ("tipologia", "setor", "locatários", "n.º")):
        return None
    if "100 000" in n:
        return "historico"      # 2020 até hoje, só concelhos com mais de 100 mil habitantes
    if "geografia 2025" in n:
        return "concelhos"      # todos os concelhos, só o trimestre mais recente
    if "nuts - 2024" in n:
        return "regioes"        # país e regiões, 2020 até hoje
    return None


def procurar_indicadores_rendas(guardados: dict) -> dict:
    """Procura, pelas fichas do INE, os códigos dos três indicadores de rendas (uma só passagem)."""
    if guardados and INCREMENTAL and all(guardados.get(k) for k in ("historico", "concelhos")):
        return guardados
    achados = dict(guardados or {})
    candidatos = [f"{c:07d}" for c in range(14700, 14780)] + [f"{c:07d}" for c in range(14600, 14700)]
    for c in candidatos:
        if sem_tempo() or ine_em_baixo() or all(achados.get(k) for k in ("historico", "concelhos", "regioes")):
            break
        if c in achados.values():
            continue
        try:
            nome = nome_indicador(ficha(c, timeout=30))
        except Exception:
            continue
        t = tipo_rendas(nome)
        if t and not achados.get(t):
            achados[t] = c
            print(f"Rendas ({t}): indicador {c}: {nome}")
        time.sleep(0.3)
    return achados


def mediana(l) -> bool:
    cat = str(l.get("dim_3_t") or l.get("dim_3") or "total").lower()
    return "2.º quartil" in cat or "2º quartil" in cat or "mediana" in cat or cat.startswith("total") or l.get("dim_3") == "T"


def atualizar_rendas(hoje: datetime) -> bool:
    antigo = {}
    if os.path.exists(RENDAS):
        with open(RENDAS, encoding="utf-8") as f:
            antigo = json.load(f)
    guardados = antigo.get("indicadores") if antigo.get("frequencia") == "trimestral" else {}
    cods = procurar_indicadores_rendas(guardados or {})
    por_local = {n: {} for n in LOCALIDADES}
    # ordem de prioridade: histórico dos concelhos grandes, depois país/regiões, depois o trimestre mais recente de todos
    for tipo in ("concelhos", "regioes", "historico"):
        cod = cods.get(tipo)
        if not cod:
            continue
        try:
            dados = recolher(hoje, cod)
            for nome, serie in dados.items():
                por_local[nome].update(serie)
            print(f"Rendas ({tipo}, {cod}): " + ", ".join(f"{n} {len(v)}" for n, v in dados.items() if v))
        except Exception as e:
            print(f"Rendas ({tipo}): falhou ({e})", file=sys.stderr)
    extra = {"fonte": "INE, renda mediana por m² dos novos contratos, últimos 12 meses (Metodologia 2026)",
             "indicadores": {k: v for k, v in cods.items() if v}, "frequencia": "trimestral"}
    if not any(por_local.values()):
        # último recurso: versão anual
        anos = [f"S7A{a}" for a in range(hoje.year, hoje.year - 9, -1)]
        try:
            por_local = recolher(hoje, RENDAS_ANUAL, codigos=anos, aceitar=mediana)
            extra = {"fonte": "INE, indicador 0014711 (renda mediana por m² dos novos contratos, anual)",
                     "indicador": RENDAS_ANUAL, "frequencia": "anual"}
        except Exception as e:
            print(f"Rendas (anual): falhou ({e})", file=sys.stderr)
    if not any(por_local.values()):
        print("Rendas: nenhum dado obtido; o ficheiro fica como estava.", file=sys.stderr)
        return False
    return gravar(RENDAS, por_local, extra, hoje)


# ================================================================ séries nacionais (oferta e procura)
MESES_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def periodo(rotulo: str, codigo: str):
    """Converte o rótulo (ou o código) de um período do INE em 'AAAA-Qn', 'AAAA-MM' ou 'AAAA'."""
    q = trimestre(rotulo)
    if q:
        return q
    r = rotulo.lower()
    for i, m in enumerate(MESES_PT):
        mm = re.search(m + r"\s*(?:de\s*)?(\d{4})", r)
        if mm:
            return f"{mm.group(1)}-{i + 1:02d}"
    m = re.match(r"^S\dA(\d{4})(\d{2})$", codigo)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{m.group(2)}"
    q = quarter_de_codigo(codigo)
    if q:
        return q
    m = re.search(r"(\d{4})", rotulo) or re.search(r"(\d{4})", codigo)
    return m.group(1) if m else None


def codigos_periodo_ficha(indicador: str) -> list:
    try:
        texto = ficha(indicador)
    except Exception as e:
        print(f"{indicador}: ficha indisponível ({e})", file=sys.stderr)
        return []
    return sorted(set(re.findall(r"S\dA\d{4}(?:\d{1,2})?(?!\d)", texto)), reverse=True)


def descobrir_codigos(indicador: str, hoje: datetime) -> list:
    """Sem ficha: experimenta formatos trimestrais e mensais num período já publicado."""
    prefixos = ["S3A", "S5A", "S4A", "S6A", "S2A", "S1A", "S7A"]
    y, m = hoje.year, hoje.month
    for recuo in (4, 6):  # meses atrás
        if sem_tempo() or ine_em_baixo():
            return []
        yy, mm = y, m - recuo
        while mm < 1:
            mm += 12
            yy -= 1
        tt = (mm - 1) // 3 + 1
        for p in prefixos:
            for fmt_, gera in (("mensal", lambda yy, k: f"{p}{yy}{k:02d}"), ("trimestral", lambda yy, k: f"{p}{yy}{k}")):
                k = mm if fmt_ == "mensal" else tt
                cod = gera(yy, k)
                try:
                    dados = pedir({"varcd": indicador, "Dim1": cod, "Dim2": "PT"}, tentativas=1)
                    if tem_dados(dados):
                        print(f"{indicador}: formato {fmt_} aceite ({cod})")
                        out, a, b = [], y, (m if fmt_ == "mensal" else (m - 1) // 3 + 1)
                        for _ in range(120 if fmt_ == "mensal" else 44):
                            out.append(gera(a, b))
                            b -= 1
                            if b < 1:
                                b, a = (12 if fmt_ == "mensal" else 4), a - 1
                        return out
                except Exception:
                    pass
    return []


def serie_nacional(indicador: str, hoje: datetime, aceitar=categoria_total, maximo: int = 120) -> dict:
    """Valores de Portugal (PT) por período, pedidos um período de cada vez."""
    mensal = FORMATOS.get(indicador, {}).get("tipo") == "mensal"
    if INCREMENTAL and indicador in FORMATOS:
        codigos = codigos_guardados(indicador, hoje, LEVE_MESES if mensal else LEVE_TRIMESTRES)
    else:
        codigos = codigos_periodo_ficha(indicador)[:maximo] or codigos_guardados(indicador, hoje, maximo if mensal else 44) or descobrir_codigos(indicador, hoje)
    if not codigos:
        raise RuntimeError(f"{indicador}: não foi possível descobrir os códigos dos períodos")
    out, falhas, obtidos = {}, 0, 0
    for c in codigos:
        if ine_em_baixo():
            break
        if sem_tempo():
            print(f"{indicador}: sem tempo, a gravar o que já tenho.", file=sys.stderr)
            break
        try:
            dados = pedir({"varcd": indicador, "Dim1": c, "Dim2": "PT"})
        except ErroINE:
            falhas += 1
            if obtidos and falhas >= 3:
                break
            continue
        falhas = 0
        registar_formato(indicador, c)
        raiz = dados[0] if isinstance(dados, list) else dados
        for rotulo, linhas in (raiz.get("Dados") or {}).items():
            per = periodo(rotulo, c)
            for l in linhas:
                if l.get("geocod") not in ("PT", None) or not aceitar(l):
                    continue
                try:
                    out[per] = float(str(l.get("valor", "")).replace(" ", "").replace(",", "."))
                    obtidos += 1
                except ValueError:
                    pass
        time.sleep(1)
    print(f"{indicador}: {len(out)} períodos")
    return out


def para_trimestres(serie: dict) -> dict:
    """Soma meses em trimestres completos; se já for trimestral, devolve igual."""
    if not serie or all("-Q" in k for k in serie):
        return serie
    soma, conta = {}, {}
    for k, v in serie.items():
        m = re.match(r"^(\d{4})-(\d{2})$", k)
        if not m:
            continue
        q = f"{m.group(1)}-Q{(int(m.group(2)) - 1) // 3 + 1}"
        soma[q] = soma.get(q, 0) + v
        conta[q] = conta.get(q, 0) + 1
    return {q: v for q, v in soma.items() if conta[q] == 3}


def procurar_indicador(nome_curto: str, teste, intervalos, guardado=None):
    if guardado and INCREMENTAL and guardado in FORMATOS:
        return guardado  # já conhecido e já funcionou: não é preciso confirmar
    candidatos = ([guardado] if guardado else []) + [f"{c:07d}" for a, b in intervalos for c in range(a, b)]
    vistos, erros = set(), 0
    for c in candidatos:
        if sem_tempo() or ine_em_baixo():
            break
        if c in vistos:
            continue
        vistos.add(c)
        try:
            nome = nome_indicador(ficha(c, timeout=30))
            erros = 0
        except Exception:
            erros += 1
            if erros >= 5:
                break
            continue
        if nome and teste(nome.lower()):
            print(f"{nome_curto}: indicador encontrado, {c}: {nome}")
            return c
        time.sleep(0.2)
    print(f"{nome_curto}: indicador não encontrado.", file=sys.stderr)
    return None


def ler(ficheiro: str) -> dict:
    if os.path.exists(ficheiro):
        with open(ficheiro, encoding="utf-8") as f:
            return json.load(f)
    return {}


def gravar_simples(ficheiro: str, series: dict, extra: dict, hoje: datetime) -> bool:
    antigo = ler(ficheiro)
    ant = {n: dict(zip(antigo.get("periodos", []), v)) for n, v in (antigo.get("valores") or {}).items()}
    if antigo.get("frequencia") == extra.get("frequencia"):
        for n in list(series):  # juntar ao que já havia: os valores novos substituem, os antigos que faltam ficam
            velhos = {k: v for k, v in (ant.get(n) or {}).items() if v is not None}
            series[n] = {**velhos, **(series.get(n) or {})}
    if not any(series.values()):
        return False
    periodos = sorted({k for s_ in series.values() for k in s_})
    novo = {**extra, "formatos": dict(FORMATOS), "periodos": periodos, "valores": {n: [series[n].get(p) for p in periodos] for n in series}}
    chaves = [k for k in novo if k not in ("atualizado", "formatos")]
    if {k: antigo.get(k) for k in chaves} == {k: novo[k] for k in chaves} and antigo.get("formatos"):
        print(f"{os.path.basename(ficheiro)}: sem alterações.")
        return True
    novo["atualizado"] = hoje.strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(ficheiro), exist_ok=True)
    with open(ficheiro, "w", encoding="utf-8") as f:
        json.dump(novo, f, ensure_ascii=False, indent=1)
    print(f"{os.path.basename(ficheiro)}: gravado, {len(periodos)} períodos, último {periodos[-1]}.")
    return True


CONSTRUCAO = os.path.join(PASTA, "construcao.json")
TRANSACOES = os.path.join(PASTA, "transacoes.json")
MIGRACAO = os.path.join(PASTA, "migracao.json")


def e_licenciados(n):
    return "fogos licenciados" in n and "construções novas" in n and "habitação familiar" in n and "nuts - 2024" in n \
        and ("trimestral" in n or "mensal" in n) and "pavimento" not in n and "entidade" not in n


def e_concluidos(n):
    return "fogos concluídos" in n and "construções novas" in n and "habitação familiar" in n and "nuts - 2024" in n \
        and "trimestral" in n and "pavimento" not in n and "entidade" not in n and "tipologia" not in n


def e_vendas(n):
    return "vendas de alojamentos familiares" in n and "n.º" in n and "trimestral" in n and "nuts - 2024" in n \
        and "12 meses" not in n and "domicílio" not in n and "setor" not in n and "quartis" not in n


def atualizar_construcao(hoje: datetime) -> bool:
    antigo = ler(CONSTRUCAO)
    cod_l = procurar_indicador("Fogos licenciados", e_licenciados, [(12090, 12110), (12760, 12800), (12060, 12090)], antigo.get("indicador_licenciados"))
    cod_c = procurar_indicador("Fogos concluídos", e_concluidos, [(12770, 12790), (12790, 12810)], antigo.get("indicador_concluidos") or "0012778")
    series = {"licenciados": {}, "concluidos": {}}
    for nome, cod in (("licenciados", cod_l), ("concluidos", cod_c)):
        if not cod:
            continue
        try:
            series[nome] = para_trimestres(serie_nacional(cod, hoje))
        except Exception as e:
            print(f"{nome}: falhou ({e})", file=sys.stderr)
    return gravar_simples(CONSTRUCAO, series, {"fonte": "INE, Estatísticas da construção (fogos em construções novas para habitação familiar)",
                                               "indicador_licenciados": cod_l, "indicador_concluidos": cod_c, "frequencia": "trimestral"}, hoje)


def atualizar_transacoes(hoje: datetime) -> bool:
    antigo = ler(TRANSACOES)
    cod = procurar_indicador("Vendas de casas", e_vendas, [(12225, 12260), (12200, 12225), (12260, 12300)], antigo.get("indicador"))
    series = {"vendas": {}}
    if cod:
        try:
            series["vendas"] = para_trimestres(serie_nacional(cod, hoje))
        except Exception as e:
            print(f"vendas: falhou ({e})", file=sys.stderr)
    return gravar_simples(TRANSACOES, series, {"fonte": "INE, Estatísticas de preços da habitação (n.º de alojamentos familiares vendidos)",
                                               "indicador": cod, "frequencia": "trimestral"}, hoje)


EUROSTAT = os.environ.get("EUROSTAT_URL", "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/")


def eurostat(dataset: str, filtros: dict, preferir: dict) -> dict:
    url = EUROSTAT + dataset + "?" + urllib.parse.urlencode({"format": "JSON", "lang": "EN", **filtros})
    req = urllib.request.Request(url, headers=CABECALHOS)
    j = json.loads(ler_url(req, limite=120).decode("utf-8"))
    ids, size = j["id"], j["size"]
    stride, acc = [0] * len(ids), 1
    for k in range(len(ids) - 1, -1, -1):
        stride[k], acc = acc, acc * size[k]
    base = 0
    for k, dim in enumerate(ids):
        if dim == "time":
            continue
        idx = j["dimension"][dim]["category"]["index"]
        cod = next((c for c in preferir.get(dim, []) if c in idx), None) or min(idx, key=idx.get)
        base += idx[cod] * stride[k]
    tk, tidx = ids.index("time"), j["dimension"]["time"]["category"]["index"]
    vals = j["value"]
    out = {}
    for t, pos in tidx.items():
        v = vals.get(str(base + pos * stride[tk])) if isinstance(vals, dict) else vals[base + pos * stride[tk]]
        if v is not None:
            out[t] = float(v)
    return out


def atualizar_migracao(hoje: datetime) -> bool:
    series = {"imigrantes": {}, "emigrantes": {}}
    pref = {"citizen": ["TOTAL"], "age": ["TOTAL"], "sex": ["T"], "agedef": ["COMPLET", "REACH"], "unit": ["NR"]}
    for nome, ds in (("imigrantes", "migr_imm1ctz"), ("emigrantes", "migr_emi1ctz")):
        for filtros in ({"geo": "PT", "citizen": "TOTAL", "age": "TOTAL", "sex": "T"}, {"geo": "PT", "sex": "T", "citizen": "TOTAL"}):
            try:
                series[nome] = eurostat(ds, filtros, pref)
                print(f"{nome}: {len(series[nome])} anos (Eurostat {ds})")
                break
            except Exception as e:
                print(f"{nome}: Eurostat falhou com {filtros} ({e})", file=sys.stderr)
    return gravar_simples(MIGRACAO, series, {"fonte": "Eurostat (migr_imm1ctz, migr_emi1ctz), a partir de dados do INE", "frequencia": "anual"}, hoje)


# ================================================================ mercados e combustíveis
MERCADOS = os.path.join(PASTA, "mercados.json")
YAHOO = os.environ.get("YAHOO_URL", "https://query1.finance.yahoo.com/v8/finance/chart/")
STOOQ = os.environ.get("STOOQ_URL", "https://stooq.com/q/d/l/")
BOLETIM = os.environ.get("BOLETIM_URL", "https://energy.ec.europa.eu/document/download/906e60ca-8b6a-44e7-8589-652854d2fd3f_en?filename=Weekly_Oil_Bulletin_Prices_History_maticni_4web.xlsx")

# nome -> (símbolo Yahoo, símbolo Stooq alternativo, descrição, unidade)
ATIVOS = {
    "brent":     ("BZ=F",      "cb.f",     "Petróleo Brent",            "USD/barril"),
    "gas":       ("TTF=F",     None,       "Gás natural europeu (TTF)", "EUR/MWh"),
    "ouro":      ("GC=F",      "xauusd",   "Ouro",                      "USD/onça"),
    "prata":     ("SI=F",      "xagusd",   "Prata",                     "USD/onça"),
    "eurusd":    ("EURUSD=X",  "eurusd",   "Euro/dólar",                "USD por EUR"),
    "psi":       ("PSI20.LS",  None,       "PSI (Lisboa)",              "pontos"),
    "stoxx50":   ("^STOXX50E", None,       "Euro Stoxx 50",             "pontos"),
    "sp500":     ("^GSPC",     "^spx",     "S&P 500 (EUA)",             "pontos"),
    "nasdaq":    ("^IXIC",     "^ndq",     "Nasdaq (EUA)",              "pontos"),
    "mundo":     ("URTH",      "urth.us",  "MSCI World (ETF)",          "USD"),
    "iwda":      ("IWDA.AS",   None,       "iShares Core MSCI World (IWDA, Amesterdão)", "EUR"),
    "eunl":      ("EUNL.DE",   None,       "iShares Core MSCI World (EUNL, Xetra)",      "EUR"),
    "btc":       ("BTC-EUR",   None,       "Bitcoin",                   "EUR"),
    "eth":       ("ETH-EUR",   None,       "Ethereum",                  "EUR"),
}


def yahoo(simbolo: str) -> dict:
    url = YAHOO + urllib.parse.quote(simbolo) + "?" + urllib.parse.urlencode({"range": "10y", "interval": "1d"})
    req = urllib.request.Request(url, headers={**CABECALHOS, "Accept": "application/json"})
    j = json.loads(ler_url(req, limite=60).decode("utf-8"))
    r = j["chart"]["result"][0]
    ts, fecho = r["timestamp"], r["indicators"]["quote"][0]["close"]
    return {datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"): round(v, 4) for t, v in zip(ts, fecho) if v is not None}


def stooq(simbolo: str) -> dict:
    url = STOOQ + "?" + urllib.parse.urlencode({"s": simbolo, "i": "d"})
    texto = ler_url(urllib.request.Request(url, headers=CABECALHOS), limite=60).decode("utf-8", "replace")
    out = {}
    for linha in texto.strip().splitlines()[1:]:
        p = linha.split(",")
        if len(p) >= 5:
            try:
                out[p[0]] = round(float(p[4]), 4)
            except ValueError:
                pass
    corte = f"{datetime.now(timezone.utc).year - 10}-01-01"
    return {k: v for k, v in out.items() if k >= corte}


def _data(cel):
    if isinstance(cel, datetime):
        return cel.strftime("%Y-%m-%d")
    if isinstance(cel, (int, float)) and 30000 < cel < 60000:   # data do Excel como número
        from datetime import timedelta
        return (datetime(1899, 12, 30) + timedelta(days=float(cel))).strftime("%Y-%m-%d")
    if isinstance(cel, str):
        t = cel.strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", t):
            return t[:10]
        m = re.match(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$", t)
        if m:
            return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace("\xa0", "").replace(" ", "").replace(",", ""))
        except ValueError:
            return None
    return None


def _produto(txt: str):
    t = txt.lower()
    if re.search(r"euro.?super|euro.?95|super.?95|\b95\b|gasoline|petrol", t):
        return "gasolina95"
    if re.search(r"diesel|gas.?oil|gasoil|gasóleo|gazole", t) and not re.search(r"heating|chauffage|heiz", t):
        return "gasoleo"
    return None


def _e_portugal(txt: str) -> bool:
    return bool(re.search(r"(^|[^a-z])pt([^a-z]|$)|portugal", txt.lower()))


def combustiveis() -> dict:
    """Preços médios semanais em Portugal (com impostos), €/litro, do boletim da Comissão Europeia."""
    import io
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("falta o módulo openpyxl")
    dados = ler_url(urllib.request.Request(BOLETIM, headers=CABECALHOS), limite=180, pausa=60)
    print(f"Combustíveis: ficheiro do boletim com {len(dados) // 1024} kB")
    wb = openpyxl.load_workbook(io.BytesIO(dados), read_only=True, data_only=True)
    diagnostico = []
    folhas = sorted(wb.worksheets, key=lambda ws: 0 if re.search(r"with.?tax|avec|mit", ws.title, re.I) else 1)
    for ws in folhas:
        linhas = []
        for n, row in enumerate(ws.iter_rows(values_only=True)):
            linhas.append(list(row))
            if n >= 3000:
                break
        # primeira linha com uma data na 1.ª coluna = início dos dados
        ini = next((k for k, r in enumerate(linhas[:60]) if r and _data(r[0])), None)
        if ini is None:
            diagnostico.append(f"folha '{ws.title}': sem datas na 1.ª coluna")
            continue
        cab = linhas[:ini]
        ncol = max((len(r) for r in linhas[:ini + 5]), default=0)
        # cabeçalho de cada coluna = texto das linhas de cima (preenchendo células fundidas para a direita)
        textos = [""] * ncol
        for r in cab:
            ult = ""
            for k in range(ncol):
                v = r[k] if k < len(r) else None
                if v not in (None, ""):
                    ult = str(v)
                elif k == 0:
                    ult = ""
                textos[k] += " " + (str(v) if v not in (None, "") else ult)
        cols = {}
        for k, t in enumerate(textos):
            if k == 0 or not _e_portugal(t):
                continue
            prod = _produto(t)
            if prod and prod not in cols and not re.search(r"without|sans|ohne|excl", t, re.I):
                cols[prod] = k
        if not cols:
            amostra = [t.strip()[:40] for t in textos if t.strip()][:8]
            diagnostico.append(f"folha '{ws.title}': cabeçalhos {amostra}")
            continue
        out = {"gasolina95": {}, "gasoleo": {}}
        for r in linhas[ini:]:
            d = _data(r[0]) if r else None
            if not d:
                continue
            for prod, k in cols.items():
                v = _num(r[k]) if k < len(r) else None
                if v and v > 0:
                    out[prod][d] = round(v / 1000 if v > 50 else v, 4)   # o boletim vem em €/1000 litros
        if any(out.values()):
            print(f"Combustíveis: folha '{ws.title}', colunas {cols}")
            return out
    raise RuntimeError("não encontrei os preços de Portugal no boletim. " + " | ".join(diagnostico[:6]))


def atualizar_mercados(hoje: datetime) -> bool:
    antigo = ler(MERCADOS)
    hoje_txt = hoje.strftime("%Y-%m-%d")
    series = {}
    for nome, (ysym, ssym, desc, unid) in ATIVOS.items():
        valores, fonte = {}, None
        try:
            valores, fonte = yahoo(ysym), f"Yahoo Finance ({ysym})"
        except Exception as e:
            print(f"Mercados, {desc}: Yahoo falhou ({e})", file=sys.stderr)
            if ssym:
                try:
                    valores, fonte = stooq(ssym), f"Stooq ({ssym})"
                except Exception as e2:
                    print(f"Mercados, {desc}: Stooq falhou ({e2})", file=sys.stderr)
        valores = {d: v for d, v in valores.items() if d < hoje_txt}   # só fechos de dias já terminados
        velho = (antigo.get("series") or {}).get(nome) or {}
        juntos = {**dict(zip(velho.get("datas", []), velho.get("valores", []))), **valores}
        if juntos:
            datas = sorted(juntos)
            series[nome] = {"nome": desc, "unidade": unid, "fonte": fonte or velho.get("fonte"),
                            "datas": datas, "valores": [juntos[d] for d in datas]}
            print(f"Mercados, {desc}: {len(valores)} dias novos/atualizados, último {datas[-1]}")
    try:
        comb = combustiveis()
        comb_out = {"fonte": "Comissão Europeia, Weekly Oil Bulletin (preços com impostos)"}
        for nome, serie in comb.items():
            datas = sorted(serie)
            comb_out[nome] = {"datas": datas, "valores": [serie[d] for d in datas]}
        print(f"Combustíveis: gasolina {len(comb['gasolina95'])} semanas, gasóleo {len(comb['gasoleo'])} semanas")
    except Exception as e:
        print(f"Combustíveis: falhou ({e})", file=sys.stderr)
        comb_out = antigo.get("combustiveis")
    novo = {"series": series, "combustiveis": comb_out}
    if not series and not comb_out:
        return False
    if {k: antigo.get(k) for k in novo} == novo:
        print("mercados.json: sem alterações.")
        return True
    novo["atualizado"] = hoje.strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(PASTA, exist_ok=True)
    with open(MERCADOS, "w", encoding="utf-8") as f:
        json.dump(novo, f, ensure_ascii=False, separators=(",", ":"))
    print("mercados.json: gravado.")
    return True



# ================================================================ previsões do FMI
PREVISOES = os.path.join(PASTA, "previsoes.json")
FMI_URL = os.environ.get("FMI_URL", "https://www.imf.org/external/datamapper/api/v1/")
FMI_IND = {
    "PCPIPCH":     "Inflação (média anual, %)",
    "NGDP_RPCH":   "Crescimento real do PIB (%)",
    "LUR":         "Taxa de desemprego (%)",
    "BCA_NGDPD":   "Balança corrente (% do PIB)",
    "GGXWDG_NGDP": "Dívida pública bruta (% do PIB)",
    "GGXCNL_NGDP": "Saldo orçamental (% do PIB)",
}


def atualizar_previsoes(hoje: datetime) -> bool:
    antigo = ler(PREVISOES)
    valores = dict((antigo.get("fmi") or {}).get("valores") or {})
    ok = 0
    for ind in FMI_IND:
        try:
            req = urllib.request.Request(FMI_URL + ind + "/PRT", headers={**CABECALHOS, "Accept": "application/json"})
            j = json.loads(ler_url(req, limite=60).decode("utf-8"))
            serie = ((j.get("values") or {}).get(ind) or {}).get("PRT") or {}
            serie = {str(a): round(float(v), 2) for a, v in serie.items() if v is not None and 2000 <= int(a) <= hoje.year + 6}
            if serie:
                valores[ind] = serie
                ok += 1
                print(f"FMI, {FMI_IND[ind]}: {min(serie)}–{max(serie)}")
        except Exception as e:
            print(f"FMI, {ind}: falhou ({e})", file=sys.stderr)
    if not valores:
        return False
    novo = {"fmi": {"fonte": "FMI, World Economic Outlook (publicado em abril e outubro)", "valores": valores}}
    if (antigo.get("fmi") or {}).get("valores") == valores:
        print("previsoes.json: sem alterações.")
        return True
    novo["fmi"]["obtido"] = hoje.strftime("%Y-%m-%d")
    os.makedirs(PASTA, exist_ok=True)
    with open(PREVISOES, "w", encoding="utf-8") as f:
        json.dump(novo, f, ensure_ascii=False, indent=1)
    print(f"previsoes.json: gravado ({ok} indicadores).")
    return True



# ================================================================ IPC (BPstat, Banco de Portugal)
IPC = os.path.join(PASTA, "ipc.json")
BPSTAT_URL = os.environ.get("BPSTAT_URL", "https://bpstat.bportugal.pt/data/v1")
IPC_SERIE = 5721524  # IPC total, taxa de variação homóloga, mensal (dados do INE)


def bpstat(caminho: str):
    req = urllib.request.Request(BPSTAT_URL + caminho, headers={**CABECALHOS, "Accept": "application/json"})
    return json.loads(ler_url(req, limite=60).decode("utf-8"))


def jsonstat_mensal(j: dict) -> dict:
    ids = j.get("id") or list((j.get("dimension") or {}).keys())
    dims = j["dimension"]
    tam = j.get("size") or [len(dims[d]["category"]["index"]) for d in ids]
    ti = next(k for k, d in enumerate(ids) if re.search(r"date|time|period", d, re.I))
    idx = dims[ids[ti]]["category"]["index"]
    datas = idx if isinstance(idx, list) else sorted(idx, key=idx.get)
    passo = 1
    for t in tam[ti + 1:]:
        passo *= t
    val = j.get("value") or []
    pares = enumerate(val) if isinstance(val, list) else ((int(k), v) for k, v in val.items())
    out = {}
    for k, v in pares:
        if v is None:
            continue
        m = str(datas[(k // passo) % tam[ti]])[:7]
        if re.match(r"^\d{4}-\d{2}$", m) and m not in out:
            out[m] = round(float(v), 2)
    return out


def atualizar_ipc(hoje: datetime) -> bool:
    try:
        meta = bpstat(f"/series/?lang=PT&series_ids={IPC_SERIE}")[0]
        j = bpstat(f"/domains/{meta['domain_ids'][0]}/datasets/{meta['dataset_id']}/?lang=PT&series_ids={IPC_SERIE}&obs_since=2000-01-01")
        serie = jsonstat_mensal(j)
    except Exception as e:
        print(f"IPC (BPstat): falhou ({e})", file=sys.stderr)
        return False
    if not serie:
        print("IPC (BPstat): sem valores.", file=sys.stderr)
        return False
    print(f"IPC (BPstat): {min(serie)}–{max(serie)}, último {serie[max(serie)]}%")
    return gravar_simples(IPC, {"IPC": serie}, {"fonte": "BPstat (Banco de Portugal), IPC do INE, taxa de variação homóloga",
                                              "serie": IPC_SERIE, "frequencia": "mensal"}, hoje)


# ================================================================ taxa da Fed (FRED, Reserva Federal de St. Louis)
FED = os.path.join(PASTA, "fed.json")
FRED_URL = os.environ.get("FRED_URL", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=")


def fred(serie: str) -> dict:
    req = urllib.request.Request(FRED_URL + serie, headers={**CABECALHOS, "Accept": "text/csv"})
    out = {}
    for linha in ler_url(req, limite=60).decode("utf-8").strip().splitlines()[1:]:
        d, _, v = linha.partition(",")
        try:
            out[d.strip()] = float(v)
        except ValueError:
            pass
    return out


def atualizar_fed(hoje: datetime) -> bool:
    """Guarda só os dias em que o intervalo-alvo da Fed mudou (antes de dez/2008 havia um alvo único)."""
    try:
        sup, inf = fred("DFEDTARU"), fred("DFEDTARL")
    except Exception as e:
        print(f"Fed (FRED): falhou ({e})", file=sys.stderr)
        return False
    try:
        antigo = fred("DFEDTAR")
    except Exception:
        antigo = {}
    datas, s_, i_ = [], [], []
    for d in sorted(set(antigo) | set(sup)):
        if d < "1999-01-01":
            continue
        h = sup.get(d, antigo.get(d) if d not in inf else None)
        l = inf.get(d, antigo.get(d) if d not in sup else None)
        if h is None or l is None:
            continue
        if not datas or s_[-1] != h or i_[-1] != l:
            datas.append(d); s_.append(h); i_.append(l)
    if not datas:
        print("Fed (FRED): sem valores.", file=sys.stderr)
        return False
    novo = {"fonte": "FRED (Reserva Federal de St. Louis): DFEDTARU, DFEDTARL e DFEDTAR",
            "mudancas": {"datas": datas, "superior": s_, "inferior": i_}}
    velho = ler(FED)
    if velho.get("mudancas") == novo["mudancas"]:
        print("fed.json: sem alterações.")
        return True
    novo["atualizado"] = hoje.strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(PASTA, exist_ok=True)
    with open(FED, "w", encoding="utf-8") as f:
        json.dump(novo, f, ensure_ascii=False, indent=1)
    print(f"fed.json: gravado, {len(datas)} mudanças, atual {i_[-1]}–{s_[-1]}% desde {datas[-1]}.")
    return True


def ine_acessivel() -> bool:
    # a tarefa corre de hora a hora: basta insistir um pouco em cada corrida
    for n in range(6):
        try:
            req = urllib.request.Request(INE_URL + "?" + urllib.parse.urlencode({"op": "2", "varcd": INDICADOR, "Dim1": "X", "lang": "PT"}), headers=CABECALHOS)
            ler_url(req, limite=40, pausa=30)
            return True
        except urllib.error.HTTPError:
            return True  # respondeu, mesmo que com erro: está acessível
        except Exception as e:
            print(f"Teste de ligação ao INE {n + 1}/6: {e}", file=sys.stderr)
            time.sleep(15)
    return False


ESTADO = os.path.join(PASTA, "estado.json")
HORAS_ENTRE_IDAS = 20


def ler_estado() -> dict:
    try:
        with open(ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main() -> int:
    global INCREMENTAL
    hoje = datetime.now(timezone.utc)
    forcar = os.environ.get("FORCAR", "").lower() in ("1", "true", "sim")
    estado = ler_estado()
    try:
        ok_mercados = atualizar_mercados(hoje)
    except Exception as e:
        print(f"Mercados: erro inesperado ({e})", file=sys.stderr)
        ok_mercados = False
    try:  # o IPC vem do BPstat, não do INE: atualiza em todas as corridas
        ok_mercados = atualizar_ipc(hoje) or ok_mercados
    except Exception as e:
        print(f"IPC: erro inesperado ({e})", file=sys.stderr)
    try:  # a taxa da Fed vem do FRED: atualiza em todas as corridas
        ok_mercados = atualizar_fed(hoje) or ok_mercados
    except Exception as e:
        print(f"Fed: erro inesperado ({e})", file=sys.stderr)
    ultima = estado.get("ultima_ida_ao_ine")
    if ultima and not forcar:
        try:
            horas = (hoje - datetime.strptime(ultima, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 3600
        except ValueError:
            horas = 999
        if horas < HORAS_ENTRE_IDAS:
            print(f"Os dados do INE já foram atualizados há {horas:.0f} h ({ultima}). Nada a fazer no INE agora; "
                  f"volto a tentar daqui a {HORAS_ENTRE_IDAS - horas:.0f} h.")
            return 0 if ok_mercados else 1
    carregar_formatos()
    # se já há dados guardados, só pede os períodos mais recentes (o histórico fica no ficheiro)
    INCREMENTAL = os.path.exists(FICHEIRO)  # há dados guardados: basta pedir os períodos mais recentes
    if INCREMENTAL:
        print("Modo leve: só os períodos mais recentes (o histórico já está guardado).")
    tarefas = [atualizar_casas, atualizar_rendas, atualizar_construcao, atualizar_transacoes]
    ine_ok = ine_acessivel()
    if not ine_ok:
        print("O INE não está acessível a partir do GitHub neste momento. Os dados guardados ficam como estão; "
              "a tarefa volta a tentar amanhã.", file=sys.stderr)
        tarefas = []
    resultados = []
    for tarefa in tarefas + [atualizar_migracao, atualizar_previsoes]:
        try:
            resultados.append(tarefa(hoje))
        except Exception as e:
            print(f"{tarefa.__name__}: erro inesperado ({e})", file=sys.stderr)
            resultados.append(False)
    # regista que o INE foi contactado com sucesso: as próximas corridas horárias não voltam a pedir-lhe dados hoje
    if ine_ok and any(resultados[:len(tarefas)]) and not ine_em_baixo():
        os.makedirs(PASTA, exist_ok=True)
        with open(ESTADO, "w", encoding="utf-8") as f:
            json.dump({**estado, "ultima_ida_ao_ine": hoje.strftime("%Y-%m-%dT%H:%M:%SZ")}, f, ensure_ascii=False, indent=1)
        print("INE contactado com sucesso: próxima ida ao INE daqui a", HORAS_ENTRE_IDAS, "horas.")
    return 0 if any(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
