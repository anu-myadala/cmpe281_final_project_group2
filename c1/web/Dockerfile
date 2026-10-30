# SCICP C1 web: build the Vite bundle, serve it with nginx on 8080 and proxy the API paths.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# The C2/C3 base paths are same-origin and proxied by nginx (see nginx/default.conf).
ENV VITE_C2_BASE=/c2/api/v1 VITE_C3_BASE=/c3/api/v1
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
CMD ["nginx", "-g", "daemon off;"]
