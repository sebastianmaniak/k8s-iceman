FROM node:22-slim

ENV NODE_ENV=production

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --production && npm cache clean --force

COPY src ./src

RUN mkdir -p /data/auth && chown -R node:node /app /data

USER node

CMD ["node", "src/index.js"]
