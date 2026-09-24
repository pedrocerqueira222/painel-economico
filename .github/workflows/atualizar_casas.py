#!/usr/bin/env python3
"""
Vai buscar ao INE, para Portugal e 5 concelhos:
  - o preço mediano por m² das casas vendidas (indicador 0012234, últimos 12 meses, trimestral) -> dados/casas.json
  - a renda mediana por m² dos novos contratos de arrendamento (últimos 12 meses, trimestral;
    se não for possível, a versão anual 0014711) -> dados/rendas.json
  - fogos licenciados e concluídos em construções novas para habitação (Portugal) -> dados/construcao.json
  - número de casas vendidas (Portugal) -> dados/transacoes.json
  - imigrantes e emigrantes por ano (Eurostat) -> dados/migracao.json
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
FORMATOS = {}
INCREMENTAL = False
FALHAS_REDE, LIMITE_FALHAS = 0, 4


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
            with urllib.request.urlopen(req, timeout=45) as r:
                dados = json.loads(r.read().decode("utf-8"))
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
        with urllib.request.urlopen(req, timeout=timeout) as r:
            texto = r.read().decode("utf-8", "replace")
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


def e_rendas_trimestral(nome: str) -> bool:
    n = nome.lower()
    return ("rendas" in n and "12 meses" in n and "trimestral" in n and "€" in n
            and "nuts" in n and "100 000" not in n and "tipologia" not in n
            and "setor" not in n and "locatários" not in n and "n.º" not in n)


def procurar_indicador_rendas(guardado: str | None) -> str | None:
    """Procura, pela ficha, o código do indicador trimestral das rendas (o INE não o divulga de forma fácil)."""
    if guardado and INCREMENTAL and guardado in FORMATOS:
        return guardado
    candidatos = ([guardado] if guardado else []) + [f"{c:07d}" for c in range(14700, 14780)] + [f"{c:07d}" for c in range(14600, 14700)]
    vistos, erros_seguidos = set(), 0
    for c in candidatos:
        if sem_tempo() or ine_em_baixo():
            break
        if c in vistos:
            continue
        vistos.add(c)
        try:
            nome = nome_indicador(ficha(c, timeout=30))
            erros_seguidos = 0
        except Exception:
            erros_seguidos += 1
            if erros_seguidos >= 5:
                print("Rendas: a ficha do INE não está a responder; desisto da procura.", file=sys.stderr)
                break
            continue
        if nome and e_rendas_trimestral(nome):
            print(f"Rendas: indicador trimestral encontrado, {c}: {nome}")
            return c
        time.sleep(0.2)
    print("Rendas: indicador trimestral não encontrado; vou usar o anual.", file=sys.stderr)
    return None


def mediana(l) -> bool:
    cat = str(l.get("dim_3_t") or l.get("dim_3") or "total").lower()
    return "2.º quartil" in cat or "2º quartil" in cat or "mediana" in cat or cat.startswith("total") or l.get("dim_3") == "T"


def atualizar_rendas(hoje: datetime) -> bool:
    antigo = {}
    if os.path.exists(RENDAS):
        with open(RENDAS, encoding="utf-8") as f:
            antigo = json.load(f)
    codigo = procurar_indicador_rendas(antigo.get("indicador") if antigo.get("frequencia") == "trimestral" else None)
    por_local, extra = {}, {}
    if codigo:
        try:
            por_local = recolher(hoje, codigo)
            extra = {"fonte": f"INE, indicador {codigo} (renda mediana por m² dos novos contratos, últimos 12 meses)",
                     "indicador": codigo, "frequencia": "trimestral"}
        except Exception as e:
            print(f"Rendas (trimestral): falhou ({e})", file=sys.stderr)
    if not any(por_local.values()):
        anos = [f"S7A{a}" for a in range(hoje.year, hoje.year - 9, -1)]
        try:
            por_local = recolher(hoje, RENDAS_ANUAL, codigos=anos, aceitar=mediana)
            extra = {"fonte": "INE, indicador 0014711 (renda mediana por m² dos novos contratos, anual)",
                     "indicador": RENDAS_ANUAL, "frequencia": "anual"}
        except Exception as e:
            print(f"Rendas (anual): falhou ({e})", file=sys.stderr)
    for nome, serie in por_local.items():
        print(f"Rendas, {nome}: {len(serie)} períodos")
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
    with urllib.request.urlopen(req, timeout=120) as r:
        j = json.loads(r.read().decode("utf-8"))
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


def ine_acessivel() -> bool:
    for n in range(3):
        try:
            req = urllib.request.Request(INE_URL + "?" + urllib.parse.urlencode({"op": "2", "varcd": INDICADOR, "Dim1": "X", "lang": "PT"}), headers=CABECALHOS)
            with urllib.request.urlopen(req, timeout=40) as r:
                r.read(200)
            return True
        except urllib.error.HTTPError:
            return True  # respondeu, mesmo que com erro: está acessível
        except Exception as e:
            print(f"Teste de ligação ao INE {n + 1}/3: {e}", file=sys.stderr)
            time.sleep(20)
    return False


def main() -> int:
    global INCREMENTAL
    hoje = datetime.now(timezone.utc)
    carregar_formatos()
    # se já há dados guardados, só pede os períodos mais recentes (o histórico fica no ficheiro)
    INCREMENTAL = bool(FORMATOS)
    if INCREMENTAL:
        print("Modo leve: só os períodos mais recentes (o histórico já está guardado).")
    tarefas = [atualizar_casas, atualizar_rendas, atualizar_construcao, atualizar_transacoes]
    if not ine_acessivel():
        print("O INE não está acessível a partir do GitHub neste momento. Os dados guardados ficam como estão; "
              "a tarefa volta a tentar amanhã.", file=sys.stderr)
        tarefas = []
    resultados = []
    for tarefa in tarefas + [atualizar_migracao]:
        try:
            resultados.append(tarefa(hoje))
        except Exception as e:
            print(f"{tarefa.__name__}: erro inesperado ({e})", file=sys.stderr)
            resultados.append(False)
    return 0 if any(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
