# ---------------------------------------------------------------------------
# Landing page (static HTML/CSS/JS served by nginx)
# ---------------------------------------------------------------------------
FROM nginx:alpine

COPY index.html /usr/share/nginx/html/index.html
COPY favicon.svg /usr/share/nginx/html/favicon.svg
COPY css /usr/share/nginx/html/css
COPY js /usr/share/nginx/html/js

EXPOSE 80
