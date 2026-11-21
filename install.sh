#!/bin/bash
# Instalador do Painel de Reservas da Mansão Eusébio
# Segue as regras da VPS: Caddyfile só recebe bloco novo no final, com cópia e validação antes.
set -e
APP=/opt/mansao-admin
DOM=admin.mansaoeusebio.com.br
RAW=https://raw.githubusercontent.com/renatabezerrag-ui/mansaoeusebio/painel
CF=/etc/caddy/Caddyfile

echo "== 1/5 Baixando o painel"
command -v python3 >/dev/null || { apt-get update -qq && apt-get install -y -qq python3; }
mkdir -p "$APP"
curl -fsSL "$RAW/app.py" -o "$APP/app.py"
curl -fsSL "$RAW/painel.html" -o "$APP/painel.html"

echo "== 2/5 Escolhendo a porta"
if [ -f "$APP/porta" ]; then PORT=$(cat "$APP/porta"); else
  PORT=8787; while ss -ltn | grep -q ":$PORT "; do PORT=$((PORT+1)); done
  echo "$PORT" > "$APP/porta"; fi

echo "== 3/5 Senha"
if [ ! -f "$APP/SENHA_INICIAL.txt" ]; then
  SENHA=$(tr -dc 'a-z0-9' </dev/urandom | head -c 8)
  echo "$SENHA" > "$APP/SENHA_INICIAL.txt"; chmod 600 "$APP/SENHA_INICIAL.txt"
  python3 "$APP/app.py" --set-password "$SENHA" >/dev/null
fi

echo "== 4/5 Ligando o servico mansao-admin"
cat > /etc/systemd/system/mansao-admin.service <<UNIT
[Unit]
Description=Painel de reservas Mansao Eusebio
After=network.target
[Service]
ExecStart=/usr/bin/python3 $APP/app.py --port $PORT
WorkingDirectory=$APP
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable mansao-admin >/dev/null 2>&1
systemctl restart mansao-admin

echo "== 5/5 Caddy (so acrescentando no final)"
if grep -q "$DOM" "$CF"; then
  echo "O Caddyfile ja tem $DOM. Nada foi mexido."
else
  BK="$CF.bak-$(date +%Y%m%d-%H%M%S)"
  cp "$CF" "$BK"
  printf '\n# Painel de reservas Mansao Eusebio\n%s {\n\treverse_proxy 127.0.0.1:%s\n}\n' "$DOM" "$PORT" >> "$CF"
  if caddy validate --config "$CF" --adapter caddyfile >/dev/null 2>&1; then
    systemctl reload caddy
    echo "Caddyfile atualizado. Copia de seguranca: $BK"
  else
    cp "$BK" "$CF"
    echo "ERRO na validacao do Caddy. Voltei a copia e nada mudou. Mande esta tela para o Claude."
    exit 1
  fi
fi

sleep 2
if curl -fsS "http://127.0.0.1:$PORT/api/datas" >/dev/null; then OK=SIM; else OK=NAO; fi
echo ""
echo "=========================================="
echo " PAINEL INSTALADO (funcionando: $OK)"
echo " Endereco: https://$DOM"
echo " Senha inicial: $(cat $APP/SENHA_INICIAL.txt)"
echo "=========================================="
