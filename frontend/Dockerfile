# syntax=docker/dockerfile:1

# --- Stage 1: install deps ---------------------------------------------------
FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --- Stage 2: build -----------------------------------------------------------
FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Baked into the client bundle at build time (Next.js public env vars are
# resolved at build, not at container start) — override via --build-arg.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_API_KEY=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_API_KEY=$NEXT_PUBLIC_API_KEY

RUN npm run build

# --- Stage 3: runtime -----------------------------------------------------
FROM node:20-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    PORT=5173 \
    HOSTNAME=0.0.0.0

# `output: "standalone"` (next.config.mjs) traces only the deps actually used
# into .next/standalone, so the runtime image ships no node_modules.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

# node:20-slim already ships a uid-1000 "node" user — reuse it instead of
# useradd'ing a new one (which collides on that uid).
RUN chown -R node:node /app
USER node
EXPOSE 5173

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://localhost:5173').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
