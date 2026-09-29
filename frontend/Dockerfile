# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS build
WORKDIR /src
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
COPY viewer-sdk /viewer-sdk
RUN npm run build

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist/agora/browser /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=5 CMD wget -q -O - http://127.0.0.1/healthz
