FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
COPY css /usr/share/nginx/html/css
COPY js /usr/share/nginx/html/js

EXPOSE 80

HEALTHCHECK --interval=20s --timeout=4s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
