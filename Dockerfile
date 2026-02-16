FROM php:8.2-apache

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip curl \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /var/www/html/iddiaarb
COPY . /var/www/html/iddiaarb

RUN python3 -m pip install --break-system-packages -e .

# Ensure required directories exist and are writable
RUN mkdir -p data .state site/cache \
    && chown -R www-data:www-data data .state site/cache

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://localhost/iddiaarb/site/health.php || exit 1
