# Next.js frontend (6.1). Build: node:22-alpine. Run: distroless Node 22, non-root, no shell.
# `next build` with output: "standalone" bundles only the files the server needs.
ARG NODE_IMAGE=node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402
ARG RUNTIME_IMAGE=gcr.io/distroless/nodejs22-debian13:nonroot@sha256:39c66687298417dbf3ff2fb8b6012c20554494e16414ef7afd07b9abbb45b501

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# public/ may be empty, and git does not keep empty directories: make sure it exists for the copy below.
RUN mkdir -p public && npm run build

FROM ${RUNTIME_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=build --chown=nonroot:nonroot /app/.next/standalone ./
COPY --from=build --chown=nonroot:nonroot /app/.next/static ./.next/static
COPY --from=build --chown=nonroot:nonroot /app/public ./public
USER nonroot
EXPOSE 3000
# The distroless entrypoint is node itself.
CMD ["server.js"]
