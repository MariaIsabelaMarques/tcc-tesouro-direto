import gzip
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

# =============================================================================
# CONFIGURAÇÃO (preencha)
# =============================================================================
DATA_EXTRACAO = "2026-03-31"      # posição da base: última data de adesão registrada no arquivo baixado
ATIVOS_BALANCO_REF = 3_418_225    # ativos no balanço de mar/2026 (Anexo 3.1 do balanço de ago/2026)
MES_BALANCO_REF = "mar/2026"      # mês desse balanço

ANO_INI, ANO_FIM = 2002, 2025
ANO_INI_RAZAO = 2005              # primeiro dezembro com investidores ativos no Anexo 3.1 do balanço
ANOS_RESUMO = [2002, 2005, 2008, 2011, 2014, 2017, 2019, 2021, 2023, 2025]
REF_PROITE_2008 = 146_000         # investidores cadastrados até o fim de 2008 (PROITE, 2009)

# =============================================================================
BASE = Path(__file__).resolve().parent
DADOS = BASE / "dados"
RES = BASE / "resultados"
GRAF = BASE / "graficos"
LOCAL = Path.home() / "tcc_td_local"   
DB = LOCAL / "td.duckdb"
ARQ_IBGE = DADOS / "projecoes_2024_tab1_idade_simples.xlsx"
ARQ_BALANCO = DADOS / "balanco_serie.csv"
ARQ_PERFIL = DADOS / "balanco_perfil.csv"
ANO_EXT = int(DATA_EXTRACAO[:4])

UFS = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
       "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"]
REGIAO = {"N": ["AC", "AM", "AP", "PA", "RO", "RR", "TO"],
          "NE": ["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"],
          "SE": ["ES", "MG", "RJ", "SP"],
          "S": ["PR", "RS", "SC"],
          "CO": ["DF", "GO", "MS", "MT"]}
UF_NOMES = {"ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM", "BAHIA": "BA",
            "CEARA": "CE", "DISTRITO FEDERAL": "DF", "ESPIRITO SANTO": "ES", "GOIAS": "GO",
            "MARANHAO": "MA", "MATO GROSSO": "MT", "MATO GROSSO DO SUL": "MS",
            "MINAS GERAIS": "MG", "PARA": "PA", "PARAIBA": "PB", "PARANA": "PR",
            "PERNAMBUCO": "PE", "PIAUI": "PI", "RIO DE JANEIRO": "RJ",
            "RIO GRANDE DO NORTE": "RN", "RIO GRANDE DO SUL": "RS", "RONDONIA": "RO",
            "RORAIMA": "RR", "SANTA CATARINA": "SC", "SAO PAULO": "SP", "SERGIPE": "SE",
            "TOCANTINS": "TO"}
FAIXAS = [(0, 17, "0-17"), (18, 24, "18-24"), (25, 34, "25-34"), (35, 44, "35-44"),
          (45, 54, "45-54"), (55, 64, "55-64"), (65, 200, "65+")]
FAIXAS_ADULTAS = [f[2] for f in FAIXAS[1:]]
ROTULO_FAIXA = {"0-17": "0 a 17", "18-24": "18 a 24", "25-34": "25 a 34", "35-44": "35 a 44",
                "45-54": "45 a 54", "55-64": "55 a 64", "65+": "65 ou mais"}
PROF_NAO_INFORMADA = {"", "NAO INFORMADO", "NAO INFORMADA", "NAO DECLARADO", "NAO DECLARADA",
                      "SEM INFORMACAO", "NULL", "NAN"}


# -----------------------------------------------------------------------------
# utilidades
# -----------------------------------------------------------------------------
class Log:
    def __init__(self, arquivo):
        RES.mkdir(exist_ok=True)
        self.f = open(arquivo, "w", encoding="utf-8")

    def __call__(self, *args):
        txt = " ".join(str(a) for a in args)
        print(txt)
        self.f.write(txt + "\n")
        self.f.flush()


def norm(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().strip().upper()


def br(x, dec=0):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n.d."
    s = f"{x:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(x, dec=1):
    return br(100 * x, dec) + "%" if x is not None and not np.isnan(x) else "n.d."


def gini(contagens):
    x = np.sort(np.asarray(contagens, dtype=float))
    n, s = len(x), x.sum()
    if n == 0 or s == 0:
        return np.nan
    return (2 * np.sum(np.arange(1, n + 1) * x)) / (n * s) - (n + 1) / n


def hhi(contagens):
    x = np.asarray(contagens, dtype=float)
    s = x.sum()
    return np.nan if s == 0 else float(np.sum((100 * x / s) ** 2))


def faixa(idade):
    for lo, hi, nome in FAIXAS:
        if lo <= idade <= hi:
            return nome
    return None


def salvar_csv(df, nome):
    df.to_csv(RES / nome, sep=";", decimal=",", index=False, encoding="utf-8-sig")


def conectar():
    LOCAL.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB))
    tmp = LOCAL / "duckdb_tmp"
    tmp.mkdir(exist_ok=True)
    con.execute(f"SET temp_directory='{tmp.as_posix()}'")
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET threads=4")
    return con


# -----------------------------------------------------------------------------
# leitura do arquivo do Tesouro
# -----------------------------------------------------------------------------
def localizar_td():
    cands = [p for p in DADOS.glob("investidores*") if p.is_file() and p.suffix.lower() != ".pdf"]
    if not cands:
        sys.exit(f"Não encontrei o arquivo de investidores em {DADOS}. "
                 "Ele deve se chamar investidorestesourodireto.gz (ou começar com 'investidores').")
    cands.sort(key=lambda p: p.stat().st_size, reverse=True)
    return cands[0]


def detectar_formato(path):
    with open(path, "rb") as f:
        magia = f.read(2)
    comp = "gzip" if magia == b"\x1f\x8b" else "none"
    abrir = gzip.open if comp == "gzip" else open
    with abrir(path, "rb") as f:
        bloco = f.read(5_000_000)
    bloco = bloco[: bloco.rfind(b"\n") + 1] or bloco
    try:
        bloco.decode("utf-8")
        enc = "utf-8"
    except UnicodeDecodeError:
        enc = "latin-1"
    linhas = bloco.decode(enc, errors="replace").splitlines()[:4]
    cab = linhas[0]
    delim = max([";", ",", "\t", "|"], key=cab.count)
    return comp, enc, delim, linhas


PADROES = [  # nome padrão, regex sobre o cabeçalho normalizado
    ("codigo", r"CODIGO"), ("data_adesao", r"ADESAO"), ("estado_civil", r"CIVIL"),
    ("genero", r"GENERO|SEXO"), ("profissao", r"PROFISS"), ("idade", r"\bIDADE\b"),
    ("uf", r"\bUF\b"), ("cidade", r"CIDADE"), ("pais", r"\bPAIS\b"),
    ("situacao", r"SITUA"), ("operou12m", r"OPEROU"),
]


