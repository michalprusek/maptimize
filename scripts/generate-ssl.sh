#!/bin/bash
# Script pro generování SSL certifikátu pro maptimize.utia.cas.cz
# Vyžaduje sudo přístup

set -e

DOMAIN="maptimize.utia.cas.cz"
EMAIL="admin@utia.cas.cz"

echo "=== Generování SSL certifikátu pro $DOMAIN ==="

# Kontrola, zda běží nginx-main
if docker ps | grep -q nginx-main; then
    echo "Zastavuji nginx-main dočasně..."
    docker stop nginx-main
    NGINX_WAS_RUNNING=true
else
    NGINX_WAS_RUNNING=false
fi

# Generování certifikátu
echo "Spouštím certbot..."
sudo certbot certonly \
    --standalone \
    --preferred-challenges http \
    -d "$DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL"

# Restart nginx-main pokud běžel
if [ "$NGINX_WAS_RUNNING" = true ]; then
    echo "Spouštím nginx-main..."
    docker start nginx-main
fi

echo ""
echo "=== Hotovo! ==="
echo "Certifikát je v: /etc/letsencrypt/live/$DOMAIN/"
echo ""
echo "Nyní můžete spustit maptimize:"
echo "  cd /home/cvat/maptimize"
echo "  docker compose -f docker-compose.prod.yml up -d"
