#!/usr/bin/env python3
"""Painel de reservas da Mansão Eusébio — Python puro + SQLite, sem dependências."""
import hashlib, hmac, http.cookies, json, os, secrets, sqlite3, sys, time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "reservas.db")
HTML = os.path.join(BASE, "painel.html")
SESSAO_DIAS = 30
CAMPOS = ["data_inicio", "data_fim", "status", "nome", "telefone", "contato", "tipo_evento",
          "convidados", "valor_total", "valor_pago", "forma_pagamento", "buffet", "pedidos", "observacoes"]
STATUS = {"pre", "confirmada", "cancelada"}
tentativas = {}


def conn():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def iniciar():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS reservas(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          data_inicio TEXT NOT NULL, data_fim TEXT, status TEXT NOT NULL DEFAULT 'pre',
          nome TEXT, telefone TEXT, contato TEXT, tipo_evento TEXT, convidados INTEGER,
          valor_total REAL, valor_pago REAL, forma_pagamento TEXT, buffet TEXT,
          pedidos TEXT, observacoes TEXT, criado_em TEXT, atualizado_em TEXT);
        CREATE TABLE IF NOT EXISTS config(chave TEXT PRIMARY KEY, valor TEXT);
        CREATE TABLE IF NOT EXISTS sessoes(token TEXT PRIMARY KEY, expira REAL);
        """)
        if not c.execute("SELECT 1 FROM config WHERE chave='semeado'").fetchone():
            agora = time.strftime("%Y-%m-%d %H:%M")
            for ini, fim in [("2026-11-27", "2026-11-29"), ("2026-12-12", None), ("2026-12-19", None)]:
                c.execute("INSERT INTO reservas(data_inicio,data_fim,status,nome,observacoes,criado_em,atualizado_em)"
                          " VALUES(?,?,?,?,?,?,?)", (ini, fim, "confirmada", "Preencher nome",
                                                       "Reserva já existente, completar os dados.", agora, agora))
            c.execute("INSERT INTO config VALUES('semeado','1')")


def hash_senha(senha, sal=None):
    sal = sal or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), sal.encode(), 200_000).hex()
    return f"{sal}${h}"


def definir_senha(senha):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO config VALUES('senha',?)", (hash_senha(senha),))
        c.execute("DELETE FROM sessoes")


def conferir_senha(senha):
    with conn() as c:
        r = c.execute("SELECT valor FROM config WHERE chave='senha'").fetchone()
    if not r:
        return False
    sal, _ = r["valor"].split("$", 1)
    return hmac.compare_digest(hash_senha(senha, sal), r["valor"])


def datas_ocupadas():
    out = set()
    with conn() as c:
        for r in c.execute("SELECT data_inicio,data_fim FROM reservas WHERE status IN ('pre','confirmada')"):
            try:
                d = date.fromisoformat(r["data_inicio"])
                f = date.fromisoformat(r["data_fim"]) if r["data_fim"] else d
            except ValueError:
                continue
            n = 0
            while d <= f and n < 31:
                out.add(d.isoformat()); d += timedelta(days=1); n += 1
    return sorted(out)


def limpar(dados):
    r = {}
    for k in CAMPOS:
        v = dados.get(k)
        if isinstance(v, str):
            v = v.strip()[:2000]
        if v in ("", None):
            v = None
        r[k] = v
    if not r["data_inicio"]:
        raise ValueError("Informe a data do evento.")
    for k in ("data_inicio", "data_fim"):
        if r[k]:
            date.fromisoformat(r[k])
    if r["data_fim"] and r["data_fim"] < r["data_inicio"]:
        raise ValueError("A data final não pode ser antes da data inicial.")
    if r["status"] not in STATUS:
        r["status"] = "pre"
    for k in ("valor_total", "valor_pago"):
        if r[k] is not None:
            r[k] = float(str(r[k]).replace(".", "").replace(",", ".")) if isinstance(r[k], str) else float(r[k])
    if r["convidados"] is not None:
        r["convidados"] = int(r["convidados"])
    return r


def conflito(r, ignorar_id=None):
    if r["status"] == "cancelada":
        return None
    ini = r["data_inicio"]; fim = r["data_fim"] or ini
    with conn() as c:
        q = c.execute("SELECT id,nome,data_inicio,COALESCE(data_fim,data_inicio) f FROM reservas WHERE status!='cancelada' "
                      "AND data_inicio<=? AND COALESCE(data_fim,data_inicio)>=? AND id!=?", (fim, ini, ignorar_id or 0)).fetchone()
    return dict(q) if q else None


class H(BaseHTTPRequestHandler):
    server_version = "Painel"

    def log_message(self, *a):
        pass

    def enviar(self, code, corpo, tipo="application/json; charset=utf-8", extra=None):
        b = corpo if isinstance(corpo, bytes) else json.dumps(corpo, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def corpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            raise ValueError("Dados grandes demais.")
        return json.loads(self.rfile.read(n) or b"{}")

    def token(self):
        ck = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return ck["sessao"].value if "sessao" in ck else None

    def logado(self):
        t = self.token()
        if not t:
            return False
        with conn() as c:
            r = c.execute("SELECT expira FROM sessoes WHERE token=?", (t,)).fetchone()
        return bool(r and r["expira"] > time.time())

    def ip(self):
        return self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()

    def do_OPTIONS(self):
        self.enviar(204, b"", extra={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET"})

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/api/datas":
            return self.enviar(200, {"reservadas": datas_ocupadas()}, extra={"Access-Control-Allow-Origin": "*"})
        if p in ("/", "/index.html"):
            with open(HTML, "rb") as f:
                return self.enviar(200, f.read(), "text/html; charset=utf-8")
        if p == "/api/eu":
            return self.enviar(200, {"logado": self.logado()})
        if p == "/api/reservas":
            if not self.logado():
                return self.enviar(401, {"erro": "Faça login."})
            with conn() as c:
                rs = [dict(r) for r in c.execute("SELECT * FROM reservas ORDER BY data_inicio")]
            return self.enviar(200, rs)
        self.enviar(404, {"erro": "Não encontrado"})

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            d = self.corpo()
        except Exception:
            return self.enviar(400, {"erro": "Dados inválidos."})
        if p == "/api/login":
            ip = self.ip(); agora = time.time()
            lst = [t for t in tentativas.get(ip, []) if agora - t < 900]
            if len(lst) >= 8:
                return self.enviar(429, {"erro": "Muitas tentativas. Espere 15 minutos."})
            if not conferir_senha(str(d.get("senha", ""))):
                lst.append(agora); tentativas[ip] = lst
                return self.enviar(401, {"erro": "Senha errada."})
            tentativas.pop(ip, None)
            t = secrets.token_urlsafe(32)
            with conn() as c:
                c.execute("DELETE FROM sessoes WHERE expira<?", (agora,))
                c.execute("INSERT INTO sessoes VALUES(?,?)", (t, agora + SESSAO_DIAS * 86400))
            ck = f"sessao={t}; Path=/; Max-Age={SESSAO_DIAS*86400}; HttpOnly; Secure; SameSite=Strict"
            return self.enviar(200, {"ok": True}, extra={"Set-Cookie": ck})
        if p == "/api/sair":
            with conn() as c:
                c.execute("DELETE FROM sessoes WHERE token=?", (self.token() or "",))
            return self.enviar(200, {"ok": True}, extra={"Set-Cookie": "sessao=; Path=/; Max-Age=0"})
        if not self.logado():
            return self.enviar(401, {"erro": "Faça login."})
        if p == "/api/senha":
            if not conferir_senha(str(d.get("atual", ""))):
                return self.enviar(400, {"erro": "A senha atual está errada."})
            nova = str(d.get("nova", ""))
            if len(nova) < 6:
                return self.enviar(400, {"erro": "A nova senha precisa ter pelo menos 6 letras ou números."})
            definir_senha(nova)
            return self.enviar(200, {"ok": True})
        if p == "/api/reservas":
            return self.salvar(d, None)
        self.enviar(404, {"erro": "Não encontrado"})

    def salvar(self, d, rid):
        try:
            r = limpar(d)
        except (ValueError, TypeError) as e:
            return self.enviar(400, {"erro": str(e) or "Confira os campos."})
        x = conflito(r, rid)
        if x and not d.get("forcar"):
            return self.enviar(409, {"erro": f"Essa data já tem reserva de {x['nome'] or 'outra pessoa'}.", "conflito": x})
        agora = time.strftime("%Y-%m-%d %H:%M")
        with conn() as c:
            if rid:
                c.execute(f"UPDATE reservas SET {','.join(k+'=?' for k in CAMPOS)},atualizado_em=? WHERE id=?",
                          [r[k] for k in CAMPOS] + [agora, rid])
            else:
                cur = c.execute(f"INSERT INTO reservas({','.join(CAMPOS)},criado_em,atualizado_em) VALUES({','.join('?'*len(CAMPOS))},?,?)",
                                [r[k] for k in CAMPOS] + [agora, agora])
                rid = cur.lastrowid
        self.enviar(200, {"ok": True, "id": rid})

    def do_PUT(self):
        p = urlparse(self.path).path
        if not self.logado():
            return self.enviar(401, {"erro": "Faça login."})
        if p.startswith("/api/reservas/"):
            try:
                rid = int(p.rsplit("/", 1)[1]); d = self.corpo()
            except Exception:
                return self.enviar(400, {"erro": "Dados inválidos."})
            return self.salvar(d, rid)
        self.enviar(404, {"erro": "Não encontrado"})

    def do_DELETE(self):
        p = urlparse(self.path).path
        if not self.logado():
            return self.enviar(401, {"erro": "Faça login."})
        if p.startswith("/api/reservas/"):
            try:
                rid = int(p.rsplit("/", 1)[1])
            except ValueError:
                return self.enviar(400, {"erro": "Dados inválidos."})
            with conn() as c:
                c.execute("DELETE FROM reservas WHERE id=?", (rid,))
            return self.enviar(200, {"ok": True})
        self.enviar(404, {"erro": "Não encontrado"})


if __name__ == "__main__":
    iniciar()
    if "--set-password" in sys.argv:
        definir_senha(sys.argv[sys.argv.index("--set-password") + 1]); print("Senha definida."); sys.exit()
    porta = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8787
    print(f"Painel em 127.0.0.1:{porta}")
    ThreadingHTTPServer(("127.0.0.1", porta), H).serve_forever()
