# syntax=docker/dockerfile:1.7

# --- build ------------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /web

# Dependencies in their own layer: they change far less often than the source.
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/tsconfig.json web/vite.config.ts web/index.html ./
COPY web/src ./src

# `npm run build` typechecks before it bundles, so a type error fails the image
# rather than shipping.
RUN npm run build

# --- runtime ----------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY docker/nginx-upgrade.conf /etc/nginx/conf.d/00-upgrade.conf
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html

# nginx's own unprivileged image variant would need a port above 1024; this
# stays on 80 inside the container, which is not the host's 80.
EXPOSE 80
