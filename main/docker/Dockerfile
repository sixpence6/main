FROM node:24-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

WORKDIR /app
COPY tsconfig.json vite.config.ts ./
COPY index.html ./
COPY src ./src
RUN npm run build

FROM nginx:1.29-alpine AS runtime
ARG NGINX_TEMPLATE=docker/nginx.conf.template
COPY ${NGINX_TEMPLATE} /etc/nginx/templates/default.conf.template
COPY docker/security-headers.conf /etc/nginx/security-headers.conf
COPY docker/hsts.conf /etc/nginx/hsts.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80 443
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || wget --no-check-certificate -q -O /dev/null https://127.0.0.1/ || exit 1
