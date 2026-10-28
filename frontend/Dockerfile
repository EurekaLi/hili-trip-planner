FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
ARG VITE_AMAP_WEB_JS_KEY=""
ARG VITE_AMAP_SECURITY_JS_CODE=""
ARG VITE_AMAP_SERVICE_HOST=""
ENV VITE_AMAP_WEB_JS_KEY=${VITE_AMAP_WEB_JS_KEY} \
    VITE_AMAP_SECURITY_JS_CODE=${VITE_AMAP_SECURITY_JS_CODE} \
    VITE_AMAP_SERVICE_HOST=${VITE_AMAP_SERVICE_HOST} \
    VITE_API_BASE_URL=""
RUN npm run build


FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