def mapear_colunas(cabecalho, log):
    nomes = [norm(c).strip('"') for c in cabecalho]
    destino = [None] * len(nomes)
    for padrao, rx in PADROES:
        for i, n in enumerate(nomes):
            if destino[i] is None and re.search(rx, n):
                destino[i] = padrao
                break
    faltando = [p for p, _ in PADROES if p not in destino]
    if faltando and len(nomes) == len(PADROES):
        log("  Aviso: cabeçalho não reconhecido por nome; usando a ordem das colunas do metadado.")
        destino = [p for p, _ in PADROES]
        faltando = []
    destino = [d if d else f"extra_{i}" for i, d in enumerate(destino)]
    return destino, faltando


# -----------------------------------------------------------------------------
# IBGE
# -----------------------------------------------------------------------------
def _eh_ano(v):
    try:
        f = float(str(v).strip())
        return f.is_integer() and 1990 <= f <= 2100
    except ValueError:
        return False


def ler_ibge(log):
    if not ARQ_IBGE.exists():
        sys.exit(f"Arquivo do IBGE não encontrado: {ARQ_IBGE}")
    folhas = pd.read_excel(ARQ_IBGE, sheet_name=None, header=None)
    partes = []
    for nome, df in folhas.items():
        hdr = None
        for i in range(min(60, len(df))):
            linha = df.iloc[i].tolist()
            n_anos = sum(_eh_ano(v) for v in linha)
            if n_anos >= 10 and any("IDADE" in norm(v) for v in linha):
                hdr = i
                break
        if hdr is None:
            continue
        cab = df.iloc[hdr].tolist()
        cabn = [norm(v) for v in cab]
        col = lambda chave: next((j for j, v in enumerate(cabn) if chave in v), None)
        j_idade, j_sexo, j_sigla = col("IDADE"), col("SEXO"), col("SIGLA")
        j_local = col("LOCAL") if col("LOCAL") is not None else col("NOME")
        anos = {j: int(float(cab[j])) for j, v in enumerate(cab) if _eh_ano(v)}
        corpo = df.iloc[hdr + 1:].copy()
        if j_sigla is not None:
            sig = corpo[j_sigla].map(lambda v: norm(v))
        elif j_local is not None:
            sig = corpo[j_local].map(lambda v: UF_NOMES.get(norm(v), norm(v)))
        else:
            continue
        sexo = corpo[j_sexo].map(norm) if j_sexo is not None else pd.Series("AMBOS", index=corpo.index)
        idade = corpo[j_idade].map(lambda v: re.match(r"\s*(\d+)", str(v)))
        idade = idade.map(lambda m: int(m.group(1)) if m else None)
        base = pd.DataFrame({"uf": sig, "sexo": sexo, "idade": idade})
        for j, ano in anos.items():
            base[ano] = pd.to_numeric(corpo[j], errors="coerce")
        partes.append(base)
    if not partes:
        log("ERRO: não reconheci o formato do arquivo do IBGE. Primeiras linhas de cada aba:")
        for nome, df in folhas.items():
            log(f"--- aba {nome} ---")
            log(df.iloc[:15, :8].to_string())
        sys.exit("Me mande o parte1_log.txt para eu ajustar a leitura do IBGE.")
    pop = pd.concat(partes)
    pop = pop[pop["uf"].isin(UFS) & pop["idade"].notna()]
    pop["sexo"] = pop["sexo"].map(lambda s: "M" if s.startswith("H") or s.startswith("MASC")
                                  else "F" if s.startswith("MULH") or s.startswith("FEM")
                                  else "T")
    pop = pop.melt(id_vars=["uf", "sexo", "idade"], var_name="ano", value_name="pop").dropna()
    pop["ano"] = pop["ano"].astype(int)
    pop["idade"] = pop["idade"].astype(int)
    pop = pop.groupby(["uf", "sexo", "idade", "ano"], as_index=False)["pop"].sum()
    if not (pop["sexo"] == "T").any():
        tot = pop.groupby(["uf", "idade", "ano"], as_index=False)["pop"].sum()
        tot["sexo"] = "T"
        pop = pd.concat([pop, tot])
    return pop


def ler_balanco(log):
    if not ARQ_BALANCO.exists():
        log(f"  Aviso: {ARQ_BALANCO.name} não encontrado; validações V2/V4 e gráficos do balanço ficam de fora.")
        return None
    txt = ARQ_BALANCO.read_text(encoding="utf-8-sig").strip().splitlines()
    sep = ";" if ";" in txt[0] else ","
    linhas = []
    for l in txt[1:]:
        p = [c.strip() for c in l.split(sep)]
        if len(p) < 3 or not p[0]:
            continue
        num = lambda s: int(re.sub(r"\D", "", s)) if re.sub(r"\D", "", s) else np.nan
        linhas.append((int(num(p[0])), num(p[1]), num(p[2])))
    bal = pd.DataFrame(linhas, columns=["ano", "cadastrados", "ativos"]).sort_values("ano")
    return bal


