FROM node:24-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
COPY main/package.json ./main/package.json
RUN npm ci --workspace main --no-audit --no-fund

WORKDIR /app/main
COPY main/tsconfig.json main/vite.config.ts ./
COPY main/index.html ./
COPY main/src ./src
RUN npm run build

FROM nginx:1.29-alpine AS runtime
ARG NGINX_TEMPLATE=main/docker/nginx.conf.template
COPY ${NGINX_TEMPLATE} /etc/nginx/templates/default.conf.template
COPY main/docker/security-headers.conf /etc/nginx/security-headers.conf
COPY main/docker/hsts.conf /etc/nginx/hsts.conf
COPY --from=build /app/main/dist /usr/share/nginx/html
EXPOSE 80 443
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || wget --no-check-certificate -q -O /dev/null https://127.0.0.1/ || exit 1
