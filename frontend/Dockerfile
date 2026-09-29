FROM nginx:1.27-alpine
# Runs as the image's "nginx" user (UID 101), listens on 8080, writes only to
# /tmp and /var/cache/nginx (emptyDir / tmpfs), so the root FS can be read-only.
COPY nginx.conf /etc/nginx/nginx.conf
COPY public/ /usr/share/nginx/html/
USER 101
EXPOSE 8080