# -----------------------------------------------------------------------------
# PARTE 1
# -----------------------------------------------------------------------------
def parte1():
    log = Log(RES / "parte1_log.txt")
    log(f"PARTE 1 | execução em {date.today().isoformat()} | DATA_EXTRACAO = {DATA_EXTRACAO}")
    log(f"duckdb {duckdb.__version__} | pandas {pd.__version__}")
    if tuple(int(x) for x in duckdb.__version__.split(".")[:2]) < (1, 1):
        sys.exit("Atualize o DuckDB: pip install -U duckdb")

    # 1. formato
    td = localizar_td()
    comp, enc, delim, linhas = detectar_formato(td)
    log(f"\n[1] Arquivo: {td.name} ({br(td.stat().st_size / 2**20, 1)} MiB)")
    log(f"    compressão={comp} | encoding={enc} | separador={repr(delim)}")
    for l in linhas:
        log("    > " + l[:200])
    cabecalho = linhas[0].split(delim)
    destino, faltando = mapear_colunas(cabecalho, log)
    log("    mapeamento de colunas:")
    for orig, dest in zip(cabecalho, destino):
        log(f"      {orig.strip()!r:40} -> {dest}")
    if faltando:
        sys.exit(f"Colunas não encontradas: {faltando}. Me mande o parte1_log.txt.")

    # 2. importação e limpeza (uma única leitura do arquivo grande)
    con = conectar()
    ja_importado = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name IN ('limpo', 'meta')").fetchone()[0] == 2
    reimportar = "--reimportar" in sys.argv or not ja_importado
    nomes = ", ".join(f"'{d}'" for d in destino)
    if not reimportar:
        log("\n[2] Importação já feita em execução anterior; reaproveitando (use --reimportar para ler o arquivo de novo).")
    else:
        log("\n[2] Importando e limpando (pode levar vários minutos; não feche o terminal)...")
    if reimportar:
        con.execute(rf"""
        CREATE OR REPLACE TABLE limpo AS
        WITH r AS (
            SELECT * FROM read_csv('{td.as_posix()}', header=true, all_varchar=true,
                delim='{delim}', compression='{comp}', encoding='{enc}',
                names=[{nomes}], store_rejects=true)),
        b AS (
            SELECT trim(codigo) AS codigo,
                trim(data_adesao) AS data_txt,
                coalesce(try_strptime(trim(data_adesao), '%d/%m/%Y'),
                         try_strptime(trim(data_adesao), '%Y-%m-%d'),
                         try_strptime(trim(data_adesao), '%d/%m/%Y %H:%M:%S'),
                         try_strptime(trim(data_adesao), '%Y-%m-%d %H:%M:%S'))::DATE AS data_adesao,
                upper(strip_accents(trim(estado_civil))) AS estado_civil,
                upper(trim(genero)) AS genero,
                trim(profissao) AS profissao_orig,
                upper(strip_accents(trim(profissao))) AS profissao,
                trim(idade) AS idade_txt,
                CASE WHEN regexp_full_match(coalesce(trim(idade), ''), '\d{{1,3}}')
                     THEN CAST(trim(idade) AS INTEGER) END AS idade_num,
                upper(trim(uf)) AS uf,
                upper(trim(situacao)) AS situacao,
                upper(trim(operou12m)) AS operou12m
            FROM r)
        SELECT * EXCLUDE (estado_civil),
            CASE WHEN codigo IS NULL OR codigo = '' THEN '1. código vazio'
                 WHEN data_adesao IS NULL THEN '2. data de adesão inválida'
                 WHEN year(data_adesao) = 1900 THEN '3. pessoa jurídica (adesão 1900)'
                 WHEN genero = 'N' OR estado_civil = 'NAO SE APLICA' OR profissao = 'NAO SE APLICA'
                      THEN '4. instituição apta a receber doações'
            END AS motivo_exclusao
        FROM b
    """)
        try:
            n_rej = con.execute("SELECT count(*) FROM reject_errors").fetchone()[0]
        except duckdb.Error:
            n_rej = 0
        con.execute(f"CREATE OR REPLACE TABLE meta AS SELECT {n_rej} AS n_rej")
    n_bruto = con.execute("SELECT count(*) FROM limpo").fetchone()[0]
    n_rej = con.execute("SELECT n_rej FROM meta").fetchone()[0]
    log(f"    linhas lidas: {br(n_bruto)} | linhas rejeitadas por erro de formato: {br(n_rej)}")

    # 3. domínios
    log("\n[3] Domínios das colunas categóricas (linhas brutas):")
    for c in ["situacao", "operou12m", "genero"]:
        d = con.execute(f"SELECT {c} AS valor, count(*) AS n FROM limpo GROUP BY 1 ORDER BY 2 DESC LIMIT 10").df()
        log(f"  {c}: " + " | ".join(f"{v}={br(n)}" for v, n in d.itertuples(index=False)))
    d = con.execute("""SELECT idade_txt AS valor, count(*) n FROM limpo WHERE idade_num IS NULL
                       GROUP BY 1 ORDER BY 2 DESC LIMIT 10""").df()
    log("  idade não numérica (10 mais frequentes): " +
        (" | ".join(f"{v!r}={br(n)}" for v, n in d.itertuples(index=False)) or "nenhuma"))
    d = con.execute("SELECT idade_num, count(*) n FROM limpo WHERE idade_num <= 1 OR idade_num >= 100 GROUP BY 1 ORDER BY 1").df()
    log("  idades extremas (0, 1 ou 100+): " + (" | ".join(f"{int(v)}={br(n)}" for v, n in d.itertuples(index=False)) or "nenhuma"))
    d = con.execute("SELECT data_txt, count(*) n FROM limpo WHERE data_adesao IS NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 5").df()
    log("  datas não reconhecidas: " + (" | ".join(f"{v!r}={br(n)}" for v, n in d.itertuples(index=False)) or "nenhuma"))

    # 4. limpeza
    log("\n[4] Limpeza (cada linha é contada só no primeiro motivo de exclusão):")
    d = con.execute("SELECT coalesce(motivo_exclusao, '0. mantidas') m, count(*) n FROM limpo GROUP BY 1 ORDER BY 1").df()
    for m, n in d.itertuples(index=False):
        log(f"    {m:45} {br(n):>15} linhas")

    # 5. consolidação
    log("\n[5] Consolidação por código de investidor (em 16 partes, para caber na memória):")
    N_PARTES = 16
    consulta = """
        SELECT codigo,
            min(data_adesao) AS data_adesao,
            year(min(data_adesao)) AS ano_adesao,
            max(CASE WHEN situacao = 'A' THEN 1 ELSE 0 END) AS ativo,
            max(CASE WHEN operou12m = 'S' THEN 1 ELSE 0 END) AS operou12m,
            arg_min(uf, data_adesao) FILTER (WHERE uf IS NOT NULL AND uf <> '') AS uf,
            arg_min(genero, data_adesao) FILTER (WHERE genero IN ('M', 'F')) AS genero,
            arg_min(profissao, data_adesao) FILTER (WHERE profissao IS NOT NULL AND profissao <> '') AS profissao,
            arg_min(profissao_orig, data_adesao) FILTER (WHERE profissao IS NOT NULL AND profissao <> '') AS profissao_rotulo,
            arg_min(idade_num, data_adesao) FILTER (WHERE idade_num IS NOT NULL) AS idade_ext,
            count(*) AS n_cadastros,
            (min(uf) <> max(uf))::INT AS uf_diverge,
            (min(genero) <> max(genero))::INT AS genero_diverge,
            (min(idade_num) <> max(idade_num))::INT AS idade_diverge
        FROM limpo WHERE motivo_exclusao IS NULL {filtro}
        GROUP BY codigo
    """
    con.execute("CREATE OR REPLACE TABLE investidores AS " + consulta.format(filtro="AND false"))
    for k in range(N_PARTES):
        con.execute("INSERT INTO investidores " + consulta.format(filtro=f"AND hash(codigo) % {N_PARTES} = {k}"))
        print(f"    parte {k + 1}/{N_PARTES} concluída", flush=True)
    n_inv, n_multi, d_uf, d_gen, d_id = con.execute("""
        SELECT count(*), sum((n_cadastros > 1)::INT), sum(coalesce(uf_diverge, 0)),
               sum(coalesce(genero_diverge, 0)), sum(coalesce(idade_diverge, 0)) FROM investidores""").fetchone()
    log(f"    investidores únicos: {br(n_inv)}")
    log(f"    com mais de um cadastro: {br(n_multi)} ({pct(n_multi / n_inv)})")
    log(f"    entre esses, com UF divergente: {br(d_uf)} | gênero divergente: {br(d_gen)} | idade divergente: {br(d_id)}")
    n_idade_inv = con.execute(f"""
        SELECT count(*) FROM investidores WHERE idade_ext IS NOT NULL
        AND (idade_ext > 110 OR idade_ext - ({ANO_EXT} - ano_adesao) < 0)""").fetchone()[0]
    con.execute(f"""
        UPDATE investidores SET idade_ext = NULL WHERE idade_ext IS NOT NULL
        AND (idade_ext > 110 OR idade_ext - ({ANO_EXT} - ano_adesao) < 0)""")
    n_idade_nula = con.execute("SELECT count(*) FROM investidores WHERE idade_ext IS NULL").fetchone()[0]
    log(f"    idade incompatível (>110 ou negativa na adesão), tratada como ausente: {br(n_idade_inv)}")
    log(f"    total com idade ausente: {br(n_idade_nula)} ({pct(n_idade_nula / n_inv)})")
    n_sem_uf = con.execute(f"SELECT count(*) FROM investidores WHERE uf IS NULL OR uf NOT IN ({','.join(repr(u) for u in UFS)})").fetchone()[0]
    log(f"    sem UF válida (exterior ou vazio): {br(n_sem_uf)} ({pct(n_sem_uf / n_inv)})")
    d = con.execute("SELECT min(data_adesao), max(data_adesao) FROM investidores").fetchone()
    log(f"    datas de adesão: de {d[0]} a {d[1]}")

    # 6. IBGE
    log("\n[6] IBGE:")
    pop = ler_ibge(log)
    salvar = pop.copy()
    salvar.to_csv(RES / "ibge_pop_long.csv", index=False)
    for a in [2002, 2014, 2025]:
        t = pop[(pop.sexo == "T") & (pop.ano == a)]
        log(f"    {a}: população (27 UFs) = {br(t['pop'].sum())} | 18+ = {br(t[t.idade >= 18]['pop'].sum())} | UFs = {t.uf.nunique()}")

    # 7. balanço
    log("\n[7] Balanço:")
    bal = ler_balanco(log)
    if bal is not None:
        log(bal.to_string(index=False))

    # 8. validações
    log("\n[8] Validações:")
    ativos_md, oper_md = con.execute("SELECT sum(ativo), sum(operou12m) FROM investidores").fetchone()
    log(f"  V1. ativos nos microdados (conta 'A'): {br(ativos_md)} | operaram 12 meses: {br(oper_md)}")
    if ATIVOS_BALANCO_REF:
        r = ativos_md / ATIVOS_BALANCO_REF
        log(f"      ativos no balanço ({MES_BALANCO_REF}): {br(ATIVOS_BALANCO_REF)} | razão microdados/balanço = {br(r, 2)}")
        if 0.9 <= r <= 1.1:
            log("      LEITURA: 'A' corresponde a ter investimentos. O texto da 3.5 está correto.")
        elif r > 1.5:
            log("      LEITURA: 'A' parece significar só conta não desativada. 'Operou 12 meses' vira a medida principal de uso efetivo.")
        else:
            log("      LEITURA: inconclusivo; discutir antes de escrever a 4.4.")
    else:
        log("      Preencha ATIVOS_BALANCO_REF e MES_BALANCO_REF para completar a V1.")
    cad_md = con.execute(f"SELECT count(*) FROM investidores WHERE data_adesao <= DATE '{ANO_FIM}-12-31'").fetchone()[0]
    cad_linhas = con.execute(f"SELECT count(*) FROM limpo WHERE motivo_exclusao IS NULL AND data_adesao <= DATE '{ANO_FIM}-12-31'").fetchone()[0]
    log(f"  V2. cadastrados até dez/{ANO_FIM}: {br(cad_md)} códigos únicos | {br(cad_linhas)} cadastros (linhas)")
    if bal is not None and (bal.ano == ANO_FIM).any():
        b = bal.loc[bal.ano == ANO_FIM, "cadastrados"].iloc[0]
        log(f"      balanço dez/{ANO_FIM}: {br(b)} | razão códigos/balanço = {br(cad_md / b, 2)} | razão linhas/balanço = {br(cad_linhas / b, 2)}")
    ate2008 = con.execute("SELECT count(*) FROM investidores WHERE ano_adesao <= 2008").fetchone()[0]
    log(f"  V3. cadastrados até 2008 nos microdados: {br(ate2008)} | Proite (2009): cerca de {br(REF_PROITE_2008)} | razão = {br(ate2008 / REF_PROITE_2008, 2)}")
    if ate2008 / REF_PROITE_2008 < 0.8:
        log("      LEITURA: contas antigas parecem ter saído da base; a retenção das coortes antigas fica inflada (limitação).")
    novos = con.execute(f"SELECT ano_adesao AS ano, count(*) AS novos FROM investidores WHERE ano_adesao BETWEEN {ANO_INI} AND {ANO_FIM} GROUP BY 1 ORDER BY 1").df()
    log("  V4. microdados x balanço, ano a ano (novos no ano e base acumulada em dezembro):")
    if bal is not None:
        v = bal.assign(delta_balanco=bal.cadastrados.diff()).merge(novos, on="ano", how="left")
        v["acum_microdados"] = v.novos.cumsum()
        v["razao_novos"] = (v.novos / v.delta_balanco).round(2)
        v["razao_acum"] = (v.acum_microdados / v.cadastrados).round(2)
        log(v[["ano", "novos", "delta_balanco", "razao_novos", "acum_microdados", "cadastrados", "razao_acum"]].to_string(index=False))
        log("      LEITURA: razao_acum perto de 1 em todos os anos = microdados reproduzem a série oficial;"
            " razao_acum crescente ao longo do tempo = contas antigas saíram da base.")
    else:
        log(novos.to_string(index=False))
    con.close()
    log("\nFim da parte 1. Envie resultados/parte1_log.txt.")


