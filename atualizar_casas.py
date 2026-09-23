#!/usr/bin/env python3
"""
Vai buscar ao INE, para Portugal e 5 concelhos:
  - o preço mediano por m² das casas vendidas (indicador 0012234, últimos 12 meses, trimestral) -> dados/casas.json
  - a renda mediana por m² dos novos contratos de arrendamento (últimos 12 meses, trimestral;
    se não for possível, a versão anual 0014711) -> dados/rendas.json
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


class ErroINE(Exception):
    """O INE respondeu, mas com uma mensagem de erro (por exemplo, trimestre ainda não publicado)."""


def pedir(params: dict, tentativas: int = 3) -> list:
    url = INE_URL + "?" + urllib.parse.urlencode({"op": "2", "lang": "PT", **params})
    ultimo = None
    for n in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=CABECALHOS)
            with urllib.request.urlopen(req, timeout=180) as r:
                dados = json.loads(r.read().decode("utf-8"))
            raiz = dados[0] if isinstance(dados, list) else dados
            if isinstance(raiz, dict) and "Sucesso" in raiz and "Falso" in raiz["Sucesso"]:
                raise ErroINE(raiz["Sucesso"]["Falso"][0].get("Msg", "erro desconhecido"))
            return dados
        except ErroINE:
            raise
        except Exception as e:  # rede lenta, erro temporário…
            ultimo = e
            print(f"  tentativa {n + 1} falhou: {e}", file=sys.stderr)
            time.sleep(10 * (n + 1))
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


def ficha(indicador: str, timeout: int = 120) -> str:
    url = INE_URL.replace("pindica.jsp", "pindicaMeta.jsp") + "?" + urllib.parse.urlencode({"varcd": indicador, "lang": "PT"})
    req = urllib.request.Request(url, headers=CABECALHOS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


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
    if codigos is None:
        codigos = codigos_da_ficha(indicador)
        if codigos:
            codigos = sorted(codigos, reverse=True)[:TRIMESTRES]
        else:
            formato = descobrir_formato(hoje, indicador)
            if not formato:
                raise RuntimeError("não foi possível descobrir o formato dos trimestres aceite pelo INE")
            codigos = lista_trimestres(hoje, formato)

    alvo = {c: nome for nome, cs in LOCALIDADES.items() for c in cs}
    nomes = {n.lower(): n for n in LOCALIDADES}
    por_local = {n: {} for n in LOCALIDADES}
    obtidos, falhas_seguidas = 0, 0
    for codigo in codigos:
        try:
            dados = pedir({"varcd": indicador, "Dim1": codigo})
        except ErroINE as e:
            falhas_seguidas += 1
            print(f"{codigo}: INE diz '{e}'", file=sys.stderr)
            if obtidos and falhas_seguidas >= 3:
                break  # chegámos ao início da série
            continue
        falhas_seguidas = 0
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
        time.sleep(0.5)
    return por_local


def gravar(ficheiro: str, por_local: dict, extra: dict, hoje: datetime) -> bool:
    antigo = {}
    if os.path.exists(ficheiro):
        with open(ficheiro, encoding="utf-8") as f:
            antigo = json.load(f)
    antigos = {nome: dict(zip(antigo.get("periodos", []), serie)) for nome, serie in (antigo.get("valores") or {}).items()}
    for nome in LOCALIDADES:  # não perder uma localidade se o INE falhar só nela
        if not por_local.get(nome) and antigos.get(nome) and antigo.get("frequencia", "trimestral") == extra.get("frequencia"):
            por_local[nome] = {k: v for k, v in antigos[nome].items() if v is not None}
    periodos = sorted({q for s in por_local.values() for q in s})
    novo = {**extra, "periodos": periodos,
            "valores": {n: [por_local.get(n, {}).get(q) for q in periodos] for n in LOCALIDADES}}
    chaves = [k for k in novo if k != "atualizado"]
    if {k: antigo.get(k) for k in chaves} == {k: novo[k] for k in chaves}:
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
    candidatos = ([guardado] if guardado else []) + [f"{c:07d}" for c in range(14700, 14780)] + [f"{c:07d}" for c in range(14600, 14700)]
    vistos, erros_seguidos = set(), 0
    for c in candidatos:
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


def main() -> int:
    hoje = datetime.now(timezone.utc)
    ok_casas = atualizar_casas(hoje)
    ok_rendas = atualizar_rendas(hoje)
    return 0 if (ok_casas or ok_rendas) else 1


if __name__ == "__main__":
    sys.exit(main())