# -----------------------------------------------------------------------------
# GRÁFICOS
# -----------------------------------------------------------------------------
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

COR = ["#2a78d6", "#eb6834", "#1baf7a"]   # categóricas, ordem fixa
REF = "#52514e"                           # referência (população)
GRADE = "#dedcd6"
RAMPA = ["#b7d3f6", "#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#123f78", "#0a2240"]  # azul, claro -> escuro
MARC = ["o", "s", "^"]
LARG = 6.3  # polegadas, cerca de 16 cm

plt.rcParams.update({
    "font.size": 9, "axes.titlesize": 9, "axes.labelsize": 9, "legend.fontsize": 8,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "axes.spines.top": False,
    "axes.spines.right": False, "axes.edgecolor": REF, "axes.labelcolor": "#0b0b0b",
    "xtick.color": REF, "ytick.color": REF, "axes.grid": True, "axes.grid.axis": "y",
    "grid.color": GRADE, "grid.linewidth": 0.6, "legend.frameon": False,
    "savefig.dpi": 300, "savefig.bbox": "tight", "figure.dpi": 100,
})


def fmt_eixo(ax, dec=0, eixo="y", sufixo=""):
    f = FuncFormatter(lambda v, p: br(v, dec) + sufixo)
    (ax.yaxis if eixo == "y" else ax.xaxis).set_major_formatter(f)


def escala(valor_max):
    return (1e6, "milhões") if valor_max >= 1e6 else (1e3, "mil")


def eixo_anos(ax, a0=ANO_INI, a1=ANO_FIM, passo=2):
    ax.set_xticks(range(a0, a1 + 1, passo))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, p: str(int(v))))
    ax.set_xlim(a0 - 0.7, a1 + 0.7)


def salvar_fig(fig, nome):
    GRAF.mkdir(exist_ok=True)
    fig.savefig(GRAF / nome, facecolor="white")
    plt.close(fig)


def linha(ax, x, y, i, rotulo, ref=False):
    if ref:
        ax.plot(x, y, color=REF, lw=1.2, ls="--", label=rotulo)
    else:
        ax.plot(x, y, color=COR[i], lw=1.6, marker=MARC[i], ms=3.2, label=rotulo)


def painel_concentracao(tab, nome_arq, com_pop=True, rot_pop="População"):
    fig, axs = plt.subplots(1, 2, figsize=(LARG, 2.7))
    for ax, met, tit in zip(axs, ["gini", "hhi"], ["(a) Coeficiente de Gini", "(b) Índice de Herfindahl-Hirschman"]):
        linha(ax, tab.ano, tab[f"{met}_fluxo"], 0, "Ingressantes no ano")
        linha(ax, tab.ano, tab[f"{met}_estoque"], 1, "Base acumulada")
        if com_pop and f"{met}_pop" in tab:
            linha(ax, tab.ano, tab[f"{met}_pop"], 0, rot_pop, ref=True)
        ax.set_title(tit, loc="left")
        eixo_anos(ax, passo=4)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 5, 10]))
        fmt_eixo(ax, 2 if met == "gini" else 0)
    h, l = axs[0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    salvar_fig(fig, nome_arq)


# -----------------------------------------------------------------------------
# PARTE 2
# -----------------------------------------------------------------------------
def parte2():
    if not DB.exists():
        sys.exit("Rode a parte 1 antes.")
    log = Log(RES / "resumo.txt")
    log(f"RESUMO DOS INDICADORES | DATA_EXTRACAO = {DATA_EXTRACAO} | recorte {ANO_INI}-{ANO_FIM}")
    con = conectar()
    W = f"ano_adesao BETWEEN {ANO_INI} AND {ANO_FIM}"
    ag_uf = con.execute(f"SELECT ano_adesao AS ano, uf, count(*) n FROM investidores WHERE {W} GROUP BY 1, 2").df()
    ag_gen = con.execute(f"SELECT ano_adesao AS ano, genero, count(*) n FROM investidores WHERE {W} GROUP BY 1, 2").df()
    ag_prof = con.execute(f"SELECT ano_adesao AS ano, profissao, count(*) n FROM investidores WHERE {W} GROUP BY 1, 2").df()
    rot_prof = con.execute(f"SELECT profissao, mode(profissao_rotulo) AS rot FROM investidores WHERE {W} GROUP BY 1").df()
    ag_id = con.execute(f"SELECT ano_adesao AS ano, idade_ext, count(*) n FROM investidores WHERE {W} GROUP BY 1, 2").df()
    ag_ret = con.execute(f"""SELECT ano_adesao AS ano, count(*) n, sum(ativo) ativos, sum(operou12m) operou
                             FROM investidores WHERE {W} GROUP BY 1 ORDER BY 1""").df()
    con.close()
    pop = pd.read_csv(RES / "ibge_pop_long.csv")
    bal = ler_balanco(log)
    anos = list(range(ANO_INI, ANO_FIM + 1))

    # ---------------- 4.2 AMPLITUDE ----------------
    log("\n" + "=" * 70 + "\n4.2 AMPLITUDE\n" + "=" * 70)
    amp = ag_ret[["ano", "n"]].rename(columns={"n": "novos"}).set_index("ano").reindex(anos, fill_value=0).reset_index()
    amp["acumulado"] = amp.novos.cumsum()
    amp["var_pct_novos"] = amp.novos.pct_change()
    if bal is not None:
        amp = amp.merge(bal, on="ano", how="left")
        amp["var_abs_cadastrados"] = amp.cadastrados.diff()
        amp["var_pct_cadastrados"] = amp.cadastrados.pct_change()
        amp["var_abs_ativos"] = amp.ativos.diff()
        amp["var_pct_ativos"] = amp.ativos.pct_change()
    salvar_csv(amp, "t42_amplitude.csv")
    log("Novos cadastrados por ano e acumulado (microdados, códigos únicos):")
    for r in amp.itertuples():
        log(f"  {r.ano}: novos {br(r.novos):>12} | acumulado {br(r.acumulado):>12}")
    pico = amp.loc[amp.novos.idxmax()]
    log(f"  Pico de novos cadastros: {int(pico.ano)} ({br(pico.novos)})")
    if bal is not None:
        b = bal[(bal.ano >= ANO_INI) & (bal.ano <= ANO_FIM)]
        log("Balanço (dezembro): cadastrados | ativos")
        for r in b.itertuples():
            log(f"  {r.ano}: {br(r.cadastrados):>12} | {br(r.ativos):>12}")
        b = b.dropna(subset=["ativos"])
        b0, b1 = b.iloc[0], b.iloc[-1]
        log(f"  Crescimento {int(b0.ano)}-{int(b1.ano)}: cadastrados x{br(b1.cadastrados / b0.cadastrados, 1)} "
            f"({pct(b1.cadastrados / b0.cadastrados - 1, 0)}); ativos x{br(b1.ativos / b0.ativos, 1)} ({pct(b1.ativos / b0.ativos - 1, 0)})")

    fig, axs = plt.subplots(2, 1, figsize=(LARG, 4.4), sharex=True)
    d0, u0 = escala(amp.novos.max())
    d1, u1 = escala(amp.acumulado.max())
    axs[0].bar(amp.ano, amp.novos / d0, color=COR[0], width=0.72, edgecolor="white", linewidth=0.5)
    axs[0].set_title(f"(a) Novos investidores cadastrados no ano ({u0})", loc="left")
    axs[1].plot(amp.ano, amp.acumulado / d1, color=COR[0], lw=1.6, marker="o", ms=3.2)
    axs[1].set_title(f"(b) Base acumulada de investidores cadastrados ({u1})", loc="left")
    for ax, d in zip(axs, [d0, d1]):
        fmt_eixo(ax, 1 if d == 1e6 else 0)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 2.5, 5, 10]))
        eixo_anos(ax)
    fig.tight_layout()
    salvar_fig(fig, "grafico_01_amplitude_cadastrados.png")

    if bal is not None:
        b = bal[(bal.ano >= ANO_INI) & (bal.ano <= ANO_FIM)]
        fig, axs = plt.subplots(1, 2, figsize=(LARG, 2.6))
        for ax, c, tit, i in zip(axs, ["cadastrados", "ativos"], ["(a) Investidores cadastrados", "(b) Investidores ativos"], [0, 1]):
            bb = b.dropna(subset=[c])
            d, u = escala(bb[c].max())
            ax.plot(bb.ano, bb[c] / d, color=COR[i], lw=1.6, marker=MARC[i], ms=3.2)
            ax.set_title(f"{tit} ({u})", loc="left")
            eixo_anos(ax, ANO_INI, ANO_FIM, 4)
            fmt_eixo(ax, 1 if d == 1e6 else 0)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 2.5, 5, 10]))
            ax.set_ylim(bottom=0)
        fig.tight_layout()
        salvar_fig(fig, "grafico_02_cadastrados_ativos_balanco.png")

    # ---------------- 4.3 DISTRIBUIÇÃO ----------------
    log("\n" + "=" * 70 + "\n4.3 DISTRIBUIÇÃO\n" + "=" * 70)
    pop_t = pop[pop.sexo == "T"]
    pop18 = pop_t[pop_t.idade >= 18].groupby(["ano", "uf"])["pop"].sum().unstack().reindex(columns=UFS)

    def fluxo_estoque(ag, coluna, categorias):
        f = ag[ag[coluna].isin(categorias)].pivot_table(index="ano", columns=coluna, values="n", aggfunc="sum")
        f = f.reindex(index=anos, columns=categorias, fill_value=0).fillna(0)
        return f, f.cumsum()

    # 4.3.1 UF
    log("\n4.3.1 Concentração geográfica (27 UFs)")
    f_uf, e_uf = fluxo_estoque(ag_uf, "uf", UFS)
    t_uf = pd.DataFrame({"ano": anos,
                         "gini_fluxo": [gini(f_uf.loc[a]) for a in anos], "hhi_fluxo": [hhi(f_uf.loc[a]) for a in anos],
                         "gini_estoque": [gini(e_uf.loc[a]) for a in anos], "hhi_estoque": [hhi(e_uf.loc[a]) for a in anos],
                         "gini_pop": [gini(pop18.loc[a]) for a in anos], "hhi_pop": [hhi(pop18.loc[a]) for a in anos]})
    salvar_csv(t_uf, "t431_concentracao_uf.csv")
    log(f"  HHI mínimo teórico (27 categorias iguais): {br(10000 / 27)}")
    log("  ano | Gini fluxo | Gini estoque | Gini pop18+ | HHI fluxo | HHI estoque | HHI pop18+")
    for r in t_uf[t_uf.ano.isin(ANOS_RESUMO)].itertuples():
        log(f"  {r.ano} | {br(r.gini_fluxo, 3)} | {br(r.gini_estoque, 3)} | {br(r.gini_pop, 3)} | {br(r.hhi_fluxo)} | {br(r.hhi_estoque)} | {br(r.hhi_pop)}")
    for a in [ANO_INI, 2014, ANO_FIM]:
        s = (e_uf.loc[a] / e_uf.loc[a].sum()).sort_values(ascending=False)
        log(f"  Participação na base acumulada {a}: " + ", ".join(f"{u} {pct(v)}" for u, v in s.head(6).items()))
    sh_uf = pd.concat({"fluxo": f_uf.div(f_uf.sum(axis=1), axis=0), "estoque": e_uf.div(e_uf.sum(axis=1), axis=0)}, names=["base", "ano"])
    salvar_csv(sh_uf.reset_index(), "t431_participacao_uf.csv")
    painel_concentracao(t_uf, "grafico_03_concentracao_uf.png", rot_pop="População adulta (referência)")

    # 4.3.2 FAIXA ETÁRIA
    log("\n4.3.2 Concentração etária")
    ida = ag_id.dropna(subset=["idade_ext"]).copy()
    ida["idade_adesao"] = ida.idade_ext - (ANO_EXT - ida.ano)
    ida["faixa"] = ida.idade_adesao.map(faixa)
    nomes_fx = [f[2] for f in FAIXAS]
    f_fx = ida.pivot_table(index="ano", columns="faixa", values="n", aggfunc="sum").reindex(index=anos, columns=nomes_fx).fillna(0)
    est = {}
    for a in anos:
        s = ida[ida.ano <= a].copy()
        s["fx"] = (s.idade_ext - (ANO_EXT - a)).map(faixa)
        est[a] = s.groupby("fx")["n"].sum().reindex(nomes_fx).fillna(0)
    e_fx = pd.DataFrame(est).T
    pop_fx = pop_t.assign(fx=pop_t.idade.map(faixa)).groupby(["ano", "fx"])["pop"].sum().unstack().reindex(columns=nomes_fx)
    ad = FAIXAS_ADULTAS
    t_id = pd.DataFrame({"ano": anos,
                         "gini_fluxo": [gini(f_fx.loc[a, ad]) for a in anos], "hhi_fluxo": [hhi(f_fx.loc[a, ad]) for a in anos],
                         "gini_estoque": [gini(e_fx.loc[a, ad]) for a in anos], "hhi_estoque": [hhi(e_fx.loc[a, ad]) for a in anos],
                         "gini_pop": [gini(pop_fx.loc[a, ad]) for a in anos], "hhi_pop": [hhi(pop_fx.loc[a, ad]) for a in anos]})
    salvar_csv(t_id, "t432_concentracao_idade.csv")
    comp = f_fx.div(f_fx.sum(axis=1), axis=0)
    salvar_csv(comp.reset_index(), "t432_composicao_etaria_ingressantes.csv")
    log("  Gini e HHI calculados sobre as 6 faixas adultas; a faixa de 0 a 17 aparece só na composição.")
    log(f"  HHI mínimo teórico (6 faixas iguais): {br(10000 / 6)}")
    log("  ano | Gini fluxo | Gini estoque | Gini pop | HHI fluxo | HHI estoque | HHI pop")
    for r in t_id[t_id.ano.isin(ANOS_RESUMO)].itertuples():
        log(f"  {r.ano} | {br(r.gini_fluxo, 3)} | {br(r.gini_estoque, 3)} | {br(r.gini_pop, 3)} | {br(r.hhi_fluxo)} | {br(r.hhi_estoque)} | {br(r.hhi_pop)}")
    log("  Composição etária dos ingressantes (idade na adesão):")
    for a in ANOS_RESUMO:
        log(f"  {a}: " + " | ".join(f"{ROTULO_FAIXA[k]} {pct(comp.loc[a, k])}" for k in nomes_fx))
    med = {}
    for a, g in ida.groupby("ano"):
        g = g.sort_values("idade_adesao")
        acum = g.n.cumsum() / g.n.sum()
        med[a] = float(g.idade_adesao.iloc[int(np.searchsorted(acum.values, 0.5))])
    log("  Idade mediana na adesão: " + " | ".join(f"{a}: {br(med.get(a, np.nan))}" for a in ANOS_RESUMO))

    fig, ax = plt.subplots(figsize=(LARG, 3.0))
    base_acum = np.zeros(len(anos))
    for k, c in zip(nomes_fx, RAMPA):
        ax.bar(anos, 100 * comp[k].values, bottom=base_acum, color=c, width=0.75,
               edgecolor="white", linewidth=0.6, label=ROTULO_FAIXA[k])
        base_acum += 100 * comp[k].values
    ax.set_ylim(0, 100)
    fmt_eixo(ax, 0, sufixo="%")
    eixo_anos(ax)
    ax.grid(False)
    ax.legend(title="Faixa etária na adesão (anos)", loc="upper left", bbox_to_anchor=(1.0, 1.0))
    fig.tight_layout()
    salvar_fig(fig, "grafico_04_composicao_etaria_ingressantes.png")
    painel_concentracao(t_id, "grafico_05_concentracao_etaria.png", rot_pop="População adulta (referência)")

    # 4.3.3 PROFISSÃO
    log("\n4.3.3 Concentração por profissão declarada")
    pr = ag_prof.copy()
    pr["profissao"] = pr.profissao.fillna("").map(norm)
    n_tot = pr.n.sum()
    n_ni = pr[pr.profissao.isin(PROF_NAO_INFORMADA)].n.sum()
    pr = pr[~pr.profissao.isin(PROF_NAO_INFORMADA)]
    cats = sorted(pr.profissao.unique())
    rotulo = {norm(p): (r.capitalize() if str(r).isupper() else str(r)) for p, r in rot_prof.itertuples(index=False) if p}
    rot = lambda k: rotulo.get(k, k.capitalize())
    log(f"  categorias distintas: {len(cats)} | não informada/vazia: {br(n_ni)} ({pct(n_ni / n_tot)} dos investidores)")
    log(f"  HHI mínimo teórico ({len(cats)} categorias iguais): {br(10000 / len(cats), 1)}")
    f_pr, e_pr = fluxo_estoque(pr, "profissao", cats)
    t_pr = pd.DataFrame({"ano": anos,
                         "gini_fluxo": [gini(f_pr.loc[a]) for a in anos], "hhi_fluxo": [hhi(f_pr.loc[a]) for a in anos],
                         "gini_estoque": [gini(e_pr.loc[a]) for a in anos], "hhi_estoque": [hhi(e_pr.loc[a]) for a in anos]})
    salvar_csv(t_pr, "t433_concentracao_profissao.csv")
    log("  ano | Gini fluxo | Gini estoque | HHI fluxo | HHI estoque")
    for r in t_pr[t_pr.ano.isin(ANOS_RESUMO)].itertuples():
        log(f"  {r.ano} | {br(r.gini_fluxo, 3)} | {br(r.gini_estoque, 3)} | {br(r.hhi_fluxo)} | {br(r.hhi_estoque)}")
    tops = {}
    for a in [2005, 2015, ANO_FIM]:
        s = (e_pr.loc[a] / e_pr.loc[a].sum()).sort_values(ascending=False).head(10)
        tops[a] = s
        log(f"  10 maiores na base acumulada {a} (soma {pct(s.sum())}): " + "; ".join(f"{rot(k)} {pct(v)}" for k, v in s.items()))
    salvar_csv(pd.concat(tops, names=["ano", "profissao"]).rename("participacao").reset_index(), "t433_top10_profissoes.csv")
    painel_concentracao(t_pr, "grafico_06_concentracao_profissao.png", com_pop=False)
    s = tops[ANO_FIM][::-1]
    fig, ax = plt.subplots(figsize=(LARG, 3.2))
    ax.barh([rot(k)[:45] for k in s.index], 100 * s.values, color=COR[0], height=0.65)
    for y, v in enumerate(100 * s.values):
        ax.text(v + 0.2, y, br(v, 1) + "%", va="center", fontsize=8, color="#0b0b0b")
    ax.grid(axis="x")
    ax.grid(axis="y", visible=False)
    fmt_eixo(ax, 0, eixo="x", sufixo="%")
    fig.tight_layout()
    salvar_fig(fig, "grafico_07_top10_profissoes.png")

    # 4.3.4 REPRESENTATIVIDADE
    log("\n4.3.4 Índices de representatividade (participação no TD / participação na população adulta)")
    sh_pop = pop18.div(pop18.sum(axis=1), axis=0)
    ir_est = (e_uf.div(e_uf.sum(axis=1), axis=0) / sh_pop.loc[anos])
    ir_flu = (f_uf.div(f_uf.sum(axis=1), axis=0) / sh_pop.loc[anos])
    salvar_csv(ir_est.reset_index(), "t434_representatividade_uf_estoque.csv")
    salvar_csv(ir_flu.reset_index(), "t434_representatividade_uf_ingressantes.csv")
    for a in [ANO_INI, 2014, ANO_FIM]:
        s = ir_est.loc[a].dropna().sort_values()
        log(f"  UF, base acumulada {a}: maiores {', '.join(f'{u} {br(v, 2)}' for u, v in s[::-1].head(5).items())}"
            f" | menores {', '.join(f'{u} {br(v, 2)}' for u, v in s.head(5).items())}")
        log(f"      UFs sobrerrepresentadas (>1): {int((s > 1).sum())} de 27")
    reg = {r: ir_est[us].mul(sh_pop.loc[anos, us]).sum(axis=1) / sh_pop.loc[anos, us].sum(axis=1) for r, us in REGIAO.items()}
    reg = pd.DataFrame(reg)
    salvar_csv(reg.reset_index(), "t434_representatividade_regiao_estoque.csv")
    log("  Regiões, base acumulada: " + " | ".join(
        f"{a}: " + ", ".join(f"{r} {br(reg.loc[a, r], 2)}" for r in REGIAO) for a in [ANO_INI, 2014, ANO_FIM]))

    for ir, nome, rot in [(ir_est, "grafico_08_representatividade_uf.png", "base acumulada"),
                          (ir_flu, "grafico_08b_representatividade_uf_ingressantes.png", "ingressantes")]:
        ordem = [u for r in REGIAO.values() for u in r]
        with np.errstate(divide="ignore", invalid="ignore"):
            m = np.log2(ir.loc[anos, ordem].T.values.astype(float))
        m = np.clip(np.where(np.isnan(m), -3, m), -3, 3)
        cmap = LinearSegmentedColormap.from_list("div", ["#8f2a2a", "#e34948", "#f0efec", "#3987e5", "#104281"])
        fig, ax = plt.subplots(figsize=(LARG, 5.2))
        im = ax.imshow(m, aspect="auto", cmap=cmap, norm=Normalize(-3, 3), interpolation="nearest")
        ax.set_yticks(range(len(ordem)), ordem)
        ax.set_xticks(range(0, len(anos), 2), [str(a) for a in anos[::2]])
        ax.grid(False)
        y = -0.5
        for r, us in REGIAO.items():
            y += len(us)
            ax.axhline(y, color="white", lw=1.5)
        cb = fig.colorbar(im, ax=ax, ticks=[-3, -2, -1, 0, 1, 2, 3], fraction=0.04, pad=0.02)
        cb.ax.set_yticklabels(["≤0,125", "0,25", "0,5", "1", "2", "4", "≥8"])
        cb.set_label(f"Índice de representatividade ({rot})")
        for s in ax.spines.values():
            s.set_visible(False)
        fig.tight_layout()
        salvar_fig(fig, nome)

    sh_fx_pop = pop_fx[ad].div(pop_fx[ad].sum(axis=1), axis=0)
    ir_fx = (f_fx[ad].div(f_fx[ad].sum(axis=1), axis=0) / sh_fx_pop.loc[anos])
    ir_fx_est = (e_fx[ad].div(e_fx[ad].sum(axis=1), axis=0) / sh_fx_pop.loc[anos])
    salvar_csv(ir_fx.reset_index(), "t434_representatividade_idade_ingressantes.csv")
    salvar_csv(ir_fx_est.reset_index(), "t434_representatividade_idade_estoque.csv")
    for a in ANOS_RESUMO:
        log(f"  Faixa etária, ingressantes {a}: " + " | ".join(f"{ROTULO_FAIXA[k]} {br(ir_fx.loc[a, k], 2)}" for k in ad))
    sh_menor_pop = pop_fx["0-17"] / pop_fx.sum(axis=1)
    log("  Menores de 18: participação nos ingressantes x na população: " + " | ".join(
        f"{a}: {pct(comp.loc[a, '0-17'])} x {pct(sh_menor_pop.loc[a])}" for a in ANOS_RESUMO))
    fig, axs = plt.subplots(2, 3, figsize=(LARG, 4.0), sharex=True, sharey=True)
    ymax = max(2.0, float(np.nanmax(ir_fx[ad].values)) * 1.08)
    for ax, k in zip(axs.flat, ad):
        ax.axhline(1, color=REF, lw=1.0, ls="--")
        ax.plot(anos, ir_fx[k], color=COR[0], lw=1.6)
        ax.set_title(f"{ROTULO_FAIXA[k]} anos", loc="left")
        ax.set_ylim(0, ymax)
        eixo_anos(ax, passo=6)
        fmt_eixo(ax, 1)
    fig.tight_layout()
    salvar_fig(fig, "grafico_09_representatividade_etaria.png")

    pop_ad = pop[pop.idade >= 18].groupby(["ano", "sexo"])["pop"].sum().unstack()
    sh_f_pop = pop_ad["F"] / pop_ad["T"]
    g = ag_gen[ag_gen.genero.isin(["M", "F"])].pivot_table(index="ano", columns="genero", values="n", aggfunc="sum").reindex(anos).fillna(0)
    ge = g.cumsum()
    t_g = pd.DataFrame({"ano": anos, "mulheres_ingressantes": (g.F / g.sum(axis=1)).values,
                        "mulheres_base_acumulada": (ge.F / ge.sum(axis=1)).values,
                        "mulheres_pop18": sh_f_pop.loc[anos].values})
    t_g["ir_ingressantes"] = t_g.mulheres_ingressantes / t_g.mulheres_pop18
    t_g["ir_base_acumulada"] = t_g.mulheres_base_acumulada / t_g.mulheres_pop18
    salvar_csv(t_g, "t434_genero.csv")
    if ARQ_PERFIL.exists():
        perf = pd.read_csv(ARQ_PERFIL, sep=";").set_index("ano")
        reg_md = pd.DataFrame({r: e_uf[us].sum(axis=1) for r, us in REGIAO.items()})
        reg_md = reg_md.div(reg_md.sum(axis=1), axis=0)
        mapa = {"N": "norte", "NE": "nordeste", "CO": "centro_oeste", "SE": "sudeste", "S": "sul"}
        conf = pd.DataFrame({"ano": anos})
        conf["mulheres_microdados"] = (ge.F / ge.sum(axis=1)).values
        for r, c in mapa.items():
            conf[f"{c}_microdados"] = reg_md[r].values
        conf = conf.merge(perf.add_suffix("_balanco").reset_index(), on="ano", how="inner")
        salvar_csv(conf, "t43_conferencia_perfil_balanco.csv")
        log("  Conferência com o perfil oficial do balanço (base acumulada em dezembro; microdados x balanço):")
        for r in conf[conf.ano.isin([2009, 2014, 2019, ANO_FIM])].itertuples():
            log(f"  {r.ano}: mulheres {pct(r.mulheres_microdados)} x {pct(r.mulheres_balanco)} | "
                + " | ".join(f"{k} {pct(getattr(r, c + '_microdados'))} x {pct(getattr(r, c + '_balanco'))}" for k, c in mapa.items()))
        log("      Diferenças pequenas esperadas: os microdados usam a UF e o cadastro atuais e não incluem contas que saíram da base.")
    log("  Gênero (% mulheres): ingressantes | base acumulada | população 18+ | IR ingressantes")
    for r in t_g[t_g.ano.isin(ANOS_RESUMO)].itertuples():
        log(f"  {r.ano}: {pct(r.mulheres_ingressantes)} | {pct(r.mulheres_base_acumulada)} | {pct(r.mulheres_pop18)} | {br(r.ir_ingressantes, 2)}")
    fig, ax = plt.subplots(figsize=(LARG, 2.8))
    linha(ax, anos, 100 * t_g.mulheres_ingressantes, 0, "Mulheres entre os ingressantes do ano")
    linha(ax, anos, 100 * t_g.mulheres_base_acumulada, 1, "Mulheres na base acumulada")
    linha(ax, anos, 100 * t_g.mulheres_pop18, 0, "Mulheres na população adulta (referência)", ref=True)
    ax.set_ylim(0, max(60, 100 * t_g[["mulheres_ingressantes", "mulheres_pop18"]].max().max() + 8))
    fmt_eixo(ax, 0, sufixo="%")
    eixo_anos(ax)
    ax.legend(loc="lower right")
    fig.tight_layout()
    salvar_fig(fig, "grafico_10_genero.png")

    # ---------------- 4.4 PROFUNDIDADE ----------------
    log("\n" + "=" * 70 + "\n4.4 PROFUNDIDADE\n" + "=" * 70)
    if bal is not None:
        b = bal[(bal.ano >= ANO_INI_RAZAO) & (bal.ano <= ANO_FIM)].dropna(subset=["ativos"]).copy()
        b["razao"] = b.ativos / b.cadastrados
        salvar_csv(b, "t44_razao_ativos_cadastrados.csv")
        log("  Razão ativos/cadastrados (balanço, dezembro): " + " | ".join(f"{r.ano}: {pct(r.razao)}" for r in b.itertuples()))
        fig, ax = plt.subplots(figsize=(LARG, 2.6))
        ax.plot(b.ano, 100 * b.razao, color=COR[0], lw=1.6, marker="o", ms=3.2)
        for r in [b.iloc[0], b.loc[b.razao.idxmax()], b.iloc[-1]]:
            ax.annotate(br(100 * r.razao, 1) + "%", (r.ano, 100 * r.razao), textcoords="offset points",
                        xytext=(0, 6), ha="center", fontsize=8)
        ax.set_ylim(0, max(100 * b.razao) * 1.25)
        fmt_eixo(ax, 0, sufixo="%")
        eixo_anos(ax, ANO_INI_RAZAO, ANO_FIM, 2)
        fig.tight_layout()
        salvar_fig(fig, "grafico_11_razao_ativos_cadastrados.png")
    ret = ag_ret.set_index("ano").reindex(anos).reset_index()
    ret["pct_operou"] = ret.operou / ret.n
    ret["pct_conta_nao_desativada"] = ret.ativos / ret.n
    ret["anos_desde_adesao"] = ANO_EXT - ret.ano
    salvar_csv(ret, "t44_retencao_coorte.csv")
    log(f"  Retenção por coorte (situação na posição da base, {DATA_EXTRACAO}):")
    log("  coorte | n | % operou nos últimos 12 meses | % conta não desativada | anos desde a adesão")
    for r in ret.itertuples():
        log(f"  {r.ano} | {br(r.n):>11} | {pct(r.pct_operou):>7} | {pct(r.pct_conta_nao_desativada):>7} | {r.anos_desde_adesao}")
    fig, ax = plt.subplots(figsize=(LARG, 2.9))
    linha(ax, ret.ano, 100 * ret.pct_operou, 0, "Operaram nos últimos 12 meses")
    linha(ax, ret.ano, 100 * ret.pct_conta_nao_desativada, 1, "Mantêm conta não desativada")
    ax.set_ylim(0, 100)
    fmt_eixo(ax, 0, sufixo="%")
    eixo_anos(ax)
    ax.set_xlabel("Ano de adesão (coorte)")
    sec = ax.secondary_xaxis("top", functions=(lambda x: ANO_EXT - x, lambda y: ANO_EXT - y))
    sec.set_xlabel("Anos decorridos desde a adesão (até mar/2026)")
    sec.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(loc="upper left")
    fig.tight_layout()
    salvar_fig(fig, "grafico_12_retencao_coorte.png")

    log("\nArquivos gerados: resultados/*.csv e graficos/*.png. Envie este resumo.txt.")


if __name__ == "__main__":
    etapa = sys.argv[1] if len(sys.argv) > 1 else ""
    if etapa == "parte1":
        parte1()
    elif etapa == "parte2":
        parte2()
    else:
        print(__doc__)
